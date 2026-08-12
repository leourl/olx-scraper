# 14 — Cadastro manual de anúncio por link

## Contexto e problema

A coleta hoje só entra pelo caminho **busca por termo** (`scrape`): ou o anúncio
aparece numa listagem da OLX para aquele termo, ou ele nunca entra no banco.
Não há como cadastrar um anúncio específico que o usuário encontrou por acaso
(ex.: num grupo de WhatsApp, num site de ofertas, numa busca solta) sem esperar
que ele apareça num `scrape` futuro — que pode nem acontecer, se o anúncio usa
um título fora dos termos monitorados.

O usuário quer: **colar o link de um anúncio da OLX num campo de texto → o
sistema valida, busca a página, faz a checagem e cadastra no banco**, seguindo o
mesmo pipeline de dados do resto da app (RawAd → upsert → specs via
regex + LLM), sem duplicar lógica.

### O que já existe e vamos reusar

- `app/scrapers/client.py::OlxClient` — transporte `curl_cffi` com impersonate
  de Chrome (D-025), politeness 1 req/s, retry 5xx, 404/410 → retorna,
  403 → `ScrapeBlockedError`. **Nenhuma mudança aqui.**
- `app/scrapers/olx.py::OlxScraper._parse_detail` — extrai do JSON-LD
  `@type: Product`: `description`, `images` (todas as `contentUrl`) e
  `price_cents`. Falta só o `name` (título), que o JSON-LD já traz.
- `app/services/ad_service.py::upsert_raw` — dedup por URL, atualiza
  descrição/imagens/preço de existentes, **re-ativa** anúncios marcados como
  removidos (`is_active=True`, limpa `removed_at`).
- `app/services/runner.py::run_process` (com `ad_id=`) — extração de specs de um
  anúncio específico (regex + DeepSeek), já centralizada.
- Página `/run` (`run.html`) — tela de operações, home natural do painel novo.

## Objetivos

- Cadastrar um anúncio a partir do **link direto da OLX**, com checagem completa
  (link válido? página existe? página é de anúncio?) e upsert no banco.
- **Extrair specs na hora** (se `DEEPSEEK_KEY` presente e o usuário quiser) —
  anúncio cadastrado já nasce completo, pronto para filtros/gráfico/ofertas.
- **Link duplicado** → re-buscar e atualizar (preço/descrição/imagens podem ter
  mudado), reativando se havia sido marcado como removido.
- Expor o mesmo fluxo em **UI (`/run`), API (`POST /api/ads/import`) e CLI**
  (`flask add`), compartilhando a lógica de coleta em `runner.py` (AGENTS: não
  duplicar coleta).
- Registrar a decisão (D-026) em `docs/specs/00-decisoes.md`.
- 100% offline nos testes (mesmo padrão de `test_runner`/`test_check`).

## Não-objetivos (fora de escopo por ora)

- **Cadastro de vários links de uma vez** (batch) — vira uma "run" e merece
  outra spec; aqui é 1 link por request.
- **Edição manual de specs** — continua em `/review` (D-003, manual).
- **Cadastro de link de outro marketplace** (Mercado Livre, Enjoei…) — o parser
  é específico da OLX; generalizar é outra spec.
- **Fila/thread em background** para a extração de specs — o request síncrono
  (1 ad, ~1–3s + ~2s de LLM) é aceitável; `RunManager` (uma run por vez) não é
  tocado nem disputa o recurso.
- **Registro no `run_history`** — é cadastro de 1 ad, não uma run.

---

## 1. `app/scrapers/olx.py` — expor o título no detalhe

`_parse_detail` (`olx.py:100-122`) hoje retorna `description`, `images` e
`price_cents`; o JSON-LD `Product` também tem `name` (o título). Adicionar a
chave sem quebrar nada (mudança aditiva):

```python
return {
    "name": data.get("name"),
    "description": ...,
    "images": urls,
    "price_cents": ...,
}
```

- `run_check` (`runner.py:157`) usa a truthiness do dict → continua igual.
- `test_olx.py::test_parse_detail_good/bad` só assere nas chaves existentes →
  seguem passando (novo assert para `name` será adicionado).

### Fix: `olx_id_from_url` ignora fragmento/query

`olx_id_from_url` (`olx.py:19-22`) hoje faz `url.split("?")[0].rstrip("/")` e
depois a regex `-(\d{5,})$`. Um link copiado do navegador pode ter **fragmento**
(ex.: `...-1523803879#photos` ou `...?origem=ml`): a regex falha e o cadastro
manual devolveria 400 indevido. Corrigir para ignorar fragmento **e** query:

```python
def olx_id_from_url(url: str) -> str | None:
    path = url.split("#")[0].split("?")[0].rstrip("/")
    m = _ID_RE.search(path)
    return m.group(1) if m else None
```

O upsert continua deduplicando pela URL **exata** como veio (fragmento incluso),
mas a extração do `olx_id` passa a ser tolerante a `#`/`?`.

## 2. `app/services/runner.py` — `import_single_ad`

Nova função, no arquivo que já centraliza a coleta:

```python
def import_single_ad(app, url: str, process: bool = True) -> dict:
    """Cadastra um anúncio a partir do link direto da OLX.

    Retorna {'status': 'ok'|'removed'|'not_an_ad', ...}. Levanta
    ValueError (link inválido), ScrapeBlockedError (403) e NETWORK_ERRORS
    (rede) — o chamador mapeia para HTTP.
    """
```

### Passos

1. **Validação/normalização da URL**
   - `url = url.strip()`; vazio → `ValueError("informe o link do anúncio")`.
   - **Normalização**: remove query string e fragmento (`?lis=...`, `#fotos`)
     **antes** de validar/buscar/cadastrar → URL canônica, idêntica à que o
     scraper grava. Evita 403 do Cloudflare por parâmetro de tracking e
     duplicatas por query distinta (ex.: `?utm_source=wa` vs sem query).
   - `urlparse(url)` → exige `scheme in ("http", "https")` e host terminando em
     `olx.com.br` → senão `ValueError("link não é da OLX")`.
   - `olx_id_from_url(url)` (tolerante a `#`/`?`, ver §1) → `None` →
     `ValueError("link não parece ser um anúncio da OLX")`.

2. **Fetch + parse** (mesmo padrão de `run_scrape`/`run_enrich`):
   ```python
   client = OlxClient(cfg["USER_AGENT"], timeout=cfg["SCRAPER_TIMEOUT"],
                      delay=cfg["SCRAPER_DELAY"], impersonate=cfg["SCRAPER_IMPERSONATE"])
   scraper = OlxScraper(client)
   resp = client.get(url)
   data = scraper._parse_detail(resp.text)
   ```
   - `404/410` → retorna `{"status": "removed", "url": url}` (não cadastra).
   - `200` sem JSON-LD (`data == {}`) → retorna
     `{"status": "not_an_ad", "url": url}`.
   - `403` → `ScrapeBlockedError` propaga (mesmo contrato das runs).
   - Erro de rede → `NETWORK_ERRORS` propaga.

3. **Upsert com refresh**:
   ```python
   raw = RawAd(
       olx_id=olx_id,
       title=data["name"] or url,          # name nunca deve vir None no JSON-LD
       url=url,
       price_cents=data.get("price_cents"),
       description=data.get("description"),
       images=data.get("images") or [],
   )
   ad, created = ad_service.upsert_raw(raw, refresh=True)   # created = é novo no banco
   ```
   - Duplicado → `upsert_raw` **re-busca e atualiza** (ver §2a) e **re-ativa** se
     estava `is_active=False`.

### §2a. `app/services/ad_service.py` — `upsert_raw` com `refresh=True`

O `upsert_raw` atual (`ad_service.py:59-98`) **não** atualiza `price_cents` de
existentes e só preenche `description` se estiver vazia
(`if not existing.description and ad.description`) — se o preço/descrição/título
mudarem na OLX, o recadastro manual não atualizaria. Para cumprir o requisito
"re-busca e atualiza", a assinatura ganha o flag **`refresh: bool = False`**
(comportamento atual preservado no caminho de scrape/enrich; só o cadastro
manual passa `refresh=True`):

```python
def upsert_raw(ad: RawAd, refresh: bool = False) -> tuple[Ad, bool]:
    existing = Ad.query.filter_by(url=ad.url).first()
    if existing:
        changed = False
        if ad.published_at and existing.published_at is None:
            existing.published_at = ad.published_at
            changed = True
        if ad.images:
            _replace_images(existing, ad.images)
            changed = True
        if refresh:
            # recadastro manual: atualiza sempre que houver dado novo
            if ad.price_cents is not None and ad.price_cents != existing.price_cents:
                existing.price_cents = ad.price_cents
                changed = True
            if ad.title and ad.title != existing.title:
                existing.title = ad.title
                changed = True
            if ad.description and ad.description != existing.description:
                existing.description = ad.description
                changed = True
            if changed:
                existing.scraped_at = _utcnow()
        elif not existing.description and ad.description:
            existing.description = ad.description
            existing.scraped_at = _utcnow()
            changed = True
        if not existing.is_active:
            existing.is_active = True
            existing.removed_at = None
            changed = True
        if changed:
            db.session.commit()
        return existing, False
    # ... criação (inalterada)
```

Regras do branch `refresh=True`:
- **`price_cents`** atualizado apenas se o novo valor não for `None` (página
  "sob consulta" **não** zera um preço existente) e for diferente do atual.
- **`title`/`description`** atualizados se vierem preenchidos e diferentes
  (nunca substituem por vazio/`None`).
- **`scraped_at`** só é tocado se houve alguma mudança real (`changed`).
- Os caminhos `scrape`/`enrich` continuam com o comportamento atual
  (`refresh=False`); nenhum teste existente muda.

4. **Extração de specs** (se solicitada e possível):
   ```python
   processed = False
   if process and app.config.get("DEEPSEEK_KEY"):
       try:
           run_process(app, ad_id=ad.id)   # reaproveita regex + LLM
           processed = True
       except Exception:
           log.exception("extração de specs falhou para o ad %s", ad.id)
   ```
   - **Best-effort**: falha da LLM não impede o cadastro — o ad fica pendente
     (`extracted_at` nulo) para o próximo `process`/revisão.

5. **Retorno** (só metadados + `ad_id` — a serialização do ad fica na camada
   API, §3):
   ```python
   return {
       "status": "ok",
       "created": created,
       "processed": processed,
       "ad_id": ad.id,
       "title": ad.title,
       "price_cents": ad.price_cents,
       "url": ad.url,
   }
   ```

## 3. API — `POST /api/ads/import`

`app/blueprints/api/routes.py`:

```python
@bp.post("/ads/import")
def import_ad():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    process = bool(data.get("process", True))
    if not url:
        return jsonify({"error": "informe o link do anúncio"}), 400
    try:
        result = runner.import_single_ad(current_app._get_current_object(), url, process=process)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ScrapeBlockedError as e:
        return jsonify({"error": str(e)}), 503
    except NETWORK_ERRORS:
        return jsonify({"error": "erro de rede ao acessar a OLX"}), 502

    status = result["status"]
    if status == "removed":
        return jsonify({"error": "anúncio não existe mais na OLX (removido)"}), 410
    if status == "not_an_ad":
        return jsonify({"error": "página não contém dados de anúncio (JSON-LD ausente)"}), 422

    ad = ad_service.get_ad(result["ad_id"])
    return jsonify({
        "status": "ok",
        "created": result["created"],       # UI usa p/ "cadastrado ✓" vs "atualizado ✓"
        "processed": result["processed"],   # UI usa p/ "specs extraídas" vs "pendente"
        "ad": ad_service.ad_to_dict(ad, include_description=True),
    }), (201 if result["created"] else 200)
```

**Contrato de resposta (sucesso)** — `created`/`processed` são metadados do
request, por isso **fora** de `ad` (o serializer `ad_to_dict` não tem esses
campos):

```json
{
  "status": "ok",
  "created": true,
  "processed": true,
  "ad": { "id": 123, "title": "...", "price_cents": 285000, "specs": {...}, ... }
}
```

**Mapa de status:**

| cenário | HTTP |
|---|---|
| criado | `201` |
| atualizado (duplicado) | `200` |
| link vazio / não-OLX / sem `olx_id` | `400` |
| anúncio removido (404/410) | `410` |
| 200 sem JSON-LD | `422` |
| 403 / Cloudflare | `503` |
| erro de rede | `502` |

## 4. UI — painel na página `/run`

### `app/blueprints/main/templates/run.html`

Novo painel antes do formulário de run (estilo Pico, mesmo padrão dos demais):

```html
<section class="add-ad-panel">
    <h2>Cadastrar anúncio manual</h2>
    <p class="page-sub muted">Cole o link de um anúncio da OLX. O sistema valida, busca
        o detalhe e cadastra no banco.</p>
    <form id="add-ad-form">
        <div class="field">
            <label for="add-ad-url">Link do anúncio</label>
            <input type="url" id="add-ad-url" name="url" placeholder="https://sp.olx.com.br/.../nome-do-anuncio-1234567890" required>
        </div>
        <label>
            <input type="checkbox" id="add-ad-process" checked>
            Extrair specs na hora (regex + DeepSeek)
        </label>
        <button type="submit" id="add-ad-button">Cadastrar</button>
        <span id="add-ad-error" class="run-error" hidden></span>
    </form>
    <p id="add-ad-result" class="muted" hidden></p>
</section>
```

### `app/static/js/app.js` — `initAddAd()`

Chamada no `DOMContentLoaded` (ao lado de `initRunPage`/`initAutostart`):

- Submit → `POST /api/ads/import` com `{url, process}`.
- Estado "cadastrando…" no botão; sem polling (request síncrono, ~1–5s).
- Sucesso → mensagem a partir de `data.created` ("Anúncio cadastrado ✓" vs
  "já existia — atualizado ✓") e `data.processed` ("specs extraídas" vs
  "specs pendentes"), com **link para `/ads/${data.ad.id}`** e preço/specs
  resumidos; limpa o input.
- Erro → mostra `data.error` em `#add-ad-error` (400/410/422/503/502) ou mensagem
  genérica de falha de rede.

## 5. CLI — `flask add <url>`

`app/cli.py` (delega, não duplica):

```python
@click.command()
@click.argument("url")
@click.option("--no-process", is_flag=True, help="Não extrair specs na hora.")
def add(url: str, no_process: bool) -> None:
    """Cadastra um anúncio da OLX a partir do link."""
    from flask import current_app
    try:
        result = import_single_ad(current_app, url, process=not no_process)
    except ScrapeBlockedError as e:
        raise click.ClickException(str(e))
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"status: {result['status']} | criado: {result.get('created')} "
               f"| id: {result.get('ad_id')} | specs: {result.get('processed')}")
```

Registrar em `register()`.

## 6. Comportamento esperado

| cenário | resultado |
|---|---|
| Link válido, anúncio ativo | cria `Ad` (título/preço/descrição/imagens), specs extraídas (se `process`) → 201, `created=true` |
| Mesmo link enviado de novo | re-busca e **atualiza** preço/título/descrição/imagens (`refresh=True`), re-ativa se estava removido → 200, `created=false` |
| Preço/descrição mudaram na OLX | atualizados no registro existente (via `refresh=True`) → 200 |
| Link com `#fragmento` ou `?query` (copiado do navegador) | normalizado para a URL canônica (sem query/fragmento) → cadastra normalmente, sem 403 por `?lis=`/tracking e sem duplicar |
| Link de listagem (sem `olx_id` final) | 400 "não parece um anúncio" |
| Link não-OLX (ex.: mercadolivre) | 400 "não é da OLX" |
| Anúncio vendido/removido (404/410) | 410, nada gravado |
| Página 200 sem JSON-LD (ex.: categoria) | 422 |
| 403/Cloudflare | 503; se houver cooldown ativo, **não** interfere no `scrape_block.json` |
| `DEEPSEEK_KEY` ausente + `process=true` | cadastra e deixa pendente (`processed=false`, sem erro) |
| LLM falha na extração | ad cadastrado, specs pendentes (`processed=false`, best-effort) |

## 7. Custo / impacto

- **Custo monetário**: igual ao `process` normal (~US$0.00016/ad, cache alto).
- **Sem migração de banco**: nenhuma mudança de schema.
- **Sem novas dependências**.
- **Tempo do request**: 1 fetch (~1–3s com delay de 1 req/s) + LLM (~1–2s) se
  `process` — aceitável para um form manual.
- **Concorrência**: não usa `RunManager` → não compete com a trava uma-run-por-vez
  e não aparece no `run_history`.

## 8. Casos de borda

- **URL com query/tracking params** (`?lis=...`, `?utm_source=...`): **removidos**
  na normalização (§2.1) → a URL gravada é a canônica, idêntica à do scraper;
  dedup consistente (sem duplicata por parâmetro).
- **URL de listagem que passa a ter `olx_id` no fim**: se não terminar em
  `-\d{5,}`, `olx_id_from_url` retorna `None` → 400. É o guarda-correto desejado.
- **`name` ausente no JSON-LD** (raro): fallback para a própria `url` no título.
- **Preço "sob consulta" num re-cadastro de ad que tem preço**: `price_cents`
  vira `None` no JSON-LD → `refresh=True` **não** zera o preço existente (guarda
  `ad.price_cents is not None`).
- **Anúncio removido que "voltou"**: `upsert_raw` re-ativa — o re-cadastro
  manual é uma forma legítima de reativar.
- **Duplicado com specs já extraídas**: o `run_process(ad_id=...)` reprocessa
  (não respeita `extracted_at` quando `ad_id` é passado — comportamento atual da
  função). Aceitável; custo ~US$0.00016. Alternativa (pular se já extraído) fica
  como pergunta em aberto.
- **403 no meio**: `ScrapeBlockedError` → 503 com a mensagem; não grava cooldown
  (isso é decisão do nível run/autorun).

## 9. Testes (offline, sem rede)

Novo `tests/test_import.py` — padrão de `test_runner.py`/`test_check.py`
(`httpx.MockTransport` + `monkeypatch.setattr(runner, "OlxClient", factory)`):

- `test_invalid_url_raises`: URL vazia, não-OLX, sem `olx_id` → `ValueError`.
- `test_url_with_fragment_or_query`: `olx_id_from_url("...-12345#photos")` e
  `"...-12345?origem=ml"` → `"12345"` (fix §1).
- `test_import_normalizes_tracking_url`: import com `?lis=...#fotos` → salvo com
  URL canônica; re-cadastro da URL com tracking deduplica na mesma row.
- `test_import_creates_ad`: handler devolve `AD_GOOD` → `status=ok`,
  `created=True`, título/preço/descrição/imagens corretos no banco.
- `test_import_duplicate_updates`: ad pré-existente → `created=False`, **preço e
  descrição atualizados** (handler com valores novos) → cobre o `refresh=True`.
- `test_import_duplicate_price_sob_consulta`: `refresh=True` com `price_cents`
  `None` **não** zera preço existente.
- `test_upsert_refresh_false_unchanged`: `upsert_raw(refresh=False)` (caminho
  scrape) continua sem atualizar preço de existente — comportamento preservado.
- `test_import_reactivates_removed`: ad `is_active=False` → re-ativa.
- `test_import_process_extracts_specs`: `process=True` + mock do LLM (como
  `test_run_process_extracts_specs`) → `processed=True` e `AdSpec` gravado.
- `test_import_process_without_key`: `DEEPSEEK_KEY=""` → cadastra sem specs.
- `test_import_removed`: handler 404 → `status=removed`, nada no banco.
- `test_import_not_an_ad`: handler com `ad_not_found.html` → `status=not_an_ad`.
- `test_import_blocked`: handler 403 → `ScrapeBlockedError` propagado.
- API: `test_api_import_*` — status codes 400/410/422/503/502 e 201/200;
  **shape da resposta** `{status, created, processed, ad}` com `created`/`processed`
  coerentes.

`tests/test_olx.py`: assert de `data["name"]` em `test_parse_detail_good`.

`tests/test_ui.py`: `run.html` contém `#add-ad-url` e `#add-ad-process`.

Rodar a suíte completa: `uv run pytest` (100% offline).

## 10. Documentação

- **D-026** em `docs/specs/00-decisoes.md` (fechada): cadastro manual por link —
  valida URL + `olx_id`, fetch via `OlxClient`, parse JSON-LD (agora com `name`),
  upsert via `ad_service` (duplicado re-busca e atualiza com `refresh=True`;
  removido re-ativa), extração de specs imediata **best-effort**; síncrono no
  request (sem `RunManager`/fila); UI em `/run` + API `POST /api/ads/import` +
  CLI `flask add`; `import_single_ad` em `runner.py` (coleta centralizada).
  Alternativas descartadas: página própria (desnecessária), batch/thread
  background (adiado), rejeitar duplicado sem atualizar (dados podem ficar
  obsoletos).
- `docs/specs/07-api-e-ui.md` — endpoint novo.
- `AGENTS.md` — seção Arquitetura: nota sobre `import_single_ad`, o endpoint e o
  `upsert_raw(refresh=True)`.

## 11. Checklist de entrega

1. `olx.py`: `_parse_detail` retorna `name` **e** `olx_id_from_url` tolerante a
   `#`/`?`.
2. `ad_service.py`: `upsert_raw(ad, refresh=False)` — branch refresh atualiza
   preço/título/descrição (scrape/enrich inalterados).
3. `runner.py`: `import_single_ad(app, url, process=True)`.
4. `blueprints/api/routes.py`: `POST /api/ads/import` + mapa de status +
   resposta `{status, created, processed, ad}`.
5. `run.html` + `app.js` (`initAddAd`).
6. `cli.py`: `flask add <url>` (com `--no-process`).
7. Testes novos (`test_import.py`, `test_olx.py`, `test_ad_service.py`,
   `test_ui.py`) + `uv run pytest` verde.
8. Docs: D-026, 07-api-e-ui, AGENTS.md.

## Perguntas em aberto

- [ ] **Duplicado com specs já extraídas**: reprocessar sempre (re-fetch do LLM)
  ou pular a extração se `extracted_at` já preenchido? (Hoje: reprocessa — barato,
  mas muda `extracted_at`/custo.)
- [ ] **Vários links de uma vez** (textarea, 1 por linha) numa "mini-run" com
  progresso? (Vira spec própria; o `RunManager` já daria o esqueleto.)
- [ ] Expor **fonte do cadastro** (`source`/`added_manually`) no `Ad` para
  auditoria/filtro? (Requer migração — fora do escopo atual.)

# 10 — Verificação de disponibilidade (anúncios ainda publicados)

## Contexto e problema

Hoje o sistema **nunca confirma** se um anúncio coletado continua publicado na
OLX:

1. O modelo `Ad` (`app/models/ad.py`) não tem nenhum campo de status
   (`is_active`/`removed_at`) — só `scraped_at`/`extracted_at`.
2. `run_scrape` só **insere** o que aparece na listagem. Anúncio que sai do ar
   (vendido/removido) nunca é marcado: permanece no banco para sempre,
   inflando a listagem, o gráfico preço × geração e o ranking de ofertas com
   preços obsoletos.
3. **Bug de robustez:** `OlxClient.get` faz `resp.raise_for_status()`
   (`app/scrapers/client.py:70`). Se o detalhe de um anúncio removido retornar
   **404**, a exceção `httpx.HTTPStatusError` propaga e **aborta a run inteira**
   — principalmente no `enrich`, que itera todos os anúncios sem descrição.
4. Se a OLX responder **200 com página "anúncio não encontrado"** (sem JSON-LD
   `Product`), `_parse_detail` devolve `{}` e o anúncio só fica sem descrição,
   silenciosamente — sem nenhum registro de remoção.

## Objetivos

- Marcar anúncios removidos de forma **confiável** (`is_active=False`,
  `removed_at`), sem falso positivo por paginação/ordenação da listagem.
- Corrigir o bug em que 404 derruba a run.
- Não gastar LLM (process) nem requests (enrich) em anúncios removidos.
- Tornar o autorun autossuficiente: a cada ciclo, detectar saídas do ar.
- UI/API: por padrão **ocultar** removidos; opção de **incluí-los**; **badge**
  "removido" no detalhe.

## Não-objetivos (fora de escopo por ora)

- Atualizar **preço** dos anúncios ativos durante a verificação (o GET já
  traria o preço — deixado como extensão futura).
- Histórico de preço / oscilação.
- Verificação via HEAD (a OLX não expõe status confiável sem body/JSON-LD).
- Controle de versão do HTML "não encontrado" além de status 404/410.

---

## 1. Modelo de dados (migração)

Colunas novas em `ads` (tabela `Ad` em `app/models/ad.py`):

| coluna | tipo | constraints | semântica |
|--------|------|-------------|-----------|
| `is_active` | `Boolean` | `nullable=False`, default `True` | `False` = saiu do ar / removido |
| `removed_at` | `DateTime(timezone=True)` | `nullable` | UTC de quando foi detectada a remoção |

- Migração Alembic: `uv run flask db migrate -m "ads is_active removed_at"` →
  `uv run flask db upgrade`.
- **`server_default="1"` obrigatório** em `is_active`: SQLite rejeita
  `ADD COLUMN ... NOT NULL` sem default; sem isso a migração falha com linhas
  existentes. Além do default Python (`default=True`) para novos inserts.
- **Backfill:** nenhum — default `True` para todos os registros existentes
  (nada foi verificado até aqui, então assumimos ativos).

## 2. `OlxClient.get` — 404/410 deixam de derrubar a run

Em `app/scrapers/client.py`, no fim do loop de retry, substituir o
`resp.raise_for_status()` incondicional por:

```
403                                   -> ScrapeBlockedError (já tratado)
404 | 410                             -> retorna a resposta (chamador decide)
5xx (após retries)                    -> raise_for_status()  (já tratado)
demais 4xx                            -> raise_for_status()
```

Efeito direto: `run_enrich`/`run_scrape` não morrem mais ao topar um anúncio
removido; `run_check` (abaixo) usa o status para marcar remoção.
Bônus colateral: uma página de listagem com 404 também deixa de derrubar o
`search()` (`_parse_listing` devolve `[]` → `break`), em vez de crashar.

## 3. Etapa `check` (nova) em `app/services/runner.py`

Novo `VALID_STEPS` member — `("scrape", "enrich", "check", "process")`.

### `run_check(app, limit=None, on_progress=None) -> dict`

1. Seleciona candidatos:
   `Ad.query.filter(Ad.is_active.is_(True))`, ordenado por `scraped_at` **asc**
   (**mais antigos primeiro**) — os recém-coletados no `scrape` acabado de rodar
   quase certamente continuam no ar; verificar os que não são checados há mais
   tempo encontra remoções mais rápido, principalmente com `--limit`.
   `limit` opcional.
2. Para cada anúncio: `client.get(ad.url)` e decide:

   | resposta | ação |
   |----------|------|
   | **404 / 410** | `is_active=False`, `removed_at=now` (UTC), commit → `removidos += 1` |
   | **200 com JSON-LD `@type: Product`** | ativo → nada a fazer |
   | **200 sem JSON-LD** | **incerto** → não marca (evita falso positivo); conta em `sem_confirmar` |
   | **erro de rede (`httpx.HTTPError`)** | **não marca** (não é evidência de remoção); conta em `erros` e segue |
   | **403** | `ScrapeBlockedError` propaga → run para com erro claro (mesmo padrão atual) |

   Obs.: o `client.get` levanta `httpx.HTTPError` após esgotar os retries —
   `run_check` captura essa exceção **por anúncio** (importar `httpx` em
   `runner.py`).

   **Reuso do parser JSON-LD:** o critério "200 com JSON-LD `@type: Product`"
   reutiliza `OlxScraper._parse_detail` (de `app/scrapers/olx.py`) — **não**
   duplicar parse de HTML/JSON-LD em `runner.py`. O `run_check` instancia
   `OlxClient` + `OlxScraper` (padrão de `run_scrape`) e usa
   `scraper._parse_detail(resp.text)` para decidir "ativo vs sem_confirmar".

3. Progresso via callback:
   `on_progress("check", i, len(ads), f"verificados: {i}/{len(ads)} (removidos: {removidos})")`.
4. Retorna `{"checados", "removidos", "ativos", "sem_confirmar", "erros"}`.

### Integração com `RunManager._run`

Adicionar o branch `elif step == "check": result = run_check(app, on_progress=...)`
no loop de etapas (mesmo padrão de `scrape`/`enrich`/`process`) **e** uma entrada
`"check": "verificando anúncios"` no dicionário `step_labels` de `job_to_dict`
(`runner.py:291`) — sem ela, `step_label` fica `None` na UI durante a etapa.

### CLI

Novo comando em `app/cli.py`: `flask check [--limit N]` delegando a `run_check`
(eco do padrão de `flask enrich`).

## 4. Scrape passa a marcar "ativos"

Em `upsert_raw` (`app/services/ad_service.py`), quando o anúncio **já existe**
e aparece na listagem:

- se `existing.is_active is False` → `is_active=True` e `removed_at=None`
  (anúncio re-publicado, ou remoção mal marcada — a listagem é evidência de
  que voltou/está ativo).
- Na criação, `is_active` usa o default `True`.

**Importante (D-021 já ratificado):** ausência da listagem **não** marca
removido — paginação (`max_pages`, `sf=1`) e ordenação podem esconder anúncios
válidos. A remoção só é confirmada pelo `check` (404 no detalhe).

## 5. Process e enrich não gastam com removidos

- `list_pending_extraction`: adicionar `Ad.is_active.is_(True)`.
- `list_missing_description`: adicionar `Ad.is_active.is_(True)`.

## 6. Autorun

Em `app/services/autoscheduler.py`, `AUTORUN_STEPS` passa de
`["scrape", "process"]` para **`["scrape", "check", "process"]`**.
Ordem: coleta (marca ativos + insere novos) → verifica inventário → extrai specs
(apenas ativos). Custo por ciclo: 1 req/s × nº de ativos (~270 ≈ 4,5 min), ok
com intervalo default de 120 min.

A etapa `check` também fica disponível na página `/run` (checkbox) e na API
`POST /api/runs` automaticamente via `VALID_STEPS`.

## 7. API

- **`AdFilters`** ganha `include_inactive: bool = False`; `_apply_filters`
  aplica `query.filter(Ad.is_active.is_(True))` quando `False` (início da
  cadeia, antes dos joins).
- **`_parse_filters`** (api e main) parseia `?include_inactive=1|true`
  (reusar o helper `_bool` da API).
- **`ad_to_dict`**: inclui `"is_active"` e `"removed_at"` no payload.
- **`/api/ads/<id>`**: continua retornando removidos (sem filtro em `get_ad`)
  — é assim que a página de detalhe exibe o badge.
- **`/api/stats`**: mantém o shape atual, mas com contagens **só de ativos** e
  acrescenta `"removidos"` (count de inativos). Atenção: `por_cpu_family`
  (ad_service.py:343) consulta `AdSpec.query` **sem join em `Ad`** — precisa
  de join + `Ad.is_active.is_(True)`.
- **`chart_data`**, **`price_benchmarks`** (via `_price_by_generation`) e
  **`best_deals`**: restringem a ativos. `chart_data`/`best_deals` já passam
  por `_apply_filters` (automático); `_price_by_generation` consulta direta —
  adicionar `Ad.is_active.is_(True)` no join.

## 8. UI

- **`_filters.html`** (partial compartilhado por `/`, `/chart`, `/offers`):
  checkbox **"incluir removidos"** (`name=include_inactive`, valor 1).
- **`ad_detail.html`**: badge **"removido em <data>"** ao lado do preço quando
  `not ad.is_active`.
- **Card da listagem (`index.html`)**: marcador discreto "removido" quando a
  listagem estiver com `include_inactive=1`.
- **`/review`**: continua só ativos (usa `list_ads` com default).
- **`/run`**: a etapa `check` exige **checkbox manual em `run.html`** — as
  checkboxes das etapas são hardcoded no template (linhas 28-33), `valid_steps`
  só alimenta o JS de validação. Adicionar "Check (verificar publicados)".

## 9. Custo e performance

| ponto | custo |
|-------|-------|
| ciclo autorun com `check` | +1 req/s × nº ativos (~270 ≈ 4,5 min) |
| marcação na listagem | zero requests extras |
| enrich/process | **menos** trabalho (pulam inativos) |

Se a base crescer muito, extensões futuras: `limit` no check com prioridade
para os mais antigos, ou `--max-check` no autorun.

## 10. Casos de borda

- **200 sem JSON-LD**: não marca (`sem_confirmar`). Evita falso positivo se a
  OLX mudar o markup do detalhe. (Pergunta em aberto: heurística por título
  "não encontrado"?)
- **Erro de rede** no check: não marca, conta erro (evidência não confiável).
- **403 durante check**: run para com erro claro (padrão atual).
- **Re-publicado**: `upsert_raw` re-ativa e limpa `removed_at`.
- **Anúncio novo já removido** (aparece na listagem, some no detalhe): o
  `scrape` não derruba mais a run (404 tratado); o `check` seguinte o marca.
- **Falso negativo da listagem** (ativo mas fora do max_pages): continua ativo
  até o check confirmar 404 — nunca marcado pela ausência.

## 11. Testes (offline)

Novo `tests/test_check.py` + ajustes nos existentes:

- **client**: 404/410 não levantam; 5xx após retries continua levantando; 403 →
  `ScrapeBlockedError`.
- **run_check**: com `httpx.MockTransport` — 404 → `is_active=False` +
  `removed_at` setado; 200 (fixture `ad_good.html`) → permanece ativo; 200 sem
  JSON-LD → `sem_confirmar`; erro de rede → `erros`, não marca; 403 → levanta.
- **upsert**: anúncio inativo visto na listagem volta a ativo (limpa
  `removed_at`).
- **pending/enrich**: filtram inativos.
- **autorun**: `AUTORUN_STEPS == ["scrape", "check", "process"]`; tick dispara
  com essas 3 etapas (**atualizar** `test_tick_starts_run_when_due`, que hoje
  assere `["scrape", "process"]`).
- **API**: default exclui inativos; `?include_inactive=1` os inclui;
  `ad_to_dict` expõe `is_active`/`removed_at`; `/api/stats` tem `removidos`
  (`test_stats` continua válido: seeds ativos + asserts por chave).
- **UI**: `/run` lista `check`; `/` com `include_inactive=1` mostra badge;
  `/ads/<id>` de inativo mostra "removido".
- **CLI**: `flask check` registrado.

Fixture nova: página de erro da OLX (ex.: `ad_not_found.html`) para o caso
"200 sem JSON-LD".

## 12. Documentação

- **D-022** em `docs/specs/00-decisoes.md` (decisão fechada, seguindo o
  formato do registro).
- `docs/specs/08-operacao.md`: tabela de comandos (`flask check`) + seção de
  agendamento (ciclo `scrape → check → process`).
- Wiki OKF: `docs/wiki/runs.md` (etapas) e `docs/wiki/banco.md` (campo
  `is_active`).
- `AGENTS.md`: nota sobre `check` em `services/runner.py`.

## 13. Checklist de entrega

1. Migração Alembic + `models/ad.py`.
2. `OlxClient.get` trata 404/410.
3. `run_check` + `VALID_STEPS` + `RunManager` + CLI `check`.
4. `upsert_raw` re-ativa; `list_pending_extraction`/`list_missing_description`
   filtram ativos.
5. `AUTORUN_STEPS = ["scrape", "check", "process"]`.
6. `AdFilters.include_inactive` + filtro + parsers API/UI + `ad_to_dict` +
   `stats`/`chart`/`offers` só ativos.
7. UI: checkbox no `_filters.html`, badge no detalhe e no card.
8. Testes novos + ajustes; suíte completa verde.
9. Docs (D-022, 08, wiki, AGENTS.md).

## Perguntas em aberto

- [ ] Heurística "200 sem JSON-LD" por título ("anúncio não encontrado")? →
  hoje: `sem_confirmar` (não marca).
- [ ] Limite máximo de checks por ciclo quando a base crescer?
- [ ] Aproveitar o GET do check para atualizar preço/descrição dos ativos?

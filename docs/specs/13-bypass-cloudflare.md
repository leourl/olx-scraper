# 13 — Bypass do Cloudflare (curl_cffi) + cooldown de bloqueio

## Contexto e problema

O autorun (APScheduler, etapa `scrape`) começou a falhar com **403 Forbidden**
direto na primeira requisição de listagem:

```
INFO app.scrapers.olx: listagem página 1: https://www.olx.com.br/estado-sp?q=dell+optiplex&sf=1&o=1
INFO httpx: HTTP Request: GET ... "HTTP/1.1 403 Forbidden"
ERROR app.services.runner: run 11 falhou
app.scrapers.client.ScrapeBlockedError: bloqueado (403) em https://www.olx.com.br/estado-sp?q=dell+optiplex&sf=1&o=1
```

O `OlxClient` (`app/scrapers/client.py:62-63`) levanta `ScrapeBlockedError` em
qualquer 403 → a run inteira morre com `status=error` e o autorun repete a
falha a cada `AUTORUN_INTERVAL_MINUTES` (default 120), batendo num Cloudflare
que não vai liberar.

### Diagnóstico (validado em execução real, 2026-08-06)

O bloqueio **não é por IP nem por User-Agent** — é o **fingerprint TLS/HTTP2
do `httpx`** que o Cloudflare reconhece e bloqueia:

| transporte | resultado |
|---|---|
| `curl` CLI com o mesmo UA do `.env` + headers de browser | **200** (listagem normal) |
| `httpx` (lib usada pela app) com os mesmos headers | **403** "Attention Required! \| Cloudflare" |
| `curl_cffi` com `impersonate="chrome"` | **200** (listagem completa, ~700 KB) |

Conclusão: o JA3 (stack TLS do `ssl` do CPython) e o HTTP/2 fingerprint do
`httpx` não correspondem a um browser real; o Cloudflare classifica como bot e
responde 403 **antes** mesmo de olhar o conteúdo. Trocar o cliente HTTP resolve
o problema sem proxy, sem rotacionar IP e sem navegador headless.

`curl_cffi` (libcurl com perfis de impersonação de browsers reais) tem wheels
binários para **`x86_64`, `aarch64` e `armv7l`** (Raspberry Pi 3 32-bit) →
`uv sync` instala sem compilar nada na máquina alvo.

### Camada defensiva (cooldown)

Mesmo com fingerprint real, o Cloudflare pode marcar o IP (o RPi usou `httpx`
por dias batendo a cada 2h). Se a OLX ainda devolver 403 após o deploy, o
comportamento atual é ruim: run `error` a cada 2h + retry imediato que só
reforça a marcação. Precisa de **backoff**: status `blocked` distinto e o
autorun **pausar** por um cooldown persistido (sobrevive a restart), retomando
sozinho quando expira.

## Objetivos

- Eliminar o 403 do Cloudflare trocando o transporte HTTP do `OlxClient` por
  `curl_cffi` com `impersonate` de browser (fingerprint TLS/HTTP2 real).
- Manter o **contrato público** do `OlxClient` e toda a lógica de politeness
  (1 req/s, retry 5xx com backoff, 404/410 → retorna, 403 → `ScrapeBlockedError`)
  intacta.
- Manter os **testes offline existentes** funcionando sem reescrever os
  handlers (`httpx.MockTransport` continua aceito via adaptador).
- Adicionar **cooldown de bloqueio**: run que termina por 403 vira
  `status="blocked"` (não `error`), persiste "até quando" em
  `instance/scrape_block.json`, e o autorun **não** dispara outra run enquanto
  o cooldown não expirar. Limpa o bloqueio assim que uma run conclui com
  sucesso.
- Registrar a decisão (D-025) em `docs/specs/00-decisoes.md`.

## Não-objetivos (fora de escopo por ora)

- **Proxy / rotação de IP** — não é necessário: o fingerprint resolve; cooldown
  cobre marcação temporária.
- **Navegador headless (Playwright)** — pesado para RPi 3, sobre-engenharia.
- **Resolver o desafio interativo do Cloudflare** (Turnstile) — se a OLX um dia
  exigir prova interativa, isso é outra spec.
- **Mudar o `DeepSeekClient`** (`app/extractors/llm.py`) — continua no `httpx`
  (API da DeepSeek, sem Cloudflare). O `httpx` permanece como dependência.
- **Retry de 403 dentro do client** — um 403 pós-fingerprint-real indica
  bloqueio (não transitório); re-tentar dentro da mesma run só gastaria tempo.
  O tratamento é no nível da run (status `blocked` + cooldown).

---

## 1. Configuração

`app/config.py`:

```python
SCRAPER_IMPERSONATE = os.getenv("SCRAPER_IMPERSONATE", "chrome")
SCRAPER_BLOCK_COOLDOWN_MINUTES = int(os.getenv("SCRAPER_BLOCK_COOLDOWN_MINUTES", "60"))
```

- `SCRAPER_IMPERSONATE` — perfil de impersonação do `curl_cffi` (default
  `"chrome"`; alternativas: `"chrome124"`, `"firefox"`, `"safari"`…). É a
  chave do fingerprint TLS/HTTP2.
- `SCRAPER_BLOCK_COOLDOWN_MINUTES` — duração do backoff após um 403 (default
  60 min). `0` = sem cooldown (comportamento atual de retry a cada intervalo).

`tests/conftest.py` (`TestConfig`):

```python
SCRAPER_IMPERSONATE = "chrome"
SCRAPER_BLOCK_COOLDOWN_MINUTES = 0  # sem cooldown nos testes unitários
```

`pyproject.toml` — adicionar `curl_cffi>=0.16.0` (wheel abi3; última stable
validada 0.16.0 com suporte `armv7l`/`aarch64`). Rodar `uv sync`.

## 2. `app/scrapers/client.py` — transporte com impersonação

### Estrutura

`OlxClient` ganha um **fetcher** interno com `.get(url) -> response`:

- **Fetcher real** (`_CurlFetcher`): `curl_cffi.requests.Session`
  com `impersonate=cfg["SCRAPER_IMPERSONATE"]`, headers
  `{**BROWSER_HEADERS, "User-Agent": user_agent}`, `timeout` e
  `follow_redirects=True`. Retorna a própria response do `curl_cffi`
  (que já expõe `.status_code`, `.text`, `.raise_for_status()`).
- **Fetcher de teste** (`_HttpxTransportFetcher`): usado quando o caller
  passa `transport=` (os testes passam `httpx.MockTransport`). Monta
  `httpx.Request("GET", url)`, chama `transport.handle_request(req)` e
  **normaliza** a `httpx.Response` num wrapper mínimo
  (`_FakeResponse(status_code, text)` com `raise_for_status()`).

Assinatura pública **inalterada**:

```python
OlxClient(user_agent: str, timeout: float = 30.0, delay: float = 1.0,
          transport: httpx.BaseTransport | None = None)
```

`transport=None` → fetcher real (`curl_cffi`); `transport` fornecido → fetcher
de teste. Nenhum chamador (`runner.py`, `cli.py`, testes) muda de assinatura.

### Lógica de `get` (inalterada em comportamento)

1. `_wait_turn()` (lock global, 1 req/s).
2. `fetcher.get(url)` — erros de rede → retry com backoff (`MAX_RETRIES=3`);
   a captura passa a ser `except (httpx.HTTPError, RequestsError)` onde
   `RequestsError = curl_cffi.requests.exceptions.RequestsError` (cobre
   `ConnectionError`, `Timeout`, `CurlError`…). Nos testes, o handler levanta
   `httpx.ConnectError` (subclasse de `httpx.HTTPError`) → continua capturado.
3. `403` → `raise ScrapeBlockedError(...)` (igual a hoje, **sem retry** — ver
   Não-objetivos).
4. `404/410` → retorna a response (igual a hoje).
5. `429/5xx` → retry com backoff (`2**attempt`, máx 8s).
6. senão `resp.raise_for_status()` e retorna.

Ajuste em `BROWSER_HEADERS`: o `curl_cffi` não usa `Sec-Ch-Ua`/`Sec-Fetch-*`
da mesma forma que o httpx, mas manter os headers não atrapalha — o
`impersonate` preenche o conjunto completo e os nossos headers só sobrescrevem
valores compatíveis com Chrome.

## 3. Cooldown de bloqueio — `app/services/scrape_block.py` (novo)

Persistência simples em JSON (mesmo padrão de `run_terms.json`/`autostart.json`):

```python
BLOCK_FILE = "scrape_block.json"

def get_blocked_until(instance_path: str) -> float:
    """Epoch (time.time) até quando a coleta está bloqueada. 0 = livre."""

def set_blocked(instance_path: str, until_ts: float) -> None:
    """Grava {'blocked_until': ts, 'reason': ..., 'updated_at': ...}."""

def clear_blocked(instance_path: str) -> None:
    """Remove o arquivo (run bem-sucedida ou usuário quer testar de novo)."""
```

- Leitura tolerante a arquivo ausente/quebrado (retorna `0` = livre).
- Escrita **best-effort**: se falhar, loga warning e segue (não derruba run).

## 4. `app/services/runner.py` — status `blocked`

### `RunManager._run` (tratamento do erro)

No bloco `try/except` atual (`runner.py:336-340`), capturar
`ScrapeBlockedError` **antes** do `except Exception`:

```python
except ScrapeBlockedError as e:
    log.warning("run %s bloqueada pela OLX: %s", job_id, e)
    job.status = "blocked"
    job.error = str(e)
    job.message = f"OLX bloqueou a coleta: {e}"
    until = time.time() + app.config["SCRAPER_BLOCK_COOLDOWN_MINUTES"] * 60
    set_blocked(app.instance_path, until)
```

E no caminho de sucesso (`job.status = "done"`, linha 334) adicionar
`clear_blocked(app.instance_path)` — a primeira run que passar confirma que o
bloqueio acabou.

### `job_to_dict`

`status == "blocked"` cai no mesmo branch de `error` (percent 0) — não precisa
de mudança, mas vale garantir que o dicionário exponha `error` (já expõe).

### Histórico

`finalize_run_entry` aceita qualquer status; `blocked` será gravado como tal
no quadro da página `/run` e em `GET /api/runs/history` (sem mudança de código,
só o novo CSS em §6).

## 5. `app/services/autoscheduler.py` — respeitar o cooldown

Em `tick` (`autoscheduler.py:72-99`), após o check de termos e antes de
`run_manager.start`, adicionar:

```python
blocked_until = get_blocked_until(self._instance_path)
if now < blocked_until:
    log.info("autorun em cooldown de bloqueio até %s", ...)
    return "blocked"
```

Detalhe importante: **não** atualizar `self._last_run_at` nesse caso — assim o
tick (a cada 30s) reavalia e dispara assim que o cooldown expirar, sem esperar
o intervalo inteiro do autorun. O retorno `"blocked"` é logado pelo
`_tick_job` (já loga qualquer ação fora de `disabled`/`not_due`).

## 6. UI

`app/static/css/app.css` — badge do status `blocked` (cinza-escuro, distinto de
`error`):

```css
.history-status[data-status="blocked"] {
    background: rgba(120, 120, 120, .15);
    color: #555;
}
```

O template `run.html` e a API renderizam `h.status`/`job.status` dinamicamente
— nenhuma outra mudança de UI.

## 7. Comportamento esperado

| cenário | resultado |
|---------|-----------|
| Requisição normal (não-bloqueada) | `curl_cffi` responde 200 com fingerprint de Chrome → coleta normal |
| Cloudflare ainda bloqueia (IP marcado) | `ScrapeBlockedError` → run `status="blocked"`, `scrape_block.json` gravado com `now + cooldown` |
| Autorun durante o cooldown | `tick` retorna `"blocked"` (log), **não** dispara run, reavalia a cada 30s |
| Cooldown expira | próxima avaliação do tick dispara a run normalmente |
| Run bem-sucedida após bloqueio | `clear_blocked()` remove o arquivo → cooldown zera |
| `SCRAPER_BLOCK_COOLDOWN_MINUTES=0` | 403 → `blocked` (status informativo) mas sem backoff (retoma no próximo intervalo) |
| Run manual (página /run) durante cooldown | **não é bloqueada** — o cooldown só guarda o autorun; manual pode testar quando quiser |

## 8. Custo / impacto

- **Zero custo monetário** (curl_cffi é open source, mesma licença MIT dos
  demais).
- **Velocidade**: `curl_cffi` (libcurl) é, se tanto, um pouco mais rápido que
  o `httpx`; o gargalo continua o `SCRAPER_DELAY` de 1 req/s.
- **Dependência**: +`curl_cffi` no `pyproject.toml` (httpx continua para o
  `DeepSeekClient` e para os testes).
- **Sem migração de banco**: nenhuma mudança de schema (cooldown é arquivo,
  não tabela).

## 9. Casos de borda

- **`scrape_block.json` corrompido/ausente**: `get_blocked_until` retorna `0`
  (livre) — autorun segue.
- **Escrita do cooldown falha** (permissão/IO): best-effort — a run ainda
  termina em `blocked`; sem o arquivo, o autorun retoma no próximo intervalo
  (pior cenário = comportamento atual).
- **Restart durante o cooldown**: o arquivo persiste → o autorun continua em
  backoff após o boot.
- **`curl_cffi` indisponível no ambiente** (import falha): o módulo `client.py`
  deve importar `curl_cffi` de forma que o erro não quebre o boot da app? Não —
  `curl_cffi` vira dependência obrigatória do projeto; falha de instalação é
  erro de deploy, não caso de borda. (Import no topo do módulo, padrão do
  projeto.)
- **Handler de teste com `raise` de rede**: `httpx.MockTransport` propaga o
  `httpx.ConnectError` do handler → capturado por `except httpx.HTTPError`.
- **404/410 no fetcher real**: `curl_cffi` não levanta em 4xx; o client verifica
  `status_code` antes de `raise_for_status` → comportamento preservado.

## 10. Testes (offline, sem rede)

### Adaptação dos testes existentes

Nenhuma mudança necessária nos handlers: o `OlxClient` continua aceitando
`httpx.MockTransport(handler)` via o adaptador `_HttpxTransportFetcher` (os
handlers só inspecionam `request.url`). `test_enrich.py`, `test_check.py`,
`test_runner.py` seguem funcionando.

### Novos testes

`tests/test_scrape_block.py`:
- `test_roundtrip_get_set_clear` (tmp_path): set → get == ts; clear → 0.
- `test_get_blocked_until_missing_file` → 0.
- `test_get_blocked_until_corrupt_file` → 0.

`tests/test_runner.py`:
- `test_manager_execute_blocked`: `monkeypatch` `run_scrape` para levantar
  `ScrapeBlockedError` → `execute` termina com `job.status == "blocked"`,
  `job.error` preenchido e `scrape_block.json` escrito em `app.instance_path`
  (com `SCRAPER_BLOCK_COOLDOWN_MINUTES=60` no config usado no teste).
- `test_manager_execute_clears_block`: run `done` remove o `scrape_block.json`.

`tests/test_autoscheduler.py`:
- `test_tick_blocked_until`: `set_blocked(tmp, now+3600)` → `tick` retorna
  `"blocked"` e **não** chama `run_manager.start`.
- `test_tick_blocked_expired`: `set_blocked(tmp, now-1)` → `tick` dispara
  normalmente.
- `test_tick_after_success_clears`: sucesso limpa o arquivo (via `run_manager`
  fake) — ou coberto no runner.

`tests/test_client.py` (ou dentro de `test_check.py`):
- O `test_client_403_raises_blocked` existente continua válido (com o adaptador).
- Opcional: `test_client_real_fetcher_uses_curl_cffi` — verifica que com
  `transport=None` o fetcher é a instância de `curl_cffi.requests.Session`
  (sem fazer rede).

Roda a suíte completa: `uv run pytest` (100% offline).

## 11. Documentação

- **D-025** em `docs/specs/00-decisoes.md` (fechada, formato do registro):
  curl_cffi com impersonação (causa: fingerprint TLS/HTTP2) + cooldown de
  bloqueio (`SCRAPER_BLOCK_COOLDOWN_MINUTES`, `scrape_block.json`, status
  `blocked`). Alternativas descartadas: proxy/rotação de IP (desnecessário),
  Playwright (pesado), retry de 403 no client (bloqueio não é transitório),
  ajustar headers/UA do httpx (não muda o JA3).
- `docs/specs/05-scraping.md` — seção de transporte: `curl_cffi` +
  `SCRAPER_IMPERSONATE`; nota sobre 403 → `blocked` + cooldown.
- `docs/specs/08-operacao.md` — variáveis `SCRAPER_IMPERSONATE` e
  `SCRAPER_BLOCK_COOLDOWN_MINUTES`; passo de deploy `uv sync` no RPi.
- `AGENTS.md` — seção "Scraping (OLX)": `OlxClient` agora usa `curl_cffi`
  (impersonate); 403 → status `blocked` + cooldown persistido.

## 12. Checklist de entrega

1. `pyproject.toml`: `curl_cffi>=0.16.0` + `uv sync`.
2. `client.py`: `_CurlFetcher` + adaptador de teste + captura
   `(httpx.HTTPError, RequestsError)`; API pública intacta.
3. `config.py` + `TestConfig`: `SCRAPER_IMPERSONATE`,
   `SCRAPER_BLOCK_COOLDOWN_MINUTES`.
4. `scrape_block.py` (novo) + `runner.py` (status `blocked`/`clear_blocked`) +
   `autoscheduler.py` (skip por cooldown).
5. CSS do badge `blocked`.
6. Testes novos + suíte completa verde (`uv run pytest`).
7. Docs (D-025, 05, 08, AGENTS.md).
8. **Validação manual (rede, fora da suíte):** `flask scrape "dell optiplex"
   --max-pages 1` confirma 200; conferir se o RPi ainda está marcado (1ª run
   pode dar `blocked` → cooldown segura).

## Perguntas em aberto

- [ ] **Rotação de perfis** (`impersonate`) por requisição para reduzir
  detecção estatística? (Provavelmente desnecessário; cooldown cobre.)
- [ ] **Persistir o motivo do 403** no `run_history`/stats além do `error`
  texto? (Já aparece em `error`; um campo estruturado ajudaria auditoria.)
- [ ] **Alerta** (e-mail/telegram) quando entrar em `blocked` por mais de N
  horas? (RPi headless sem notificação hoje.)
- [ ] Usar o **endpoint JSON interno da OLX** (SPA) como fallback quando a
  listagem HTML for bloqueada? (D-006 descartou; revisitar se 403 persistir.)

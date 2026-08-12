# 08 — Operação

## Execução local

- uv: `uv sync`, `uv run flask ...`
- SQLite em `instance/`
- config via `.env` (ver `app/config.py`): `DATABASE_URL`, `SCRAPER_DELAY`,
  `SCRAPER_IMPERSONATE` (perfil do curl_cffi, default `chrome`),
  `SCRAPER_BLOCK_COOLDOWN_MINUTES` (cooldown após 403 do Cloudflare, default
  `60`, `0` = sem cooldown), `USER_AGENT`, `LLM_*` (provedor/modelo/chave),
  `LLM_MAX_RETRIES` (retries extras após a 1ª chamada quando a LLM devolve JSON
  inválido; default `2`, `0` = sem retry)

## Comandos CLI

| comando | o que faz |
|---------|-----------|
| `flask db upgrade` | aplica migrações |
| `flask scrape "dell optiplex sff" [--region estado-sp] [--max-pages 5] [--no-details]` | coleta listagem (+ detalhes dos anúncios novos) |
| `flask enrich [--limit N]` | busca detalhe dos anúncios sem descrição (preenche descrição/imagens) |
| `flask check [--limit N]` | verifica se anúncios ativos ainda estão publicados (404/410 → `is_active=False`) |
| `flask process [--limit N] [--ad <id> --force]` | extrai specs (regex + LLM) dos pendentes |
| `flask run` | sobe UI + API |

## Agendamento (D-021/D-022: autorun in-app — RPi 3, 24/7)

- **APScheduler** dentro da app (`app/services/autoscheduler.py`), puro Python
  (roda em ARM). `AUTORUN_ENABLED=1` no `.env` liga o scheduler do processo;
  o job checa a cada 30s se passou `AUTORUN_INTERVAL_MINUTES` (default 120) e
  dispara **`scrape → check → process`** com os termos de
  `instance/run_terms.json` (o `check` marca removidos — D-022).
- O **switch na página `/run`** liga/desliga o autorun em runtime (estado em
  `instance/autostart.json`, sobrevive a restart). O switch só tem efeito se o
  scheduler do processo estiver ativo (`AUTORUN_ENABLED=1`).
- Uma run por vez: se houver run ativa (manual ou do próprio autorun), o tick
  é pulado e tenta de novo no intervalo seguinte.
- `uv run flask run` com `debug=True` usa o reloader — o scheduler inicia só no
  processo filho (`WERKZEUG_RUN_MAIN`), evitando duplicação.

## Tratamento de erros

- scraping bloqueado (403/anti-bot): run vira **`blocked`** (não `error`),
  autorun pausa por `SCRAPER_BLOCK_COOLDOWN_MINUTES` (persistido em
  `instance/scrape_block.json`) e retoma sozinho; run bem-sucedida limpa o
  cooldown. Runs manuais não são bloqueadas pelo cooldown (só o autorun).
- LLM timeout: ad fica sem specs, marcado p/ re-tentar
- LLM JSON inválido: retry automático no mesmo run (`LLM_MAX_RETRIES`, D-023)
- rede instável: retry com backoff no `client.py`
- qualquer falha de parse de um anúncio: logar e seguir para o próximo

## Logs

- `LOG_LEVEL` (default `INFO`) e `LOG_FILE` via `.env` — `app/logging_setup.py`
  configura o root logger (console sempre; arquivo rotativo `RotatingFileHandler`
  5MB × 3 backups se `LOG_FILE` setado; relativo → `instance/`).
- Sem `LOG_FILE`, logs vão só para o stdout/stderr do processo.
- log por anúncio: coletado, dedup (pulou), extraído, falha
- métricas simples: total coletado, sem specs, custo estimado de LLM (nº chamadas)

## Histórico de execuções

- Tabela **`run_history`** (SQLite): registra cada run gerenciada pelo
  `RunManager` (`source`: `autorun` | `manual`). O `RunManager` cria a entrada
  ao iniciar e finaliza (`done`/`error` + resultado por etapa + duração) ao
  terminar; gravação é best-effort (falha → warning, run segue). Órfãs
  `running` viram `interrupted` no boot.
- Execuções por **CLI** (`flask scrape/process/enrich`) não passam pelo
  `RunManager` → **não** aparecem no histórico.
- Visão: quadro de log na página `/run` + `GET /api/runs/history`.

## Perguntas em aberto

- [x] ~~D-003/D-004~~ → **manual por ora (ambiente WSL)**
- [ ] Precisa de healthcheck/estatísticas na UI? → hoje: `/api/stats` existe

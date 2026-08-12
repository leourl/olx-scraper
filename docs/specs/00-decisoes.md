# Decisões do projeto

Registro único de escolhas tomadas (com contexto) e alternativas descartadas (com motivo).

## Status: implementação em andamento (Fase 0 e 1 concluídas)

> Decisões fechadas com data e doc de referência. Em aberto: D-003.

---

## Formato do registro

Cada decisão segue o modelo:

```md
### D-XXX — <título da decisão>
- **Data:** AAAA-MM-DD
- **Contexto:** por que essa decisão existe
- **Decisão:** o que foi escolhido
- **Alternativas:** o que foi considerado e por que foi descartado
- **Impacto:** o que muda no código/docs
```

---

## Decisões abertas (a definir ao longo do planejamento)

- D-001 — ~~Provedor de LLM~~ → **fechada** (ver abaixo)
- D-002 — ~~Persistência das specs~~ → **fechada** (ver abaixo)
- D-003 — Reprocessamento → **manual por ora** (ambiente WSL) — ver 08
- D-004 — ~~Agendamento~~ → **fechada** (ver abaixo)

## Decisões fechadas

### D-001 — Provedor de LLM
- **Data:** 2026-08-05
- **Contexto:** extrair specs de descrições despadronizadas; custo deve ser mínimo.
- **Decisão:** **DeepSeek V4 Flash** via **Responses API** (`POST /responses`,
  base `https://api.deepseek.com`, modelo `deepseek-v4-flash`). Saída estruturada
  com `text.format: {type: json_schema}`. **Thinking desligado**
  (`reasoning.effort: none`) para rapidez/determinismo. Auth: `DEEPSEEK_KEY`.
- **Alternativas:** Ollama local (descartado por exigir servidor/gpu); json_object
  (menos confiável que json_schema).
- **Impacto:** `app/extractors/llm.py`; custo ~US$0.00015/anúncio (≈US$0.18/1k).
  Validado em execução real: 32/32, ~950 in + 90 out tokens, cache hit ~70%.

### D-002 — Persistência das specs
- **Data:** 2026-08-05
- **Contexto:** filtrar por hardware na UI/API (RAM ≥ 8GB, ≤ R$ 800).
- **Decisão:** **colunas normalizadas** na tabela `ad_specs` (brand, model,
  form_factor, cpu, ram_gb, storage_gb, storage_type, gpu, year, condition,
  confidence, extraction_method) com FK unique → `ads`.
- **Alternativas:** coluna JSON (filtro só em pós-processamento); híbrido.
- **Impacto:** migração Alembic; `save_specs` no service; filtros SQL diretos.

### D-010 — Confidence na extração
- **Data:** 2026-08-05
- **Contexto:** priorizar revisão manual em anúncios ambíguos.
- **Decisão:** manter `confidence` (0–1) no schema; campos de regex elevam a 0.9.
- **Impacto:** coluna `confidence` em `ad_specs`; ordenação de revisão.

### D-011 — Imagens de anúncio (todas)
- **Data:** 2026-08-05
- **Contexto:** `image_url` única era insuficiente para comparar anúncios.
- **Decisão:** nova tabela **`ad_images`** (id, ad_id FK, url, position) — 1:N.
  Todas as `contentUrl` do JSON-LD são salvas.
- **Alternativas:** coluna JSON em `ads` (menos flexível).
- **Impacto:** `models/image.py`; `RawAd.images: list[str]`; upsert substitui a
  lista ao re-coletar.

### D-012 — CPU estruturada
- **Data:** 2026-08-05
- **Contexto:** `cpu` livre (ex.: "core i3" vs "i5-8500") não permite agrupar
  em gráficos/filtros de forma consistente.
- **Decisão:** manter `cpu` (texto) e adicionar **`cpu_family`** (i3/i5/i7/i9/
  ryzen3..9) e **`cpu_model`** (int) em `ad_specs`, calculados de forma
  **determinística** por `normalize_cpu()` no pipeline.
- **Impacto:** `extractors/regex.py::normalize_cpu`; colunas novas; gráficos
  agrupam por família/modelo.

### D-013 — Remoção de moeda/ano/condição
- **Data:** 2026-08-05
- **Contexto:** moeda é sempre BRL (irrelevante); `year` é redundante com a CPU;
  `condition` na OLX é essencialmente "usado".
- **Decisão:** remover `ads.currency`, `ad_specs.year` e `ad_specs.condition`.
- **Impacto:** colunas removidas por migração (dados antigos descartados);
  schema da LLM sem esses campos.

### D-014 — Data de publicação
- **Data:** 2026-08-05
- **Contexto:** útil para ordenar por novidade; presente apenas na listagem
  ("Ontem, 16:33", "31 de jul, 15:25").
- **Decisão:** `ads.published_at` (datetime), convertido para **UTC**
  (assumindo `America/Sao_Paulo`). Novo `parse_olx_date()`.
- **Impacto:** `scrapers/dates.py`; coluna nova; backfill da listagem preenche
  os existentes (32/32 preenchidos).

### D-016 — Geração da CPU (filtro)
- **Data:** 2026-08-05
- **Contexto:** usuário quer filtrar por geração ("pelo menos 8ª geração").
- **Decisão:** coluna **`cpu_generation`** (int) em `ad_specs`, derivada de
  forma determinística: Intel `model // 1000` (8500→8, 10100→10); Ryzen = série
  (5600→5). Faixas plausíveis: Intel 1–14, Ryzen 1–9; fora disso → `None`
  (ex.: "i5 18500" de vendedor → None). Filtros `gen_min`/`gen_max` — que, por
  **D-029**, passam a valer **por família** (ignorados sem `cpu_family`), pois
  as escalas Intel/Ryzen não são comparáveis.
- **Impacto:** `generation_from()` no regex; filtro na API/UI; backfill offline.

### D-019 — Página de ofertas (decisão de compra)
- **Data:** 2026-08-05
- **Contexto:** ajudar na decisão de compra com análise de mercado.
- **Decisão:** página **`/offers`** com (a) **benchmark** de preço por geração
  (p25/p50/p75) e (b) **ranking de ofertas** pelo desconto % vs mediana da
  geração, com bandeiras (peça/sucata, muito barato, muito abaixo/acima do
  mercado). Filtros próprios da página. Por **D-029**, benchmark e ranking
  passaram a comparar **por (família, geração)** (coluna "Família · Geração"),
  já que a mediana global misturava Intel e Ryzen. Palavras-chave de peças/sucata
  restritas a termos inequívocos (evita falso positivo com "mouse/teclado"
  incluídos no anúncio).
- **Impacto:** `ad_service.price_benchmarks`/`best_deals`/`is_parts`;
  rota `/offers` + `offers.html`.

### D-018 — Gráfico preço × geração
- **Data:** 2026-08-05
- **Contexto:** visualizar relação custo/geração para achar bom custo-benefício.
- **Decisão:** página **`/chart`** com scatter **Chart.js via CDN** (X = preço em
  R$, Y = geração 2–14). Pontos coloridos **por marca** (Dell/Lenovo/Outras),
  hover com tooltip (modelo, CPU, RAM, preço) e clique abre o anúncio.
  **Filtros próprios da página** (reusam o partial `_filters.html`).
  Dados via `ad_service.chart_data()` (só com preço + geração). Por **D-029**,
  o gráfico passou a **exigir família de CPU** (aviso quando não há família;
  eixo Y 1–14 Intel / 1–9 Ryzen), pois o eixo global misturava eras.
- **Impacto:** `blueprints/main/routes.py` (`/chart`) + `chart.html` + partial
  `_filters.html` compartilhado; `ad_service.chart_data`.

### D-017 — Imagens na UI (anti-hotlink)
- **Data:** 2026-08-05
- **Contexto:** imagens não carregavam — OLX bloqueia hotlink (403 quando o
  browser envia `Referer: localhost:5000`).
- **Decisão:** `<meta name="referrer" content="no-referrer">` no `base.html` —
  browser deixa de enviar Referer → CDN serve 200. Sem proxy server-side.
- **Impacto:** 1 linha no template; resolve index/detalhe/review.

### D-015 — API e UI (shape da resposta, estilo)
- **Data:** 2026-08-05
- **Contexto:** definir contrato da API e estilo da interface.
- **Decisão:** API expõe **`price_cents`** (int) e `images[]`; sem `currency`.
  UI com **Pico CSS via CDN**; endpoints `GET /api/ads`, `/api/ads/<id>`,
  `/api/stats` e páginas `/`, `/ads/<id>`, `/review`. Sem CSV por ora.
- **Impacto:** `blueprints/api` + `blueprints/main`; docs 07.

### D-005 — Limite de páginas por execução
- **Data:** 2026-08-05
- **Contexto:** compatibilidade com o limite de 1 request/segundo.
- **Decisão:** rate limit fixo em **1 req/s** (`SCRAPER_DELAY=1.0`); `max_pages`
  default 5 (~250 ads → run ~5–7 min), configurável via `.env`/CLI `--max-pages`.
- **Impacto:** implementado no `client.py` (lock global) e no comando `scrape`.

### D-007 — Armazenamento do preço
- **Data:** 2026-08-05
- **Contexto:** evitar erro de float; comparar preços com exatidão.
- **Decisão:** `price_cents` como **int** (R$ 1.499,90 → 149990), moeda em `currency`.
- **Impacto:** `parse_price_cents` no scraper; coluna `price_cents` na tabela `ads`.

### D-008 — Região da busca
- **Data:** 2026-08-05
- **Contexto:** busca deve ser limitável a estado/região.
- **Decisão:** opção CLI `--region` (default `estado-sp`); URL base
  `https://www.olx.com.br/{region}?q=...&sf=1&o={page}`.
- **Impacto:** assinatura de `OlxScraper.search(query, region, max_pages)`.

### D-009 — Fluxo de coleta (listagem + detalhes no mesmo run)
- **Data:** 2026-08-05
- **Contexto:** descrição completa só existe na página de detalhe.
- **Decisão:** `flask scrape` faz listagem → upsert → detalhes dos anúncios novos
  no mesmo run (com a opção `--no-details` para só listar).
- **Impacto:** N+1 requests; comportamento validado em execução real (32/32 com
  descrição).

### D-006 — Fonte de dados (HTML vs endpoints JSON internos)
- **Data:** 2026-08-05
- **Contexto:** testamos as 3 páginas de exemplo (listagem + 2 detalhes).
- **Decisão:** **HTML via BeautifulSoup**. Listagem usa seletores `olx-adcard-*`
  / `data-testid`; página de detalhe usa JSON-LD `@type: Product` (preço int,
  descrição, imagens). Páginas retornam 200 com User-Agent realista.
- **Alternativas:** endpoints JSON internos da OLX (não usados; menos estáveis).
- **Impacto:** documentado em `05-scraping.md`; sem navegador headless.

### D-006b — Fluxo de coleta (descrição requer página de detalhe)
- **Data:** 2026-08-05
- **Contexto:** a listagem não traz a descrição completa (essencial para a
  extração de specs com LLM).
- **Decisão:** coletar a listagem primeiro; depois buscar a página de detalhe de
  cada anúncio novo (com delay entre requests) para obter descrição + specs.
- **Impacto:** N+1 requests por busca (~33 requests/32 ads); aceitável em CLI
  batch com delay ~2.5s.

### D-020 — Página /run (execução via UI)
- **Data:** 2026-08-05
- **Contexto:** rodar scrape/enrich/process sem CLI, com termos definidos numa
  tela e feedback ao vivo.
- **Decisão:** página **`/run`** com textarea (1 termo por linha), região e
  checkboxes das etapas. A run roda em **thread de fundo** (`RunManager`, uma
  por vez — 409 se já houver ativa) e a UI faz **polling** em
  `GET /api/runs/<id>` a cada 1s (barra de progresso + log). Termos persistidos
  em **JSON em `instance/run_terms.json`**; histórico de runs **só em memória**
  (some ao reiniciar). Lógica de coleta extraída de `cli.py` para
  `app/services/runner.py` (CLI e UI compartilham).
- **Alternativas:** fila de runs (descartada — sem risco de 403 com 1 run);
  histórico em tabela (descartado — migração desnecessária por ora).
- **Impacto:** `app/services/runner.py`, endpoints `POST/GET /api/runs`,
  `GET /api/runs/current`, template `run.html`, `run.py` com `threaded=True`.
  Testes offline em `tests/test_runner.py`.

### D-021 — Autorun (agendamento automático)
- **Data:** 2026-08-05
- **Contexto:** rodar a coleta sem intervenção num Raspberry Pi 3 (24/7);
  D-004 era "manual por ora".
- **Decisão:** **APScheduler in-app** (`BackgroundScheduler`, puro Python).
  `AUTORUN_ENABLED=1` no `.env` inicia o scheduler do processo; o job checa a
  cada 30s se está na hora (intervalo `AUTORUN_INTERVAL_MINUTES`, default 120)
  e dispara **`scrape + process`** pelos termos de `instance/run_terms.json`,
  via `RunManager.start` (reusa a trava uma-run-por-vez — pula o tick se houver
  run ativa). Estado **ligado/desligado persistido** em `instance/autostart.json`
  e controlável por um **switch na página `/run`** (`GET/POST /api/autostart`).
- **Alternativas:** systemd/cron externo (descartado — runs CLI não aparecem na
  página `/run` e o ambiente era WSL; agora RPi); thread própria com
  `threading.Timer` (descartado — APScheduler traz coalescing/replace).
- **Impacto:** dep `apscheduler`; `app/services/autoscheduler.py`;
  `AUTORUN_ENABLED`/`AUTORUN_INTERVAL_MINUTES` no `.env`; endpoints
  `/api/autostart`; switch em `run.html`. Testes offline em
  `tests/test_autoscheduler.py`.

### D-022 — Verificação de disponibilidade (anúncios ainda publicados)
- **Data:** 2026-08-05
- **Contexto:** o sistema nunca confirma se um anúncio continua publicado;
  anúncios vendidos/removidos permanecem no banco para sempre (preços
  obsoletos em lista/gráfico/ofertas) e um 404 no detalhe derrubava a run.
- **Decisão:** colunas **`ads.is_active`** (bool, default True) e
  **`ads.removed_at`** (datetime UTC). Nova etapa **`check`** no `RunManager`
  (+ CLI `flask check`): GET no detalhe de cada ativo; **404/410 → removido**;
  200 com JSON-LD `Product` → ativo; 200 sem JSON-LD → `sem_confirmar` (não
  marca, evita falso positivo); erro de rede → não marca. `OlxClient.get`
  passa a **não levantar em 404/410** (corrige run que abortava). Scrape marca
  `is_active=True`/limpa `removed_at` para vistos na listagem (ausência na
  listagem **não** marca removido — paginação pode esconder). Autorun vira
  **`scrape → check → process`**. UI/API: removidos **ocultos por padrão**,
  filtro `include_inactive`, badge "removido" no detalhe/card; `stats`/gráfico/
  ofertas contam só ativos.
- **Alternativas:** marcar removido só por não aparecer na listagem (falso
  negativo por paginação/ordenação — descartado); HEAD request (OLX não expõe
  status confiável — descartado); verificação só no detalhe de anúncios novos
  (cobertura insuficiente — descartado).
- **Impacto:** migração Alembic; `runner.py` (`run_check` + `VALID_STEPS`),
  `client.py` (404/410), `ad_service` (filtros + `include_inactive` +
  `ad_to_dict` + stats), `autoscheduler.py` (3 etapas), API/UI (`_filters.html`,
  badge), CLI `check`. Spec detalhada em `docs/specs/10-verificacao-disponibilidade.md`.

### D-023 — Retry automático da extração LLM (JSON inválido)
- **Data:** 2026-08-05
- **Contexto:** a LLM às vezes ecoa o schema (JSON inválido) e o ad falha,
  atrasando-o para o próximo ciclo (ad falho não recebe `extracted_at`, então o
  autorun já o re-tenta; o `--force` só reprocessa concluídos). A falha vira um
  atraso de um ciclo + `falhas` nas stats.
- **Decisão:** retry síncrono em `DeepSeekClient.extract_specs` (até
  `LLM_MAX_RETRIES` extras, default 2) quando a saída falhar o parse/validação
  pydantic. Nas re-tentativas, anexar **nota corretiva anti-echo ao final do
  `input`** (as `instructions` permanecem constantes → cache de prefixo da
  DeepSeek preservado, retry barato). `LlmUsage` ganha `retries`; tokens somados
  entre tentativas; **`LlmError` carrega o uso acumulado** (pipeline retorna no
  caminho de falha, então tentativas falhas também entram em tokens/`retries`/
  custo das stats); `run_process` expõe `retries` e o CLI ecoa. Erros HTTP
  (5xx/429/timeout) continuam falhando imediatamente (fora do escopo).
  Esgotadas as tentativas → `LlmError` → ad fica pendente para o próximo ciclo.
- **Alternativas:** limpeza "cosmética" do JSON ecoado por regex (frágil,
  descartada por ora); retry também em HTTP com backoff (adiado); fila de
  reprocessamento assíncrono (D-003, fora desta spec).
- **Impacto:** `extractors/llm.py` (loop + `RETRY_NOTE` + `LlmUsage.retries` +
  `LlmError.usage`), `extractors/pipeline.py` (uso no caminho de falha),
  `config.py`/`TestConfig` (`LLM_MAX_RETRIES`), `runner.py` (`max_retries` +
  stats + falha soma uso), `cli.py` (eco de retries), testes em
  `tests/test_llm.py`. Spec detalhada em `docs/specs/11-retry-llm.md`.

### D-024 — Rastro de execuções (tabela `run_history`) + logging com rotação
- **Data:** 2026-08-05
- **Contexto:** não há como saber se o autorun executou devidamente: sem
  configuração de logging os `log.info` do autorun são engolidos (sem handlers
  só WARNING+ sai por `lastResort`), e o estado das runs é só em memória
  (some no restart) — `autostart.json` guarda apenas o último toggle.
- **Decisão:** (1) **logging real** via `app/logging_setup.py`
  (`LOG_LEVEL` default INFO; `LOG_FILE` opcional relativo a `instance/`;
  **`RotatingFileHandler`** 5MB × 3 — `FileHandler` simples foi descartado por
  crescer sem limite e esgotar o disco do RPi 3; skip em `TESTING`, sem duplicar
  handlers); (2) **histórico em tabela SQLite `run_history`** (não JSON — JSON
  sem ACID e cap/leitura manuais são retrabalho; o D-020 já previa migrar runs
  para o banco): `source` ('autorun'|'manual'), `started_at`/`ended_at`
  (timezone=True), `duration_sec`, `steps`/`result` em `db.JSON`, `status`,
  `error`.   **O `RunManager` cria e finaliza** as entradas (`start(source=)`; `_run`/
  `execute` gravam `done`/`error` + result + duração no fim) — a finalização
  independe do scheduler, então cobre manual + autorun + switch desligado no
  meio; órfãs `running` viram `interrupted` no boot do `create_app` (guardas
  `TESTING`/`_in_main_process`), **com `try/except OperationalError`** — sem
  isso o `flask db upgrade` (que roda `create_app()` antes de migrar) abortaria
  num deploy do zero (tabela inexistente) e a migração nunca executaria;
  `create_run_entry` roda **antes** de registrar o job (falha → `history_id=
  None` + warning, sem job zumbi que travaria `current()`); `finalize_run_entry`
  é best-effort (falha não vira run `done` em `error`);
  (3) **quadro de log na página `/run`** (tabela server-side: id, tipo, início
  UTC, duração, status, resumo por etapa, erro) + endpoint
  **`GET /api/runs/history`**.
- **Alternativas:** `instance/autorun_history.json` (cap manual) — descartado
  (sem ACID, retrabalho, D-020); `FileHandler` simples — descartado
  (exaustão de disco); tracking no `AutoScheduler` via tick — descartado (a
  finalização pertence ao `RunManager`); polling na UI — desnecessário.
- **Não-objetivos:** CLI (`flask scrape/process/enrich`) não passa pelo
  `RunManager` → fora do histórico por ora.
- **Impacto:** `app/logging_setup.py` (novo), `config.py`/`TestConfig`
  (`LOG_LEVEL`/`LOG_FILE`/`SQLALCHEMY_ENGINE_OPTIONS` com `busy_timeout=30`),
  `app/models/run_history.py` (novo) + migração,
  `app/services/run_history_service.py` (novo), `app/services/runner.py`
  (`RunJob.history_id`, `start(source=)`, criação antes do job, `rollback` +
  finalização best-effort em `_run`/`execute`), `app/services/autoscheduler.py`
  (`source="autorun"`), API (`GET /api/runs/history`), UI (`run.html` + CSS),
  testes (`test_run_history`, `test_logging`, API/UI, ajustes em
  runner/autoscheduler). Spec detalhada em
  `docs/specs/12-autorun-log-historico.md`.

### D-025 — Bypass do Cloudflare (curl_cffi) + cooldown de bloqueio
- **Data:** 2026-08-06
- **Contexto:** o autorun passou a falhar com **403 Forbidden** no primeiro
  request de listagem (`ScrapeBlockedError`). Diagnóstico: **não é IP nem UA** —
  do mesmo IP, `curl` com o mesmo User-Agent retorna 200 e o `httpx` retorna
  403 ("Attention Required! | Cloudflare"); o `curl_cffi` com
  `impersonate="chrome"` retorna 200. O Cloudflare bloqueia o **fingerprint
  TLS/HTTP2 (JA3)** do stack `ssl` do CPython (httpx), antes mesmo de avaliar
  headers.
- **Decisão:** (1) trocar o transporte do `OlxClient` por **`curl_cffi`** com
  `impersonate` de Chrome (`SCRAPER_IMPERSONATE`, default `chrome`; wheels para
  `x86_64`/`aarch64`/`armv7l`, instala no RPi sem compilar). A assinatura
  pública (`OlxClient(user_agent, timeout, delay, transport=)`) é preservada:
  testes continuam injetando `httpx.MockTransport` via um adaptador
  (`_HttpxTransportFetcher`). Erros de rede passam a ser capturados como
  `(httpx.HTTPError, RequestException, CurlError)` — inclusive no `run_check`
  (sem isso, falha de rede no `check` quebrava a run em vez de contar
  `erros`). (2) **Cooldown de bloqueio**: run que termina por 403 vira
  `status="blocked"` (não `error`), persiste "até quando" em
  `instance/scrape_block.json` (`SCRAPER_BLOCK_COOLDOWN_MINUTES`, default 60;
  `0` = sem cooldown) e o **autorun** não dispara novas runs enquanto durar o
  cooldown (reavalia a cada 30s e retoma sozinho; não reseta `_last_run_at`).
  Run bem-sucedida limpa o bloqueio (`clear_blocked`). Log do estado `blocked`
  só na transição (evita ~120 linhas/hora de spam do tick de 30s). Badge CSS
  para `blocked` no quadro do `/run`.
- **Alternativas:** proxy/rotação de IP (desnecessário — o fingerprint resolve;
  cooldown cobre marcação temporária); Playwright headless (pesado para RPi 3);
  retry de 403 dentro do client (bloqueio pós-fingerprint-real não é
  transitório — tratamento é no nível da run); mexer em headers/UA do httpx
  (não muda o JA3 — não resolveria); endpoints JSON internos da OLX (D-006 já
  descartou).
- **Impacto:** dep `curl_cffi` (+`pyproject.toml`; httpx permanece para o
  `DeepSeekClient`); `client.py` (fetcher curl_cffi + adaptador de teste +
  `NETWORK_ERRORS`); `config.py`/`TestConfig` (`SCRAPER_IMPERSONATE`,
  `SCRAPER_BLOCK_COOLDOWN_MINUTES`); `app/services/scrape_block.py` (novo);
  `runner.py` (status `blocked` + `set_blocked`/`clear_blocked` + catch duplo
  no `run_check`); `autoscheduler.py` (skip por cooldown + log na transição);
  CSS do badge; testes novos (`test_scrape_block`, runner/autoscheduler/client);
  docs (05, 08, AGENTS). Sem migração de banco. Spec detalhada em
  `docs/specs/13-bypass-cloudflare.md`.

### D-026 — Cadastro manual de anúncio por link
- **Data:** 2026-08-10
- **Contexto:** a coleta só entra por busca por termo (`scrape`); não há como
  cadastrar um anúncio específico encontrado fora da monitoração (grupo, site de
  ofertas, busca avulsa) sem esperar aparecer numa listagem futura — que pode
  nunca acontecer.
- **Decisão:** novo fluxo de **cadastro manual por link**, com checagem completa
  e cadastro via o mesmo pipeline do resto da app: `import_single_ad` em
  `app/services/runner.py` (coleta centralizada, sem duplicação) valida a URL
  (OLX + `olx_id` via `olx_id_from_url`, agora tolerante a `#`/`?` de links
  copiados do navegador), **normaliza a URL** (remove query de tracking como
  `?lis=...` e fragmento → URL canônica, idêntica à do scraper; evita 403 do
  Cloudflare e duplicatas por parâmetro), busca o detalhe com `OlxClient`
  (curl_cffi/impersonate), faz o parse do JSON-LD (agora com `name`/título) e
  grava via `ad_service.upsert_raw(raw, refresh=True)`. **Duplicado re-busca e
  atualiza de verdade** — `upsert_raw` ganha o flag `refresh` que atualiza
  `price_cents` (não zera com "sob consulta"), `title` e `description` quando
  vierem preenchidos e diferentes, **sem** alterar o comportamento do caminho
  scrape/enrich (`refresh=False`) — e re-ativa se estava removido;
  404/410 → não cadastra (`removed`); 200 sem JSON-LD → `not_an_ad`. Extração de
  specs (regex + DeepSeek) é **imediata e best-effort** (falha da LLM não
  impede o cadastro). **Síncrono no request** (1 ad, ~1–5s; não usa `RunManager`
  nem compete com a trava uma-run-por-vez; não entra no `run_history`).
  Superfícies: painel na página **`/run`**, endpoint **`POST /api/ads/import`**
  (201 criado / 200 atualizado / 400 / 410 / 422 / 503 bloqueado / 502 rede) com
  resposta **`{status, created, processed, ad}`** (metadados do request fora do
  serializer `ad_to_dict`, que não os tem) e **CLI `flask add <url>`**.
  Sem migração de banco.
- **Alternativas:** página própria de cadastro (descartada — `/run` já é a tela
  de operações); batch de vários links / extração em thread de fundo (adiado —
  spec própria); rejeitar link duplicado (descartado — dados ficariam obsoletos);
  reusar `run_check` para "checagem" (não faz sentido — o link é novo, precisa
  fetch+parse).
- **Impacto:** `scrapers/olx.py` (`_parse_detail` + `name`; `olx_id_from_url`
  tolerante a `#`/`?`), `services/ad_service.py` (`upsert_raw(ad, refresh=False)`
  — branch refresh atualiza preço/título/descrição), `runner.py`
  (`import_single_ad`), `blueprints/api/routes.py` (`/api/ads/import`, resposta
  `{status, created, processed, ad}`), `run.html` + `app.js` (`initAddAd`),
  `cli.py` (`flask add`), testes novos em `tests/test_import.py` (+ asserts em
  `test_olx.py`/`test_ad_service.py`/`test_ui.py`), docs.
  Spec detalhada em `docs/specs/14-cadastro-manual.md`.

### D-027 — Geração explícita de CPU no texto (sem número de modelo)
- **Data:** 2026-08-11
- **Contexto:** anúncios que descrevem a CPU sem número de modelo (ex.: "Intel
  Core i5 de 10ª geração" — ad 888 do cadastro manual D-026) ficam com
  `cpu_model=None` → `cpu_generation=None` (derivada de `model // 1000`) → **não
  aparecem no `/chart` e `/offers`** (filtram `cpu_generation IS NOT NULL`),
  embora apareçam na busca. Medido no RPi: 667 `ad_specs` sem geração; 135
  mencionam "geração" no texto.
- **Decisão:** (1) **detecção de geração explícita** no regex (`extract_regex`)
  — padrão `Nª/º/a/o/st/nd/rd/th` + `geração|geracao|generation|gen` (número
  antes ou depois, faixa **1–14 garantida pela regex**, com `(?<!\w)` contra
  dígito parcial em números maiores e o dígito da família ("i7 geração") e
  `(?!\d)` no ramo número-depois) → `RegexResult.cpu_generation`,
  campo **fora do `resolved`** (não é campo do schema `AdSpec` — o `_merge`
  pydantic o ignoraria silenciosamente; é semântica do pipeline). (2) **fallback no pipeline** (`extract_specs`): se
  `generation_from(family, model)` → `None` **e** houver `spec.cpu` **e** a
  família for compatível (Intel 1–14, Ryzen 1–9, desconhecida 1–14), usar a
  geração explícita. `cpu_model` permanece `None` (não inventa modelo).
  (3) **re-processo seletivo** `flask process --missing-generation`
  (`run_process(missing_generation=True)`, precedência `ad_id` → `missing_generation`
  → `force` → pendentes): regex + LLM só nos ads com specs e `cpu_generation
  IS NULL` (~667 no RPi, custo ~US$0,11; o `--force` reprocessaria ~900
  indiscriminadamente).
- **Alternativas:** mapa Dell OptiPlex/Lenovo ThinkCentre → geração (heurístico,
  coberto pelo texto explícito; adiado); backfill determinístico só-regex sem
  LLM (descartado — usuário optou por re-processo via LLM, que também pode pegar
  modelo perdido pelo regex).
- **Impacto:** `extractors/regex.py` (`_GEN_RE` + `RegexResult.cpu_generation`),
  `extractors/pipeline.py` (`_gen_in_family` + fallback), `services/runner.py`
  (`missing_generation` + import `AdSpec`), `cli.py` (flag), testes novos
  (`test_regex`/`test_pipeline`/`test_runner`), deploy RPi (`git pull` +
  `flask process --missing-generation`), docs. Sem migração de banco. Spec
  detalhada em `docs/specs/15-geracao-explicita-cpu.md`.

### D-028 — Ocultar anúncio manualmente (toggle "Disponível")
- **Data:** 2026-08-11
- **Contexto:** o re-processo de geração (D-027) e o cadastro manual (D-026)
  trouxeram muita sucata/peças ao banco; não há como o usuário esconder um
  anúncio — o `is_active` é controlado pelo `run_check` e o `upsert_raw`
  **re-ativa** anúncios que reaparecem na listagem (sucata costuma ficar
  publicada), então reusá-lo faria o anúncio voltar no próximo scrape.
- **Decisão:** flag **separada e persistente** `Ad.user_disabled` (bool, default
  `False`) controlada por um **toggle "Disponível"** na página `/ads/<id>`
  (`POST /api/ads/<id>/disabled` com `{disabled}`). O scrape (`upsert_raw`) **não
  toca** essa coluna — o anúncio fica oculto até o usuário reativar. Todos os
  filtros "ativo" passam a ser `is_active=True AND user_disabled=False`
  (lista, `/chart`, `/offers`, `/review` via `_apply_filters`; `stats`,
  `_price_by_family_gen`); as filas de trabalho pulam ocultos
  (`run_check`, `run_process` `missing_generation`/`force`,
  `list_pending_extraction`, `list_missing_description`). `include_inactive`
  revela removidos **e** ocultos (badges distintos); `ad_to_dict` expõe
  `user_disabled`; `stats` ganha contador `ocultos`. Requer migração.
- **Alternativas:** reusar `is_active` (descartado — `upsert_raw` re-ativa na
  próxima listagem); ocultar = apagar o ad (descartado — perde dados e specs).
- **Impacto:** migração Alembic (`ads.user_disabled`), `models/ad.py`,
  `services/ad_service.py` (filtros + `set_user_disabled` + `ad_to_dict` +
  `stats.ocultos`), `services/runner.py` (filtros), `blueprints/api/routes.py`
  (endpoint), `ad_detail.html` + `app.js` (`initAdDetail`) + `index.html` +
  `_filters.html` + CSS, testes novos, deploy RPi (`git pull` + `flask db
  upgrade` + reiniciar), docs. Spec detalhada em
  `docs/specs/16-ocultar-anuncio-manualmente.md`.

### D-029 — Filtro de CPU por família (Intel × Ryzen)
- **Data:** 2026-08-11
- **Contexto:** `cpu_generation` usa escalas **incomparáveis entre vendedores** —
  Intel = `modelo // 1000` (faixa 1–14), Ryzen = série comercial (faixa 1–9). O
  filtro global `gen_min`/`gen_max` misturava eras (Intel 8ª/2017 com Ryzen
  8000G/2024 no mesmo balde) e "mín. 10" excluía todo Ryzen silenciosamente; o
  mesmo eixo global contaminava `/chart` (scatter) e o benchmark de `/offers`.
- **Decisão:** geração vira filtro **dependente de família** — `gen_min`/`gen_max`
  só se aplicam dentro de `cpu_family` (`_apply_filters`); **sem família, são
  ignorados** (API permissiva, sem 400). Select de família agrupado em
  Intel/AMD (`<optgroup>`) com seletor de geração **disabled** sem família e
  faixa dinâmica (Intel 1–14, Ryzen 1–9; core2/pentium/celeron/athlon sem
  geração) via `GEN_RANGE`/`gen_range_for` (exposto ao Jinja2 como template
  global). `/chart` passa a **exigir família** (aviso no lugar do scatter) com
  eixo Y por faixa. `/offers`: `_price_by_generation` → `_price_by_family_gen`;
  benchmark e ranking de desconto comparam **dentro da mesma (família, geração)**.
- **Alternativas:** manter eixo global único (é o bug — mistura eras); preset de
  era cross-vendor ("moderno: Intel ≥8 / Ryzen ≥3") (descartado por ora — vira
  spec própria); filtro por `cpu_model` na UI (descartado — pouco aderente);
  retornar 400 para `gen_min` sem família (descartado — quebra clientes).
- **Impacto:** `services/ad_service.py` (`CPU_GROUPS`/`GEN_RANGE`/
  `gen_range_for`/`cpu_group`, `_apply_filters`, `_price_by_family_gen`,
  `price_benchmarks`/`best_deals` + `family`), `blueprints/main/routes.py`
  (template global + `cpu_family_groups`), `_filters.html`, `app.js`
  (`initCpuFamilyFilter`), `chart.html`, `offers.html`, seed/testes. Sem
  migração de banco. Spec detalhada em
  `docs/specs/17-filtro-cpu-por-familia.md`.

### D-030 — Filtro por fabricante de CPU (Intel / AMD)
- **Data:** 2026-08-11
- **Contexto:** o filtro D-029 exige escolher uma **família** específica — não
  dá para ver **todos** os Intel ou **todos** os AMD de uma vez.
- **Decisão:** valores especiais **`intel`**/**`amd`** em `cpu_family`
  (`VENDOR_ALIASES`), que filtram pelo grupo de famílias do fabricante
  (`AdSpec.cpu_family.in_(CPU_GROUPS[...])`). Geração passa a valer por
  **fabricante ou família** (domínio de escala: `VENDOR_RANGE` intel 1–14 /
  amd 1–9, já que as famílias de um mesmo fabricante compartilham a escala).
  UI: opções `Intel (qualquer)`/`AMD (qualquer)` hardcoded no topo do select de
  família (auto-submit de D-029 já cobre); `_cpu_family_groups` não insere os
  aliases nos optgroups. Parsers normalizam `cpu_family` com `.lower()`
  (selected coerente em `?cpu_family=Intel`). `/chart` aceita fabricante
  (eixo Y na faixa do fabricante).
- **Alternativas:** select separado "Fabricante" na UI (descartado — controle a
  mais e exclusão mútua com a família); parâmetro novo `cpu_vendor=` (descartado
  — API mais verbosa sem ganho; `cpu_family` já é livre); filtro por era
  cross-vendor (vira spec própria).
- **Impacto:** `services/ad_service.py` (`VENDOR_ALIASES`/`VENDOR_RANGE`,
  `gen_range_for`, `cpu_group`, `_apply_filters`), `blueprints/main/routes.py`
  (`.lower()` no parse + fallback de grupos), `blueprints/api/routes.py`
  (`.lower()`), `_filters.html`, `chart.html` (textos), testes, docs. Sem
  migração de banco. Spec detalhada em
  `docs/specs/18-filtro-por-fabricante.md`.

### D-031 — Backlog de geração: fill determinístico seguro + regex Ryzen
- **Data:** 2026-08-11
- **Contexto:** medido no RPi, **558 de 929 specs (60%) sem `cpu_generation`**.
  Causas: (1) specs extraídas antes do fallback D-027 nunca foram reprocessadas;
  (2) o `run_process` default (autorun) só processava `list_pending_extraction`
  (ads **sem** specs) — spec com CPU "bare" (`core i5`) sem geração ficava assim
  para sempre; (3) o regex `normalize_cpu` falhava em "Ryzen 5 PRO 4650GE" (o
  "PRO" impedia capturar o modelo `4650` → gen 4). **Lições do backfill inicial:**
  rodar `--missing-generation` (LLM) nos 485 ativos recuperou só **+4 gerações**
  (o texto dos ads "bare" não tem a informação) e ainda **degradou ~5 specs**
  (ex.: ad 930 perdeu a `cpu`; ram 606→604, storage 572→569) — o LLM é
  não-determinístico e sobrescreve campos bons.
- **Decisão:** (1) **`run_fill_specs`** — fill **determinístico e seguro** (só
  regex, sem LLM) que preenche lacunas de `cpu`/`cpu_family`/`cpu_model`/
  `cpu_generation`/`ram`/`storage` **sem sobrescrever** valores existentes. O
  `run_process` default passa a ser: pendentes (LLM) + `run_fill_specs` limitado
  (`PROCESS_MISSING_GEN_LIMIT`, default 50) — autorun drena o backlog sem risco
  e sem pico de LLM. `--ad`/`--force`/`--missing-generation` (LLM) continuam
  para reprocesso manual explícito. (2) **Regex CPU com Ryzen**: `_CPU_RE`/
  `_CPU_BARE_RE` capturam `ryzen 5 PRO 4650GE` → `cpu`, e `normalize_cpu` já
  lida com a palavra opcional → `("ryzen5", 4650)` → gen 4 — o fill restaura o
  ad 930 deterministicamente. (3) CLI **`flask fill-specs`** para o backfill
  manual seguro. (4) Backfill no RPi via `flask fill-specs` (não LLM).
  Mapa máquina→geração (OptiPlex/ThinkCentre) avaliado e **descartado por ora**
  (heurístico). **Pendência honesta:** ads cujo texto não traz modelo nem
  geração explícita continuam sem geração — a informação não existe nos dados.
- **Alternativas:** reprocessar o backlog com LLM (`--missing-generation`) —
  provou recuperar pouco (+4) e degradar specs (recusado como caminho default;
  mantido só manual); mapa máquina→geração (descartado — heurístico).
- **Impacto:** `app/config.py` (`PROCESS_MISSING_GEN_LIMIT`),
  `app/extractors/regex.py` (`_CPU_RE`/`_CPU_BARE_RE` com Ryzen + `normalize_cpu`),
  `app/services/runner.py` (`run_fill_specs` + default), `app/cli.py`
  (`flask fill-specs`), testes, docs. Sem migração.

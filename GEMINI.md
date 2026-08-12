# AGENTS.md

Flask app que coleta anúncios da OLX (Dell OptiPlex / Lenovo ThinkCentre), extrai specs de hardware via LLM (DeepSeek) e expõe UI + API REST. Banco: SQLite + SQLAlchemy/Alembic. Deps: `uv` (Python 3.13).

**Este diretório NÃO é um repositório git** — não use comandos git.

## Comandos (sempre via `uv run`, nunca pip/venv manual)

- Deps: `uv sync` · adicionar: `uv add <pkg>` / `uv add --group dev <pkg>`
- Migrações (após alterar `app/models/*`): `uv run flask db migrate -m "..."` → `uv run flask db upgrade`
- Coletar: `uv run flask scrape "dell optiplex" --region estado-sp [--max-pages 5] [--no-details]`
- Cadastrar anúncio por link: `uv run flask add "https://.../anuncio-12345" [--no-process]`
- Preencher descrição/imagens de ads sem descrição: `uv run flask enrich`
- Extrair specs: `uv run flask process [--limit N] [--ad <id> --force]`
- Servidor (UI + API em :5000): `uv run flask run`
- Testes (offline): `uv run pytest`

## Config (.env)

- `DEEPSEEK_KEY` é obrigatória para `flask process`.
- `config.py` resolve `DATABASE_URL` sqlite relativo para **absoluto** — o Flask-SQLAlchemy resolve relativos contra o *instance path* (senão vira `instance/instance/olx.db`). Não "simplifique" isso.
- `LOG_LEVEL`/`LOG_FILE` (rotativo) via `app/logging_setup.py`; `SQLALCHEMY_ENGINE_OPTIONS` com `busy_timeout=30` para concorrência no SQLite.

## Arquitetura

- App factory em `app/__init__.py`; registra blueprints `api` (REST, prefixo `/api`) e `main` (UI, templates em `app/blueprints/main/templates/`).
- Pipeline de specs: `extractors/regex.py` (determinístico, **prevalece** sobre a LLM) → `extractors/llm.py` (DeepSeek) → `pipeline.py` (merge). `extract_specs()` retorna tupla de 6: `(spec, usage, method, cpu_family, cpu_model, cpu_generation)`.
- `services/ad_service.py` centraliza consultas (filtros/sort/serializers). Novos filtros entram em `AdFilters` + `_apply_filters`, e nos parsers de `blueprints/api/routes.py` e `blueprints/main/routes.py`. `upsert_raw(ad, refresh=False)` ganhou o flag `refresh=True` para o **recadastro manual** (atualiza preço/título/descrição de existentes; sem o flag preserva o comportamento legado de só preencher lacunas).
- **Cadastro manual por link (D-026)** — `import_single_ad(app, url, process=True)` em `services/runner.py` (valida link OLX + `olx_id`, **normaliza a URL** removendo query de tracking/fragmento — evita 403 do Cloudflare e duplicatas por parâmetro —, busca o detalhe com `OlxClient`, parse JSON-LD e `upsert_raw(..., refresh=True)`; extrai specs na hora se `process` e `DEEPSEEK_KEY`, best-effort). Superfícies: painel em `/run` (`#add-ad-form`, JS `initAddAd` em `app/static/js/app.js`), API `POST /api/ads/import` (resposta `{status, created, processed, ad}`) e CLI `flask add`. É síncrono e fora do `RunManager` (não compete com a trava uma-run-por-vez; não entra no `run_history`). Spec: `docs/specs/14-cadastro-manual.md`.
- **Ocultar anúncio manualmente (D-028)** — coluna `Ad.user_disabled` (bool, default False) controlada por um toggle "Disponível" em `/ads/<id>` (`POST /api/ads/<id>/disabled`). O scrape (`upsert_raw`) **não** toca essa flag — anúncio oculto continua oculto mesmo reaparecendo na OLX. Filtros "ativo" = `is_active=True AND user_disabled=False` (`_apply_filters` em `ad_service.py` cobre lista/gráfico/ofertas/review; `stats`, `_price_by_family_gen`, filas de trabalho `run_check`/`run_process`/`list_pending_extraction`/`list_missing_description` também pulam ocultos). `include_inactive` revela removidos **e** ocultos; `stats` expõe `ocultos`; `ad_to_dict` expõe `user_disabled`. Spec: `docs/specs/16-ocultar-anuncio-manualmente.md`.
- **Filtro de CPU por família (D-029)** — `cpu_generation` usa escalas incomparáveis (Intel `modelo//1000` 1–14, Ryzen série 1–9), então `gen_min`/`gen_max` **só se aplicam dentro de `cpu_family`** (`_apply_filters`); sem família, são ignorados (API permissiva). Constantes/helpers em `ad_service.py`: `CPU_GROUPS`, `GEN_RANGE`, `gen_range_for` (exposto ao Jinja2 via `@bp.app_template_global()`), `cpu_group`. O select de família no `_filters.html` é `<optgroup>` Intel/AMD e o seletor de geração fica `disabled` sem família (faixa dinâmica server-side; `initCpuFamilyFilter` no `app.js` dá auto-submit ao trocar família). `/chart` **exige família** (aviso sem ela); `/offers` compara preços por **(família, geração)** (`_price_by_family_gen`). Spec: `docs/specs/17-filtro-cpu-por-familia.md`.
- **Fabricante de CPU (D-030)** — valores especiais `cpu_family=intel|amd` (`VENDOR_ALIASES`) filtram pelo grupo de famílias do fabricante (`IN(CPU_GROUPS[...])`) e valem como **domínio de escala** da geração (`VENDOR_RANGE` intel 1–14, amd 1–9). UI: opções `Intel (qualquer)`/`AMD (qualquer)` hardcoded no topo do select de família; parsers normalizam `cpu_family` com `.lower()`; `_cpu_family_groups` não insere aliases nos optgroups. `/chart` aceita fabricante. Spec: `docs/specs/18-filtro-por-fabricante.md`.
- **Backlog de geração (D-031)** — o `run_process` **default** (autorun/`flask process` sem flags) processa os pendentes via LLM **+ um lote limitado de `run_fill_specs`** (`PROCESS_MISSING_GEN_LIMIT`, default 50): fill **determinístico e seguro** (só regex, sem LLM) que preenche lacunas de cpu/geração/ram/storage **sem sobrescrever** valores bons. `flask process --missing-generation` segue como reprocesso LLM **manual** (não-default — o LLM pode degradar specs). `flask fill-specs` roda o fill completo. `normalize_cpu` e `_CPU_RE` capturam Ryzen com palavra opcional (`Ryzen 5 PRO 4650GE` → `ryzen5`/4650 → gen 4).
- **Runs (scrape/enrich/process) vivem em `services/runner.py`** — a lógica foi extraída do `cli.py` (que agora só delega), e a página `/run` usa as mesmas funções via `RunManager` (uma run por vez, thread de fundo, estado em memória). Termos persistidos em `instance/run_terms.json`; API `POST/GET /api/runs`. **Não duplicar lógica de coleta em novos lugares — use `runner.py`.**
- **Histórico de runs (`services/run_history_service.py`)** — o `RunManager` persiste cada execução (autorun/manual) na tabela `run_history` (`services/run_history_service.py`): cria ao iniciar (`create_run_entry`), finaliza ao terminar (`finalize_run_entry`; `rollback` antes + best-effort). Órfãs `running` → `interrupted` no boot do `create_app` (com `try/except OperationalError` — deploy inicial não quebra o `flask db upgrade`). Quadro de log em `/run` + `GET /api/runs/history`. CLI **não** registra (fora do `RunManager`).
- **Autorun (`services/autoscheduler.py`)** — APScheduler in-app (só inicia com `AUTORUN_ENABLED=1` no `.env`); job checa a cada 30s se passou `AUTORUN_INTERVAL_MINUTES` (default 120) e dispara `scrape + check + process` via `RunManager` com `source="autorun"`. Estado ligado/desligado em `instance/autostart.json`, controlado por switch em `/run` (`GET/POST /api/autostart`).
- O form de filtros da UI é o partial `templates/_filters.html` (param `action` = endpoint), reusado por `/`, `/chart`, `/offers`. Novas páginas com filtro devem reusá-lo.
- Modelos: `Ad` (raw) ↔ `AdSpec` (1:1, specs estruturadas) + `AdImage` (1:N). Ajustes de schema sempre exigem migração.

## Scraping (OLX)

- Listagem: seletores `olx-adcard-*` / `a[data-testid="adcard-link"]`. Detalhe: JSON-LD `@type: Product` (preço int, descrição completa, **todas** as imagens).
- **Transporte = `curl_cffi` com `impersonate` de Chrome** (`SCRAPER_IMPERSONATE`) — o Cloudflare bloqueia o fingerprint TLS/HTTP2 do httpx com 403 (não é IP/UA); `_CurlFetcher` é o caminho real, `_HttpxTransportFetcher` é o adaptador dos testes (os handlers continuam `httpx.MockTransport`). Não voltar para `httpx` puro no scraper.
- 1 req/s (rate limit global); retry em 502/5xx; 403 → `ScrapeBlockedError` → run termina com status **`blocked`** e o autorun respeita `SCRAPER_BLOCK_COOLDOWN_MINUTES` (default 60, `instance/scrape_block.json`, limpo por run bem-sucedida; ver `app/services/scrape_block.py`).
- `scrape` só busca o detalhe de anúncios **novos**; existentes sem descrição exigem `flask enrich`.
- **Disponibilidade (`runner.run_check`)** — etapa `check` verifica se anúncios ativos ainda existem: 404/410 → `is_active=False` + `removed_at`; 200 com JSON-LD Product → ativo; 200 sem JSON-LD → `sem_confirmar` (não marca); erro de rede → `erros`. `OlxClient.get` **não levanta em 404/410**. Vistos na listagem são re-ativados no `upsert_raw`; filtros default ocultam removidos (`include_inactive`). Reusa o parser de `olx.py` — não duplique.
- Imagens são bloqueadas por hotlink via `Referer` — `base.html` contém `<meta name="referrer" content="no-referrer">`. **Não remover.**

## Extração LLM (DeepSeek)

- Modelo `deepseek-v4-flash`, Responses API (`POST /responses`), saída `text.format=json_schema`, thinking desligado (`reasoning.effort=none`).
- Falhas conhecidas: o modelo às vezes ecoa o schema (JSON inválido) ou devolve `0` para `ram_gb`/`storage_gb`. JSON inválido/validação falha agora tem **retry automático no mesmo run** (`LLM_MAX_RETRIES`, default 2): a nota corretiva anti-echo (`RETRY_NOTE`) é anexada ao **final do input** (as `instructions` permanecem constantes → cache de prefixo preservado); tokens somados entre tentativas e `LlmError` carrega o `usage` acumulado. Erros HTTP (5xx/429/timeout) NÃO são retentados. Ad que esgota as tentativas: retente `flask process --ad <id> --force` (falha transitória é comum). O prompt tem linha anti-echo; `pipeline._merge` e o regex sanitizam ≤0.
- `cpu_generation` é derivada deterministicamente: `model // 1000` (Intel 1–14, Ryzen 1–9; fora da faixa → `None`). `brand`/`model` também vêm de regex e sobrescrevem a LLM.
- **Geração explícita no texto (D-027)** — sem `cpu_model`, uma "10ª geração"/"10th gen"/"geração 10" no título/descrição vira `cpu_generation` (regex `_GEN_RE` + fallback no pipeline, só com CPU presente e faixa por família). Re-processo seletivo: `uv run flask process --missing-generation` (regex+LLM só nos ads com specs mas sem `cpu_generation`).
- Custo real ~US$0.00016/anúncio (cache alto); não é preocupação.

## Testes

- 100% offline: LLM via `httpx.MockTransport`, HTML real em `tests/fixtures/`, SQLite in-memory (`tests/conftest.py`). `tests/seed.py` popula dados para testes de API/UI. **Nunca** faça rede em testes.
- pandas é só dependência de **dev** (análise ad-hoc em `/tmp/opencode`); a app não deve importá-lo.

## Documentação

- `docs/specs/00-decisoes.md` registra as decisões (D-XXX). Leia antes de mudar comportamento e registre decisões novas lá; `docs/specs/*.md` tem o design (banco, scraping, API/UI, roadmap). `docs/wiki/` é a wiki do projeto em formato OKF (`docs/okf.md`).

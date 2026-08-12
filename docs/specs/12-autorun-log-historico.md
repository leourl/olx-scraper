# 12 — Rastro de execuções (tabela `run_history`) + logging com rotação + quadro de log na UI

## Contexto e problema

Não há forma confiável de saber se o autorun foi executado devidamente
(especialmente num RPi 3, 24/7):

1. **Sem configuração de logging na app** — não existe `basicConfig`/
   `FileHandler`/`LOG_LEVEL` em lugar nenhum. Sem handlers, o Python só imprime
   **WARNING+** via `lastResort` → os `log.info` do autorun são **engolidos
   silenciosamente**.
2. **Estado das runs é só em memória** (`RunManager._jobs`) — some no restart;
   a página `/run` só mostra a run atual/ativa.
3. `instance/autostart.json` registra apenas `enabled` + `updated_at` (o último
   toggle) — **não** registra execuções.
4. Resultado: após um restart, é impossível saber quando o autorun disparou,
   se a run concluiu, quanto levou ou se errou.

### Revisão da abordagem (decisões desta spec)

A primeira versão desta spec previa um JSON (`instance/autorun_history.json`) +
`FileHandler` simples. A revisão apontou duas consequências ruins para o RPi 3:

- **Corrupção/trabalho manual:** JSON não tem garantias ACID; falha de energia
  no meio de uma escrita corrompe o arquivo, e o cap/leitura manuais
  (retrabalho) duplicam o que SQLite/SQLAlchemy já entregam. O projeto já
  previa migrar runs para o banco (D-020, "histórico em tabela — adiado").
- **Exaustão de disco:** `FileHandler` simples cresceria indefinidamente,
  esgotando o armazenamento do RPi 3.

**Decisão:** usar a **tabela SQLite `run_history`** (via SQLAlchemy) desde o
início, registrando **runs do autorun e manuais (via `RunManager`)**; logging
com **`RotatingFileHandler`** (5MB × 3 backups).

## Objetivos

- Logging real com `LOG_LEVEL`/`LOG_FILE` e **rotação** (`RotatingFileHandler`),
  estável em disco no RPi 3.
- **Persistir cada execução** gerenciada pelo `RunManager` (autorun **e** manual
  via página `/run`) em `run_history`; finalizar a entrada quando a run termina.
- **Quadro de log na página `/run`**: tabela das últimas execuções (server-side)
  + endpoint `GET /api/runs/history`.
- Registrar a conclusão **mesmo que o switch seja desligado no meio da run** (a
  finalização vive no `RunManager`, independente do scheduler).

## Não-objetivos

- **CLI não registra**: `flask scrape/process/enrich` chamam as funções do
  `runner` diretamente (não passam pelo `RunManager`); ficam fora do histórico
  por ora (documentado).
- Polling/live update do quadro (atualiza ao recarregar; o painel de progresso
  existente já mostra a run ao vivo).

---

## 1. Logging real (`app/logging_setup.py`)

Novo módulo com `setup_logging(app)`:

```python
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logging(app) -> None:
    if app.config.get("TESTING"):
        return
    root = logging.getLogger()
    if root.handlers:            # não duplicar handlers no reloader
        return
    level = getattr(logging, app.config["LOG_LEVEL"].upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler()]
    if app.config.get("LOG_FILE"):
        path = Path(app.config["LOG_FILE"])
        if not path.is_absolute():
            path = Path(app.instance_path) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        ))
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(level)
```

**Config** (`app/config.py` + `TestConfig` em `conftest.py`):

```python
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "")     # "" = só console; relativo → instance/
SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
```

- `SQLALCHEMY_ENGINE_OPTIONS["connect_args"]["timeout"]` = `busy_timeout` do
  SQLite (30s, default é 5s): a thread de run + requisições + scheduler
  escrevem no mesmo banco; com operações de gravação longas, evita
  `database is locked` em `create_run_entry`/`finalize_run_entry`. A resiliência
  principal continua o best-effort (§4); o timeout reduz a frequência.
- **Nota (futuro/opcional):** `PRAGMA journal_mode=WAL` permitiria leitores
  concorrentes com um único escritor, mas não funciona em `:memory:` (testes) e
  exigiria `db.event.listen(engine, "connect", ...)` — fica fora do escopo por
  ora.

**Hook** em `create_app`: `if not app.config.get("TESTING"): setup_logging(app)`.

## 2. Modelo `RunHistory` (SQLite)

`app/models/run_history.py`:

```python
from datetime import datetime, timezone
from app.extensions import db

def _utcnow():
    return datetime.now(timezone.utc)

class RunHistory(db.Model):
    __tablename__ = "run_history"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), nullable=False)   # 'autorun' | 'manual'
    started_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True))
    duration_sec = db.Column(db.Float)
    steps = db.Column(db.JSON, nullable=False)          # ["scrape", "process"]
    status = db.Column(db.String(50), nullable=False, default="running")
    result = db.Column(db.JSON)                          # {"scrape": {...}, ...}
    error = db.Column(db.Text)
```

- `steps` e `result` em **`db.JSON`** (consistente; SQLite guarda como TEXT).
- `DateTime(timezone=True)` — padrão dos modelos existentes (`Ad.published_at`).
- Export em `app/models/__init__.py` (importar junto com `Ad`/`AdImage`/`AdSpec`)
  para o Alembic autodetect. **`create_app` importa os modelos** — manter padrão.
- Migração: `uv run flask db migrate -m "Add run_history table"` →
  `uv run flask db upgrade`.

## 3. `app/services/run_history_service.py`

- `create_run_entry(source: str, steps: list[str]) -> int` — insere `running`,
  retorna o id.
- `finalize_run_entry(history_id, status: str, result: dict | None,
  error: str | None)` — seta `ended_at=_utcnow()`, `duration_sec =
  (ended_at - started_at).total_seconds()`, `status`, `result`, `error`.
  **Grava e commita de forma isolada** (a própria sessão do service), sem
  arrastar mudanças pendentes de anúncios — ver §4 (`rollback` antes).
- `get_recent_runs(limit=50) -> list[RunHistory]` — mais recentes primeiro
  (`order_by(RunHistory.id.desc())` ou `started_at.desc()`).
- `mark_running_as_interrupted()` — `update` de todos `status == "running"` →
  `interrupted` com `error` explicativo (usado no boot).

## 4. Integração: quem cria e quem finaliza (RunManager)

**Ponto central da revisão (G1):** a finalização não depende do tick do
`AutoScheduler` — é o **`RunManager` quem sabe quando a run termina**
(`_run` seta `done`/`error`, `runner.py:267-273`). Assim cobre manual +
autorun + "switch desligado no meio" naturalmente.

### `RunManager`

- `start(app, terms, region, steps, source: str = "manual")`:
  1. **Cria a entrada do histórico ANTES de registrar o job em `_jobs`**
     (`history_id = create_run_entry(source, steps)`), guardando em
     `job.history_id` (novo campo no `RunJob`, default `None`).
  2. **Se `create_run_entry` falhar** (ex.: SQLite `database is locked`
     temporário), `log.warning` e **segue com `history_id=None`** — o histórico
     é observabilidade (best-effort), não caminho crítico. Registrar o job
     somente depois evita o **estado zumbi**: job preso em `_jobs` com status
     `queued` bloquearia `current()` para sempre (409 em todas as runs futuras).
- `_run` finaliza **no final** (dentro do `with app.app_context()`):
  - **Antes de finalizar, `db.session.rollback()`** (nos dois caminhos): a
    thread da run usa a mesma sessão que o pipeline de anúncios; se uma exceção
    deixou estado pendente (ex.: `save_specs` falhou no commit), o rollback
    descarta a sujeira e o `finalize_run_entry` commita **apenas** o histórico —
    sem commitar de arrasto alterações de anúncios de forma parcial/inadvertida.
  - sucesso: `finalize_run_entry(job.history_id, "done", job.result, None)`.
  - `except`: `finalize_run_entry(job.history_id, "error", job.result, str(e))`.
  - Se `job.history_id is None` (testes que montam `RunJob` à mão) → não finaliza.
  - **`finalize_run_entry` também em `try/except` (best-effort)**: se a
    gravação final falhar (db locked), só loga `warning` — **não** pode virar a
    run `done` em `error` (o `except Exception` do `_run` pegaria).
- `execute()` (execução síncrona usada em testes) finaliza igualmente (mesmo
  código de `_run`).

### `AutoScheduler`

Sem atributos de tracking próprios (`_tracked_run_*`/`_started_at` não existem
mais). No `tick()`, ao disparar:

```python
run_manager.start(app, data["terms"], data["region"], list(AUTORUN_STEPS), source="autorun")
```

Os ticks sem run (`disabled/not_due/...`) **não** criam entrada (só `log.info`).

### `POST /api/runs` (manual)

Chama `_run_manager().start(..., source="manual")` (default — sem mudança
explícita necessária).

## 5. Recuperação de órfãs no boot

Se o processo morrer com a run no meio, a entrada fica `running` (o `RunManager`
não finalizou). No `create_app`, junto com o scheduler, recuperar **todas** as
órfãs — **independente** de `AUTORUN_ENABLED` (senão com autorun desligado
ficariam `running` para sempre):

```python
import logging
from sqlalchemy.exc import OperationalError

log = logging.getLogger(__name__)

if not app.config.get("TESTING") and _in_main_process(app):
    from app.services.run_history_service import mark_running_as_interrupted
    with app.app_context():
        try:
            mark_running_as_interrupted()
        except OperationalError:
            log.warning("tabela run_history ausente — deploy inicial (migração pendente)")
```

- **Obrigatório o `try/except OperationalError`**: `flask db upgrade`/`flask db
  migrate` auto-detectam o pacote `app/` e **executam `create_app()` antes de
  rodar qualquer migração**. Num deploy do zero (tabela ainda não existe), a
  query subiria `no such table: run_history` → a criação da app abortaria → a
  migração nunca rodaria (**deadlock permanente**). O `except` deixa o boot
  seguir e a migração acontecer; na próxima subida a recuperação roda de fato.
- Guardas `TESTING` + `_in_main_process` (sem efeito no reloader/testes).
- Roda antes de qualquer run iniciar (o scheduler também inicia no `create_app`,
  mas não dispara imediatamente — `_last_run_at` inicial evita run no boot).

## 6. API

`app/blueprints/api/routes.py` — novo endpoint (nome **revisado**: agora o
histórico inclui runs manuais):

```
GET /api/runs/history?limit=20
→ {"entries": [ ...campos da RunHistory serializados, mais recente primeiro... ]}
```

- `limit` clampado em `[1, 50]`, default 20.
- Serialização: `id`, `source`, `started_at`, `ended_at`, `duration_sec`,
  `steps`, `status`, `result`, `error`.

## 7. UI — quadro de log na página `/run`

### `main/routes.py` (`/run`)

```python
history = run_history_service.get_recent_runs(limit=50)
for h in history:
    h.summary = _history_summary(h)      # atributo transitório p/ template
```

`_history_summary(h)` → string curta por etapa (de `h.result`), ex.:
- `scrape: novos 3 · listados 32`; `process: ok 2/3`; `check: removidos 1`
- etapa ausente → `—`

Passar `run_history=history` ao template. Horários exibidos como **UTC**
(consistente com `ad_detail.html`, que rotula "(UTC)").

### `run.html`

Nova seção abaixo do painel do autorun:

```html
<section class="history-panel">
    <h2>Histórico de execuções</h2>
    <table class="history-table">
        <thead><tr><th>#</th><th>Tipo</th><th>Início (UTC)</th><th>Duração</th><th>Status</th><th>Resultado</th></tr></thead>
        <tbody>
        {% for h in run_history %}
            <tr>
                <td>#{{ h.id }}</td>
                <td>{{ h.source }}</td>
                <td>{{ h.started_at.strftime('%d/%m %H:%M') if h.started_at else '—' }}</td>
                <td>{{ '%.0fs' | format(h.duration_sec) if h.duration_sec else '—' }}</td>
                <td><span class="history-status" data-status="{{ h.status }}">{{ h.status }}</span></td>
                <td>{{ h.summary }}{% if h.error %}<span class="muted"> · {{ h.error }}</span>{% endif %}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    {% if not run_history %}
    <p class="muted">Nenhuma execução registrada ainda.</p>
    {% endif %}
</section>
```

### CSS (`app.css`)

- `.history-panel` (borda/card, padrão de `.autostart-panel`).
- `.history-status` com cores por status (done=verde, error=vermelho,
  running=pulso, interrupted=cinza/âmbar) reusando o padrão de
  `.run-status[data-status=...]`.

## 8. Comportamento esperado

| cenário | histórico |
|---------|-----------|
| autorun dispara e conclui | `running` → `done` com `result` por etapa + duração |
| run manual (página `/run`) | entrada com `source='manual'` criada/finalizada |
| switch desligado no meio da run | conclusão registrada (finalização no `RunManager`, não no scheduler) |
| app reinicia com run no meio | órfãs `running` → `interrupted` no boot do `create_app` (com `try/except OperationalError` — deploy inicial não quebra) |
| deploy do zero (sem migração) | `flask db upgrade` roda (boot tolera tabela ausente); recuperação efetiva na 1ª subida pós-migração |
| `create_run_entry`/`finalize` falham (db locked) | best-effort: `log.warning`, run segue normalmente (sem zumbi; `done` não vira `error`) |
| exceção no pipeline de anúncios | `rollback` antes de finalizar → histórico gravado sem commitar estado parcial de anúncios |
| ticks sem run (`disabled/not_due/...`) | **sem** entrada no banco (só `log.info`) |
| CLI (`flask scrape/process`) | **fora do histórico** (não passa pelo `RunManager`) |

## 9. Testes (offline, sem rede)

`tests/test_run_history.py` (novo):
- `create_run_entry` insere `running` e retorna id; `finalize_run_entry` seta
  `done`/`result`/`ended_at`/`duration_sec`.
- `mark_running_as_interrupted` converte só as `running`.
- `get_recent_runs` retorna mais recentes primeiro e respeita `limit`.
- Integração com `RunManager`: `manager.start(source="manual")` cria entrada e
  `manager.execute()` finaliza `done`; simular `run_scrape` com erro →
  `finalize` grava `error`. (`conftest` já roda `db.create_all()` → inclui a
  tabela nova, importada em `app/models/__init__.py`.)
- **Zumbi (regressão)**: `create_run_entry` falhando (mock) → `start()` não
  deixa job em `_jobs` preso como `queued` (ou roda com `history_id=None`, sem
  bloquear `current()`).
- **Finalização best-effort**: `finalize_run_entry` falhando (mock) → a run
  permanece `done` (não vira `error`).
- **Rollback antes de finalizar**: com um erro no pipeline (ex.: `run_scrape`
  lançando), assertar que `db.session.rollback()` foi chamado antes do
  `finalize_run_entry` (mock) e que nenhuma alteração pendente de anúncio é
  commitada de arrasto.
- **Boot sem tabela (deploy inicial)**: `mark_running_as_interrupted` levantando
  `OperationalError` (mock de `has_table`/query) → a chamada no `create_app`
  não propaga (capturada) — o boot e a migração conseguem seguir.
- `AutoScheduler.tick` dispara com `source="autorun"` (assert no fake).

`tests/test_logging.py` (novo):
- `setup_logging` não adiciona handlers com `TESTING=True`.
- `LOG_FILE` relativo + `instance_path=tmp_path` → `RotatingFileHandler` cria
  arquivo; `log.info` gravado; chamada dupla não duplica handlers.
- **Limpeza obrigatória (snapshot/restore)**: em fixture/`finally`, salvar
  `handlers_before = list(root.handlers)` e restaurar com
  `root.handlers[:] = handlers_before`. **Não usar `handlers.clear()`** — isso
  removeria handlers pré-existentes (ex.: plugins do pytest) e o `RotatingFileHandler`
  para `tmp_path` ficaria ativo poluindo os testes seguintes.

`tests/test_api.py`:
- `GET /api/runs/history` com entradas semeadas (via `app.instance_path`? não —
  agora é banco: usar a `app` fixture + `db.session`) → retorna mais recente
  primeiro e respeita `limit`.

`tests/test_ui.py`:
- `/run` renderiza o quadro (semeando `run_history` no banco): contém
  "Histórico de execuções", id, `source`, status.

Ajustes:
- `tests/test_runner.py`: `RunJob` com novo campo `history_id` (default None) —
  testes que montam `RunJob` à mão seguem válidos; `_run`/`execute` só finalizam
  se `history_id` presente.
- `tests/test_autoscheduler.py`: remover testes de `autorun_history.json`
  (não existem mais); `FakeManager.start` aceita `source=`.

## 10. Documentação

- **D-024** em `docs/specs/00-decisoes.md` reescrito (tabela `run_history` +
  `RotatingFileHandler`; JSON/FileHandler descartados; CLI fora de escopo).
- `docs/specs/08-operacao.md`: env `LOG_LEVEL`/`LOG_FILE`; tabela `run_history`;
  comando de migração.
- `AGENTS.md` — seção Autorun/Runs: histórico em `run_history` (não JSON).
- Wiki OKF: `docs/wiki/banco.md` (tabela nova) e `docs/wiki/runs.md`
  (quadro de log + `/api/runs/history`).

## 11. Checklist de entrega

1. `config.py` + `TestConfig`: `LOG_LEVEL`/`LOG_FILE` +
   `SQLALCHEMY_ENGINE_OPTIONS` (`busy_timeout=30`).
2. `app/logging_setup.py` + hook no `create_app` (skip `TESTING`, sem duplicar).
3. `app/models/run_history.py` + export em `models/__init__.py` + migração.
4. `app/services/run_history_service.py` (create/finalize/recent/interrupted;
   commit isolado).
5. `runner.py`: `RunJob.history_id`, `RunManager.start(source=)` (create antes
   do job, best-effort), `_run`/`execute` finalizam (rollback antes + finalize
   best-effort).
6. `autoscheduler.py`: `source="autorun"`, sem tracking próprio.
7. `create_app`: `mark_running_as_interrupted()` no boot (`try/except
   OperationalError`, guardas).
8. API `GET /api/runs/history`; UI (`_history_summary` + `run.html` + CSS).
9. Testes novos + ajustes; suíte completa verde.
10. Docs (D-024, 08, AGENTS.md, wiki).

"""Autorun: dispara scrape + process a cada X minutos usando o RunManager.

O estado ligado/desligado é persistido em `instance/autostart.json` (o switch
da página /run manda lá). O intervalo vem do `.env` (`AUTORUN_INTERVAL_MINUTES`).

O scheduler (thread de fundo do APScheduler) só é iniciado quando a app sobe
com `AUTORUN_ENABLED=1` — é o "interruptor mestre" do processo. Com ele ativo,
o job checa o estado persistido a cada 30s e dispara a run quando é a vez.
"""
import json
import logging
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.services.runner import RunAlreadyActive, load_terms
from app.services.scrape_block import get_blocked_until

log = logging.getLogger(__name__)

AUTOSTART_FILE = "autostart.json"
AUTORUN_STEPS = ["scrape", "check", "process"]
CHECK_EVERY_SECONDS = 30


def get_enabled(instance_path: str, default: bool = False) -> bool:
    """Lê o estado persistido do autorun; usa `default` se não houver arquivo."""
    path = Path(instance_path) / AUTOSTART_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("enabled", default))
    except (OSError, json.JSONDecodeError):
        return default


def set_enabled(instance_path: str, enabled: bool) -> None:
    """Persiste o estado do autorun (também registra a hora da mudança)."""
    data = {
        "enabled": enabled,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (Path(instance_path) / AUTOSTART_FILE).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class AutoScheduler:
    """Estado + tick do autorun. O tick é testável de forma síncrona.

    `_last_run_at` fica em memória (inicializa com `time.time()`, evitando run
    imediata logo após o boot da app).
    """

    def __init__(self, instance_path: str, default_enabled: bool = False) -> None:
        self._instance_path = instance_path
        self._default_enabled = default_enabled
        self._last_run_at = time.time()
        self._blocked = False
        self._scheduler: BackgroundScheduler | None = None

    @property
    def enabled(self) -> bool:
        return get_enabled(self._instance_path, default=self._default_enabled)

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def set_enabled(self, enabled: bool) -> None:
        set_enabled(self._instance_path, enabled)

    def tick(self, app, now: float | None = None) -> str:
        """Decide se deve rodar agora e dispara. Retorna o que aconteceu."""
        now = now or time.time()
        if not self.enabled:
            return "disabled"

        # Cooldown de bloqueio: não dispara nem reseta `_last_run_at`, então o
        # tick (30s) reavalia e retoma sozinho assim que o cooldown expirar.
        blocked_until = get_blocked_until(self._instance_path)
        if now < blocked_until:
            return "blocked"

        interval = app.config["AUTORUN_INTERVAL_MINUTES"] * 60
        if now - self._last_run_at < interval:
            return "not_due"

        data = load_terms(self._instance_path)
        if not data["terms"]:
            self._last_run_at = now
            return "no_terms"

        run_manager = app.extensions["run_manager"]
        if run_manager.current():
            self._last_run_at = now
            return "skipped_active"

        try:
            run_manager.start(app, data["terms"], data["region"], list(AUTORUN_STEPS), source="autorun")
        except RunAlreadyActive:
            self._last_run_at = now
            return "skipped_active"
        self._last_run_at = now
        log.info("autorun disparou run %s (%s)", run_manager.current().id, ", ".join(AUTORUN_STEPS))
        return "started"

    def start(self, app) -> None:
        """Inicia o BackgroundScheduler com o job de checagem periódica."""
        self._scheduler = BackgroundScheduler(daemon=True)
        self._scheduler.add_job(
            self._tick_job,
            IntervalTrigger(seconds=CHECK_EVERY_SECONDS),
            args=[app],
            id="autostart",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("autorun scheduler iniciado (intervalo %d min)", app.config["AUTORUN_INTERVAL_MINUTES"])

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

    def _tick_job(self, app) -> None:
        with app.app_context():
            action = self.tick(app)
            if action in ("disabled", "not_due"):
                return
            if action == "blocked":
                # loga só na transição para não inundar o log (tick a cada 30s)
                if not self._blocked:
                    log.info("autorun em cooldown de bloqueio (OLX 403); retomará sozinho")
                    self._blocked = True
                return
            self._blocked = False
            log.info("autorun tick → %s", action)


def start_scheduler(app):
    """Cria o AutoScheduler, registra em `extensions` e inicia o job."""
    scheduler = AutoScheduler(
        instance_path=app.instance_path,
        default_enabled=bool(app.config["AUTORUN_ENABLED"]),
    )
    app.extensions["autoscheduler"] = scheduler
    scheduler.start(app)
    return scheduler

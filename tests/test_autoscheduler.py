import time
from types import SimpleNamespace

from app.services import autoscheduler
from app.services.runner import save_terms


class FakeManager:
    """Substitui o RunManager: sem threads, sem rede."""

    def __init__(self, active=None) -> None:
        self.active = active
        self.started = None

    def current(self):
        return self.active

    def start(self, app, terms, region, steps, source="manual"):
        self.started = {"terms": terms, "region": region, "steps": steps, "source": source}
        self.active = SimpleNamespace(id=1)
        return 1


def test_get_set_enabled_roundtrip(tmp_path):
    assert autoscheduler.get_enabled(str(tmp_path)) is False
    assert autoscheduler.get_enabled(str(tmp_path), default=True) is True

    autoscheduler.set_enabled(str(tmp_path), True)
    assert autoscheduler.get_enabled(str(tmp_path)) is True
    assert (tmp_path / "autostart.json").exists()

    autoscheduler.set_enabled(str(tmp_path), False)
    assert autoscheduler.get_enabled(str(tmp_path)) is False


def test_tick_disabled(tmp_path):
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=False)
    assert sched.tick(None, now=1000.0) == "disabled"


def _app_config():
    return type("_App", (), {"config": {"AUTORUN_INTERVAL_MINUTES": 120}})()


def test_tick_not_due(tmp_path):
    autoscheduler.set_enabled(str(tmp_path), True)
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = time.time() - 60  # rodou há 1 minuto
    assert sched.tick(_app_config(), now=time.time()) == "not_due"


def test_tick_no_terms(tmp_path):
    autoscheduler.set_enabled(str(tmp_path), True)
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = 0.0
    assert sched.tick(_app_config(), now=10000.0) == "no_terms"


def test_tick_skipped_when_active(tmp_path):
    autoscheduler.set_enabled(str(tmp_path), True)
    save_terms(str(tmp_path), ["dell optiplex"], "estado-sp")
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = 0.0

    class _App:
        config = {"AUTORUN_INTERVAL_MINUTES": 120}
        extensions = {"run_manager": FakeManager(active=SimpleNamespace(id=99))}

    assert sched.tick(_App(), now=10000.0) == "skipped_active"


def test_tick_starts_run_when_due(app, tmp_path):
    autoscheduler.set_enabled(str(tmp_path), True)
    save_terms(str(tmp_path), ["dell optiplex", "thinkcentre"], "estado-sp")
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = 0.0

    manager = FakeManager()
    app.extensions["run_manager"] = manager

    action = sched.tick(app, now=10000.0)
    assert action == "started"
    assert manager.started == {
        "terms": ["dell optiplex", "thinkcentre"],
        "region": "estado-sp",
        "steps": ["scrape", "check", "process"],
        "source": "autorun",
    }
    # dentro do mesmo intervalo não dispara de novo
    assert sched.tick(app, now=10000.0 + 10) == "not_due"


def test_tick_blocked_skips_without_resetting_last_run(tmp_path):
    from app.services import scrape_block

    autoscheduler.set_enabled(str(tmp_path), True)
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = 0.0
    scrape_block.set_blocked(str(tmp_path), time.time() + 3600)

    assert sched.tick(_app_config(), now=time.time()) == "blocked"
    # _last_run_at não muda: o tick reavalia a cada 30s e retoma no fim do cooldown
    assert sched._last_run_at == 0.0


def test_tick_blocked_expired_starts_run(tmp_path):
    from app.services import scrape_block

    autoscheduler.set_enabled(str(tmp_path), True)
    save_terms(str(tmp_path), ["dell optiplex"], "estado-sp")
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = 0.0
    scrape_block.set_blocked(str(tmp_path), time.time() - 1)

    class _App:
        config = {"AUTORUN_INTERVAL_MINUTES": 120}
        extensions = {"run_manager": FakeManager()}

    assert sched.tick(_App(), now=time.time()) == "started"


def test_tick_job_logs_blocked_once_on_transition(app, tmp_path, caplog):
    from app.services import scrape_block

    autoscheduler.set_enabled(str(tmp_path), True)
    sched = autoscheduler.AutoScheduler(str(tmp_path), default_enabled=True)
    sched._last_run_at = 0.0
    scrape_block.set_blocked(str(tmp_path), time.time() + 3600)

    with caplog.at_level("INFO", logger="app.services.autoscheduler"):
        sched._tick_job(app)
        sched._tick_job(app)
        sched._tick_job(app)

    cooldown_logs = [r for r in caplog.records if "cooldown" in r.getMessage()]
    assert len(cooldown_logs) == 1


def test_api_autostart_get(app, tmp_path):
    app.instance_path = str(tmp_path)
    resp = app.test_client().get("/api/autostart")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"enabled": False, "interval_minutes": 120, "running": False}


def test_api_autostart_toggle_and_persists(app, tmp_path):
    app.instance_path = str(tmp_path)
    client = app.test_client()

    resp = client.post("/api/autostart", json={"enabled": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["running"] is False
    assert data["warning"] is not None  # scheduler do processo não iniciado

    assert autoscheduler.get_enabled(str(tmp_path)) is True
    assert client.get("/api/autostart").get_json()["enabled"] is True

    resp = client.post("/api/autostart", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is False
    assert autoscheduler.get_enabled(str(tmp_path)) is False


def test_api_autostart_invalid_payload(app, tmp_path):
    app.instance_path = str(tmp_path)
    resp = app.test_client().post("/api/autostart", json={"enabled": "yes"})
    assert resp.status_code == 400


def test_run_page_shows_autostart_switch(app, tmp_path):
    app.instance_path = str(tmp_path)
    resp = app.test_client().get("/run")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="autostart-toggle"' in body
    assert "Autorun" in body

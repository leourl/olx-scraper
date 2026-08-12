import pytest
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import RunHistory
from app.services import run_history_service as rhs
from app.services.runner import RunJob, RunManager


def test_create_and_finalize(app):
    with app.app_context():
        hid = rhs.create_run_entry("autorun", ["scrape", "process"])
        entry = db.session.get(RunHistory, hid)
        assert entry.status == "running"
        assert entry.source == "autorun"
        assert entry.steps == ["scrape", "process"]

        rhs.finalize_run_entry(hid, "done", {"scrape": {"novos": 2}}, None)
        entry = db.session.get(RunHistory, hid)
        assert entry.status == "done"
        assert entry.result == {"scrape": {"novos": 2}}
        assert entry.ended_at is not None
        assert entry.duration_sec is not None and entry.duration_sec >= 0


def test_get_recent_runs_order_and_limit(app):
    with app.app_context():
        ids = [rhs.create_run_entry("manual", ["scrape"]) for _ in range(5)]
        recent = rhs.get_recent_runs(limit=3)
        assert [e.id for e in recent] == list(reversed(ids))[:3]


def test_mark_running_as_interrupted(app):
    with app.app_context():
        h1 = rhs.create_run_entry("autorun", ["scrape"])
        h2 = rhs.create_run_entry("autorun", ["scrape"])
        rhs.finalize_run_entry(h2, "done", None, None)
        rhs.mark_running_as_interrupted()
        db.session.expire_all()
        assert db.session.get(RunHistory, h1).status == "interrupted"
        assert db.session.get(RunHistory, h2).status == "done"


def test_runmanager_start_creates_history(app, monkeypatch):
    from app.services import runner as r

    monkeypatch.setattr(r, "run_scrape", lambda *a, **k: {"listados": 0, "novos": 0, "total": 0})
    manager = RunManager()
    run_id = manager.start(app, ["dell"], "estado-sp", ["scrape"], source="autorun")
    job = manager.get(run_id)
    assert job.history_id is not None
    with app.app_context():
        entry = db.session.get(RunHistory, job.history_id)
        assert entry.source == "autorun"
        assert entry.status in ("running", "done")  # thread pode finalizar antes do assert


def test_runmanager_start_besteffort_when_db_fails(app, monkeypatch):
    from app.services import runner as r

    def boom(*a, **k):
        raise OperationalError("database is locked", None, None)

    monkeypatch.setattr(r, "create_run_entry", boom)
    monkeypatch.setattr(r, "run_scrape", lambda *a, **k: {"listados": 0, "novos": 0, "total": 0})

    manager = RunManager()
    run_id = manager.start(app, ["dell"], "estado-sp", ["scrape"])
    job = manager.get(run_id)
    # sem exceção propagada; run registrada sem histórico (sem job zumbi)
    assert job.history_id is None
    assert job.id == run_id


def test_finalize_besteffort_keeps_done(app, monkeypatch):
    from app.services import runner as r

    monkeypatch.setattr(r, "run_scrape", lambda *a, **k: {"listados": 0, "novos": 0, "total": 0})

    def boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(r, "finalize_run_entry", boom)

    manager = RunManager()
    job = RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"], history_id=999)
    manager._jobs[1] = job
    manager.execute(app, 1)
    # falha ao finalizar não vira run 'done' em 'error'
    assert job.status == "done"


def test_run_failure_records_error_in_history(app, monkeypatch):
    from app.services import runner as r

    def boom(*a, **k):
        raise RuntimeError("pipeline falhou")

    monkeypatch.setattr(r, "run_scrape", boom)

    with app.app_context():
        hid = rhs.create_run_entry("manual", ["scrape"])

    manager = RunManager()
    job = RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"], history_id=hid)
    manager._jobs[1] = job
    manager.execute(app, 1)

    assert job.status == "error"
    with app.app_context():
        assert db.session.get(RunHistory, hid).status == "error"


def test_finalize_history_rolls_back_before_finalize(app, monkeypatch):
    from app.services import runner as r

    manager = RunManager()
    job = RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"], history_id=5)

    real_rollback = db.session.rollback
    rolled = []

    def spy_rollback():
        rolled.append(1)
        real_rollback()

    monkeypatch.setattr(db.session, "rollback", spy_rollback)
    manager._finalize_history(job)

    # rollback acontece antes da gravação final (descarta estado pendente)
    assert rolled == [1]


def test_create_app_tolerates_missing_table(monkeypatch):
    """Deploy inicial: tabela ausente no create_app não aborta (OperationalError)."""
    import logging

    from app import create_app
    from app.services import run_history_service

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        def boom():
            raise OperationalError("no such table: run_history", None, None)

        monkeypatch.setattr(run_history_service, "mark_running_as_interrupted", boom)

        class ProdLikeConfig:
            TESTING = False
            SQLALCHEMY_DATABASE_URI = "sqlite://"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            SECRET_KEY = "x"
            AUTORUN_ENABLED = False
            AUTORUN_INTERVAL_MINUTES = 120
            LOG_LEVEL = "WARNING"
            LOG_FILE = ""

        app = create_app(ProdLikeConfig)
        assert app is not None
    finally:
        root.handlers[:] = before

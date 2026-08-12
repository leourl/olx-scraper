"""Persistência do histórico de runs (RunHistory) em SQLite.

Toda a escrita é best-effort: o histórico é observabilidade e não pode derrubar
nem bloquear a execução das runs. Os chamadores (RunManager) já tratam falhas
como warning; este módulo só concentra as operações.
"""
import logging
from datetime import datetime, timezone

from app.extensions import db
from app.models.run_history import RunHistory

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite não preserva tz: normaliza valores naive como UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def create_run_entry(source: str, steps: list[str]) -> int:
    """Insere uma run em andamento e retorna o id."""
    entry = RunHistory(source=source, steps=list(steps), status="running")
    db.session.add(entry)
    db.session.commit()
    return entry.id


def finalize_run_entry(
    history_id: int,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Fecha uma entrada: ended_at, duração, status, result/error."""
    entry = db.session.get(RunHistory, history_id)
    if entry is None:
        log.warning("run_history %s não encontrada para finalizar", history_id)
        return
    entry.ended_at = _utcnow()
    entry.status = status
    entry.result = result
    entry.error = error
    if entry.started_at:
        started = _ensure_utc(entry.started_at)
        entry.duration_sec = (entry.ended_at - started).total_seconds()
    db.session.commit()


def get_recent_runs(limit: int = 50) -> list[RunHistory]:
    """Runs mais recentes primeiro."""
    limit = max(1, min(int(limit), 100))
    return (
        RunHistory.query.order_by(RunHistory.id.desc()).limit(limit).all()
    )


def mark_running_as_interrupted() -> None:
    """Órfãs `running` (processo morreu no meio) → `interrupted` no boot."""
    db.session.query(RunHistory).filter(RunHistory.status == "running").update(
        {
            "status": "interrupted",
            "error": "processo reiniciado com run em andamento",
        },
        synchronize_session=False,
    )
    db.session.commit()


def run_history_to_dict(entry: RunHistory) -> dict:
    def iso(dt):
        return _ensure_utc(dt).isoformat() if dt else None

    return {
        "id": entry.id,
        "source": entry.source,
        "started_at": iso(entry.started_at),
        "ended_at": iso(entry.ended_at),
        "duration_sec": entry.duration_sec,
        "steps": entry.steps,
        "status": entry.status,
        "result": entry.result,
        "error": entry.error,
    }

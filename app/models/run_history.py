from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunHistory(db.Model):
    """Registro persistido de cada execução gerenciada pelo RunManager.

    `source`: 'autorun' | 'manual'. Criação e finalização vivem no RunManager
    (o histórico é observabilidade — best-effort, não caminho crítico).
    """

    __tablename__ = "run_history"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), nullable=False, default="manual")
    started_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    ended_at = db.Column(db.DateTime(timezone=True))
    duration_sec = db.Column(db.Float)
    steps = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(50), nullable=False, default="running")
    result = db.Column(db.JSON)
    error = db.Column(db.Text)

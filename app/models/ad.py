from datetime import datetime, timezone

from sqlalchemy import text

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ad(db.Model):
    __tablename__ = "ads"

    id = db.Column(db.Integer, primary_key=True)
    olx_id = db.Column(db.String(64))
    title = db.Column(db.String(512))
    description = db.Column(db.Text)
    price_cents = db.Column(db.Integer)
    url = db.Column(db.String(1024), unique=True, index=True, nullable=False)
    city = db.Column(db.String(128))
    state = db.Column(db.String(4))
    published_at = db.Column(db.DateTime(timezone=True))
    scraped_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    extracted_at = db.Column(db.DateTime(timezone=True))
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=text("1")
    )
    user_disabled = db.Column(
        db.Boolean, nullable=False, default=False, server_default=text("0")
    )
    removed_at = db.Column(db.DateTime(timezone=True))

    images = db.relationship(
        "AdImage", backref="ad", cascade="all, delete-orphan", order_by="AdImage.position"
    )

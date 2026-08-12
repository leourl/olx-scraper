from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdSpec(db.Model):
    __tablename__ = "ad_specs"

    id = db.Column(db.Integer, primary_key=True)
    ad_id = db.Column(db.Integer, db.ForeignKey("ads.id"), unique=True, nullable=False)

    brand = db.Column(db.String(64))
    model = db.Column(db.String(128))
    form_factor = db.Column(db.String(16))
    cpu = db.Column(db.String(64))
    cpu_family = db.Column(db.String(16))
    cpu_model = db.Column(db.Integer)
    cpu_generation = db.Column(db.Integer)
    ram_gb = db.Column(db.Integer)
    storage_gb = db.Column(db.Integer)
    storage_type = db.Column(db.String(8))
    gpu = db.Column(db.String(64))

    confidence = db.Column(db.Float, default=0.5)
    extraction_method = db.Column(db.String(16))
    extracted_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    ad = db.relationship("Ad", backref=db.backref("spec", uselist=False, lazy=True))

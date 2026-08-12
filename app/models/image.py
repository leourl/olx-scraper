from app.extensions import db


class AdImage(db.Model):
    __tablename__ = "ad_images"

    id = db.Column(db.Integer, primary_key=True)
    ad_id = db.Column(db.Integer, db.ForeignKey("ads.id"), nullable=False, index=True)
    url = db.Column(db.String(1024), nullable=False)
    position = db.Column(db.Integer, default=0)

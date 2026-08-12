from datetime import datetime, timezone

from app.extractors.schema import AdSpec as AdSpecSchema
from app.extensions import db
from app.models import Ad, AdImage
from app.services import ad_service
from app.scrapers.base import RawAd


def seed_ads(app) -> None:
    """Cria um conjunto variado de anúncios com/sem specs."""
    with app.app_context():
        raw_ads = [
            RawAd(
                olx_id="1", title="Dell OptiPlex 7050 SFF i5-8500",
                url="https://sp.olx.com.br/x/optiplex-7050-1",
                price_cents=80000, city="São Paulo", state="SP",
                published_at=datetime(2026, 7, 31, 18, 25, tzinfo=timezone.utc),
                images=["https://img/a.jpg", "https://img/b.jpg"],
            ),
            RawAd(
                olx_id="2", title="Lenovo ThinkCentre M700 mini",
                url="https://sp.olx.com.br/x/thinkcentre-m700-2",
                price_cents=50000, city="Campinas", state="SP",
                published_at=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
            ),
            RawAd(
                olx_id="3", title="Dell OptiPlex 3020 (sem specs)",
                url="https://sp.olx.com.br/x/optiplex-3020-3",
                price_cents=30000, city="São Paulo", state="SP",
                published_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
            ),
            RawAd(
                olx_id="4", title="Dell OptiPlex 7010 i7-6700",
                url="https://sp.olx.com.br/x/optiplex-7010-4",
                price_cents=None, city="Santos", state="SP",
                published_at=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
            ),
            RawAd(
                olx_id="5", title="Dell OptiPlex 5050 SFF ryzen5 5600G",
                url="https://sp.olx.com.br/x/optiplex-5050-5",
                price_cents=90000, city="São Paulo", state="SP",
                published_at=datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc),
            ),
        ]
        for raw in raw_ads:
            ad, _ = ad_service.upsert_raw(raw)

        specs = [
            (1, AdSpecSchema(brand="Dell", model="OptiPlex 7050", form_factor="sff",
                             cpu="i5-8500", ram_gb=16, storage_gb=512,
                             storage_type="ssd", confidence=0.9)),
            (2, AdSpecSchema(brand="Lenovo", model="ThinkCentre M700", form_factor="mini",
                             cpu="i3-6100", ram_gb=8, storage_gb=256,
                             storage_type="ssd", confidence=0.8)),
            (4, AdSpecSchema(brand="Dell", model="OptiPlex 7010", form_factor="sff",
                             cpu="i7-6700", ram_gb=8, storage_gb=256,
                             storage_type="ssd", confidence=0.3)),
            (5, AdSpecSchema(brand="Dell", model="OptiPlex 5050", form_factor="sff",
                             cpu="ryzen5 5600G", ram_gb=8, storage_gb=512,
                             storage_type="ssd", confidence=0.9)),
        ]
        for ad_id, spec in specs:
            ad = db.session.get(Ad, ad_id)
            from app.extractors.regex import generation_from, normalize_cpu

            family, model = normalize_cpu(spec.cpu)
            generation = generation_from(family, model)
            ad_service.save_specs(
                ad, spec, "regex+llm",
                cpu_family=family, cpu_model=model, cpu_generation=generation,
            )
        db.session.commit()

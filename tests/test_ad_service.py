from datetime import datetime, timezone

from app.extensions import db
from app.models import Ad, AdImage
from app.services import ad_service
from app.scrapers.base import RawAd


def make_ad(**kw) -> RawAd:
    base = dict(
        olx_id="12345",
        title="Dell Optiplex",
        url="https://sp.olx.com.br/x/dell-optiplex-12345",
        price_cents=100000,
        city="São Paulo",
        state="SP",
    )
    base.update(kw)
    return RawAd(**base)


def test_upsert_inserts(app):
    with app.app_context():
        ad, created = ad_service.upsert_raw(make_ad())
        assert created is True
        assert Ad.query.count() == 1
        assert ad.price_cents == 100000


def test_upsert_dedup_by_url(app):
    with app.app_context():
        _, created = ad_service.upsert_raw(make_ad())
        ad2, created2 = ad_service.upsert_raw(make_ad(title="outro titulo", price_cents=1))
        assert created is True and created2 is False
        assert Ad.query.count() == 1
        assert ad2.title == "Dell Optiplex"  # não sobrescreve
        assert ad2.price_cents == 100000


def test_upsert_fills_missing_description(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad())
        assert ad.description is None
        ad2, created = ad_service.upsert_raw(make_ad(description="i5 8GB SSD"))
        assert created is False
        assert ad2.description == "i5 8GB SSD"


def test_upsert_saves_images_and_published_at(app):
    with app.app_context():
        published = datetime(2026, 7, 31, 18, 25, tzinfo=timezone.utc)
        ad, _ = ad_service.upsert_raw(
            make_ad(
                published_at=published,
                images=["https://img.olx.com.br/a.jpg", "https://img.olx.com.br/b.jpg"],
            )
        )
        # SQLite persiste datetime sem tz (UTC implícito)
        assert ad.published_at.replace(tzinfo=timezone.utc) == published
        assert [i.url for i in ad.images] == ["https://img.olx.com.br/a.jpg", "https://img.olx.com.br/b.jpg"]
        assert [i.position for i in ad.images] == [0, 1]
        assert AdImage.query.count() == 2


def test_upsert_replaces_images_on_refetch(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad(images=["https://img.olx.com.br/a.jpg"]))
        ad2, created = ad_service.upsert_raw(make_ad(images=["https://img.olx.com.br/x.jpg"]))
        assert created is False
        assert [i.url for i in ad2.images] == ["https://img.olx.com.br/x.jpg"]
        assert AdImage.query.count() == 1


def test_upsert_refresh_false_unchanged(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad(title="Antigo", price_cents=100000, description="desc antiga"))
        ad2, created = ad_service.upsert_raw(
            make_ad(title="Novo", price_cents=150000, description="desc nova")
        )
        assert created is False
        assert ad2.title == "Antigo"
        assert ad2.price_cents == 100000
        assert ad2.description == "desc antiga"


def test_upsert_refresh_updates_fields(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad(title="Antigo", price_cents=100000, description="desc antiga"))
        ad2, created = ad_service.upsert_raw(
            make_ad(title="Novo", price_cents=150000, description="desc nova"), refresh=True
        )
        assert created is False
        assert ad2.title == "Novo"
        assert ad2.price_cents == 150000
        assert ad2.description == "desc nova"


def test_upsert_refresh_does_not_zero_price(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad(price_cents=100000))
        ad2, created = ad_service.upsert_raw(make_ad(price_cents=None), refresh=True)
        assert created is False
        assert ad2.price_cents == 100000


def test_save_specs_stores_cpu_structured(app):
    from app.extractors.schema import AdSpec as AdSpecSchema

    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad())
        spec = AdSpecSchema(brand="Dell", model="OptiPlex 7050", cpu="i5-8500")
        row = ad_service.save_specs(ad, spec, "regex+llm", cpu_family="i5", cpu_model=8500)
        assert row.cpu_family == "i5"
        assert row.cpu_model == 8500
        assert ad.extracted_at is not None


def test_list_missing_description(app):
    with app.app_context():
        ad_service.upsert_raw(make_ad(url="https://sp.olx.com.br/x/sem-desc-1"))  # sem descrição
        ad_service.upsert_raw(make_ad(url="https://sp.olx.com.br/x/com-desc-2", description="i5 8GB SSD"))
        missing = ad_service.list_missing_description()
        assert len(missing) == 1
        assert missing[0].description is None


def test_list_hides_user_disabled(app):
    from app.services.ad_service import AdFilters

    with app.app_context():
        ad_service.upsert_raw(make_ad())
        hidden, _ = ad_service.upsert_raw(make_ad(url="https://sp.olx.com.br/x/hidden-1", title="Oculto"))
        hidden.user_disabled = True
        db.session.commit()

    items, total = ad_service.list_ads(AdFilters())
    assert total == 1
    assert all(not a.user_disabled for a in items)

    items2, total2 = ad_service.list_ads(AdFilters(include_inactive=True))
    assert total2 == 2
    assert any(a.user_disabled for a in items2)


def test_gen_filter_scoped_to_family(app):
    from app.services.ad_service import AdFilters
    from tests.seed import seed_ads

    seed_ads(app)
    with app.app_context():
        # com família: intervalo aplicado à geração daquela família
        items, total = ad_service.list_ads(AdFilters(cpu_family="i5", gen_min=8))
        assert total == 1
        assert {a.id for a in items} == {1}

        # sem família: gen_min é ignorado (escalas Intel/Ryzen incompatíveis)
        _, total = ad_service.list_ads(AdFilters(gen_min=8))
        assert total == 5

        # ryzen usa a própria faixa (5600G → 5ª)
        items, total = ad_service.list_ads(AdFilters(cpu_family="ryzen5", gen_min=5))
        assert total == 1 and items[0].id == 5
        items, total = ad_service.list_ads(AdFilters(cpu_family="ryzen5", gen_min=6))
        assert total == 0


def test_gen_range_for_vendor(app):
    assert ad_service.gen_range_for("intel") == (1, 14)
    assert ad_service.gen_range_for("amd") == (1, 9)
    assert ad_service.gen_range_for("AMD") == (1, 9)
    assert ad_service.gen_range_for("i5") == (1, 14)
    assert ad_service.gen_range_for("ryzen5") == (1, 9)
    assert ad_service.gen_range_for("core2") is None
    assert ad_service.gen_range_for(None) is None


def test_list_ads_by_vendor(app):
    from app.services.ad_service import AdFilters
    from tests.seed import seed_ads

    seed_ads(app)
    with app.app_context():
        items, total = ad_service.list_ads(AdFilters(cpu_family="intel"))
        assert total == 3 and {a.id for a in items} == {1, 2, 4}

        items, total = ad_service.list_ads(AdFilters(cpu_family="amd"))
        assert total == 1 and items[0].id == 5

        # caixa alta normaliza no parser, mas o service aceita .lower() também
        items, total = ad_service.list_ads(AdFilters(cpu_family="Intel"))
        assert total == 3


def test_gen_by_vendor(app):
    from app.services.ad_service import AdFilters
    from tests.seed import seed_ads

    seed_ads(app)
    with app.app_context():
        items, _ = ad_service.list_ads(AdFilters(cpu_family="intel", gen_min=8))
        assert {a.id for a in items} == {1}

        items, _ = ad_service.list_ads(AdFilters(cpu_family="amd", gen_min=5))
        assert {a.id for a in items} == {5}

        items, _ = ad_service.list_ads(AdFilters(cpu_family="amd", gen_min=6))
        assert items == []


def test_chart_and_deals_exclude_user_disabled(app):
    from app.services.ad_service import AdFilters
    from tests.seed import seed_ads

    seed_ads(app)
    with app.app_context():
        ad = db.session.get(Ad, 1)
        ad.user_disabled = True
        db.session.commit()

    assert 1 not in {p["id"] for p in ad_service.chart_data(AdFilters())}
    assert 1 not in {d["id"] for d in ad_service.best_deals(AdFilters())}
    benchmarks = {b["generation"]: b["count"] for b in ad_service.price_benchmarks()}
    assert benchmarks.get(8, 0) == 0  # única ad da 8ª geração ficou oculta


def test_stats_exclude_user_disabled(app):
    from tests.seed import seed_ads

    seed_ads(app)
    with app.app_context():
        ad = db.session.get(Ad, 1)  # Dell i5-8500 com specs
        ad.user_disabled = True
        db.session.commit()

    data = ad_service.stats()
    assert data["total"] == 4
    assert data["ocultos"] == 1
    assert data["com_specs"] == 3  # 4 com specs no seed; 1 oculto
    assert data["sem_specs"] == 1  # ad 3 sem specs continua visível
    assert data["por_cpu_family"].get("i5", 0) == 0


def test_pending_and_enrich_skip_user_disabled(app):
    with app.app_context():
        pend, _ = ad_service.upsert_raw(make_ad(
            url="https://sp.olx.com.br/x/pend-1", description="i5 8gb ssd"))
        miss, _ = ad_service.upsert_raw(make_ad(url="https://sp.olx.com.br/x/miss-1"))
        pend.user_disabled = True
        miss.user_disabled = True
        db.session.commit()
        assert ad_service.list_pending_extraction() == []
        assert ad_service.list_missing_description() == []


def test_set_user_disabled(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(make_ad())
        assert ad_service.set_user_disabled(ad.id, True).user_disabled is True
        assert ad_service.set_user_disabled(ad.id, False).user_disabled is False
        assert ad_service.set_user_disabled(99999, True) is None

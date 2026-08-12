from app.services import ad_service
from app.services.ad_service import AdFilters
from tests.seed import seed_ads


def test_price_benchmarks(app):
    seed_ads(app)
    with app.app_context():
        benchmarks = ad_service.price_benchmarks()
        # seed: i5·8ª (R$800), i3·6ª (R$500), ryzen5·5ª (R$900); i7-6700 sem preço → excluído
        gen8 = next(b for b in benchmarks if b["family"] == "i5" and b["generation"] == 8)
        assert gen8["count"] == 1
        assert gen8["p50"] == 80000
        gen6 = next(b for b in benchmarks if b["family"] == "i3" and b["generation"] == 6)
        assert gen6["count"] == 1  # i7-6700 sem preço é excluído
        assert all("family" in b for b in benchmarks)


def test_best_deals_ranking(app):
    seed_ads(app)
    with app.app_context():
        deals = ad_service.best_deals(AdFilters())
        discounts = [d["discount"] for d in deals]
        assert discounts == sorted(discounts)
        # todos com 1 ad por (família, geração) → mediana = o próprio preço → 0%
        assert all(d["discount"] == 0 for d in deals)
        assert all("family" in d for d in deals)


def test_best_deals_excludes_no_price(app):
    seed_ads(app)
    with app.app_context():
        deals = ad_service.best_deals(AdFilters())
        assert all(d["price_cents"] is not None for d in deals)
        assert 4 not in {d["id"] for d in deals}  # ad 4 sem preço


def test_best_deals_filters(app):
    seed_ads(app)
    with app.app_context():
        deals = ad_service.best_deals(AdFilters(cpu_family="i5", gen_min=8))
        assert {d["id"] for d in deals} == {1}

        # sem família, gen_min é ignorado → todos os deals com preço e geração
        deals = ad_service.best_deals(AdFilters(gen_min=8))
        assert {d["id"] for d in deals} == {1, 2, 5}


def test_is_parts():
    assert ad_service.is_parts("Riser Card Dell Optiplex CN-0G5459")
    assert ad_service.is_parts("Fonte Dell Optiplex GX240")
    assert not ad_service.is_parts("Computador Dell Optiplex 3040 i5")
    assert not ad_service.is_parts(None)
    # acessórios incluídos (teclado/mouse) NÃO são peça
    assert not ad_service.is_parts("Computador Dell OptiPlex 3050 i5 | 8GB RAM | SSD 256GB + Teclado E Mouse Sem Fio Dell")


def test_offers_page(app):
    seed_ads(app)
    resp = app.test_client().get("/offers")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Benchmark de mercado" in html
    assert "Melhores ofertas" in html

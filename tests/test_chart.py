from app.services import ad_service
from app.services.ad_service import AdFilters
from tests.seed import seed_ads


def test_chart_data_points(app):
    seed_ads(app)
    with app.app_context():
        points = ad_service.chart_data(AdFilters())
        # seed: specs com preço → i5-8500 (gen8), i3-6100 (gen6), ryzen5 5600G (gen5);
        # i7-6700 sem preço → excluído
        ids = {p["id"] for p in points}
        assert ids == {1, 2, 5}
        p1 = next(p for p in points if p["id"] == 1)
        assert p1["price_cents"] == 80000
        assert p1["generation"] == 8
        assert p1["brand"] == "Dell"
        assert p1["model"] == "OptiPlex 7050"


def test_chart_data_filters(app):
    seed_ads(app)
    with app.app_context():
        points = ad_service.chart_data(AdFilters(cpu_family="i5", gen_min=8))
        assert len(points) == 1 and points[0]["id"] == 1

        # sem família, gen_min é ignorado (escalas Intel/Ryzen incompatíveis)
        assert len(ad_service.chart_data(AdFilters(gen_min=8))) == 3

        points = ad_service.chart_data(AdFilters(brand="lenovo"))
        assert len(points) == 1 and points[0]["id"] == 2


def test_chart_page(app):
    seed_ads(app)
    resp = app.test_client().get("/chart")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Preço × Geração" in html
    # sem família → aviso no lugar do canvas (não carrega chart.js)
    assert "não dá para compará-las no mesmo eixo" in html
    assert "chart.js" not in html

    resp = app.test_client().get("/chart?cpu_family=i5")
    html = resp.get_data(as_text=True)
    assert "chart.js" in html
    assert '"generation": 8' in html      # dados embutidos via tojson


def test_chart_page_respects_own_filters(app):
    seed_ads(app)
    resp = app.test_client().get("/chart?cpu_family=i5&gen_min=8")
    html = resp.get_data(as_text=True)
    assert "anúncio(s) com preço e geração" in html
    assert '"id": 1' in html
    assert '"id": 2' not in html


def test_chart_page_vendor(app):
    seed_ads(app)
    resp = app.test_client().get("/chart?cpu_family=intel")
    html = resp.get_data(as_text=True)
    assert "chart.js" in html
    assert '"generation": 8' in html      # i5-8500
    assert '"id": 5' not in html          # ryzen5 (AMD) fora do Intel

    resp = app.test_client().get("/chart?cpu_family=amd")
    html = resp.get_data(as_text=True)
    assert "chart.js" in html
    assert '"generation": 5' in html      # ryzen5 5600G
    assert '"id": 1' not in html

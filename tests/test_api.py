from tests.seed import seed_ads


def test_list_ads_all(app):
    seed_ads(app)
    resp = app.test_client().get("/api/ads")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 5
    assert data["page"] == 1


def test_list_ads_filters(app):
    seed_ads(app)

    # filtro ram_min
    data = app.test_client().get("/api/ads?ram_min=16").get_json()
    assert data["total"] == 1 and data["items"][0]["specs"]["ram_gb"] == 16

    # filtro brand
    data = app.test_client().get("/api/ads?brand=lenovo").get_json()
    assert data["total"] == 1
    assert data["items"][0]["specs"]["brand"] == "Lenovo"

    # filtro cpu_family
    data = app.test_client().get("/api/ads?cpu_family=i7").get_json()
    assert data["total"] == 1

    # filtro gen_min por família (i5-8500→8, ryzen5 5600G→5)
    data = app.test_client().get("/api/ads?cpu_family=i5&gen_min=8").get_json()
    assert data["total"] == 1
    assert data["items"][0]["specs"]["cpu_generation"] == 8

    data = app.test_client().get("/api/ads?cpu_family=ryzen5&gen_min=5").get_json()
    assert data["total"] == 1
    assert data["items"][0]["specs"]["cpu_generation"] == 5

    # sem família, gen_min/gen_max são ignorados (não restringem por geração)
    data = app.test_client().get("/api/ads?gen_min=8").get_json()
    assert data["total"] == 5

    data = app.test_client().get("/api/ads?cpu_family=ryzen5&gen_max=4").get_json()
    assert data["total"] == 0

    # filtro price_max (centavos)
    data = app.test_client().get("/api/ads?price_max=60000").get_json()
    ids = {a["id"] for a in data["items"]}
    assert data["total"] == 2 and ids == {2, 3}

    # filtro has_specs
    data = app.test_client().get("/api/ads?has_specs=false").get_json()
    assert data["total"] == 1 and data["items"][0]["id"] == 3

    # filtro q
    data = app.test_client().get("/api/ads?q=thinkcentre").get_json()
    assert data["total"] == 1


def test_cpu_generation_requires_family(app):
    seed_ads(app)

    # gen_min/gen_max sem família não restringem (escalas Intel/Ryzen incompatíveis)
    assert app.test_client().get("/api/ads?gen_min=8").get_json()["total"] == 5
    assert app.test_client().get("/api/ads?gen_max=6").get_json()["total"] == 5

    # com família, o intervalo vale só para a geração daquela família
    assert app.test_client().get("/api/ads?cpu_family=i5&gen_min=8").get_json()["total"] == 1
    assert app.test_client().get("/api/ads?cpu_family=ryzen5&gen_min=5").get_json()["total"] == 1
    assert app.test_client().get("/api/ads?cpu_family=ryzen5&gen_min=6").get_json()["total"] == 0


def test_cpu_family_vendor(app):
    seed_ads(app)

    # fabricante via cpu_family=intel|amd
    intel = app.test_client().get("/api/ads?cpu_family=intel").get_json()
    assert intel["total"] == 3
    assert {a["specs"]["cpu_family"] for a in intel["items"]} <= {"i3", "i5", "i7"}

    amd = app.test_client().get("/api/ads?cpu_family=amd").get_json()
    assert amd["total"] == 1
    assert amd["items"][0]["specs"]["cpu_family"] == "ryzen5"

    # caixa alta normaliza no parser
    assert app.test_client().get("/api/ads?cpu_family=Intel").get_json()["total"] == 3

    # geração dentro do fabricante (domínio de escala)
    assert app.test_client().get("/api/ads?cpu_family=intel&gen_min=8").get_json()["total"] == 1
    assert app.test_client().get("/api/ads?cpu_family=amd&gen_min=5").get_json()["total"] == 1
    assert app.test_client().get("/api/ads?cpu_family=amd&gen_min=6").get_json()["total"] == 0


def test_list_ads_sort(app):
    seed_ads(app)

    data = app.test_client().get("/api/ads?sort=price_asc").get_json()
    prices = [a["price_cents"] for a in data["items"]]
    assert prices[0] == 30000

    data = app.test_client().get("/api/ads?sort=price_desc").get_json()
    assert data["items"][0]["price_cents"] == 90000

    data = app.test_client().get("/api/ads?sort=confidence").get_json()
    confs = [a["specs"]["confidence"] for a in data["items"] if a["specs"]]
    assert confs == sorted(confs, reverse=True)


def test_list_ads_pagination(app):
    seed_ads(app)
    data = app.test_client().get("/api/ads?per_page=2&page=2").get_json()
    assert data["total"] == 5
    assert len(data["items"]) == 2


def test_get_ad_detail(app):
    seed_ads(app)
    resp = app.test_client().get("/api/ads/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["description"] is None  # anúncio 1 não tem descrição no seed
    assert data["specs"]["brand"] == "Dell"
    assert data["specs"]["cpu_family"] == "i5"
    assert data["specs"]["cpu_model"] == 8500
    assert data["images"] == ["https://img/a.jpg", "https://img/b.jpg"]
    assert data["price_cents"] == 80000


def test_get_ad_404(app):
    seed_ads(app)
    assert app.test_client().get("/api/ads/999").status_code == 404


def test_set_ad_disabled(app):
    from app.extensions import db
    from app.models import Ad

    with app.app_context():
        a = Ad(olx_id="d1", title="Sucata", url="https://sp.olx.com.br/x/d-1", price_cents=10000)
        db.session.add(a)
        db.session.commit()
        ad_id = a.id

    client = app.test_client()
    r = client.post(f"/api/ads/{ad_id}/disabled", json={"disabled": True})
    assert r.status_code == 200
    assert r.get_json()["ad"]["user_disabled"] is True
    r2 = client.post(f"/api/ads/{ad_id}/disabled", json={"disabled": False})
    assert r2.status_code == 200
    assert r2.get_json()["ad"]["user_disabled"] is False

    assert client.post(f"/api/ads/{ad_id}/disabled", json={}).status_code == 400
    assert client.post(f"/api/ads/{ad_id}/disabled", json={"disabled": "sim"}).status_code == 400
    assert client.post("/api/ads/99999/disabled", json={"disabled": True}).status_code == 404


def test_stats(app):
    seed_ads(app)
    data = app.test_client().get("/api/stats").get_json()
    assert data["total"] == 5
    assert data["com_specs"] == 4
    assert data["sem_specs"] == 1
    assert data["preco_cents"]["min"] == 30000
    assert data["preco_cents"]["max"] == 90000
    assert data["por_cpu_family"] == {"i3": 1, "i5": 1, "i7": 1, "ryzen5": 1}


def test_start_run_validation(app, monkeypatch):
    client = app.test_client()
    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs", json={"terms": ["dell"]}).status_code == 400
    assert client.post("/api/runs", json={"terms": [], "steps": ["scrape"]}).status_code == 400
    # steps inválidos são filtrados → fica sem nenhum step válido
    assert client.post(
        "/api/runs", json={"terms": ["dell"], "steps": ["nao-existe"]}
    ).status_code == 400


def test_start_run_creates_job(app, monkeypatch):
    from app.services import runner

    def fake_scrape(*a, **k):
        return {"listados": 0, "novos": 0, "total": 0}

    monkeypatch.setattr(runner, "run_scrape", fake_scrape)
    client = app.test_client()
    resp = client.post("/api/runs", json={"terms": ["dell optiplex"], "steps": ["scrape"]})
    assert resp.status_code == 202
    run_id = resp.get_json()["id"]
    assert run_id == 1

    data = client.get(f"/api/runs/{run_id}").get_json()
    assert data["id"] == run_id
    assert data["status"] in ("queued", "running", "done")
    assert data["steps"] == ["scrape"]


def test_save_run_terms(app, tmp_path, monkeypatch):
    from app.services.runner import load_terms

    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    resp = app.test_client().put(
        "/api/runs/terms",
        json={"terms": ["dell optiplex sff", "lenovo thinkcentre"], "region": "estado-sp"},
    )
    assert resp.status_code == 200
    data = load_terms(str(tmp_path))
    assert data["terms"] == ["dell optiplex sff", "lenovo thinkcentre"]
    assert data["region"] == "estado-sp"


def test_save_run_terms_cleans_empty(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "instance_path", str(tmp_path))
    resp = app.test_client().put(
        "/api/runs/terms", json={"terms": ["  dell  ", "", "  "], "region": "estado-sp"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["terms"] == ["dell"]


def test_run_current_and_404(app, monkeypatch):
    import threading
    import time

    from app.services import runner

    block = threading.Event()

    def fake_scrape(*a, **k):
        block.wait(timeout=5)
        return {"listados": 0, "novos": 0, "total": 0}

    monkeypatch.setattr(runner, "run_scrape", fake_scrape)
    client = app.test_client()
    assert client.get("/api/runs/current").get_json() is None

    client.post("/api/runs", json={"terms": ["dell"], "steps": ["scrape"]})
    assert client.get("/api/runs/current").get_json() is not None

    assert client.get("/api/runs/999").status_code == 404
    block.set()


def test_runs_history_endpoint(app):
    from app.extensions import db
    from app.models import RunHistory

    with app.app_context():
        for i in range(5):
            db.session.add(
                RunHistory(
                    source="manual" if i % 2 else "autorun",
                    steps=["scrape"],
                    status="done",
                    result={"scrape": {"novos": i}},
                )
            )
        db.session.commit()

    data = app.test_client().get("/api/runs/history?limit=3").get_json()
    assert len(data["entries"]) == 3
    assert data["entries"][0]["status"] == "done"
    assert data["entries"][0]["result"]["scrape"]["novos"] == 4  # mais recente primeiro
    assert data["entries"][0]["source"] == "autorun"


def test_ads_include_inactive(app):
    from app.extensions import db
    from app.models import Ad

    with app.app_context():
        a = Ad(olx_id="inc1", title="Removido", url="https://sp.olx.com.br/x/inc-1",
               is_active=False, description=None)
        db.session.add(a)
        db.session.commit()
        ad_id = a.id

    client = app.test_client()
    # default: removidos ocultos
    default = client.get("/api/ads?q=Removido").get_json()
    assert default["total"] == 0

    # include_inactive os inclui e ad_to_dict expõe is_active/removed_at
    inc = client.get("/api/ads?q=Removido&include_inactive=1").get_json()
    assert inc["total"] == 1
    item = inc["items"][0]
    assert item["is_active"] is False
    assert item["removed_at"] is None

    # detalhe de removido continua acessível (badge)
    assert client.get(f"/api/ads/{ad_id}").status_code == 200


def test_stats_has_removidos(app):
    from app.extensions import db
    from app.models import Ad

    with app.app_context():
        db.session.add(Ad(olx_id="st1", title="Removido", url="https://sp.olx.com.br/x/st-1", is_active=False))
        db.session.commit()

    data = app.test_client().get("/api/stats").get_json()
    assert data["removidos"] == 1

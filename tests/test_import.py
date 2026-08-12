import json
from pathlib import Path

import httpx
import pytest

from app.models import Ad
from app.scrapers.base import RawAd
from app.scrapers.olx import olx_id_from_url
from app.services import ad_service
from app.services import runner
from app.services.runner import import_single_ad

FIXTURES = Path(__file__).parent / "fixtures"

AD_GOOD = (FIXTURES / "ad_good.html").read_text(encoding="utf-8")
AD_NOT_FOUND = (FIXTURES / "ad_not_found.html").read_text(encoding="utf-8")

URL = (
    "https://sp.olx.com.br/sao-paulo-e-regiao/informatica/computadores-e-desktops/"
    "dell-sff-optiplex-7010-i5-13500-13th-gen-1523803879"
)


def _install_mock_client(monkeypatch, handler):
    real_client = runner.OlxClient

    def factory(user_agent, timeout=30.0, delay=1.0, impersonate="chrome"):
        client = real_client(
            user_agent, timeout=timeout, delay=delay,
            transport=httpx.MockTransport(handler), impersonate=impersonate,
        )
        client._backoff = lambda attempt: None  # acelera os retries nos testes
        return client

    monkeypatch.setattr(runner, "OlxClient", factory)


def _good_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=AD_GOOD, request=request)


def test_olx_id_from_url_tolerates_fragment_and_query():
    assert olx_id_from_url("https://sp.olx.com.br/x/dell-12345#photos") == "12345"
    assert olx_id_from_url("https://sp.olx.com.br/x/dell-12345?origem=ml") == "12345"
    assert olx_id_from_url("https://sp.olx.com.br/x/dell-12345?q=1#fotos") == "12345"
    assert olx_id_from_url("https://sp.olx.com.br/x/dell-sem-id") is None


def test_invalid_url_raises(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    with pytest.raises(ValueError):
        import_single_ad(app, "")
    with pytest.raises(ValueError):
        import_single_ad(app, "https://mercadolivre.com.br/item/123")
    with pytest.raises(ValueError):
        import_single_ad(app, "https://notolx.com.br/item/123")
    with pytest.raises(ValueError):
        import_single_ad(app, "https://sp.olx.com.br/estado-sp?q=dell")


def test_import_normalizes_tracking_url(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    url_with_tracking = URL + "?lis=listing_19010#fotos"

    result = import_single_ad(app, url_with_tracking, process=False)
    assert result["status"] == "ok"
    assert result["created"] is True

    with app.app_context():
        assert Ad.query.count() == 1
        ad = Ad.query.first()
        assert ad.url == URL  # salvo com a URL canônica, sem query/fragmento

        # re-cadastro da URL limpa deduplica com a com tracking (mesma row)
        again = import_single_ad(app, url_with_tracking, process=False)
        assert again["created"] is False
        assert Ad.query.count() == 1


def test_import_creates_ad(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    result = import_single_ad(app, URL, process=False)
    assert result["status"] == "ok"
    assert result["created"] is True
    assert result["processed"] is False

    with app.app_context():
        ad = Ad.query.filter_by(url=URL).first()
        assert ad is not None
        assert "Optiplex" in ad.title
        assert ad.price_cents == 285000
        assert "i5-13500" in ad.description
        assert [i.url for i in ad.images] == ["https://img.olx.com.br/images/32/322664079265501.jpg"]


def test_import_duplicate_updates(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    with app.app_context():
        ad_service.upsert_raw(RawAd(
            olx_id="1523803879", title="título antigo", url=URL,
            price_cents=300000, description="descrição antiga",
        ))

    result = import_single_ad(app, URL, process=False)
    assert result["status"] == "ok"
    assert result["created"] is False

    with app.app_context():
        ad = Ad.query.filter_by(url=URL).first()
        assert ad.price_cents == 285000
        assert "i5-13500" in ad.description
        assert ad.title != "título antigo"


def test_import_reactivates_removed(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    with app.app_context():
        ad, _ = ad_service.upsert_raw(RawAd(olx_id="1523803879", title="x", url=URL))
        ad.is_active = False
        from app.extensions import db

        db.session.commit()

    result = import_single_ad(app, URL, process=False)
    assert result["created"] is False
    with app.app_context():
        ad = Ad.query.filter_by(url=URL).first()
        assert ad.is_active is True
        assert ad.removed_at is None


def test_import_process_extracts_specs(app, monkeypatch):
    spec_json = json.dumps({
        "brand": "Dell", "model": "OptiPlex 7010", "form_factor": "sff",
        "cpu": "i5-13500", "ram_gb": 8, "storage_gb": 256,
        "storage_type": "ssd", "gpu": None, "confidence": 0.9,
    })

    def llm_handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "x1", "object": "response", "status": "completed",
            "model": "deepseek-v4-flash",
            "output": [{
                "type": "message", "id": "m1", "role": "assistant",
                "content": [{"type": "output_text", "text": spec_json}],
            }],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}},
        }
        return httpx.Response(200, json=payload, request=request)

    _install_mock_client(monkeypatch, _good_handler)
    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(llm_handler))
    with app.app_context():
        app.config["DEEPSEEK_KEY"] = "test-key"

    result = import_single_ad(app, URL, process=True)
    assert result["processed"] is True
    with app.app_context():
        ad = Ad.query.filter_by(url=URL).first()
        assert ad.spec is not None
        assert ad.spec.brand == "Dell"
        assert ad.spec.ram_gb == 8


def test_import_process_without_key(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    app.config["DEEPSEEK_KEY"] = ""
    result = import_single_ad(app, URL, process=True)
    assert result["processed"] is False
    with app.app_context():
        ad = Ad.query.filter_by(url=URL).first()
        assert ad.spec is None


def test_import_removed(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(404, text="", request=r))
    result = import_single_ad(app, URL)
    assert result["status"] == "removed"
    with app.app_context():
        assert Ad.query.count() == 0


def test_import_410_removed(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(410, text="", request=r))
    assert import_single_ad(app, URL)["status"] == "removed"


def test_import_not_an_ad(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(200, text=AD_NOT_FOUND, request=r))
    result = import_single_ad(app, URL)
    assert result["status"] == "not_an_ad"
    with app.app_context():
        assert Ad.query.count() == 0


def test_import_blocked(app, monkeypatch):
    from app.scrapers.client import ScrapeBlockedError

    _install_mock_client(monkeypatch, lambda r: httpx.Response(403, text="", request=r))
    with pytest.raises(ScrapeBlockedError):
        import_single_ad(app, URL)


def test_import_network_error(app, monkeypatch):
    def boom(request):
        raise httpx.ConnectError("boom", request=request)

    _install_mock_client(monkeypatch, boom)
    with pytest.raises(httpx.HTTPError):
        import_single_ad(app, URL)


# --- API ---


def test_api_import_ok(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    resp = app.test_client().post("/api/ads/import", json={"url": URL, "process": False})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["created"] is True
    assert data["processed"] is False
    assert data["ad"]["price_cents"] == 285000
    assert data["ad"]["id"] is not None


def test_api_import_duplicate(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    client = app.test_client()
    assert client.post("/api/ads/import", json={"url": URL}).status_code == 201
    resp = client.post("/api/ads/import", json={"url": URL})
    assert resp.status_code == 200
    assert resp.get_json()["created"] is False


def test_api_import_validation(app, monkeypatch):
    _install_mock_client(monkeypatch, _good_handler)
    client = app.test_client()
    assert client.post("/api/ads/import", json={}).status_code == 400
    assert client.post("/api/ads/import", json={"url": ""}).status_code == 400
    assert client.post("/api/ads/import", json={"url": "https://mercadolivre.com.br/x"}).status_code == 400


def test_api_import_removed(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(404, text="", request=r))
    resp = app.test_client().post("/api/ads/import", json={"url": URL})
    assert resp.status_code == 410


def test_api_import_not_an_ad(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(200, text=AD_NOT_FOUND, request=r))
    resp = app.test_client().post("/api/ads/import", json={"url": URL})
    assert resp.status_code == 422


def test_api_import_blocked(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(403, text="", request=r))
    resp = app.test_client().post("/api/ads/import", json={"url": URL})
    assert resp.status_code == 503


def _make_llm(handler):
    from app.extractors.llm import DeepSeekClient

    return DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )

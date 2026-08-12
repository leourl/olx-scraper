from pathlib import Path

import httpx
import pytest

from app.models import Ad
from app.scrapers.base import RawAd
from app.scrapers.client import ScrapeBlockedError
from app.services import ad_service
from app.services import runner

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_HTML = (FIXTURES / "search.html").read_text(encoding="utf-8")
AD_GOOD = (FIXTURES / "ad_good.html").read_text(encoding="utf-8")
AD_NOT_FOUND = (FIXTURES / "ad_not_found.html").read_text(encoding="utf-8")


def _install_mock_client(monkeypatch, handler):
    real_client = runner.OlxClient

    def factory(user_agent, timeout=30.0, delay=1.0, impersonate="chrome"):
        return real_client(
            user_agent, timeout=timeout, delay=delay,
            transport=httpx.MockTransport(handler), impersonate=impersonate,
        )

    monkeypatch.setattr(runner, "OlxClient", factory)


def _seed_ads(app):
    with app.app_context():
        for i, (olx_id, url) in enumerate([
            ("100", "https://sp.olx.com.br/x/bom-100"),
            ("101", "https://sp.olx.com.br/x/removido-101"),
            ("102", "https://sp.olx.com.br/x/semld-102"),
            ("103", "https://sp.olx.com.br/x/falha-103"),
        ]):
            ad_service.upsert_raw(RawAd(olx_id=olx_id, title=f"Anúncio {i}", url=url))


def _detail_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "removido" in url:
        return httpx.Response(404, text="", request=request)
    if "semld" in url:
        return httpx.Response(200, text=AD_NOT_FOUND, request=request)
    if "falha" in url:
        raise httpx.ConnectError("boom", request=request)
    if "gum" in url:  # 410
        return httpx.Response(410, text="", request=request)
    return httpx.Response(200, text=AD_GOOD, request=request)


def test_client_404_410_do_not_raise():
    for status in (404, 410):
        transport = httpx.MockTransport(lambda r, s=status: httpx.Response(s, text="", request=r))
        client = runner.OlxClient("test-agent", delay=0.0, transport=transport)
        resp = client.get("https://x")
        assert resp.status_code == status


def test_client_5xx_raises_after_retries():
    transport = httpx.MockTransport(lambda r: httpx.Response(500, text="", request=r))
    client = runner.OlxClient("test-agent", delay=0.0, transport=transport)
    client._backoff = lambda attempt: None  # acelera os retries no teste
    with pytest.raises(httpx.HTTPStatusError):
        client.get("https://x")


def test_client_403_raises_blocked():
    transport = httpx.MockTransport(lambda r: httpx.Response(403, text="", request=r))
    client = runner.OlxClient("test-agent", delay=0.0, transport=transport)
    with pytest.raises(ScrapeBlockedError):
        client.get("https://x")


def test_client_curl_network_error_retries_then_raises():
    from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

    def handler(request):
        raise CurlConnectionError("boom")

    transport = httpx.MockTransport(handler)
    client = runner.OlxClient("test-agent", delay=0.0, transport=transport)
    client._backoff = lambda attempt: None  # acelera os retries no teste
    with pytest.raises(CurlConnectionError):
        client.get("https://x")


def test_run_check_marks_removed_and_active(app, monkeypatch):
    _seed_ads(app)
    _install_mock_client(monkeypatch, _detail_handler)

    stats = runner.run_check(app)
    assert stats["removidos"] == 1  # 101
    assert stats["sem_confirmar"] == 1  # 102 (200 sem JSON-LD)
    assert stats["erros"] == 1  # 103 (falha de rede)
    assert stats["checados"] == 4

    with app.app_context():
        assert Ad.query.filter_by(olx_id="101").first().is_active is False
        assert Ad.query.filter_by(olx_id="101").first().removed_at is not None
        assert Ad.query.filter_by(olx_id="100").first().is_active is True
        assert Ad.query.filter_by(olx_id="102").first().is_active is True


def test_run_check_410_marks_removed(app, monkeypatch):
    with app.app_context():
        ad_service.upsert_raw(RawAd(olx_id="200", title="x", url="https://sp.olx.com.br/x/gum-200"))
    _install_mock_client(monkeypatch, _detail_handler)

    stats = runner.run_check(app)
    assert stats["removidos"] == 1
    with app.app_context():
        assert Ad.query.filter_by(olx_id="200").first().is_active is False


def test_run_check_403_raises(app, monkeypatch):
    with app.app_context():
        ad_service.upsert_raw(RawAd(olx_id="300", title="x", url="https://sp.olx.com.br/x/qualquer-300"))

    def blocked(request):
        return httpx.Response(403, text="", request=request)

    _install_mock_client(monkeypatch, blocked)
    with pytest.raises(ScrapeBlockedError):
        runner.run_check(app)


def test_run_check_limit(app, monkeypatch):
    with app.app_context():
        for i in range(5):
            ad_service.upsert_raw(RawAd(olx_id=str(400 + i), title=f"x{i}", url=f"https://sp.olx.com.br/x/ok-{400+i}"))
    _install_mock_client(monkeypatch, _detail_handler)

    stats = runner.run_check(app, limit=2)
    assert stats["checados"] == 2


def test_run_check_skips_user_disabled(app, monkeypatch):
    _install_mock_client(monkeypatch, lambda r: httpx.Response(404, text="", request=r))
    with app.app_context():
        ad, _ = ad_service.upsert_raw(RawAd(olx_id="oc1", title="x", url="https://sp.olx.com.br/x/oc-1"))
        ad.user_disabled = True
        from app.extensions import db

        db.session.commit()

    stats = runner.run_check(app)
    assert stats["checados"] == 0
    assert stats["removidos"] == 0
    with app.app_context():
        assert Ad.query.filter_by(olx_id="oc1").first().is_active is True  # nunca foi checado


def test_upsert_reactivates_removed(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(
            RawAd(olx_id="500", title="x", url="https://sp.olx.com.br/x/reativa-500")
        )
        ad.is_active = False
        ad.removed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        from app.extensions import db

        db.session.commit()

        # reaparece na listagem → re-ativa e limpa removed_at
        updated, created = ad_service.upsert_raw(
            RawAd(olx_id="500", title="x", url="https://sp.olx.com.br/x/reativa-500")
        )
        assert created is False
        assert updated.is_active is True
        assert updated.removed_at is None


def test_pending_and_enrich_skip_inactive(app):
    with app.app_context():
        ad, _ = ad_service.upsert_raw(RawAd(
            olx_id="600", title="x", url="https://sp.olx.com.br/x/pend-600",
            description="descrição aqui",
        ))
        ad.is_active = False
        from app.extensions import db

        db.session.commit()

    assert ad_service.list_pending_extraction() == []
    assert ad_service.list_missing_description() == []


def test_stats_removidos(app):
    from app.extensions import db

    with app.app_context():
        ad, _ = ad_service.upsert_raw(RawAd(olx_id="700", title="x", url="https://sp.olx.com.br/x/rm-700"))
        ad.is_active = False
        db.session.commit()

    data = ad_service.stats()
    assert data["removidos"] == 1
    assert data["total"] == 0

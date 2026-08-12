from pathlib import Path

import httpx

from app.scrapers.base import RawAd
from app.scrapers.client import OlxClient
from app.scrapers.olx import OlxScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _scraper(fixture: str) -> OlxScraper:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    client = OlxClient("test-agent", delay=0.0, transport=httpx.MockTransport(handler))
    return OlxScraper(client)


def test_fetch_detail_fills_description_and_images():
    scraper = _scraper("ad_bad.html")
    ad = RawAd(olx_id="1519461609", title="Dell Optiplex 7020 SFF", url="https://sp.olx.com.br/x/y-1519461609")
    scraper.fetch_detail(ad)
    assert "4GB de memória DDR3" in ad.description
    assert len(ad.images) == 5
    assert ad.price_cents == 99900


def test_fetch_detail_keeps_no_images_when_absent():
    # ad_good.html só tem 1 imagem no JSON-LD
    scraper = _scraper("ad_good.html")
    ad = RawAd(olx_id="1523803879", title="Dell", url="https://sp.olx.com.br/x/y-1523803879")
    scraper.fetch_detail(ad)
    assert len(ad.images) == 1
    assert "256GB SSD NVMe" in ad.description

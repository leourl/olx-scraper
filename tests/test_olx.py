from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.scrapers.dates import parse_olx_date
from app.scrapers.olx import (
    OlxScraper,
    olx_id_from_url,
    parse_price_cents,
    state_from_region,
)

FIXTURES = Path(__file__).parent / "fixtures"


def scraper() -> OlxScraper:
    return OlxScraper(None)  # type: ignore[arg-type]


# --- helpers ---


def test_parse_price_cents():
    assert parse_price_cents("R$ 2.850") == 285000
    assert parse_price_cents("R$ 950,00") == 95000
    assert parse_price_cents("R$1.499,90") == 149990
    assert parse_price_cents("sob consulta") is None
    assert parse_price_cents(None) is None


def test_olx_id_from_url():
    url = ("https://sp.olx.com.br/sao-paulo-e-regiao/informatica/"
           "computadores-e-desktops/dell-sff-optiplex-7010-i5-13500-13th-gen-1523803879")
    assert olx_id_from_url(url) == "1523803879"
    assert olx_id_from_url("https://x.com.br/foo") is None


def test_state_from_region():
    assert state_from_region("estado-sp") == "SP"
    assert state_from_region("sp") == "SP"
    assert state_from_region("brasil") is None


def test_parse_olx_date():
    now = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    d = parse_olx_date("Ontem, 16:33", now)
    assert d is not None and d.year == 2026 and d.month == 8 and d.day == 4
    assert d.hour == 19 and d.minute == 33  # 16:33 -03:00 = 19:33 UTC

    d = parse_olx_date("31 de jul, 15:25", now)
    assert d.day == 31 and d.month == 7 and d.hour == 18

    d = parse_olx_date("Hoje, 08:00", now)
    assert d.day == 5 and d.hour == 11

    d = parse_olx_date("1 de janeiro de 2025", now)
    assert d.year == 2025 and d.month == 1 and d.day == 1

    assert parse_olx_date(None, now) is None
    assert parse_olx_date("texto aleatório", now) is None


# --- parse de listagem ---


def test_parse_listing():
    html = (FIXTURES / "search.html").read_text(encoding="utf-8")
    ads = scraper()._parse_listing(html, "estado-sp")
    assert len(ads) == 32

    first = ads[0]
    assert first.olx_id == "1523803879"
    assert "Optiplex" in first.title
    assert first.price_cents == 285000
    assert first.city == "São Paulo"
    assert first.state == "SP"
    assert first.published_at is not None
    assert first.url.startswith("https://sp.olx.com.br/")

    assert {a.olx_id for a in ads} == {a.olx_id for a in ads}  # únicos


# --- parse de detalhe ---


def test_parse_detail_good():
    html = (FIXTURES / "ad_good.html").read_text(encoding="utf-8")
    data = scraper()._parse_detail(html)
    assert data["price_cents"] == 285000
    assert data["images"] == ["https://img.olx.com.br/images/32/322664079265501.jpg"]
    assert "i5-13500" in data["description"]
    assert "256GB SSD NVMe" in data["description"]
    assert "Optiplex" in data["name"]


def test_parse_detail_bad():
    html = (FIXTURES / "ad_bad.html").read_text(encoding="utf-8")
    data = scraper()._parse_detail(html)
    assert data["price_cents"] == 99900
    assert len(data["images"]) == 5
    assert data["images"][0].startswith("https://img.olx.com.br/images/40/")
    assert "4GB de memória DDR3" in data["description"]

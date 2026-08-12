import json
import logging
import re

from bs4 import BeautifulSoup

from app.scrapers.base import RawAd
from app.scrapers.client import OlxClient
from app.scrapers.dates import parse_olx_date

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.olx.com.br/{region}?q={query}&sf=1&o={page}"

_PRICE_RE = re.compile(r"([\d.,]+)")
_ID_RE = re.compile(r"-(\d{5,})$")


def olx_id_from_url(url: str) -> str | None:
    path = url.split("#")[0].split("?")[0].rstrip("/")
    m = _ID_RE.search(path)
    return m.group(1) if m else None


def parse_price_cents(text: str | None) -> int | None:
    """'R$ 2.850' -> 285000; 'R$ 950,00' -> 95000; 'sob consulta' -> None."""
    if not text:
        return None
    m = _PRICE_RE.search(text.replace(" ", ""))
    if not m:
        return None
    raw = m.group(1)
    if "," in raw:
        int_part, _, dec = raw.partition(",")
        dec = dec.ljust(2, "0")[:2]
        return int(int_part.replace(".", "")) * 100 + int(dec)
    return int(raw.replace(".", "")) * 100


def state_from_region(region: str) -> str | None:
    m = re.match(r"^(?:[^-]+-)?([a-z]{2})$", region)
    return m.group(1).upper() if m else None


class OlxScraper:
    def __init__(self, client: OlxClient):
        self.client = client

    def search(self, query: str, region: str, max_pages: int) -> list[RawAd]:
        ads: dict[str, RawAd] = {}
        for page in range(1, max_pages + 1):
            url = SEARCH_URL.format(
                region=region, query=query.replace(" ", "+"), page=page
            )
            log.info("listagem página %s: %s", page, url)
            resp = self.client.get(url)
            found = self._parse_listing(resp.text, region)
            if not found:
                break
            for ad in found:
                ads.setdefault(ad.olx_id, ad)
        return list(ads.values())

    def fetch_detail(self, ad: RawAd) -> RawAd:
        resp = self.client.get(ad.url)
        data = self._parse_detail(resp.text)
        if data.get("description"):
            ad.description = data["description"]
        if data.get("images"):
            ad.images = data["images"]
        if data.get("price_cents") is not None:
            ad.price_cents = data["price_cents"]
        return ad

    def _parse_listing(self, html: str, region: str) -> list[RawAd]:
        soup = BeautifulSoup(html, "html.parser")
        ads: list[RawAd] = []
        for link in soup.select("a[data-testid='adcard-link']"):
            url = link.get("href")
            olx_id = olx_id_from_url(url)
            if not olx_id:
                continue
            card = link.find_parent("div", class_="olx-adcard__content")
            price_el = card.select_one(".olx-adcard__price") if card else None
            loc_el = card.select_one(".olx-adcard__location") if card else None
            date_el = card.select_one(".olx-adcard__date") if card else None
            ads.append(
                RawAd(
                    olx_id=olx_id,
                    title=(link.get("title") or "").strip(),
                    url=url,
                    price_cents=parse_price_cents(price_el.get_text(strip=True) if price_el else None),
                    city=self._parse_city(loc_el.get_text(" ", strip=True) if loc_el else None),
                    state=state_from_region(region),
                    published_at=parse_olx_date(date_el.get_text(" ", strip=True) if date_el else None),
                )
            )
        return ads

    def _parse_detail(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get("@type") != "Product":
                continue
            offers = data.get("offers") or {}
            images = data.get("image") or []
            urls = [
                img.get("contentUrl")
                for img in images
                if isinstance(img, dict) and img.get("contentUrl")
            ]
            desc = data.get("description")
            return {
                "name": data.get("name"),
                "description": desc.replace("<br>", "\n").strip() if desc else None,
                "images": urls,
                "price_cents": parse_price_cents(str(offers.get("price")) if offers.get("price") is not None else None),
            }
        return {}

    @staticmethod
    def _parse_city(location: str) -> str | None:
        if not location:
            return None
        return location.split(",")[0].strip()

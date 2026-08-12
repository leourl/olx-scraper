"""Parse de datas relativas da OLX ("Ontem, 16:33", "31 de jul, 15:25")."""
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

BR_TZ = ZoneInfo("America/Sao_Paulo")

_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MONTHS_ABBR = {m[:3]: n for m, n in _MONTHS.items()}
_MONTHS.update(_MONTHS_ABBR)

_DAY_TIME_RE = re.compile(
    r"^(?P<day>\d{1,2})\s+de\s+(?P<month>\w+?)\s+de\s+(?P<year>\d{4})(?:,\s*(?P<time>\d{2}:\d{2}))?$",
    re.IGNORECASE,
)
_DAY_TIME_NO_YEAR_RE = re.compile(
    r"^(?P<day>\d{1,2})\s+de\s+(?P<month>\w+?)(?:,\s*(?P<time>\d{2}:\d{2}))?$",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\d{2}:\d{2}")


def _as_utc(dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo("UTC"))


def parse_olx_date(text: str | None, now: datetime | None = None) -> datetime | None:
    """Converte texto de data da OLX em datetime UTC. None se não entender."""
    if not text:
        return None
    now = now or datetime.now(BR_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BR_TZ)
    text = text.strip()
    lowered = text.lower()

    tm = None
    t = _TIME_RE.search(text)
    if t:
        try:
            hh, mm = map(int, t.group().split(":"))
            tm = time(hh, mm)
        except ValueError:
            tm = None

    if lowered.startswith("hoje"):
        base = now.date()
    elif lowered.startswith("ontem"):
        base = now.date() - timedelta(days=1)
    else:
        m = _DAY_TIME_RE.match(text)
        if m:
            base = datetime(int(m.group("year")), _MONTHS[m.group("month").lower()], int(m.group("day"))).date()
        else:
            m = _DAY_TIME_NO_YEAR_RE.match(text)
            if not m:
                return None
            month = _MONTHS.get(m.group("month").lower())
            if not month:
                return None
            base = datetime(now.year, month, int(m.group("day"))).date()

    dt = datetime.combine(base, tm or time(0, 0), tzinfo=BR_TZ)
    return _as_utc(dt)

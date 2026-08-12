from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawAd:
    """Dados brutos de um anúncio (listagem + detalhe)."""

    olx_id: str
    title: str
    url: str
    price_cents: int | None = None
    city: str | None = None
    state: str | None = None
    published_at: datetime | None = None
    images: list[str] = field(default_factory=list)
    description: str | None = None
    extra: dict = field(default_factory=dict)

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median, quantiles

from sqlalchemy import or_

from app.extensions import db
from app.extractors.schema import AdSpec as AdSpecSchema
from app.models import Ad, AdImage, AdSpec
from app.scrapers.base import RawAd

ALLOWED_SORTS = {"newest", "price_asc", "price_desc", "confidence"}
ALLOWED_FORM_FACTORS = {"mini", "sff", "tower", "notebook", "all-in-one", "desktop"}

CPU_GROUPS: dict[str, list[str]] = {
    "Intel": ["i3", "i5", "i7", "i9", "core2", "pentium", "celeron"],
    "AMD": ["ryzen3", "ryzen5", "ryzen7", "ryzen9", "athlon"],
}

# Faixa plausível de geração por família (mesma regra do generation_from).
# Famílias sem geração (core2/pentium/celeron/athlon) → None.
GEN_RANGE: dict[str, tuple[int, int] | None] = {
    "i3": (1, 14), "i5": (1, 14), "i7": (1, 14), "i9": (1, 14),
    "ryzen3": (1, 9), "ryzen5": (1, 9), "ryzen7": (1, 9), "ryzen9": (1, 9),
    "core2": None, "pentium": None, "celeron": None, "athlon": None,
}

# Valores especiais de cpu_family que significam "todas as famílias do fabricante".
VENDOR_ALIASES: dict[str, str] = {"intel": "Intel", "amd": "AMD"}

# Faixa de geração por fabricante (todas as famílias do grupo usam a mesma escala).
VENDOR_RANGE: dict[str, tuple[int, int]] = {"intel": (1, 14), "amd": (1, 9)}


def gen_range_for(family: str | None) -> tuple[int, int] | None:
    """Faixa de geração aceita pela família **ou fabricante** (None = sem geração).

    Normaliza `family.lower()`: as chaves de `GEN_RANGE`/`VENDOR_RANGE` são
    minúsculas e a UI/API pode enviar "I5"/"INTEL" — sem o lower haveria falha
    silenciosa.
    """
    if not family:
        return None
    key = family.lower()
    if key in VENDOR_RANGE:
        return VENDOR_RANGE[key]
    return GEN_RANGE.get(key)


def cpu_group(family: str) -> str:
    """'Intel' ou 'AMD' para agrupar no select da UI (case-insensitive)."""
    key = family.lower()
    if key in VENDOR_ALIASES:
        return VENDOR_ALIASES[key]
    return next((g for g, fams in CPU_GROUPS.items() if key in fams), "Intel")

PARTS_KEYWORDS = (
    "sucata", "peça", "peças", "riser", "cooler", "fonte", "antenna", "antena",
    "não liga", "nao liga", "placa", "bios", "coreboot", "soquete",
)


def is_parts(title: str | None) -> bool:
    t = (title or "").lower()
    return any(k in t for k in PARTS_KEYWORDS)


@dataclass
class AdFilters:
    q: str | None = None
    brand: str | None = None
    model: str | None = None
    cpu_family: str | None = None
    cpu_model: int | None = None
    gen_min: int | None = None
    gen_max: int | None = None
    ram_min: int | None = None
    storage_min: int | None = None
    price_min: int | None = None
    price_max: int | None = None
    form_factor: str | None = None
    has_specs: bool | None = None
    confidence_min: float | None = None
    include_inactive: bool = False
    sort: str = "newest"
    page: int = 1
    per_page: int = 20
    extra: dict = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _replace_images(ad: Ad, urls: list[str]) -> None:
    ad.images.clear()
    for position, url in enumerate(urls):
        ad.images.append(AdImage(url=url, position=position))


def upsert_raw(ad: RawAd, refresh: bool = False) -> tuple[Ad, bool]:
    """Salva anúncio se não existir por URL. Retorna (Ad, criado_agora).

    Com `refresh=True` (recadastro manual), atualiza preço/título/descrição de
    anúncios existentes quando houver dado novo; sem o flag, mantém o
    comportamento legado (só preenche lacunas).
    """
    existing = Ad.query.filter_by(url=ad.url).first()
    if existing:
        changed = False
        if ad.published_at and existing.published_at is None:
            existing.published_at = ad.published_at
            changed = True
        if ad.images:
            _replace_images(existing, ad.images)
            changed = True
        if refresh:
            if ad.price_cents is not None and ad.price_cents != existing.price_cents:
                existing.price_cents = ad.price_cents
                changed = True
            if ad.title and ad.title != existing.title:
                existing.title = ad.title
                changed = True
            if ad.description and ad.description != existing.description:
                existing.description = ad.description
                changed = True
            if changed:
                existing.scraped_at = _utcnow()
        elif not existing.description and ad.description:
            existing.description = ad.description
            existing.scraped_at = _utcnow()
            changed = True
        if not existing.is_active:
            # apareceu na listagem → re-publicado/ativo; limpa a remoção
            existing.is_active = True
            existing.removed_at = None
            changed = True
        if changed:
            db.session.commit()
        return existing, False

    row = Ad(
        olx_id=ad.olx_id,
        title=ad.title,
        description=ad.description,
        price_cents=ad.price_cents,
        url=ad.url,
        city=ad.city,
        state=ad.state,
        published_at=ad.published_at,
        scraped_at=_utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    _replace_images(row, ad.images)
    db.session.commit()
    return row, True


def list_pending_extraction(limit: int | None = None) -> list[Ad]:
    query = Ad.query.filter(
        Ad.description.isnot(None),
        Ad.description != "",
        Ad.extracted_at.is_(None),
        Ad.is_active.is_(True),
        Ad.user_disabled.is_(False),
    ).order_by(Ad.scraped_at)
    if limit:
        query = query.limit(limit)
    return query.all()


def list_missing_description(limit: int | None = None) -> list[Ad]:
    query = Ad.query.filter(
        (Ad.description.is_(None)) | (Ad.description == ""),
        Ad.is_active.is_(True),
        Ad.user_disabled.is_(False),
    ).order_by(Ad.scraped_at)
    if limit:
        query = query.limit(limit)
    return query.all()


def save_specs(
    ad: Ad,
    spec: AdSpecSchema,
    method: str,
    cpu_family: str | None = None,
    cpu_model: int | None = None,
    cpu_generation: int | None = None,
) -> AdSpec:
    """Grava (ou atualiza) as specs extraídas de um anúncio."""
    row = AdSpec.query.filter_by(ad_id=ad.id).first()
    if row is None:
        row = AdSpec(ad_id=ad.id)
        db.session.add(row)

    row.brand = spec.brand
    row.model = spec.model
    row.form_factor = spec.form_factor
    row.cpu = spec.cpu
    row.cpu_family = cpu_family
    row.cpu_model = cpu_model
    row.cpu_generation = cpu_generation
    row.ram_gb = spec.ram_gb
    row.storage_gb = spec.storage_gb
    row.storage_type = spec.storage_type
    row.gpu = spec.gpu
    row.confidence = spec.confidence
    row.extraction_method = method
    row.extracted_at = _utcnow()

    ad.extracted_at = _utcnow()
    db.session.commit()
    return row


def _spec_join(query, joined: dict, kind: str = "inner"):
    """Garante um único join em AdSpec (inner ou outer)."""
    if joined["spec"]:
        return query
    if kind == "outer":
        query = query.outerjoin(AdSpec, Ad.id == AdSpec.ad_id)
    else:
        query = query.join(AdSpec, Ad.id == AdSpec.ad_id)
    joined["spec"] = True
    return query


def _apply_filters(query, f: AdFilters, joined: dict):
    if not f.include_inactive:
        query = query.filter(Ad.is_active.is_(True), Ad.user_disabled.is_(False))
    if f.q:
        like = f"%{f.q.strip()}%"
        query = query.filter(or_(Ad.title.ilike(like), Ad.description.ilike(like)))
    if f.brand:
        query = _spec_join(query, joined).filter(AdSpec.brand.ilike(f"%{f.brand}%"))
    if f.model:
        query = _spec_join(query, joined).filter(AdSpec.model.ilike(f"%{f.model}%"))
    if f.cpu_family:
        fam = f.cpu_family.lower()
        if fam in VENDOR_ALIASES:
            query = _spec_join(query, joined).filter(
                AdSpec.cpu_family.in_(CPU_GROUPS[VENDOR_ALIASES[fam]])
            )
        else:
            query = _spec_join(query, joined).filter(AdSpec.cpu_family == fam)
        # Geração só faz sentido dentro de um domínio de escala: família
        # (Intel 1–14, Ryzen 1–9) ou fabricante (intel 1–14, amd 1–9).
        # Sem família/fabricante, gen_min/gen_max são ignorados.
        if f.gen_min is not None:
            query = query.filter(AdSpec.cpu_generation >= f.gen_min)
        if f.gen_max is not None:
            query = query.filter(AdSpec.cpu_generation <= f.gen_max)
    if f.cpu_model is not None:
        query = _spec_join(query, joined).filter(AdSpec.cpu_model == f.cpu_model)
    if f.ram_min is not None:
        query = _spec_join(query, joined).filter(AdSpec.ram_gb >= f.ram_min)
    if f.storage_min is not None:
        query = _spec_join(query, joined).filter(AdSpec.storage_gb >= f.storage_min)
    if f.price_min is not None:
        query = query.filter(Ad.price_cents >= f.price_min)
    if f.price_max is not None:
        query = query.filter(Ad.price_cents <= f.price_max)
    if f.form_factor:
        query = _spec_join(query, joined).filter(AdSpec.form_factor == f.form_factor)
    if f.confidence_min is not None:
        query = _spec_join(query, joined).filter(AdSpec.confidence >= f.confidence_min)
    if f.has_specs is True:
        query = _spec_join(query, joined)
    elif f.has_specs is False:
        query = _spec_join(query, joined, kind="outer").filter(AdSpec.id.is_(None))
    return query


def _apply_sort(query, sort: str, joined: dict):
    if sort == "price_asc":
        return query.order_by(Ad.price_cents.asc().nulls_last())
    if sort == "price_desc":
        return query.order_by(Ad.price_cents.desc().nulls_last())
    if sort == "confidence":
        return _spec_join(query, joined, kind="outer").order_by(
            AdSpec.confidence.desc().nulls_last()
        )
    return query.order_by(Ad.published_at.desc().nulls_last(), Ad.scraped_at.desc())


def list_ads(f: AdFilters) -> tuple[list[Ad], int]:
    """Aplica filtros/ordenação e pagina. Retorna (itens, total)."""
    joined: dict = {"spec": False}
    query = _apply_sort(_apply_filters(Ad.query, f, joined), f.sort, joined)
    total = query.count()
    items = (
        query.offset((f.page - 1) * f.per_page)
        .limit(f.per_page)
        .all()
    )
    return items, total


def get_ad(ad_id: int) -> Ad | None:
    return db.session.get(Ad, ad_id)


def set_user_disabled(ad_id: int, disabled: bool) -> Ad | None:
    """Oculta/exibe um anúncio manualmente (toggle 'Disponível'). Retorna None se não existir."""
    ad = db.session.get(Ad, ad_id)
    if ad is None:
        return None
    if ad.user_disabled != disabled:
        ad.user_disabled = disabled
        db.session.commit()
    return ad


def _price_by_family_gen() -> dict[tuple[str, int], list[int]]:
    """Preços por (família, geração) — geração só é comparável dentro da família."""
    rows = (
        db.session.query(AdSpec.cpu_family, AdSpec.cpu_generation, Ad.price_cents)
        .join(Ad, AdSpec.ad_id == Ad.id)
        .filter(
            AdSpec.cpu_family.isnot(None),
            AdSpec.cpu_generation.isnot(None),
            Ad.price_cents.isnot(None),
            Ad.is_active.is_(True),
            Ad.user_disabled.is_(False),
        )
        .all()
    )
    groups: dict[tuple[str, int], list[int]] = {}
    for fam, gen, price in rows:
        groups.setdefault((fam, gen), []).append(price)
    return groups


def price_benchmarks() -> list[dict]:
    """Benchmark de mercado: p25/p50/p75 de preço por (família, geração)."""
    groups = _price_by_family_gen()
    out = []
    for fam, gen in sorted(groups):
        prices = groups[(fam, gen)]
        if len(prices) >= 2:
            q1, q2, q3 = quantiles(sorted(prices), n=4, method="inclusive")
        else:
            q1 = q2 = q3 = prices[0]
        out.append(
            {
                "family": fam,
                "generation": gen,
                "count": len(prices),
                "p25": q1,
                "p50": q2,
                "p75": q3,
            }
        )
    return out


def _flag_deal(title: str | None, price_cents: int | None, discount: float) -> str:
    if is_parts(title):
        return "peça/sucata"
    if price_cents is not None and price_cents < 20000:
        return "muito barato — confira"
    if discount <= -40:
        return "muito abaixo do mercado"
    if discount >= 50:
        return "acima do mercado"
    return ""


def best_deals(f: AdFilters, limit: int = 30) -> list[dict]:
    """Ranking de valor: desconto % vs mediana da (família, geração)."""
    groups = _price_by_family_gen()
    medians = {(fam, gen): median(prices) for (fam, gen), prices in groups.items()}

    joined: dict = {"spec": False}
    query = _apply_filters(Ad.query, f, joined)
    query = _spec_join(query, joined).filter(
        Ad.price_cents.isnot(None),
        AdSpec.cpu_family.isnot(None),
        AdSpec.cpu_generation.isnot(None),
    )

    deals = []
    for ad in query.all():
        spec = ad.spec
        med = medians.get((spec.cpu_family, spec.cpu_generation))
        if not med:
            continue
        discount = (ad.price_cents - med) / med * 100
        deals.append(
            {
                "id": ad.id,
                "title": ad.title,
                "url": ad.url,
                "price_cents": ad.price_cents,
                "family": spec.cpu_family,
                "generation": spec.cpu_generation,
                "cpu": spec.cpu,
                "ram_gb": spec.ram_gb,
                "storage_gb": spec.storage_gb,
                "storage_type": spec.storage_type,
                "discount": round(discount, 1),
                "flag": _flag_deal(ad.title, ad.price_cents, discount),
            }
        )
    deals.sort(key=lambda d: d["discount"])
    return deals[:limit]


def chart_data(f: AdFilters) -> list[dict]:
    """Pontos para o gráfico preço × geração (só com preço e geração presentes)."""
    joined: dict = {"spec": False}
    query = _apply_filters(Ad.query, f, joined)
    query = _spec_join(query, joined).filter(
        Ad.price_cents.isnot(None),
        AdSpec.cpu_generation.isnot(None),
    )
    rows = query.order_by(Ad.price_cents).all()
    return [
        {
            "id": ad.id,
            "price_cents": ad.price_cents,
            "generation": ad.spec.cpu_generation,
            "brand": ad.spec.brand,
            "model": ad.spec.model,
            "cpu": ad.spec.cpu,
            "ram_gb": ad.spec.ram_gb,
            "url": ad.url,
        }
        for ad in rows
        if ad.spec is not None
    ]


def stats() -> dict:
    ativos = (Ad.is_active.is_(True), Ad.user_disabled.is_(False))
    total = Ad.query.filter(*ativos).count()
    removidos = Ad.query.filter(Ad.is_active.is_(False)).count()
    ocultos = Ad.query.filter(Ad.user_disabled.is_(True)).count()
    sem_specs = (
        Ad.query.outerjoin(AdSpec, Ad.id == AdSpec.ad_id)
        .filter(AdSpec.id.is_(None), *ativos)
        .count()
    )
    preco = Ad.query.with_entities(
        db.func.min(Ad.price_cents), db.func.max(Ad.price_cents), db.func.avg(Ad.price_cents)
    ).filter(*ativos).one()
    por_cpu = dict(
        db.session.query(AdSpec.cpu_family, db.func.count(AdSpec.id))
        .join(Ad, AdSpec.ad_id == Ad.id)
        .filter(AdSpec.cpu_family.isnot(None), *ativos)
        .group_by(AdSpec.cpu_family)
        .order_by(db.func.count(AdSpec.id).desc())
        .all()
    )
    return {
        "total": total,
        "removidos": removidos,
        "ocultos": ocultos,
        "com_specs": total - sem_specs,
        "sem_specs": sem_specs,
        "preco_cents": {
            "min": preco[0],
            "max": preco[1],
            "avg": round(preco[2], 2) if preco[2] is not None else None,
        },
        "por_cpu_family": por_cpu,
    }


def ad_to_dict(ad: Ad, include_description: bool = False) -> dict:
    data = {
        "id": ad.id,
        "olx_id": ad.olx_id,
        "title": ad.title,
        "price_cents": ad.price_cents,
        "url": ad.url,
        "city": ad.city,
        "state": ad.state,
        "published_at": ad.published_at.isoformat() if ad.published_at else None,
        "scraped_at": ad.scraped_at.isoformat() if ad.scraped_at else None,
        "is_active": ad.is_active,
        "user_disabled": ad.user_disabled,
        "removed_at": ad.removed_at.isoformat() if ad.removed_at else None,
        "images": [img.url for img in ad.images],
        "specs": _spec_to_dict(ad.spec),
    }
    if include_description:
        data["description"] = ad.description
    return data


def _spec_to_dict(spec: AdSpec | None) -> dict | None:
    if spec is None:
        return None
    return {
        "brand": spec.brand,
        "model": spec.model,
        "form_factor": spec.form_factor,
        "cpu": spec.cpu,
        "cpu_family": spec.cpu_family,
        "cpu_model": spec.cpu_model,
        "cpu_generation": spec.cpu_generation,
        "ram_gb": spec.ram_gb,
        "storage_gb": spec.storage_gb,
        "storage_type": spec.storage_type,
        "gpu": spec.gpu,
        "confidence": spec.confidence,
        "extraction_method": spec.extraction_method,
        "extracted_at": spec.extracted_at.isoformat() if spec.extracted_at else None,
    }

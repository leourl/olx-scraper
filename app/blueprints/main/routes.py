from datetime import timezone
from zoneinfo import ZoneInfo

from flask import abort, current_app, redirect, render_template, request, url_for

from app.blueprints.main import bp
from app.services import ad_service
from app.services.ad_service import AdFilters
from app.services.runner import VALID_STEPS, load_terms
from app.services.run_history_service import get_recent_runs

BR_TZ = ZoneInfo("America/Sao_Paulo")


def _to_br(dt):
    """Converte datetime para o fuso do Brasil (America/Sao_Paulo, UTC-3).

    O SQLite não preserva timezone; datas naive são tratadas como UTC (o mesmo
    critério de `run_history_service._ensure_utc`).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BR_TZ)

SORT_LABELS = {
    "newest": "Mais recentes",
    "price_asc": "Preço (menor→maior)",
    "price_desc": "Preço (maior→menor)",
    "confidence": "Confiança das specs",
}


@bp.app_template_global()
def gen_range_for(family):
    """Expõe ad_service.gen_range_for aos templates (partial _filters.html)."""
    return ad_service.gen_range_for(family)


def _cpu_family_groups(selected: str | None, por_cpu_family: dict) -> dict[str, list[str]]:
    """Famílias agrupadas Intel/AMD, só as com anúncios (ou a selecionada)."""
    present = set(por_cpu_family.keys())
    groups: dict[str, list[str]] = {}
    for grupo, familias in ad_service.CPU_GROUPS.items():
        fams = [f for f in familias if f in present]
        if fams:
            groups[grupo] = fams
    # aliases de fabricante (intel/amd) são hardcoded no topo do select — não entram
    # no fallback dos optgroups (senão virariam <option> duplicadas dentro do grupo)
    if selected and selected.lower() not in ad_service.VENDOR_ALIASES \
            and not any(selected in fams for fams in groups.values()):
        groups.setdefault(ad_service.cpu_group(selected), []).append(selected)
    return groups


@bp.app_template_filter("brl")
def brl_filter(price_cents):
    if price_cents is None:
        return "sob consulta"
    return f"R$ {price_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _parse_filters() -> AdFilters:
    args = request.args

    def _int(name):
        v = args.get(name)
        return int(v) if v not in (None, "") else None

    sort = args.get("sort", "newest")
    if sort not in ad_service.ALLOWED_SORTS:
        sort = "newest"
    return AdFilters(
        q=args.get("q"),
        brand=args.get("brand"),
        cpu_family=(args.get("cpu_family") or "").lower() or None,
        gen_min=_int("gen_min"),
        ram_min=_int("ram_min"),
        storage_min=_int("storage_min"),
        price_max=_int("price_max"),
        form_factor=args.get("form_factor"),
        has_specs=(
            args.get("has_specs").lower() in ("1", "true", "yes")
            if args.get("has_specs") not in (None, "")
            else None
        ),
        confidence_min=(
            float(args["confidence_min"]) if args.get("confidence_min") not in (None, "") else None
        ),
        include_inactive=args.get("include_inactive") in ("1", "true", "yes"),
        sort=sort,
        page=max(_int("page") or 1, 1),
        per_page=24,
    )


@bp.get("/")
def index():
    f = _parse_filters()
    items, total = ad_service.list_ads(f)
    total_pages = max((total + f.per_page - 1) // f.per_page, 1)
    stats = ad_service.stats()
    page_args = {k: v for k, v in request.args.items() if k != "page"}
    return render_template(
        "index.html",
        ads=items,
        total=total,
        page=f.page,
        total_pages=total_pages,
        filters=f,
        sort_labels=SORT_LABELS,
        cpu_family_groups=_cpu_family_groups(f.cpu_family, stats.get("por_cpu_family", {})),
        stats=stats,
        page_args=page_args,
        action="main.index",
    )


@bp.get("/chart")
def chart():
    f = _parse_filters()
    points = ad_service.chart_data(f)
    por_cpu = ad_service.stats().get("por_cpu_family", {})
    return render_template(
        "chart.html",
        points=points,
        filters=f,
        sort_labels=SORT_LABELS,
        cpu_family_groups=_cpu_family_groups(f.cpu_family, por_cpu),
        action="main.chart",
    )


@bp.get("/ads/<int:ad_id>")
def detail(ad_id: int):
    ad = ad_service.get_ad(ad_id)
    if ad is None:
        abort(404)
    return render_template("ad_detail.html", ad=ad)


@bp.get("/offers")
def offers():
    f = _parse_filters()
    benchmarks = ad_service.price_benchmarks()
    deals = ad_service.best_deals(f)
    return render_template(
        "offers.html",
        benchmarks=benchmarks,
        deals=deals,
        filters=f,
        sort_labels=SORT_LABELS,
        cpu_family_groups=_cpu_family_groups(f.cpu_family, ad_service.stats().get("por_cpu_family", {})),
        action="main.offers",
    )


@bp.get("/review")
def review():
    items, total = ad_service.list_ads(
        AdFilters(
            has_specs=False,
            confidence_min=None,
            sort="newest",
            page=1,
            per_page=100,
        )
    )
    baixa_conf, _ = ad_service.list_ads(
        AdFilters(confidence_min=0, sort="confidence", page=1, per_page=100)
    )
    baixa_conf = [a for a in baixa_conf if a.spec and a.spec.confidence < 0.6]
    return render_template("review.html", sem_specs=items, baixa_conf=baixa_conf)


@bp.get("/run")
def run():
    data = load_terms(current_app.instance_path)
    scheduler = current_app.extensions.get("autoscheduler")
    history = get_recent_runs(limit=50)
    for h in history:
        h.summary = _history_summary(h)
        h.started_at = _to_br(h.started_at)
    return render_template(
        "run.html",
        terms=data["terms"],
        region=data["region"],
        valid_steps=VALID_STEPS,
        autostart={
            "enabled": scheduler.enabled if scheduler else False,
            "interval_minutes": current_app.config["AUTORUN_INTERVAL_MINUTES"],
            "running": bool(scheduler and scheduler.running),
        },
        run_history=history,
    )


def _history_summary(entry) -> str:
    """Resumo curto por etapa a partir do `result` da run (ex.: 'novos 3')."""
    parts = []
    result = entry.result or {}
    for step in ("scrape", "enrich", "check", "process"):
        r = result.get(step)
        if not r:
            continue
        if step == "scrape":
            parts.append(f"scrape: novos {r.get('novos', 0)}")
        elif step == "enrich":
            parts.append(f"enrich: ok {r.get('enriquecidos', 0)}")
        elif step == "check":
            parts.append(f"check: removidos {r.get('removidos', 0)}")
        elif step == "process":
            parts.append(f"process: ok {r.get('ok', 0)}/{r.get('ok', 0) + r.get('falhas', 0)}")
    return " · ".join(parts) or "—"

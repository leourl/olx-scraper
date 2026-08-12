from flask import current_app, jsonify, request

from app.blueprints.api import bp
from app.scrapers.client import NETWORK_ERRORS, ScrapeBlockedError
from app.services import ad_service, runner
from app.services.ad_service import AdFilters
from app.services.autoscheduler import get_enabled, set_enabled
from app.services.run_history_service import get_recent_runs, run_history_to_dict
from app.services.runner import RunAlreadyActive, VALID_STEPS, job_to_dict, save_terms


def _parse_filters() -> AdFilters:
    args = request.args

    def _int(name):
        v = args.get(name)
        return int(v) if v not in (None, "") else None

    def _bool(name):
        v = args.get(name)
        if v is None:
            return None
        return v.lower() in ("1", "true", "yes")

    def _float(name):
        v = args.get(name)
        return float(v) if v not in (None, "") else None

    sort = args.get("sort", "newest")
    if sort not in ad_service.ALLOWED_SORTS:
        sort = "newest"

    page = max(_int("page") or 1, 1)
    per_page = min(_int("per_page") or 20, 100)

    return AdFilters(
        q=args.get("q"),
        brand=args.get("brand"),
        model=args.get("model"),
        cpu_family=(args.get("cpu_family") or "").lower() or None,
        cpu_model=_int("cpu_model"),
        gen_min=_int("gen_min"),
        gen_max=_int("gen_max"),
        ram_min=_int("ram_min"),
        storage_min=_int("storage_min"),
        price_min=_int("price_min"),
        price_max=_int("price_max"),
        form_factor=args.get("form_factor"),
        has_specs=_bool("has_specs"),
        confidence_min=_float("confidence_min"),
        include_inactive=bool(_bool("include_inactive")),
        sort=sort,
        page=page,
        per_page=per_page,
    )


@bp.get("/ads")
def list_ads():
    f = _parse_filters()
    items, total = ad_service.list_ads(f)
    return jsonify(
        {
            "items": [ad_service.ad_to_dict(ad) for ad in items],
            "total": total,
            "page": f.page,
            "per_page": f.per_page,
        }
    )


@bp.get("/ads/<int:ad_id>")
def get_ad(ad_id: int):
    ad = ad_service.get_ad(ad_id)
    if ad is None:
        return jsonify({"error": "não encontrado"}), 404
    return jsonify(ad_service.ad_to_dict(ad, include_description=True))


@bp.post("/ads/<int:ad_id>/disabled")
def set_ad_disabled(ad_id: int):
    data = request.get_json(silent=True) or {}
    disabled = data.get("disabled")
    if not isinstance(disabled, bool):
        return jsonify({"error": "campo 'disabled' deve ser booleano"}), 400
    ad = ad_service.set_user_disabled(ad_id, disabled)
    if ad is None:
        return jsonify({"error": "não encontrado"}), 404
    return jsonify({
        "ok": True,
        "disabled": ad.user_disabled,
        "ad": ad_service.ad_to_dict(ad, include_description=True),
    })


@bp.post("/ads/import")
def import_ad():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    process = bool(data.get("process", True))
    if not url:
        return jsonify({"error": "informe o link do anúncio"}), 400
    try:
        result = runner.import_single_ad(
            current_app._get_current_object(), url, process=process
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ScrapeBlockedError as e:
        return jsonify({"error": str(e)}), 503
    except NETWORK_ERRORS:
        return jsonify({"error": "erro de rede ao acessar a OLX"}), 502

    status = result["status"]
    if status == "removed":
        return jsonify({"error": "anúncio não existe mais na OLX (removido)"}), 410
    if status == "not_an_ad":
        return jsonify({"error": "página não contém dados de anúncio (JSON-LD ausente)"}), 422

    ad = ad_service.get_ad(result["ad_id"])
    return jsonify({
        "status": "ok",
        "created": result["created"],
        "processed": result["processed"],
        "ad": ad_service.ad_to_dict(ad, include_description=True),
    }), (201 if result["created"] else 200)


@bp.get("/stats")
def stats():
    return jsonify(ad_service.stats())


def _run_manager():
    return current_app.extensions["run_manager"]


@bp.put("/runs/terms")
def save_run_terms():
    data = request.get_json(silent=True) or {}
    terms = [t.strip() for t in (data.get("terms") or []) if isinstance(t, str) and t.strip()]
    region = (data.get("region") or "estado-sp").strip()
    save_terms(current_app.instance_path, terms, region)
    return jsonify({"ok": True, "terms": terms, "region": region}), 200


@bp.post("/runs")
def start_run():
    data = request.get_json(silent=True) or {}
    terms = [t.strip() for t in (data.get("terms") or []) if isinstance(t, str) and t.strip()]
    region = (data.get("region") or "estado-sp").strip()
    steps = [s for s in (data.get("steps") or []) if s in VALID_STEPS]
    if not terms:
        return jsonify({"error": "informe ao menos um termo"}), 400
    if not steps:
        return jsonify({"error": "selecione ao menos uma etapa"}), 400

    save_terms(current_app.instance_path, terms, region)

    try:
        run_id = _run_manager().start(current_app._get_current_object(), terms, region, steps)
    except RunAlreadyActive:
        return jsonify({"error": "já existe uma run ativa"}), 409
    return jsonify({"id": run_id, "terms": terms, "steps": steps}), 202


@bp.get("/runs/current")
def current_run():
    job = _run_manager().current()
    return jsonify(job_to_dict(job) if job else None)


@bp.get("/runs/history")
def runs_history():
    limit = request.args.get("limit", 20, type=int)
    entries = get_recent_runs(limit)
    return jsonify({"entries": [run_history_to_dict(e) for e in entries]})


@bp.get("/runs/<int:run_id>")
def get_run(run_id: int):
    job = _run_manager().get(run_id)
    if job is None:
        return jsonify({"error": "run não encontrada"}), 404
    return jsonify(job_to_dict(job))


def _autostart_state() -> dict:
    scheduler = current_app.extensions.get("autoscheduler")
    return {
        "enabled": scheduler.enabled if scheduler else get_enabled(current_app.instance_path, False),
        "interval_minutes": current_app.config["AUTORUN_INTERVAL_MINUTES"],
        "running": bool(scheduler and scheduler.running),
    }


@bp.get("/autostart")
def get_autostart():
    return jsonify(_autostart_state())


@bp.post("/autostart")
def set_autostart():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "campo 'enabled' deve ser booleano"}), 400

    set_enabled(current_app.instance_path, enabled)
    state = _autostart_state()
    warning = None
    if enabled and not state["running"]:
        warning = "scheduler não iniciado (AUTORUN_ENABLED=0 no .env); reinicie a app para ativar"
    return jsonify({**state, "warning": warning})

"""Execução de runs (scrape/enrich/process) com progresso e estado em memória.

Também centraliza a lógica que antes vivia inline em `app/cli.py`, para que
CLI e a página `/run` compartilhem o mesmo código.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.extensions import db
from app.extractors.llm import DeepSeekClient, LlmUsage
from app.extractors.pipeline import extract_specs
from app.models import Ad, AdSpec
from app.scrapers.base import RawAd
from app.scrapers.client import NETWORK_ERRORS, OlxClient, ScrapeBlockedError
from app.scrapers.olx import OlxScraper, olx_id_from_url
from app.services import ad_service
from app.services.run_history_service import create_run_entry, finalize_run_entry
from app.services.scrape_block import clear_blocked, set_blocked

log = logging.getLogger(__name__)

VALID_STEPS = ("scrape", "enrich", "check", "process")

TERMS_FILE = "run_terms.json"

# DeepSeek (por 1M tokens): input miss $0.14, hit $0.0028, output $0.28
INPUT_MISS_USD = 0.14 / 1_000_000
INPUT_HIT_USD = 0.0028 / 1_000_000
OUTPUT_USD = 0.28 / 1_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def save_terms(instance_path: str, terms: list[str], region: str) -> None:
    """Persiste os termos (e região) usados na página /run em JSON."""
    data = {"terms": terms, "region": region, "saved_at": _utcnow().isoformat()}
    (Path(instance_path) / TERMS_FILE).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_terms(instance_path: str) -> dict:
    """Lê os termos salvos; retorna vazio se o arquivo não existir/estiver quebrado."""
    path = Path(instance_path) / TERMS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "terms": [t for t in data.get("terms", []) if t],
            "region": data.get("region", "estado-sp"),
        }
    except (OSError, json.JSONDecodeError):
        return {"terms": [], "region": "estado-sp"}


def run_scrape(
    app,
    query: str,
    region: str,
    max_pages: int | None = None,
    no_details: bool = False,
    on_progress=None,
) -> dict:
    """Listagem + detalhes dos anúncios novos de uma busca. Retorna stats."""
    cfg = app.config
    client = OlxClient(
        cfg["USER_AGENT"], timeout=cfg["SCRAPER_TIMEOUT"], delay=cfg["SCRAPER_DELAY"],
        impersonate=cfg["SCRAPER_IMPERSONATE"],
    )
    scraper = OlxScraper(client)

    listings = scraper.search(query, region, max_pages or cfg["SCRAPER_MAX_PAGES"])
    on_progress and on_progress("scrape", 1, 1, f"listagem: {len(listings)} anúncios únicos")

    novos = 0
    if no_details:
        for ad in listings:
            _, created = ad_service.upsert_raw(ad)
            novos += int(created)
    else:
        for i, ad in enumerate(listings, 1):
            raw, created = ad_service.upsert_raw(ad)
            if created:
                novos += 1
            if created and not raw.description:
                scraper.fetch_detail(ad)
                ad_service.upsert_raw(ad)
            on_progress and on_progress("scrape", i, len(listings), f"detalhes: {i}/{len(listings)}")

    total = Ad.query.count()
    return {"listados": len(listings), "novos": novos, "total": total}


def import_single_ad(app, url: str, process: bool = True) -> dict:
    """Cadastra um anúncio da OLX a partir do link direto.

    Retorna {'status': 'ok'|'removed'|'not_an_ad', ...}. Levanta ValueError
    (link inválido), ScrapeBlockedError (403) e NETWORK_ERRORS (rede) — o
    chamador mapeia para HTTP.
    """
    cfg = app.config
    url = url.strip()
    if not url:
        raise ValueError("informe o link do anúncio")
    # remove query/tracking (ex.: ?lis=...) e fragmento → URL canônica, como a
    # gravada pelo scraper; evita 403 do Cloudflare e duplicatas por parâmetros
    url = url.split("#")[0].split("?")[0].rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("link não é da OLX")
    if parsed.hostname != "olx.com.br" and not parsed.hostname.endswith(".olx.com.br"):
        raise ValueError("link não é da OLX")
    olx_id = olx_id_from_url(url)
    if not olx_id:
        raise ValueError("link não parece ser um anúncio da OLX")

    client = OlxClient(
        cfg["USER_AGENT"], timeout=cfg["SCRAPER_TIMEOUT"], delay=cfg["SCRAPER_DELAY"],
        impersonate=cfg["SCRAPER_IMPERSONATE"],
    )
    scraper = OlxScraper(client)
    resp = client.get(url)
    if resp.status_code in (404, 410):
        return {"status": "removed", "url": url}
    data = scraper._parse_detail(resp.text)
    if not data:
        return {"status": "not_an_ad", "url": url}

    raw = RawAd(
        olx_id=olx_id,
        title=data.get("name") or url,
        url=url,
        price_cents=data.get("price_cents"),
        description=data.get("description"),
        images=data.get("images") or [],
    )
    ad, created = ad_service.upsert_raw(raw, refresh=True)

    processed = False
    if process and cfg.get("DEEPSEEK_KEY"):
        try:
            run_process(app, ad_id=ad.id)
            processed = True
        except Exception:
            log.exception("extração de specs falhou para o ad %s", ad.id)

    return {
        "status": "ok",
        "created": created,
        "processed": processed,
        "ad_id": ad.id,
        "title": ad.title,
        "price_cents": ad.price_cents,
        "url": ad.url,
    }


def run_enrich(app, limit: int | None = None, on_progress=None) -> dict:
    """Busca detalhes dos anúncios sem descrição. Retorna stats."""
    cfg = app.config
    ads = ad_service.list_missing_description(limit)
    if not ads:
        return {"enriquecidos": 0, "total": 0}

    client = OlxClient(
        cfg["USER_AGENT"], timeout=cfg["SCRAPER_TIMEOUT"], delay=cfg["SCRAPER_DELAY"],
        impersonate=cfg["SCRAPER_IMPERSONATE"],
    )
    scraper = OlxScraper(client)
    ok = 0
    for i, ad in enumerate(ads, 1):
        raw = RawAd(olx_id=ad.olx_id or "", title=ad.title or "", url=ad.url)
        scraper.fetch_detail(raw)
        if raw.description:
            ad_service.upsert_raw(raw)
            ok += 1
        on_progress and on_progress("enrich", i, len(ads), f"enriquecidos: {i}/{len(ads)} (ok: {ok})")

    return {"enriquecidos": ok, "total": len(ads)}


def run_check(app, limit: int | None = None, on_progress=None) -> dict:
    """Verifica se anúncios ativos ainda estão publicados (404/410 → removido)."""
    cfg = app.config
    ads = Ad.query.filter(Ad.is_active.is_(True), Ad.user_disabled.is_(False)).order_by(Ad.scraped_at.asc())
    if limit:
        ads = ads.limit(limit)
    ads = ads.all()
    if not ads:
        return {"checados": 0, "removidos": 0, "ativos": 0, "sem_confirmar": 0, "erros": 0}

    client = OlxClient(
        cfg["USER_AGENT"], timeout=cfg["SCRAPER_TIMEOUT"], delay=cfg["SCRAPER_DELAY"],
        impersonate=cfg["SCRAPER_IMPERSONATE"],
    )
    scraper = OlxScraper(client)
    removidos = sem_confirmar = erros = 0
    for i, ad in enumerate(ads, 1):
        try:
            resp = client.get(ad.url)
        except ScrapeBlockedError:
            raise
        except NETWORK_ERRORS as e:
            erros += 1
            log.warning("check #%s: erro de rede (%s): %s", ad.id, type(e).__name__, e)
            on_progress and on_progress("check", i, len(ads), f"verificados: {i}/{len(ads)} (removidos: {removidos})")
            continue
        if resp.status_code in (404, 410):
            ad.is_active = False
            ad.removed_at = _utcnow()
            db.session.commit()
            removidos += 1
        elif resp.status_code == 200:
            # reusa o parser JSON-LD do scraper: dict não-vazio = Product ativo
            if scraper._parse_detail(resp.text):
                pass  # ativo
            else:
                sem_confirmar += 1
        on_progress and on_progress("check", i, len(ads), f"verificados: {i}/{len(ads)} (removidos: {removidos})")

    return {
        "checados": len(ads),
        "removidos": removidos,
        "ativos": len(ads) - removidos - sem_confirmar,
        "sem_confirmar": sem_confirmar,
        "erros": erros,
    }


def _missing_generation_ads(limit: int | None = None) -> list[Ad]:
    """Ads com specs mas sem cpu_generation (reprocessa para completar a geração)."""
    query = (
        Ad.query.join(AdSpec, Ad.id == AdSpec.ad_id)
        .filter(
            AdSpec.cpu_generation.is_(None),
            Ad.description.isnot(None),
            Ad.description != "",
            Ad.is_active.is_(True),
            Ad.user_disabled.is_(False),
        )
        .order_by(Ad.id.asc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()


def run_fill_specs(app, limit: int | None = None, on_progress=None) -> dict:
    """Fill determinístico e seguro (só regex, sem LLM): preenche lacunas de
    cpu/cpu_family/cpu_model/cpu_generation/ram/storage SEM sobrescrever valores
    já existentes. Backlog de geração (D-031) sem risco de degradar specs.
    """
    from app.extractors.regex import extract_regex, generation_from, normalize_cpu

    specs = (
        AdSpec.query.join(Ad, AdSpec.ad_id == Ad.id)
        .filter(
            Ad.description.isnot(None),
            Ad.description != "",
            Ad.user_disabled.is_(False),
            AdSpec.cpu_generation.is_(None),  # foco no backlog de geração
        )
        .order_by(AdSpec.ad_id.asc())
    )
    if limit:
        specs = specs.limit(limit)
    specs = specs.all()

    filled = 0
    for spec in specs:
        text = f"{spec.ad.title or ''}\n{spec.ad.description or ''}"
        r = extract_regex(text)
        cpu_family = spec.cpu_family
        cpu_model = spec.cpu_model
        cpu_generation = spec.cpu_generation
        changed = False

        if spec.cpu is None and r.cpu:
            spec.cpu = r.cpu
            changed = True
        if spec.cpu:
            fam, model = normalize_cpu(spec.cpu)
            if cpu_model is None and model is not None:
                cpu_model = model
            if fam and (cpu_family is None or cpu_family.lower() != fam.lower()):
                cpu_family = fam
            cpu_generation = cpu_generation or generation_from(cpu_family, cpu_model)
            if cpu_generation is None and r.cpu_generation is not None and r.cpu:
                if fam and fam.startswith("ryzen"):
                    ok = 1 <= r.cpu_generation <= 9
                else:
                    ok = 1 <= r.cpu_generation <= 14
                if ok:
                    cpu_generation = r.cpu_generation
        if spec.ram_gb is None and r.ram_gb:
            spec.ram_gb = r.ram_gb
            changed = True
        if spec.storage_gb is None and r.storage_gb:
            spec.storage_gb = r.storage_gb
            spec.storage_type = spec.storage_type or r.storage_type
            changed = True

        if cpu_model != spec.cpu_model or cpu_family != spec.cpu_family:
            spec.cpu_model = cpu_model
            spec.cpu_family = cpu_family
            changed = True
        if cpu_generation != spec.cpu_generation:
            spec.cpu_generation = cpu_generation
            changed = True
        if changed:
            filled += 1
            db.session.commit()
        on_progress and on_progress(
            "process", filled, len(specs), f"fill: {filled}/{len(specs)}"
        )
    return {"fill": filled, "total": len(specs)}


def run_process(
    app,
    limit: int | None = None,
    ad_id: int | None = None,
    force: bool = False,
    missing_generation: bool = False,
    on_progress=None,
) -> dict:
    """Extrai specs (regex + LLM) dos pendentes. Retorna stats + custo."""
    from app.extensions import db

    cfg = app.config
    client = DeepSeekClient(
        api_key=cfg["DEEPSEEK_KEY"],
        base_url=cfg["DEEPSEEK_BASE_URL"],
        model=cfg["DEEPSEEK_MODEL"],
        timeout=cfg["LLM_TIMEOUT"],
        reasoning_effort=cfg["LLM_REASONING_EFFORT"],
        max_chars=cfg["LLM_MAX_CHARS"],
        max_retries=cfg["LLM_MAX_RETRIES"],
    )

    if ad_id is not None:
        ad = db.session.get(Ad, ad_id)
        if ad is None:
            raise ValueError(f"anúncio {ad_id} não existe")
        ads = [ad]
    elif missing_generation:
        ads = _missing_generation_ads(limit)
    elif force:
        ads = Ad.query.filter(
            Ad.description.isnot(None), Ad.description != "", Ad.user_disabled.is_(False)
        ).all()
    else:
        # Padrão (autorun/CLI sem flags): pendentes sem specs via LLM + fill
        # determinístico seguro (só regex) das specs sem geração (D-031).
        ads = list(ad_service.list_pending_extraction(limit))

    ok = falha = 0
    tot_in = tot_out = tot_cache = tot_retries = 0
    custo = 0.0
    for i, ad in enumerate(ads, 1):
        spec, usage, method, cpu_family, cpu_model, cpu_generation = extract_specs(
            ad.title, ad.description, client
        )
        tot_in += usage.input_tokens
        tot_out += usage.output_tokens
        tot_cache += usage.cached_tokens
        tot_retries += usage.retries
        custo += (
            (usage.input_tokens - usage.cached_tokens) * INPUT_MISS_USD
            + usage.cached_tokens * INPUT_HIT_USD
            + usage.output_tokens * OUTPUT_USD
        )
        if spec is None:
            falha += 1
            on_progress and on_progress(
                "process", i, len(ads), f"#{ad.id} FALHOU: {ad.title[:50]}"
            )
            continue
        ad_service.save_specs(
            ad, spec, method,
            cpu_family=cpu_family, cpu_model=cpu_model, cpu_generation=cpu_generation,
        )
        ok += 1
        on_progress and on_progress("process", i, len(ads), f"processando: {i}/{len(ads)} (ok: {ok})")

    result = {
        "ok": ok, "falhas": falha,
        "tokens_in": tot_in, "tokens_out": tot_out, "tokens_cache": tot_cache,
        "retries": tot_retries, "custo": custo,
    }
    # No caminho default (sem flags), completa o backlog de geração com o fill
    # determinístico seguro — sem LLM, sem sobrescrever valores bons (D-031).
    if ad_id is None and not missing_generation and not force:
        fill = run_fill_specs(app, cfg.get("PROCESS_MISSING_GEN_LIMIT", 50), on_progress=on_progress)
        result["fill"] = fill
    return result


@dataclass
class RunJob:
    id: int
    terms: list[str]
    region: str
    steps: list[str]
    status: str = "queued"  # queued | running | done | error
    step_index: int = 0
    done: int = 0
    total: int = 0
    message: str = ""
    log: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str | None = None
    history_id: int | None = None
    created_at: float = field(default_factory=time.time)


class RunAlreadyActive(RuntimeError):
    """Já existe uma run em andamento (uma por vez)."""


class RunManager:
    """Estado das runs em memória + execução em thread de fundo (uma por vez)."""

    def __init__(self) -> None:
        self._jobs: dict[int, RunJob] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def start(
        self,
        app,
        terms: list[str],
        region: str,
        steps: list[str],
        source: str = "manual",
    ) -> int:
        """Cria a run e dispara a thread de execução. Recusa se houver ativa."""
        with self._lock:
            if self.current():
                raise RunAlreadyActive("já existe uma run ativa")
            job = RunJob(id=self._next_id, terms=list(terms), region=region, steps=list(steps))
            self._jobs[job.id] = job
            self._next_id += 1
            job_id = job.id
        # Histórico é best-effort: cria a entrada antes de rodar; se falhar,
        # segue com history_id=None (sem job zumbi travando current()).
        try:
            job.history_id = create_run_entry(source, job.steps)
        except Exception:
            log.exception("não foi possível registrar a run %s no histórico", job_id)
        threading.Thread(target=self._run, args=(app, job_id), daemon=True).start()
        return job_id

    def get(self, job_id: int) -> RunJob | None:
        return self._jobs.get(job_id)

    def current(self) -> RunJob | None:
        for job in self._jobs.values():
            if job.status in ("queued", "running"):
                return job
        return None

    def execute(self, app, job_id: int) -> None:
        """Executa a run de forma síncrona (usado em testes/offline)."""
        self._run(app, job_id)

    def _run(self, app, job_id: int) -> None:
        job = self._jobs[job_id]
        with app.app_context():
            job.status = "running"
            try:
                for idx, step in enumerate(job.steps):
                    if step not in VALID_STEPS:
                        raise ValueError(f"etapa desconhecida: {step}")
                    job.step_index = idx
                    self._report(job, step, 0, 0, f"iniciando etapa {step}...")
                    if step == "scrape":
                        result = {
                            "listados": 0, "novos": 0,
                            "total": Ad.query.count(),
                            "termos": len(job.terms),
                        }
                        for t_idx, term in enumerate(job.terms, 1):
                            r = run_scrape(
                                app, term, job.region,
                                on_progress=lambda *a: self._report(job, *a),
                            )
                            result["listados"] += r["listados"]
                            result["novos"] += r["novos"]
                            result["total"] = r["total"]
                            self._report(
                                job, "scrape", t_idx, len(job.terms),
                                f"termo {t_idx}/{len(job.terms)} concluído: {term}",
                            )
                    elif step == "enrich":
                        result = run_enrich(app, on_progress=lambda *a: self._report(job, *a))
                    elif step == "check":
                        result = run_check(app, on_progress=lambda *a: self._report(job, *a))
                    else:
                        result = run_process(app, on_progress=lambda *a: self._report(job, *a))
                    job.result[step] = result
                job.status = "done"
                job.message = "run concluída"
                clear_blocked(app.instance_path)
            except ScrapeBlockedError as e:
                log.warning("run %s bloqueada pela OLX: %s", job_id, e)
                job.status = "blocked"
                job.error = str(e)
                job.message = f"OLX bloqueou a coleta: {e}"
                cooldown = int(app.config.get("SCRAPER_BLOCK_COOLDOWN_MINUTES", 0))
                if cooldown > 0:
                    set_blocked(app.instance_path, time.time() + cooldown * 60)
            except Exception as e:
                log.exception("run %s falhou", job_id)
                job.status = "error"
                job.error = str(e)
                job.message = f"erro: {e}"
            self._finalize_history(job)

    def _finalize_history(self, job: RunJob) -> None:
        """Fecha a entrada do histórico (best-effort, nunca quebra a run).

        Faz `rollback` antes para não commitar estado pendente do pipeline de
        anúncios de arrasto; se a gravação final falhar, só loga warning.
        """
        if job.history_id is None:
            return
        try:
            db.session.rollback()
            finalize_run_entry(job.history_id, job.status, job.result, job.error)
        except Exception:
            log.exception("não foi possível finalizar a run %s no histórico", job.id)

    @staticmethod
    def _report(job: RunJob, step: str, done: int, total: int, message: str) -> None:
        job.done = done
        job.total = total
        job.message = message
        job.log.append(message)


def job_to_dict(job: RunJob) -> dict:
    """Serializa a run para a API/polling."""
    if job.total and job.status in ("running", "queued"):
        percent = round((job.done / job.total) * 100)
    elif job.status == "done":
        percent = 100
    else:
        percent = 0
    step_labels = {
        "scrape": "coletando anúncios",
        "enrich": "buscando descrições",
        "check": "verificando anúncios",
        "process": "extraindo specs",
    }
    return {
        "id": job.id,
        "status": job.status,
        "steps": job.steps,
        "step": job.steps[job.step_index] if job.steps else None,
        "step_label": step_labels.get(job.steps[job.step_index]) if job.steps else None,
        "percent": percent,
        "done": job.done,
        "total": job.total,
        "message": job.message,
        "log": job.log,
        "result": job.result,
        "error": job.error,
    }

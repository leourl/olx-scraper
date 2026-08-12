import json
import time
from pathlib import Path

import httpx
import pytest

from app.scrapers.base import RawAd
from app.services import ad_service
from app.services import runner

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_HTML = (FIXTURES / "search.html").read_text(encoding="utf-8")
AD_GOOD = (FIXTURES / "ad_good.html").read_text(encoding="utf-8")


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "?q=" in str(request.url):
            return httpx.Response(200, text=SEARCH_HTML, request=request)
        return httpx.Response(200, text=AD_GOOD, request=request)

    return httpx.MockTransport(handler)


def _install_mock_client(monkeypatch, app, transport=None):
    transport = transport or _mock_transport()
    real_client = runner.OlxClient

    def factory(user_agent, timeout=30.0, delay=1.0, impersonate="chrome"):
        return real_client(
            user_agent, timeout=timeout, delay=delay, transport=transport, impersonate=impersonate
        )

    monkeypatch.setattr(runner, "OlxClient", factory)


def test_save_load_terms_roundtrip(tmp_path):
    runner.save_terms(str(tmp_path), ["dell optiplex sff", "lenovo thinkcentre"], "estado-sp")
    data = runner.load_terms(str(tmp_path))
    assert data["terms"] == ["dell optiplex sff", "lenovo thinkcentre"]
    assert data["region"] == "estado-sp"


def test_load_terms_missing_file(tmp_path):
    data = runner.load_terms(str(tmp_path))
    assert data == {"terms": [], "region": "estado-sp"}


def test_run_scrape_saves_and_returns_stats(app, monkeypatch):
    _install_mock_client(monkeypatch, app)

    stats = runner.run_scrape(app, "dell optiplex", "estado-sp", max_pages=2)
    assert stats["listados"] > 0
    assert stats["novos"] == stats["listados"]
    assert stats["total"] == stats["novos"]

    with app.app_context():
        from app.models import Ad

        assert Ad.query.count() == stats["novos"]


def test_run_scrape_no_details(app, monkeypatch):
    _install_mock_client(monkeypatch, app)

    stats = runner.run_scrape(app, "dell optiplex", "estado-sp", max_pages=2, no_details=True)
    assert stats["listados"] > 0
    assert stats["novos"] == stats["listados"]


def test_run_enrich_fills_descriptions(app, monkeypatch):
    _install_mock_client(monkeypatch, app)
    with app.app_context():
        ad_service.upsert_raw(RawAd(olx_id="1", title="Sem descrição", url="https://sp.olx.com.br/x/1"))

    stats = runner.run_enrich(app)
    assert stats["enriquecidos"] == 1
    assert stats["total"] == 1

    with app.app_context():
        from app.models import Ad

        ad = Ad.query.filter_by(url="https://sp.olx.com.br/x/1").first()
        assert ad.description


def test_run_process_missing_key_fails(app):
    with pytest.raises(ValueError):
        runner.run_process(app)


def test_run_process_extracts_specs(app, monkeypatch):
    spec_json = json.dumps({
        "brand": "Dell", "model": "OptiPlex 7050", "form_factor": "sff",
        "cpu": "i5-8500", "ram_gb": 16, "storage_gb": 512,
        "storage_type": "ssd", "gpu": None, "confidence": 0.9,
    })

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "x1", "object": "response", "status": "completed",
            "model": "deepseek-v4-flash",
            "output": [{
                "type": "message", "id": "m1", "role": "assistant",
                "content": [{"type": "output_text", "text": spec_json}],
            }],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}},
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(handler))

    with app.app_context():
        app.config["DEEPSEEK_KEY"] = "test-key"
        ad, _ = ad_service.upsert_raw(RawAd(
            olx_id="9", title="Dell OptiPlex 7050 SFF",
            url="https://sp.olx.com.br/x/7050-9",
            description="Dell OptiPlex 7050 SFF, i5-8500, 16GB, SSD 512GB",
        ))
        ad_id = ad.id

    stats = runner.run_process(app)
    assert stats["ok"] == 1
    assert stats["falhas"] == 0
    assert stats["retries"] == 0
    assert stats["custo"] >= 0

    with app.app_context():
        from app.models import AdSpec

        spec = AdSpec.query.filter_by(ad_id=ad_id).first()
        assert spec is not None
        assert spec.brand == "Dell"
        assert spec.ram_gb == 16


def _make_llm(handler):
    from app.extractors.llm import DeepSeekClient

    return DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )


def test_manager_start_creates_job(app, monkeypatch):
    _install_mock_client(monkeypatch, app)
    manager = runner.RunManager()

    run_id = manager.start(app, ["dell optiplex"], "estado-sp", ["scrape"])
    assert run_id == 1
    assert manager.get(run_id) is not None
    assert manager.get(999) is None


def test_manager_rejects_second_active(app, monkeypatch):
    _install_mock_client(monkeypatch, app)
    manager = runner.RunManager()
    # job travado em "running" para não depender de thread
    manager._jobs[1] = runner.RunJob(id=1, terms=["x"], region="estado-sp",
                                     steps=["scrape"], status="running")

    with pytest.raises(runner.RunAlreadyActive):
        manager.start(app, ["outro termo"], "estado-sp", ["scrape"])

    # após concluir, permite nova run
    manager._jobs[1].status = "done"
    assert manager.start(app, ["novo"], "estado-sp", ["scrape"]) == 1


def test_manager_current(app, monkeypatch):
    manager = runner.RunManager()
    manager._jobs[1] = runner.RunJob(id=1, terms=["x"], region="estado-sp",
                                     steps=["scrape"], status="running")
    assert manager.current().id == 1
    manager._jobs[1].status = "done"
    assert manager.current() is None


def test_manager_execute_sync_done(app, monkeypatch):
    monkeypatch.setattr(runner, "run_scrape", lambda *a, **k: {"listados": 0, "novos": 0, "total": 0})
    monkeypatch.setattr(runner, "run_enrich", lambda *a, **k: {"enriquecidos": 0, "total": 0})
    manager = runner.RunManager()
    job = runner.RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape", "enrich"])
    manager._jobs[1] = job

    manager.execute(app, 1)
    assert job.status == "done"
    assert job.result == {
        "scrape": {"listados": 0, "novos": 0, "total": 0, "termos": 1},
        "enrich": {"enriquecidos": 0, "total": 0},
    }


def test_manager_execute_sync_error(app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "run_scrape", boom)
    manager = runner.RunManager()
    job = runner.RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"])
    manager._jobs[1] = job

    manager.execute(app, 1)
    assert job.status == "error"
    assert job.error == "boom"


def test_manager_execute_blocked_writes_cooldown(app, monkeypatch):
    from app.scrapers.client import ScrapeBlockedError
    from app.services import scrape_block

    app.config["SCRAPER_BLOCK_COOLDOWN_MINUTES"] = 60
    app.instance_path = str(app.instance_path)

    def blocked(*a, **k):
        raise ScrapeBlockedError("bloqueado (403) em https://x")

    monkeypatch.setattr(runner, "run_scrape", blocked)
    manager = runner.RunManager()
    job = runner.RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"])
    manager._jobs[1] = job

    manager.execute(app, 1)
    assert job.status == "blocked"
    assert "403" in (job.error or "")
    assert scrape_block.get_blocked_until(app.instance_path) > time.time()


def test_run_process_missing_generation_only(app, monkeypatch):
    spec_json = json.dumps({
        "brand": "Dell", "model": "OptiPlex 7010", "form_factor": "sff",
        "cpu": "i5-8500", "ram_gb": 8, "storage_gb": 256,
        "storage_type": "ssd", "gpu": None, "confidence": 0.9,
    })

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "x1", "object": "response", "status": "completed",
            "model": "deepseek-v4-flash",
            "output": [{
                "type": "message", "id": "m1", "role": "assistant",
                "content": [{"type": "output_text", "text": spec_json}],
            }],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}},
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(handler))
    with app.app_context():
        from app.extractors.schema import AdSpec as AdSpecSchema

        app.config["DEEPSEEK_KEY"] = "test-key"
        a, _ = ad_service.upsert_raw(RawAd(
            olx_id="gen-a", title="Dell A", url="https://sp.olx.com.br/x/gen-a",
            description="Dell OptiPlex 7010 SFF i5-8500 8GB 256GB SSD",
        ))
        ad_service.save_specs(a, AdSpecSchema(brand="Dell", model="OptiPlex 7010", cpu="i5-8500"),
                              "regex+llm", cpu_family="i5", cpu_model=8500, cpu_generation=8)
        b, _ = ad_service.upsert_raw(RawAd(
            olx_id="gen-b", title="Dell B", url="https://sp.olx.com.br/x/gen-b",
            description="Dell OptiPlex i5 de 10ª geração 8GB SSD",
        ))
        ad_service.save_specs(b, AdSpecSchema(brand="Dell", model="OptiPlex 3080", cpu="core i5"),
                              "regex+llm", cpu_family="i5", cpu_model=None, cpu_generation=None)

    stats = runner.run_process(app, missing_generation=True)
    assert stats["ok"] == 1
    assert stats["falhas"] == 0

    with app.app_context():
        from app.models import Ad, AdSpec

        spec_b = AdSpec.query.join(Ad).filter(Ad.olx_id == "gen-b").first()
        assert spec_b.cpu_generation == 10  # re-processado (regex prevalece → "core i5" + gen explícita)
        spec_a = AdSpec.query.join(Ad).filter(Ad.olx_id == "gen-a").first()
        assert spec_a.cpu_generation == 8  # intocado


def test_run_process_missing_generation_limit(app, monkeypatch):
    spec_json = json.dumps({
        "brand": "Dell", "model": None, "form_factor": None,
        "cpu": "core i5", "ram_gb": None, "storage_gb": None,
        "storage_type": None, "gpu": None, "confidence": 0.5,
    })

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "x1", "object": "response", "status": "completed",
            "model": "deepseek-v4-flash",
            "output": [{
                "type": "message", "id": "m1", "role": "assistant",
                "content": [{"type": "output_text", "text": spec_json}],
            }],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}},
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(handler))
    with app.app_context():
        from app.extractors.schema import AdSpec as AdSpecSchema

        app.config["DEEPSEEK_KEY"] = "test-key"
        for i in range(3):
            ad, _ = ad_service.upsert_raw(RawAd(
                olx_id=f"lim-{i}", title=f"Dell {i}", url=f"https://sp.olx.com.br/x/lim-{i}",
                description="Dell i5 de 10ª geração",
            ))
            ad_service.save_specs(ad, AdSpecSchema(brand="Dell", cpu="core i5"),
                                  "regex+llm", cpu_family="i5", cpu_model=None, cpu_generation=None)

    stats = runner.run_process(app, missing_generation=True, limit=1)
    assert stats["ok"] == 1


def test_run_process_default_fills_missing_gen(app, monkeypatch):
    spec_json = json.dumps({
        "brand": "Dell", "model": "OptiPlex 7010", "form_factor": "sff",
        "cpu": "i5-8500", "ram_gb": 8, "storage_gb": 256,
        "storage_type": "ssd", "gpu": None, "confidence": 0.9,
    })

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "x1", "object": "response", "status": "completed",
            "model": "deepseek-v4-flash",
            "output": [{
                "type": "message", "id": "m1", "role": "assistant",
                "content": [{"type": "output_text", "text": spec_json}],
            }],
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}},
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(handler))
    with app.app_context():
        from app.extractors.schema import AdSpec as AdSpecSchema

        app.config["DEEPSEEK_KEY"] = "test-key"
        # pending (sem specs) — só esse usa o LLM
        ad_service.upsert_raw(RawAd(
            olx_id="pend-1", title="Dell P", url="https://sp.olx.com.br/x/pend-1",
            description="Dell OptiPlex 7010 SFF i5-8500 8GB 256GB SSD",
        ))
        # spec sem geração — o caminho default resolve com fill determinístico (sem LLM)
        b, _ = ad_service.upsert_raw(RawAd(
            olx_id="nog-1", title="Dell N", url="https://sp.olx.com.br/x/nog-1",
            description="Dell OptiPlex i5 de 10ª geração 8GB SSD",
        ))
        ad_service.save_specs(b, AdSpecSchema(brand="Dell", model="OptiPlex 3080", cpu="core i5"),
                              "regex+llm", cpu_family="i5", cpu_model=None, cpu_generation=None)

    # caminho default (sem flags — o que o autorun usa)
    stats = runner.run_process(app)
    assert stats["ok"] == 1          # só o pending foi pro LLM
    assert stats["fill"]["fill"] == 1

    with app.app_context():
        from app.models import Ad, AdSpec

        spec_b = AdSpec.query.join(Ad).filter(Ad.olx_id == "nog-1").first()
        assert spec_b.cpu_generation == 10  # fill (só regex) preencheu a geração


def test_run_process_default_fill_respects_limit(app, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "x1", "object": "response", "status": "completed",
            "model": "deepseek-v4-flash",
            "output": [{"type": "message", "id": "m1", "role": "assistant",
                        "content": [{"type": "output_text", "text": "{}"}], }],
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "input_tokens_details": {"cached_tokens": 0}},
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(handler))
    with app.app_context():
        from app.extractors.schema import AdSpec as AdSpecSchema

        app.config["DEEPSEEK_KEY"] = "test-key"
        app.config["PROCESS_MISSING_GEN_LIMIT"] = 1
        for i in range(3):
            ad, _ = ad_service.upsert_raw(RawAd(
                olx_id=f"lim2-{i}", title=f"Dell {i}", url=f"https://sp.olx.com.br/x/lim2-{i}",
                description="Dell i5 de 10ª geração",
            ))
            ad_service.save_specs(ad, AdSpecSchema(brand="Dell", cpu="core i5"),
                                  "regex+llm", cpu_family="i5", cpu_model=None, cpu_generation=None)

    # sem pendentes: só o fill, limitado a 1
    stats = runner.run_process(app)
    assert stats["ok"] == 0
    assert stats["fill"]["fill"] == 1


def test_run_fill_specs_restores_cpu_and_gen(app):
    from app.extractors.schema import AdSpec as AdSpecSchema

    with app.app_context():
        app.config["DEEPSEEK_KEY"] = "test-key"
        ad, _ = ad_service.upsert_raw(RawAd(
            olx_id="ry-930", title="Lenovo Tiny Thinkcentre M75Q Ryzen 5 PRO 4650GE",
            url="https://sp.olx.com.br/x/ry-930",
            description="AMD Ryzen 5 PRO 4650GE, 8 GB DDR4, SSD de 256GB PCIe NVMe",
        ))
        ad_id = ad.id
        # simula o spec degradado (cpu vazio, sem geração)
        ad_service.save_specs(ad, AdSpecSchema(brand="Lenovo", cpu=None),
                              "regex+llm", cpu_family=None, cpu_model=None, cpu_generation=None)

    stats = runner.run_fill_specs(app)
    assert stats["fill"] == 1

    with app.app_context():
        from app.models import AdSpec

        s = AdSpec.query.filter_by(ad_id=ad_id).first()
        assert s.cpu == "ryzen 5 pro 4650"
        assert s.cpu_family == "ryzen5"
        assert s.cpu_model == 4650
        assert s.cpu_generation == 4
        assert s.ram_gb == 8
        assert s.storage_gb == 256


def test_run_process_missing_generation_skips_user_disabled(app, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar o LLM")

    monkeypatch.setattr(runner, "DeepSeekClient", lambda **kw: _make_llm(handler))
    with app.app_context():
        from app.extractors.schema import AdSpec as AdSpecSchema

        app.config["DEEPSEEK_KEY"] = "test-key"
        ad, _ = ad_service.upsert_raw(RawAd(
            olx_id="gen-oc", title="Dell oculto", url="https://sp.olx.com.br/x/gen-oc",
            description="Dell i5 de 10ª geração",
        ))
        ad_service.save_specs(ad, AdSpecSchema(brand="Dell", cpu="core i5"),
                              "regex+llm", cpu_family="i5", cpu_model=None, cpu_generation=None)
        ad.user_disabled = True
        from app.extensions import db

        db.session.commit()

    stats = runner.run_process(app, missing_generation=True)
    assert stats["ok"] == 0
    assert stats["falhas"] == 0


def test_manager_execute_success_clears_block(app, monkeypatch):
    from app.services import scrape_block

    app.config["SCRAPER_BLOCK_COOLDOWN_MINUTES"] = 60
    app.instance_path = str(app.instance_path)
    scrape_block.set_blocked(app.instance_path, time.time() + 3600)

    monkeypatch.setattr(
        runner, "run_scrape", lambda *a, **k: {"listados": 0, "novos": 0, "total": 0}
    )
    manager = runner.RunManager()
    job = runner.RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"])
    manager._jobs[1] = job

    manager.execute(app, 1)
    assert job.status == "done"
    assert scrape_block.get_blocked_until(app.instance_path) == 0.0


def test_job_to_dict_done():
    job = runner.RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape"], status="done")
    data = runner.job_to_dict(job)
    assert data["status"] == "done"
    assert data["percent"] == 100


def test_job_to_dict_running():
    job = runner.RunJob(id=1, terms=["x"], region="estado-sp", steps=["scrape", "enrich"],
                        status="running", step_index=0, done=2, total=10, message="trabalhando")
    data = runner.job_to_dict(job)
    assert data["step"] == "scrape"
    assert data["percent"] == 20
    assert data["message"] == "trabalhando"

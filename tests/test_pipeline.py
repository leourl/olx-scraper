import json

import httpx

from app.extractors.llm import DeepSeekClient
from app.extractors.pipeline import extract_specs
from app.extractors.schema import AdSpec


def fake_llm(spec_json: str) -> DeepSeekClient:
    payload = {
        "id": "x1",
        "object": "response",
        "status": "completed",
        "model": "deepseek-v4-flash",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": spec_json}],
            }
        ],
        "usage": {"input_tokens": 100, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 0}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return DeepSeekClient("k", "https://api.deepseek.com", "deepseek-v4-flash", transport=httpx.MockTransport(handler))


def test_pipeline_regex_prevails_over_llm():
    # regex resolve ram=16 e ssd=512; LLM erra para 8/256
    llm = fake_llm(json.dumps({
        "brand": "Dell", "model": "OptiPlex 7050", "form_factor": "sff",
        "cpu": "i5-8500", "ram_gb": 8, "storage_gb": 256, "storage_type": "ssd",
        "gpu": None, "confidence": 0.5,
    }))
    spec, usage, method, cpu_family, cpu_model, cpu_generation = extract_specs(
        "OptiPlex 7050 SFF i5-8500 16GB RAM 512GB SSD", "16GB RAM DDR4 512GB SSD", llm
    )
    assert method == "regex+llm"
    assert spec.ram_gb == 16
    assert spec.storage_gb == 512
    assert spec.storage_type == "ssd"
    assert spec.brand == "Dell"
    assert spec.confidence >= 0.7
    assert cpu_family == "i5"
    assert cpu_model == 8500
    assert cpu_generation == 8
    assert usage.output_tokens == 50


def test_pipeline_llm_only_when_no_regex():
    llm = fake_llm(json.dumps({
        "brand": "Lenovo", "model": "ThinkCentre M700", "form_factor": "mini",
        "cpu": "i3-6100", "ram_gb": None, "storage_gb": None, "storage_type": None,
        "gpu": None, "confidence": 0.8,
    }))
    spec, _, method, cpu_family, cpu_model, cpu_generation = extract_specs(
        "Lenovo ThinkCentre M700", "computador seminovo", llm
    )
    assert method == "regex+llm"  # marca/modelo resolvidos por regex agora
    assert spec.brand == "Lenovo"
    assert spec.ram_gb is None
    assert cpu_family == "i3"
    assert cpu_model == 6100
    assert cpu_generation == 6


def test_pipeline_llm_failure_returns_none():
    client = DeepSeekClient(
        "k", "https://api.deepseek.com", "deepseek-v4-flash",
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={}, request=r)),
    )
    spec, usage, method, cpu_family, cpu_model, cpu_generation = extract_specs("Dell", "desc", client)
    assert spec is None
    assert usage.input_tokens == 0
    assert method == ""
    assert cpu_family is None
    assert cpu_model is None
    assert cpu_generation is None


def test_pipeline_explicit_generation_without_model():
    llm = fake_llm(json.dumps({
        "brand": "Dell", "model": "OptiPlex 3080", "form_factor": "mini",
        "cpu": "core i5", "ram_gb": 8, "storage_gb": 118, "storage_type": "ssd",
        "gpu": None, "confidence": 0.9,
    }))
    spec, _, method, cpu_family, cpu_model, cpu_generation = extract_specs(
        "Mini Desktop Dell i5 10ª",
        "Dell OptiPlex 3080, Intel Core i5 de 10ª geração, 8GB, SSD 118GB",
        llm,
    )
    assert cpu_family == "i5"
    assert cpu_model is None
    assert cpu_generation == 10


def test_pipeline_explicit_generation_requires_cpu():
    llm = fake_llm(json.dumps({
        "brand": "Dell", "model": None, "form_factor": None,
        "cpu": None, "ram_gb": None, "storage_gb": None, "storage_type": None,
        "gpu": None, "confidence": 0.5,
    }))
    _, _, _, cpu_family, cpu_model, cpu_generation = extract_specs(
        "Computador", "Gabinete compatível com 10ª geração de processadores", llm
    )
    assert cpu_family is None
    assert cpu_model is None
    assert cpu_generation is None


def test_pipeline_model_still_derives_generation():
    llm = fake_llm(json.dumps({
        "brand": "Dell", "model": "OptiPlex 7050", "form_factor": "sff",
        "cpu": "i5-10500", "ram_gb": 8, "storage_gb": 256, "storage_type": "ssd",
        "gpu": None, "confidence": 0.9,
    }))
    _, _, _, cpu_family, cpu_model, cpu_generation = extract_specs(
        "OptiPlex 7050 i5-10500 10ª geração", "16GB 512GB SSD", llm
    )
    assert cpu_model == 10500
    assert cpu_generation == 10

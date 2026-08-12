import json

import httpx
import pytest

from app.extractors.llm import RETRY_NOTE, DeepSeekClient, LlmError

VALID_SPEC = {
    "brand": "Dell",
    "model": "OptiPlex 7050",
    "form_factor": "sff",
    "cpu": "i5-8500",
    "ram_gb": 16,
    "storage_gb": 512,
    "storage_type": "ssd",
    "gpu": None,
    "year": None,
    "condition": "usado",
    "confidence": 0.9,
}


def make_client(payload, status=200, max_retries=0) -> DeepSeekClient:
    """Monta client com MockTransport.

    `payload`/`status` podem ser listas (uma resposta por chamada, repetindo a
    última se exceder). O client ganha `_test_calls` (contador de requests) e
    `_test_bodies` (bodies enviados) para inspeção nos testes.
    """
    responses = payload if isinstance(payload, list) else [payload]
    statuses = status if isinstance(status, list) else [status]
    calls = {"n": 0}
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        bodies.append(body)
        assert request.headers["Authorization"] == "Bearer test-key"
        assert body["model"] == "deepseek-v4-flash"
        assert body["reasoning"]["effort"] == "none"
        fmt = body["text"]["format"]
        assert fmt["type"] == "json_schema"
        assert "schema" in fmt
        idx = min(calls["n"] - 1, len(responses) - 1)
        return httpx.Response(
            statuses[min(calls["n"] - 1, len(statuses) - 1)],
            json=responses[idx],
            request=request,
        )

    transport = httpx.MockTransport(handler)
    client = DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        max_retries=max_retries,
        transport=transport,
    )
    client._test_calls = calls
    client._test_bodies = bodies
    return client


def response_with(text: str, status: str = "completed") -> dict:
    return {
        "id": "x1",
        "object": "response",
        "status": status,
        "model": "deepseek-v4-flash",
        "output": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "content": [{"type": "reasoning_text", "text": "..."}],
            },
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {
            "input_tokens": 800,
            "output_tokens": 150,
            "input_tokens_details": {"cached_tokens": 100},
        },
    }


def test_extract_specs_ok():
    client = make_client(response_with(json.dumps(VALID_SPEC)))
    spec, usage = client.extract_specs("título", "descrição")
    assert spec.brand == "Dell"
    assert spec.ram_gb == 16
    assert spec.form_factor == "sff"
    assert usage.input_tokens == 800
    assert usage.output_tokens == 150
    assert usage.cached_tokens == 100
    assert usage.retries == 0
    assert client._test_calls["n"] == 1


def test_extract_specs_returns_nulls_when_unknown():
    spec_json = json.dumps({
        "brand": "Dell",
        "model": None,
        "form_factor": None,
        "cpu": None,
        "ram_gb": None,
        "storage_gb": None,
        "storage_type": None,
        "gpu": None,
        "year": None,
        "condition": None,
        "confidence": 0.3,
    })
    client = make_client(response_with(spec_json))
    spec, _ = client.extract_specs("Dell", "computador usado")
    assert spec.model is None
    assert spec.ram_gb is None
    assert spec.confidence == 0.3


def test_extract_specs_invalid_json_raises():
    client = make_client(response_with("não é json"))
    with pytest.raises(LlmError):
        client.extract_specs("título", "descrição")
    assert client._test_calls["n"] == 1


def test_extract_specs_http_error():
    client = make_client({"error": "overloaded"}, status=429)
    with pytest.raises(LlmError):
        client.extract_specs("título", "descrição")


def test_retries_then_succeeds():
    client = make_client([response_with("não é json"), response_with(json.dumps(VALID_SPEC))], max_retries=1)
    spec, usage = client.extract_specs("título", "descrição")
    assert spec.brand == "Dell"
    assert usage.retries == 1
    assert usage.input_tokens == 1600
    assert usage.output_tokens == 300
    assert usage.cached_tokens == 200
    assert client._test_calls["n"] == 2


def test_retry_appends_corrective_note():
    client = make_client([response_with("não é json"), response_with(json.dumps(VALID_SPEC))], max_retries=1)
    client.extract_specs("título", "descrição")
    bodies = client._test_bodies
    assert len(bodies) == 2
    assert bodies[0]["instructions"] == bodies[1]["instructions"]
    assert RETRY_NOTE not in bodies[0]["input"]
    assert RETRY_NOTE in bodies[1]["input"]
    assert bodies[1]["input"].startswith(bodies[0]["input"])


def test_max_retries_calls_limit():
    client = make_client(response_with("não é json"), max_retries=2)
    with pytest.raises(LlmError):
        client.extract_specs("título", "descrição")
    assert client._test_calls["n"] == 3


def test_http_error_not_retried():
    client = make_client({"error": "overloaded"}, status=429, max_retries=2)
    with pytest.raises(LlmError):
        client.extract_specs("título", "descrição")
    assert client._test_calls["n"] == 1


def test_failure_carries_usage():
    from app.extractors.pipeline import extract_specs

    client = make_client(response_with("não é json"), max_retries=2)
    spec, usage, method, *_ = extract_specs("título", "descrição", client)
    assert spec is None
    assert usage.input_tokens == 2400
    assert usage.cached_tokens == 300
    assert usage.retries == 2
    assert method == ""


def test_missing_key_raises():
    with pytest.raises(ValueError):
        DeepSeekClient("", "https://api.deepseek.com", "deepseek-v4-flash")

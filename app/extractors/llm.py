"""Cliente da API de Responses da DeepSeek (deepseek-v4-flash)."""
import json
import logging
from dataclasses import dataclass

import httpx

from app.extractors.schema import AdSpec, specs_json_schema

log = logging.getLogger(__name__)

SCHEMA_NAME = "ad_specs"


class LlmError(RuntimeError):
    """Falha na chamada/parse da LLM (ad fica pendente para reprocessar)."""

    def __init__(self, message: str, usage: "LlmUsage | None" = None):
        super().__init__(message)
        self.usage = usage


@dataclass
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    retries: int = 0

    def __add__(self, other: "LlmUsage") -> "LlmUsage":
        return LlmUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            retries=self.retries + other.retries,
        )


RETRY_NOTE = (
    "Sua resposta anterior não passou na validação: o JSON estava inválido "
    "(provavelmente você ecoou a definição do schema — 'description', 'properties', "
    "'title', 'anyOf' — ou adicionou caracteres extras). Responda APENAS com o "
    "objeto JSON preenchido com os valores dos campos (ex.: "
    '{"brand": "Dell", "ram_gb": 16}), sem mais nada.'
)


INSTRUCTIONS = (
    "Você é um assistente que extrai informações técnicas de anúncios de "
    "computadores usados (Dell OptiPlex, Lenovo ThinkCentre etc.) publicados "
    "na OLX. Receba o título e a descrição do anúncio e preencha o JSON no "
    "schema informado. Regras estritas:\n"
    "1. Use null para qualquer informação que não esteja presente ou que seja ambígua. NUNCA invente valores.\n"
    "2. Se o texto citar um campo como 'confirmado', mantenha o valor informado.\n"
    "3. Normalize marca (Dell, Lenovo, HP...), modelo (ex.: 'optiplex 7050' -> 'OptiPlex 7050') e fator de forma de forma razoável.\n"
    "4. ram_gb e storage_gb em gigabytes (inteiros). storage_type: ssd, hdd ou nvme.\n"
    "5. confidence: número entre 0 e 1 representando sua certeza sobre a extração como um todo (1 = certeza alta).\n"
    "6. Responda unicamente com o objeto JSON preenchido — sem o schema, sem texto extra, sem comentários.\n"
    "Responda somente com o JSON válido no schema."
)


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        reasoning_effort: str = "none",
        max_chars: int = 1500,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 0,
    ):
        if not api_key:
            raise ValueError("DEEPSEEK_KEY não configurada no .env")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._effort = reasoning_effort
        self._max_chars = max_chars
        self._max_retries = max_retries
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    def extract_specs(
        self, title: str, description: str | None, confirmed: AdSpec | None = None
    ) -> tuple[AdSpec, LlmUsage]:
        confirmed = confirmed or AdSpec()
        confirmed_fields = confirmed.model_dump(exclude_unset=True, exclude_none=True)
        base_input = self._build_input(title, description, confirmed_fields)

        total_usage = LlmUsage()
        for attempt in range(self._max_retries + 1):
            input_text = base_input
            if attempt > 0:
                input_text = f"{base_input}\n\n{RETRY_NOTE}"

            body = {
                "model": self._model,
                "instructions": INSTRUCTIONS,
                "input": input_text,
                "reasoning": {"effort": self._effort},
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": SCHEMA_NAME,
                        "schema": specs_json_schema(),
                    }
                },
            }

            try:
                resp = self._client.post(f"{self._base_url}/responses", json=body)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                raise LlmError(f"erro HTTP na LLM: {type(e).__name__}: {e}") from e

            total_usage = total_usage + self._parse_usage(data.get("usage"))
            spec = self._parse_output(data)
            if spec is not None:
                return spec, total_usage
            if attempt < self._max_retries:
                total_usage.retries += 1
                log.warning(
                    "LLM: JSON inválido (tentativa %d/%d), retentando com nota corretiva",
                    attempt + 1,
                    self._max_retries + 1,
                )

        raise LlmError(
            f"resposta sem content/mensagem válida da LLM após {self._max_retries + 1} tentativas",
            usage=total_usage,
        )

    def _build_input(self, title: str, description: str | None, confirmed: dict) -> str:
        desc = (description or "").strip()
        if self._max_chars and len(desc) > self._max_chars:
            desc = desc[: self._max_chars] + "…"
        confirmed_note = (
            f"\n\nCampos já confirmados por método determinístico (não alterar): "
            f"{json.dumps(confirmed, ensure_ascii=False)}"
            if confirmed
            else ""
        )
        return f"Título: {title}\n\nDescrição:\n{desc}{confirmed_note}"

    @staticmethod
    def _parse_output(data: dict) -> AdSpec | None:
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            content = item.get("content") or []
            if not content:
                continue
            text = content[0].get("text")
            if not text:
                continue
            try:
                return AdSpec.model_validate_json(text)
            except Exception as e:
                log.warning("JSON da LLM inválido (%s): %.200s", e, text)
                return None
        return None

    @staticmethod
    def _parse_usage(usage: dict | None) -> LlmUsage:
        if not usage:
            return LlmUsage()
        details = usage.get("input_tokens_details") or {}
        return LlmUsage(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_tokens=int(details.get("cached_tokens", 0)),
        )

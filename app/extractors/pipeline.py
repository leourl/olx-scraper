"""Orquestra: regex primeiro (determinístico), LLM preenche o resto."""
from app.extractors.llm import DeepSeekClient, LlmError, LlmUsage
from app.extractors.regex import RegexResult, extract_regex, generation_from, normalize_cpu
from app.extractors.schema import AdSpec


def _gen_in_family(family: str | None, gen: int) -> bool:
    """Faixa plausível de geração por família (mesma regra do generation_from)."""
    if family and family.startswith("ryzen"):
        return 1 <= gen <= 9
    return 1 <= gen <= 14


def extract_specs(
    title: str, description: str | None, client: DeepSeekClient
) -> tuple[AdSpec | None, LlmUsage, str, str | None, int | None, int | None]:
    """Retorna (spec, uso, método, cpu_family, cpu_model, cpu_generation)."""
    text = f"{title}\n\n{description or ''}"
    regex = extract_regex(text)
    confirmed = regex.as_specs()

    try:
        llm, usage = client.extract_specs(title, description, confirmed=confirmed)
        spec = _merge(regex, llm)
    except LlmError as e:
        return None, (e.usage or LlmUsage()), "", None, None, None
    except Exception:
        return None, LlmUsage(), "", None, None, None
    if regex.resolved:
        method = "regex+llm"
    else:
        method = "llm"
    cpu_family, cpu_model = normalize_cpu(spec.cpu)
    cpu_generation = generation_from(cpu_family, cpu_model)
    if (
        cpu_generation is None
        and spec.cpu
        and regex.cpu_generation is not None
        and _gen_in_family(cpu_family, regex.cpu_generation)
    ):
        cpu_generation = regex.cpu_generation
    return spec, usage, method, cpu_family, cpu_model, cpu_generation


def _merge(regex: RegexResult, llm: AdSpec) -> AdSpec:
    data = llm.model_dump()
    for field in regex.resolved:
        value = getattr(regex, field)
        if value is not None:
            data[field] = value
    for field in ("ram_gb", "storage_gb"):
        value = data.get(field)
        if value is not None and value <= 0:
            data[field] = None
    if regex.resolved and data.get("confidence", 0) < 0.7:
        data["confidence"] = 0.7
    return AdSpec(**data)

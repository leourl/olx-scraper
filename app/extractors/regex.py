"""Extração determinística de specs (rápida e barata) para padrões claros."""
import re
from dataclasses import dataclass, field

from app.extractors.schema import AdSpec

_RAM_RE = re.compile(
    r"(\d+)\s*gb(?:\s+de)?\s*(?:ram|mem|memória|memoria)"
    r"|\b(?:ram|memória|memoria)\s+(\d+)\s*gb"
    r"|(\d+)\s*gb\s+ddr\d",
    re.IGNORECASE,
)
_SSD_RE = re.compile(
    r"(?P<size1>\d+)\s*(?P<unit1>gb|tb)\s*(?:de\s*)?(?P<kind1>ssd|nvme)"
    r"|\b(?P<kind2>ssd|nvme)\s*(?:de\s*)?(?P<size2>\d+)\s*(?P<unit2>gb|tb)",
    re.IGNORECASE,
)
_HDD_RE = re.compile(
    r"(?P<size1>\d+)\s*(?P<unit1>gb|tb)\s*(?:de\s*)?(?:hd|hdd)"
    r"|\b(?:hd|hdd)\s*(?:de\s*)?(?P<size2>\d+)\s*(?P<unit2>gb|tb)",
    re.IGNORECASE,
)
_CPU_RE = re.compile(
    r"\b(i[3579][\s-]?\d{3,5}(?:\s*\([^)]*gen\))?)"
    r"|\b(ryzen\s?[3579](?:\s+pro)?\s*\d{3,4})",
    re.IGNORECASE,
)
_CPU_CORE_RE = re.compile(r"\b(core\s+i[3579])\b", re.IGNORECASE)
_CPU_BARE_RE = re.compile(r"\b(i[3579])\b|\b(ryzen\s?[3579])\b", re.IGNORECASE)
_GEN_RE = re.compile(
    r"(?<!\w)(?P<n1>1[0-4]|[1-9])\s*(?:ª|º|a|o|st|nd|rd|th)?\.?\s*(?:geração|geracao|generation|gen)\b"
    r"|\b(?:geração|geracao|generation|gen)\s+(?P<n2>1[0-4]|[1-9])(?!\d)",
    re.IGNORECASE,
)
_BRAND_RE = re.compile(r"\b(dell|lenovo|hp|hewlett[- ]packard|ibm|asus|acer|apple|samsung|positivo|fujitsu|lg)\b", re.IGNORECASE)
_MODEL_RE = re.compile(r"\b(optiplex\s*[- ]?\d{3,4}|thinkcentre\s*[- ]?[a-z]\d{2,4})\b", re.IGNORECASE)
_FORM_FACTOR_RE = [
    ("notebook", re.compile(r"\b(notebook|laptop)\b", re.IGNORECASE)),
    ("sff", re.compile(r"\b(sff)\b", re.IGNORECASE)),
    ("tower", re.compile(r"\b(tower|torre)\b", re.IGNORECASE)),
    ("all-in-one", re.compile(r"\b(all\s?in\s?one|aio)\b", re.IGNORECASE)),
    ("mini", re.compile(r"\b(mff|micro|minitorre|mini torre|mini\b)", re.IGNORECASE)),
    ("desktop", re.compile(r"\bdesktop\b", re.IGNORECASE)),
]


@dataclass
class RegexResult:
    ram_gb: int | None = None
    storage_gb: int | None = None
    storage_type: str | None = None
    cpu: str | None = None
    form_factor: str | None = None
    brand: str | None = None
    model: str | None = None
    cpu_generation: int | None = None
    resolved: set[str] = field(default_factory=set)

    def as_specs(self) -> AdSpec:
        return AdSpec(
            ram_gb=self.ram_gb,
            storage_gb=self.storage_gb,
            storage_type=self.storage_type,
            cpu=self.cpu,
            form_factor=self.form_factor,
            brand=self.brand,
            model=self.model,
            confidence=0.9,
        )


def _normalize_cpu(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _normalize_model(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"(?i)optiplex", "OptiPlex", value)
    value = re.sub(r"(?i)thinkcentre", "ThinkCentre", value)
    return value.replace("-", " ")


def normalize_cpu(cpu: str | None) -> tuple[str | None, int | None]:
    """'i5-8500' -> ('i5', 8500); 'core i3' -> ('i3', None);
    'ryzen 5 2600' -> ('ryzen5', 2600); 'Core 2 Duo E7500' -> ('core2', 7500)."""
    if not cpu:
        return None, None
    text = cpu.lower()

    ry = re.search(r"ryzen\s?([3579])(?:\s+pro)?\s*(\d{3,4})?", text)
    if ry:
        return f"ryzen{ry.group(1)}", (int(ry.group(2)) if ry.group(2) else None)

    if re.search(r"athlon", text):
        m = re.search(r"(\d{3,4})", text)
        return "athlon", (int(m.group(1)) if m else None)

    if re.search(r"pentium", text):
        m = re.search(r"\bg(\d{3,4})\b", text)
        return "pentium", (int(m.group(1)) if m else None)

    if re.search(r"celeron", text):
        return "celeron", None

    duo = re.search(r"core\s*2\s*duo|duo|dual[-\s]?core", text)
    if duo:
        m = re.search(r"\be(\d{3,5})\b", text)
        return "core2", (int(m.group(1)) if m else None)

    it = re.search(r"i([3579])(?:\s?[-]?\s?(\d{3,5}))?", text)
    if it:
        return f"i{it.group(1)}", (int(it.group(2)) if it.group(2) else None)

    return None, None


def generation_from(family: str | None, model: int | None) -> int | None:
    """Geração da CPU. Intel: 6100→6, 10100→10, 13500→13. Ryzen: série (5600→5).
    Modelos < 1000 ou fora de faixa plausível (ex.: 'i5 18500' de vendedor) → None."""
    if not family or not model or model < 1000:
        return None
    gen = model // 1000
    if family.startswith("i"):
        return gen if 1 <= gen <= 14 else None
    if family.startswith("ryzen"):
        return gen if 1 <= gen <= 9 else None
    return None


def extract_regex(text: str) -> RegexResult:
    result = RegexResult()

    m = _RAM_RE.search(text)
    if m:
        value = int(m.group(1) or m.group(2) or m.group(3))
        if value > 0:
            result.ram_gb = value
            result.resolved.add("ram_gb")

    storage_m = _SSD_RE.search(text)
    kind = None
    if storage_m:
        kind = (storage_m.group("kind1") or storage_m.group("kind2")).lower()
    else:
        storage_m = _HDD_RE.search(text)
        if storage_m:
            kind = "hdd"
    if storage_m:
        size = int(storage_m.group("size1") or storage_m.group("size2"))
        unit = (storage_m.group("unit1") or storage_m.group("unit2")).lower()
        if unit == "tb":
            size *= 1000
        if size > 0:
            result.storage_gb = size
            result.storage_type = kind
            result.resolved.update(["storage_gb", "storage_type"])

    cpu_m = _CPU_RE.search(text)
    if cpu_m:
        result.cpu = _normalize_cpu(cpu_m.group(1) or cpu_m.group(2))
        result.resolved.add("cpu")
    else:
        core_m = _CPU_CORE_RE.search(text)
        if core_m:
            result.cpu = _normalize_cpu(core_m.group(1))
            result.resolved.add("cpu")
        else:
            bare = _CPU_BARE_RE.search(text)
            if bare:
                result.cpu = _normalize_cpu(bare.group(1) or bare.group(2))
                result.resolved.add("cpu")

    brand_m = _BRAND_RE.search(text)
    if brand_m:
        result.brand = brand_m.group(1).replace("-", " ").title()
        result.resolved.add("brand")
    model_m = _MODEL_RE.search(text)
    if model_m:
        result.model = _normalize_model(model_m.group(1))
        result.resolved.add("model")

    gen_m = _GEN_RE.search(text)
    if gen_m:
        result.cpu_generation = int(gen_m.group("n1") or gen_m.group("n2"))

    for name, pattern in _FORM_FACTOR_RE:
        if pattern.search(text):
            result.form_factor = name
            result.resolved.add("form_factor")
            break

    return result

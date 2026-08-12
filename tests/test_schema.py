import pytest
from pydantic import ValidationError

from app.extractors.schema import AdSpec, specs_json_schema


def test_specs_json_schema_has_fields():
    schema = specs_json_schema()
    assert "ram_gb" in schema["properties"]
    enums = [
        a.get("enum")
        for a in schema["properties"]["form_factor"]["anyOf"]
        if "enum" in a
    ]
    assert enums == [["mini", "sff", "tower", "notebook", "all-in-one", "desktop"]]


def test_ad_spec_allows_nulls():
    spec = AdSpec()
    assert spec.ram_gb is None
    assert spec.brand is None
    assert spec.confidence == 0.5


def test_ad_spec_valid():
    spec = AdSpec(brand="Dell", model="OptiPlex 7050", ram_gb=16, storage_gb=512, confidence=0.9)
    assert spec.ram_gb == 16


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ram_gb": 0},
        {"ram_gb": -4},
        {"storage_gb": 0},
        {"form_factor": "gigante"},
        {"storage_type": "magnetico"},
        {"confidence": 1.5},
    ],
)
def test_ad_spec_rejects_invalid(kwargs):
    with pytest.raises(ValidationError):
        AdSpec(**kwargs)

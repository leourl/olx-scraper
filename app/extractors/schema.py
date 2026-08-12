from typing import Literal

from pydantic import BaseModel, Field

FormFactor = Literal["mini", "sff", "tower", "notebook", "all-in-one", "desktop"]
StorageType = Literal["ssd", "hdd", "nvme"]


class AdSpec(BaseModel):
    """Specs estruturados de um anúncio. null = informação não informada."""

    brand: str | None = None
    model: str | None = None
    form_factor: FormFactor | None = None
    cpu: str | None = None
    ram_gb: int | None = Field(default=None, gt=0, le=1024)
    storage_gb: int | None = Field(default=None, gt=0, le=102400)
    storage_type: StorageType | None = None
    gpu: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


def specs_json_schema() -> dict:
    """JSON Schema (OpenAPI) para o formato json_schema do endpoint /responses."""
    return AdSpec.model_json_schema()

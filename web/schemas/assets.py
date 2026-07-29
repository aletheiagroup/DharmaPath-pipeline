"""web/schemas/assets.py"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


AssetCategory = Literal[
    "characters", "locations", "weapons", "animals",
    "architecture", "festivals", "symbols", "clothing", "loras",
]


class AgeStateSchema(BaseModel):
    reference_image: str | None = None
    reference_image_full: str | None = None
    candidates: list[str] = []
    appears_arcs: list[int] = []
    is_approved: bool = False


class AssetResponse(BaseModel):
    asset_id: str
    name: str
    category: AssetCategory
    description: str
    status: str
    first_appears: str
    ip_adapter_weight: float
    default_state: str
    states: dict[str, AgeStateSchema]
    tags: list[str] = []
    thumbnail_url: str | None = None
    prompt_template: str | None = None
    lora_path: str | None = None
    lora_strength: float | None = None


class CreateAssetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    category: AssetCategory
    description: str = Field(..., min_length=1, max_length=1000)
    first_appears: str
    ip_adapter_weight: float = Field(0.65, ge=0.0, le=1.0)
    tags: list[str] = []
    lora_path: str | None = None
    lora_strength: float | None = None


class AssetSearchResponse(BaseModel):
    results: list[AssetResponse]
    total: int
    query: str
    category: AssetCategory | None = None

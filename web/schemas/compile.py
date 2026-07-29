"""web/schemas/compile.py"""
from __future__ import annotations
from pydantic import BaseModel, Field


class CompileResult(BaseModel):
    chapter_id: str
    strip_path: str
    strip_height_px: int
    strip_width_px: int = 800
    panels_included: int
    panels_skipped: int


class SliceResult(BaseModel):
    chapter_id: str
    episodes: list[dict]   # [{episode_number, path, height_px}]
    total_episodes: int


class PublishResult(BaseModel):
    chapter_id: str
    cloud_urls: list[str]
    storage_provider: str  # "r2" | "gcs"

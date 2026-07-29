"""web/schemas/generation.py"""
from __future__ import annotations
from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    chapter_id: str
    upload: bool = True
    max_failure_pct_auto: float = Field(5.0, ge=0.0, le=100.0)
    max_failure_pct_degraded: float = Field(15.0, ge=0.0, le=100.0)


class RunSummary(BaseModel):
    run_id: str
    chapter_id: str
    status: str
    started_at: str
    completed_at: str | None = None
    panels_total: int
    panels_completed: int
    panels_failed: int
    progress_pct: float
    errors: list[str]


class RunDetail(RunSummary):
    panel_logs: list[dict]
    result: dict | None = None


class PanelProgressEvent(BaseModel):
    type: str
    timestamp: str
    panel_id: str | None = None
    panel_number: int | None = None
    completed: int | None = None
    total: int | None = None
    elapsed_s: float | None = None
    error: str | None = None
    message: str | None = None

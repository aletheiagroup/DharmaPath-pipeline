"""web/schemas/panels.py"""
from __future__ import annotations
from pydantic import BaseModel, Field


class PanelVersionSchema(BaseModel):
    version_id: str
    version_number: int
    image_path: str | None = None
    cloud_url: str | None = None
    positive_prompt: str = ""
    seed: int | None = None
    cfg: float | None = None
    steps: int | None = None
    sampler: str | None = None
    model: str | None = None
    created_at: str


class QCIssue(BaseModel):
    severity: str   # "error" | "warning"
    description: str


class PanelReviewSchema(BaseModel):
    review_id: str
    chapter_id: str
    panel_id: str
    panel_number: int
    status: str
    active_version: int
    versions: list[PanelVersionSchema]
    suggestion: str | None = None
    reviewer_note: str | None = None
    qc_score: int | None = None
    qc_issues: list[QCIssue] = []
    human_required: bool = False
    approved_at: str | None = None
    updated_at: str
    # From screenplay
    beat: str
    size: str
    shot_type: str
    characters: list[str]
    scene: str
    action: str


class ApproveRequest(BaseModel):
    reviewer_note: str | None = None


class RejectRequest(BaseModel):
    reviewer_note: str | None = Field(None, description="Reason for rejection.")


class RegenerateRequest(BaseModel):
    suggestion: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Natural language description of the change. e.g. 'Make Rama smile slightly.'",
    )
    reviewer_note: str | None = None


class BulkApproveRequest(BaseModel):
    panel_ids: list[str] = Field(..., min_length=1)


class BulkRegenerateRequest(BaseModel):
    panel_ids: list[str] = Field(..., min_length=1)
    suggestion: str = Field(..., min_length=3, max_length=500)


class RestoreVersionRequest(BaseModel):
    version_number: int = Field(..., ge=1)

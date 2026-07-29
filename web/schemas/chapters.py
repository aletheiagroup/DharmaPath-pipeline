"""web/schemas/chapters.py"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class ChapterSummary(BaseModel):
    chapter_id: str
    path: str
    arc: str
    title: str
    description: str
    arc_number: int
    lesson_id: str | None = None
    status: str                      # "draft" | "in_review" | "compiled" | "published"
    total_panels: int
    approved_count: int
    last_generated: str | None = None
    screenplay_path: str


class ChapterDetail(ChapterSummary):
    """Full chapter detail, including panel list."""
    panels: list[dict]


class CreateChapterRequest(BaseModel):
    """Payload when uploading a new screenplay via JSON body."""
    screenplay: dict = Field(..., description="Full screenplay JSON matching the Screenplay schema.")


class ValidationErrorItem(BaseModel):
    panel_id: str
    rule_name: str
    severity: Literal["error", "warning"]
    message: str


class ValidateScreenplayResponse(BaseModel):
    passed: bool
    summary: str
    errors: list[ValidationErrorItem]
    warnings: list[ValidationErrorItem]

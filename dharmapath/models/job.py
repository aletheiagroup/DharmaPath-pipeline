"""
dharmapath/models/job.py

Pydantic v2 models for generation job tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel


class JobStatus(str, Enum):
    """Lifecycle states for a generation job."""
    queued = "queued"
    generating = "generating"
    uploading = "uploading"
    assembling = "assembling"
    exporting = "exporting"
    complete = "complete"
    failed = "failed"


class GenerationJob(BaseModel):
    """
    Tracks a single panel's generation job through ComfyUI.
    Created by ChapterRunner for each panel before queuing.
    """

    panel_id: str
    chapter_id: str
    prompt_id: str | None = None
    """ComfyUI prompt_id returned by POST /prompt. Set after queuing."""

    status: JobStatus = JobStatus.queued
    output_path: str | None = None
    """Local file path once the panel image has been downloaded."""

    r2_url: str | None = None
    """R2 URL once uploaded."""

    human_required: bool = False
    """Pre-flagged by screenplay (impact panels) or by DWPose anomaly detection."""

    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class BatchJob(BaseModel):
    """
    Tracks an entire chapter's generation run.
    Contains all individual GenerationJobs.
    """

    chapter_id: str
    screenplay_path: str
    status: JobStatus = JobStatus.queued
    jobs: list[GenerationJob] = []
    created_at: datetime = datetime.utcnow()
    completed_at: datetime | None = None

    @property
    def total_panels(self) -> int:
        return len(self.jobs)

    @property
    def completed_panels(self) -> int:
        return sum(1 for j in self.jobs if j.status == JobStatus.complete)

    @property
    def failed_panels(self) -> int:
        return sum(1 for j in self.jobs if j.status == JobStatus.failed)

    @property
    def progress_pct(self) -> float:
        if self.total_panels == 0:
            return 0.0
        return round((self.completed_panels / self.total_panels) * 100, 1)


@dataclass
class RunResult:
    """
    Final result returned by ChapterRunner.run().
    Contains paths, R2 URLs, flagged panels, and any errors.
    """

    chapter_id: str
    success: bool
    panels_generated: int
    panels_flagged_human: list[str] = field(default_factory=list)
    """List of panel_ids that require human review (impact panels + DWPose flagged)."""

    episode_paths: list[str] = field(default_factory=list)
    """Local paths to exported episode JPG files."""

    r2_urls: list[str] = field(default_factory=list)
    """R2 URLs of uploaded episode files."""

    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "success": self.success,
            "panels_generated": self.panels_generated,
            "panels_flagged_human": self.panels_flagged_human,
            "episode_paths": self.episode_paths,
            "r2_urls": self.r2_urls,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
        }

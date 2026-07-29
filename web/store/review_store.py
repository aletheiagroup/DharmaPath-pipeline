"""
web/store/review_store.py

Abstract ReviewStore interface + MemoryReviewStore.

Owns all panel review state:
  - approval / rejection status
  - suggestions (natural language regeneration requests)
  - reviewer notes
  - QC scores

Separated from GenerationJob intentionally — approvals are an editorial
decision, not a pipeline state.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ReviewStatus(str, Enum):
    needs_review  = "needs_review"
    approved      = "approved"
    rejected      = "rejected"
    needs_regen   = "needs_regen"
    regenerating  = "regenerating"


@dataclass
class PanelVersion:
    version_id: str
    panel_id: str
    chapter_id: str
    version_number: int
    image_path: str | None = None
    cloud_url: str | None = None
    positive_prompt: str = ""
    negative_prompt: str = ""
    seed: int | None = None
    cfg: float | None = None
    steps: int | None = None
    sampler: str | None = None
    model: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "version_number": self.version_number,
            "image_path": self.image_path,
            "cloud_url": self.cloud_url,
            "positive_prompt": self.positive_prompt,
            "seed": self.seed,
            "cfg": self.cfg,
            "steps": self.steps,
            "sampler": self.sampler,
            "model": self.model,
            "created_at": self.created_at,
        }


@dataclass
class PanelReview:
    review_id: str
    chapter_id: str
    panel_id: str
    status: ReviewStatus = ReviewStatus.needs_review
    active_version: int = 1
    versions: list[PanelVersion] = field(default_factory=list)
    suggestion: str | None = None
    reviewer_note: str | None = None
    qc_score: int | None = None
    qc_issues: list[dict[str, str]] = field(default_factory=list)
    human_required: bool = False
    approved_at: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "chapter_id": self.chapter_id,
            "panel_id": self.panel_id,
            "status": self.status.value,
            "active_version": self.active_version,
            "versions": [v.to_dict() for v in self.versions],
            "suggestion": self.suggestion,
            "reviewer_note": self.reviewer_note,
            "qc_score": self.qc_score,
            "qc_issues": self.qc_issues,
            "human_required": self.human_required,
            "approved_at": self.approved_at,
            "updated_at": self.updated_at,
        }


# ── Abstract interface ────────────────────────────────────────────────────────

class ReviewStore(ABC):

    @abstractmethod
    async def initialise_chapter(self, chapter_id: str, panel_ids: list[str]) -> None:
        """Create default PanelReview records for all panels in a chapter."""
        ...

    @abstractmethod
    async def get_panel_review(self, chapter_id: str, panel_id: str) -> PanelReview | None:
        ...

    @abstractmethod
    async def list_panel_reviews(self, chapter_id: str) -> list[PanelReview]:
        ...

    @abstractmethod
    async def update_review(self, chapter_id: str, panel_id: str, **kwargs: Any) -> PanelReview:
        ...

    @abstractmethod
    async def add_version(self, chapter_id: str, panel_id: str, version: PanelVersion) -> PanelReview:
        ...

    @abstractmethod
    async def get_versions(self, chapter_id: str, panel_id: str) -> list[PanelVersion]:
        ...

    @abstractmethod
    async def delete_version(self, chapter_id: str, panel_id: str, version_number: int) -> None:
        ...

    @abstractmethod
    async def bulk_approve(self, chapter_id: str, panel_ids: list[str]) -> int:
        """Returns count of actually updated panels."""
        ...

    @abstractmethod
    async def approved_count(self, chapter_id: str) -> int:
        ...


# ── MemoryReviewStore ─────────────────────────────────────────────────────────

class MemoryReviewStore(ReviewStore):
    """Thread-safe in-memory review store. Swap for DatabaseReviewStore in production."""

    def __init__(self) -> None:
        # {chapter_id: {panel_id: PanelReview}}
        self._reviews: dict[str, dict[str, PanelReview]] = {}
        self._lock = asyncio.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def initialise_chapter(self, chapter_id: str, panel_ids: list[str]) -> None:
        async with self._lock:
            if chapter_id not in self._reviews:
                self._reviews[chapter_id] = {}
            chapter_reviews = self._reviews[chapter_id]
            for pid in panel_ids:
                if pid not in chapter_reviews:
                    chapter_reviews[pid] = PanelReview(
                        review_id=str(uuid.uuid4()),
                        chapter_id=chapter_id,
                        panel_id=pid,
                    )

    async def get_panel_review(self, chapter_id: str, panel_id: str) -> PanelReview | None:
        return self._reviews.get(chapter_id, {}).get(panel_id)

    async def list_panel_reviews(self, chapter_id: str) -> list[PanelReview]:
        reviews = self._reviews.get(chapter_id, {})
        return sorted(reviews.values(), key=lambda r: r.panel_id)

    async def update_review(self, chapter_id: str, panel_id: str, **kwargs: Any) -> PanelReview:
        async with self._lock:
            review = self._reviews[chapter_id][panel_id]
            for key, value in kwargs.items():
                setattr(review, key, value)
            review.updated_at = self._now()
        return review

    async def add_version(self, chapter_id: str, panel_id: str, version: PanelVersion) -> PanelReview:
        async with self._lock:
            review = self._reviews[chapter_id][panel_id]
            review.versions.append(version)
            review.active_version = version.version_number
            review.updated_at = self._now()
        return review

    async def get_versions(self, chapter_id: str, panel_id: str) -> list[PanelVersion]:
        review = self._reviews.get(chapter_id, {}).get(panel_id)
        return review.versions if review else []

    async def delete_version(self, chapter_id: str, panel_id: str, version_number: int) -> None:
        async with self._lock:
            review = self._reviews[chapter_id][panel_id]
            review.versions = [v for v in review.versions if v.version_number != version_number]
            # If active version was deleted, roll back to latest
            if review.versions:
                review.active_version = max(v.version_number for v in review.versions)
            review.updated_at = self._now()

    async def bulk_approve(self, chapter_id: str, panel_ids: list[str]) -> int:
        now = self._now()
        count = 0
        async with self._lock:
            for pid in panel_ids:
                review = self._reviews.get(chapter_id, {}).get(pid)
                if review and review.status != ReviewStatus.approved:
                    review.status = ReviewStatus.approved
                    review.approved_at = now
                    review.updated_at = now
                    count += 1
        return count

    async def approved_count(self, chapter_id: str) -> int:
        reviews = self._reviews.get(chapter_id, {})
        return sum(1 for r in reviews.values() if r.status == ReviewStatus.approved)

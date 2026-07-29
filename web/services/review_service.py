"""
web/services/review_service.py

ReviewService — owns all editorial operations on panels:
  - Approval / rejection
  - Natural-language regeneration suggestions → enqueued to GenerationService
  - Version management (add, restore, delete)
  - QC score writes
  - Bulk operations

Keeps regeneration async: enqueues work, returns immediately.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from web.exceptions import NotFoundError
from web.schemas.panels import PanelReviewSchema, PanelVersionSchema, QCIssue
from web.store.review_store import (
    PanelReview,
    PanelVersion,
    ReviewStatus,
    ReviewStore,
)

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(
        self,
        review_store: ReviewStore,
        outputs_dir: Path,
    ) -> None:
        self._review_store = review_store
        self._outputs_dir = outputs_dir

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _to_schema(self, review: PanelReview, screenplay_panel: dict | None = None) -> PanelReviewSchema:
        return PanelReviewSchema(
            review_id=review.review_id,
            chapter_id=review.chapter_id,
            panel_id=review.panel_id,
            panel_number=int(review.panel_id.lstrip("p")),
            status=review.status.value,
            active_version=review.active_version,
            versions=[
                PanelVersionSchema(
                    version_id=v.version_id,
                    version_number=v.version_number,
                    image_path=v.image_path,
                    cloud_url=v.cloud_url,
                    positive_prompt=v.positive_prompt,
                    seed=v.seed,
                    cfg=v.cfg,
                    steps=v.steps,
                    sampler=v.sampler,
                    model=v.model,
                    created_at=v.created_at,
                ) for v in review.versions
            ],
            suggestion=review.suggestion,
            reviewer_note=review.reviewer_note,
            qc_score=review.qc_score,
            qc_issues=[QCIssue(**i) for i in review.qc_issues],
            human_required=review.human_required,
            approved_at=review.approved_at,
            updated_at=review.updated_at,
            # Screenplay fields
            beat=screenplay_panel.get("beat", "") if screenplay_panel else "",
            size=screenplay_panel.get("size", "") if screenplay_panel else "",
            shot_type=screenplay_panel.get("shot_type", "") if screenplay_panel else "",
            characters=screenplay_panel.get("characters", []) if screenplay_panel else [],
            scene=screenplay_panel.get("environment", "") if screenplay_panel else "",
            action=screenplay_panel.get("action", "") if screenplay_panel else "",
        )

    async def _require_review(self, chapter_id: str, panel_id: str) -> PanelReview:
        review = await self._review_store.get_panel_review(chapter_id, panel_id)
        if not review:
            raise NotFoundError("Panel review", f"{chapter_id}/{panel_id}")
        return review

    # ── Public API ────────────────────────────────────────────────────────────

    async def list_panels(self, chapter_id: str, screenplay_panels: list[dict]) -> list[PanelReviewSchema]:
        """Merge ReviewStore state with screenplay panel metadata."""
        panel_map = {p["panel_id"]: p for p in screenplay_panels}
        reviews = await self._review_store.list_panel_reviews(chapter_id)

        # Initialise missing panel reviews (e.g. after first run)
        if not reviews:
            await self._review_store.initialise_chapter(chapter_id, list(panel_map.keys()))
            reviews = await self._review_store.list_panel_reviews(chapter_id)

        # Attach image path to versions from disk if not set
        for review in reviews:
            if not review.versions:
                img_path = self._outputs_dir / chapter_id / "panels" / f"dp_ch{chapter_id.split('ch')[-1]}_p{review.panel_id.lstrip('p')}_v1.png"
                if img_path.exists():
                    v = PanelVersion(
                        version_id=str(uuid.uuid4()),
                        panel_id=review.panel_id,
                        chapter_id=chapter_id,
                        version_number=1,
                        image_path=str(img_path),
                    )
                    await self._review_store.add_version(chapter_id, review.panel_id, v)
                    review = await self._review_store.get_panel_review(chapter_id, review.panel_id)  # type: ignore

            if review.status == ReviewStatus.needs_review:
                # Promote to needs_review if image found
                pass

        reviews = await self._review_store.list_panel_reviews(chapter_id)
        return [self._to_schema(r, panel_map.get(r.panel_id)) for r in reviews]

    async def get_panel(self, chapter_id: str, panel_id: str, screenplay_panel: dict | None = None) -> PanelReviewSchema:
        review = await self._require_review(chapter_id, panel_id)
        return self._to_schema(review, screenplay_panel)

    async def approve_panel(self, chapter_id: str, panel_id: str, reviewer_note: str | None = None) -> PanelReviewSchema:
        await self._require_review(chapter_id, panel_id)
        review = await self._review_store.update_review(
            chapter_id, panel_id,
            status=ReviewStatus.approved,
            approved_at=self._now(),
            reviewer_note=reviewer_note,
        )
        logger.info("Panel %s/%s approved", chapter_id, panel_id)
        return self._to_schema(review)

    async def reject_panel(self, chapter_id: str, panel_id: str, reviewer_note: str | None = None) -> PanelReviewSchema:
        await self._require_review(chapter_id, panel_id)
        review = await self._review_store.update_review(
            chapter_id, panel_id,
            status=ReviewStatus.rejected,
            reviewer_note=reviewer_note,
            approved_at=None,
        )
        logger.info("Panel %s/%s rejected", chapter_id, panel_id)
        return self._to_schema(review)

    async def request_regeneration(
        self,
        chapter_id: str,
        panel_id: str,
        suggestion: str,
        reviewer_note: str | None = None,
    ) -> PanelReviewSchema:
        """
        Mark the panel as needing regen with a suggestion.
        Actual regeneration is submitted to GenerationService separately
        so this call returns immediately.
        """
        await self._require_review(chapter_id, panel_id)
        review = await self._review_store.update_review(
            chapter_id, panel_id,
            status=ReviewStatus.needs_regen,
            suggestion=suggestion,
            reviewer_note=reviewer_note,
            approved_at=None,
        )
        logger.info("Panel %s/%s flagged for regen: %s", chapter_id, panel_id, suggestion[:60])
        return self._to_schema(review)

    async def set_regenerating(self, chapter_id: str, panel_id: str) -> None:
        """Called by GenerationService when regen job actually starts."""
        await self._review_store.update_review(
            chapter_id, panel_id,
            status=ReviewStatus.regenerating,
        )

    async def complete_regeneration(
        self,
        chapter_id: str,
        panel_id: str,
        image_path: str,
        prompt_data: dict,
    ) -> PanelReviewSchema:
        """Called by GenerationService after a successful regen. Adds a new version."""
        review = await self._require_review(chapter_id, panel_id)
        next_version = len(review.versions) + 1

        version = PanelVersion(
            version_id=str(uuid.uuid4()),
            panel_id=panel_id,
            chapter_id=chapter_id,
            version_number=next_version,
            image_path=image_path,
            positive_prompt=prompt_data.get("positive", ""),
            negative_prompt=prompt_data.get("negative", ""),
            seed=prompt_data.get("seed"),
            cfg=prompt_data.get("cfg"),
            steps=prompt_data.get("steps"),
            sampler=prompt_data.get("sampler"),
            model=prompt_data.get("model"),
        )

        review = await self._review_store.add_version(chapter_id, panel_id, version)
        review = await self._review_store.update_review(
            chapter_id, panel_id,
            status=ReviewStatus.needs_review,
            suggestion=None,
        )
        return self._to_schema(review)

    async def bulk_approve(self, chapter_id: str, panel_ids: list[str]) -> int:
        return await self._review_store.bulk_approve(chapter_id, panel_ids)

    async def get_approved_count(self, chapter_id: str) -> int:
        return await self._review_store.approved_count(chapter_id)

    # ── Version management ────────────────────────────────────────────────────

    async def list_versions(self, chapter_id: str, panel_id: str) -> list[PanelVersionSchema]:
        await self._require_review(chapter_id, panel_id)
        versions = await self._review_store.get_versions(chapter_id, panel_id)
        return [
            PanelVersionSchema(
                version_id=v.version_id,
                version_number=v.version_number,
                image_path=v.image_path,
                cloud_url=v.cloud_url,
                positive_prompt=v.positive_prompt,
                seed=v.seed,
                cfg=v.cfg,
                steps=v.steps,
                sampler=v.sampler,
                model=v.model,
                created_at=v.created_at,
            ) for v in versions
        ]

    async def restore_version(self, chapter_id: str, panel_id: str, version_number: int) -> PanelReviewSchema:
        """Set the active version to a previous version."""
        review = await self._require_review(chapter_id, panel_id)
        exists = any(v.version_number == version_number for v in review.versions)
        if not exists:
            raise NotFoundError("Version", str(version_number))

        review = await self._review_store.update_review(
            chapter_id, panel_id,
            active_version=version_number,
            status=ReviewStatus.needs_review,
        )
        return self._to_schema(review)

    async def delete_version(self, chapter_id: str, panel_id: str, version_number: int) -> None:
        review = await self._require_review(chapter_id, panel_id)
        if len(review.versions) <= 1:
            from web.exceptions import ValidationError
            raise ValidationError("Cannot delete the only remaining version of a panel.")
        await self._review_store.delete_version(chapter_id, panel_id, version_number)

    # ── QC ────────────────────────────────────────────────────────────────────

    async def set_qc_score(
        self, chapter_id: str, panel_id: str, score: int, issues: list[dict]
    ) -> PanelReviewSchema:
        await self._require_review(chapter_id, panel_id)
        review = await self._review_store.update_review(
            chapter_id, panel_id,
            qc_score=score,
            qc_issues=issues,
        )
        return self._to_schema(review)

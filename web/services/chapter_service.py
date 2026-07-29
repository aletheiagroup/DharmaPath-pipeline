"""
web/services/chapter_service.py

Owns all chapter/screenplay operations:
  - Listing, loading, and saving screenplay files
  - Validation
  - Panel status aggregation from the ReviewStore
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from dharmapath.models.screenplay import Screenplay
from dharmapath.registry.registry import CharacterRegistry
from dharmapath.validator.screenplay_validator import ScreenplayValidator
from web.exceptions import NotFoundError, ScreenplayInvalidError, ValidationError
from web.schemas.chapters import (
    ChapterDetail,
    ChapterSummary,
    ValidateScreenplayResponse,
    ValidationErrorItem,
)
from web.store.review_store import ReviewStore

logger = logging.getLogger(__name__)


class ChapterService:
    def __init__(
        self,
        screenplays_dir: Path,
        outputs_dir: Path,
        registry: CharacterRegistry,
        review_store: ReviewStore,
    ) -> None:
        self._screenplays_dir = screenplays_dir
        self._outputs_dir = outputs_dir
        self._registry = registry
        self._review_store = review_store
        self._validator = ScreenplayValidator(registry)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _screenplay_path(self, chapter_id: str) -> Path:
        """Resolve screenplay path from chapter_id."""
        return self._screenplays_dir / f"{chapter_id}.json"

    def _load_screenplay(self, chapter_id: str) -> Screenplay:
        path = self._screenplay_path(chapter_id)
        if not path.exists():
            raise NotFoundError("Chapter", chapter_id)
        try:
            return Screenplay.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValidationError(f"Failed to parse screenplay for '{chapter_id}': {e}")

    async def _chapter_status(self, chapter_id: str, total_panels: int) -> tuple[str, int]:
        """Derive chapter status from review state."""
        approved = await self._review_store.approved_count(chapter_id)

        ep_dir = self._outputs_dir / chapter_id / "episodes"
        if ep_dir.exists() and any(ep_dir.iterdir()):
            return "compiled", approved

        if approved == total_panels and total_panels > 0:
            return "in_review", approved

        panel_dir = self._outputs_dir / chapter_id / "panels"
        if panel_dir.exists() and any(panel_dir.glob("*.png")):
            return "in_review", approved

        return "draft", approved

    # ── Public API ────────────────────────────────────────────────────────────

    async def list_chapters(self) -> list[ChapterSummary]:
        """List all chapters discovered from screenplay files."""
        summaries: list[ChapterSummary] = []
        for path in sorted(self._screenplays_dir.glob("*.json")):
            try:
                sp = Screenplay.model_validate_json(path.read_text(encoding="utf-8"))
                ch = sp.chapter
                status, approved = await self._chapter_status(ch.chapter_id, sp.panel_count)
                summaries.append(ChapterSummary(
                    chapter_id=ch.chapter_id,
                    path=ch.path.value,
                    arc=ch.arc.value,
                    title=ch.title,
                    description=ch.description,
                    arc_number=ch.arc_number,
                    lesson_id=ch.lesson_id,
                    status=status,
                    total_panels=sp.panel_count,
                    approved_count=approved,
                    screenplay_path=str(path),
                ))
            except Exception as e:
                logger.warning("Skipping invalid screenplay file %s: %s", path, e)
        return summaries

    async def get_chapter(self, chapter_id: str) -> ChapterDetail:
        sp = self._load_screenplay(chapter_id)
        ch = sp.chapter
        status, approved = await self._chapter_status(ch.chapter_id, sp.panel_count)
        return ChapterDetail(
            chapter_id=ch.chapter_id,
            path=ch.path.value,
            arc=ch.arc.value,
            title=ch.title,
            description=ch.description,
            arc_number=ch.arc_number,
            lesson_id=ch.lesson_id,
            status=status,
            total_panels=sp.panel_count,
            approved_count=approved,
            screenplay_path=str(self._screenplay_path(chapter_id)),
            panels=[p.model_dump() for p in sp.panels],
        )

    async def create_chapter(self, screenplay_dict: dict) -> ChapterSummary:
        """Validate and save a new screenplay JSON."""
        try:
            sp = Screenplay.model_validate(screenplay_dict)
        except Exception as e:
            raise ValidationError(f"Invalid screenplay structure: {e}")

        result = self._validator.validate(sp)
        if not result.passed():
            raise ScreenplayInvalidError([str(e) for e in result.hard_errors])

        path = self._screenplay_path(sp.chapter.chapter_id)
        if path.exists():
            raise ValidationError(f"Chapter '{sp.chapter.chapter_id}' already exists. Use a unique chapter_id.")

        path.write_text(json.dumps(screenplay_dict, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved new screenplay: %s", path)

        # Initialise review records for all panels
        await self._review_store.initialise_chapter(
            sp.chapter.chapter_id,
            [p.panel_id for p in sp.panels],
        )

        return ChapterSummary(
            chapter_id=sp.chapter.chapter_id,
            path=sp.chapter.path.value,
            arc=sp.chapter.arc.value,
            title=sp.chapter.title,
            description=sp.chapter.description,
            arc_number=sp.chapter.arc_number,
            lesson_id=sp.chapter.lesson_id,
            status="draft",
            total_panels=sp.panel_count,
            approved_count=0,
            screenplay_path=str(path),
        )

    async def validate_screenplay(self, screenplay_dict: dict) -> ValidateScreenplayResponse:
        """Validate without saving. Returns full rule violation detail."""
        try:
            sp = Screenplay.model_validate(screenplay_dict)
        except Exception as e:
            return ValidateScreenplayResponse(
                passed=False,
                summary=f"Schema parse error: {e}",
                errors=[ValidationErrorItem(panel_id="schema", rule_name="SCHEMA_PARSE", severity="error", message=str(e))],
                warnings=[],
            )

        result = self._validator.validate(sp)
        return ValidateScreenplayResponse(
            passed=result.passed(),
            summary=result.summary(),
            errors=[
                ValidationErrorItem(
                    panel_id=e.panel_id, rule_name=e.rule_name,
                    severity=e.severity, message=e.message,
                ) for e in result.hard_errors
            ],
            warnings=[
                ValidationErrorItem(
                    panel_id=w.panel_id, rule_name=w.rule_name,
                    severity=w.severity, message=w.message,
                ) for w in result.warnings
            ],
        )

    def get_screenplay(self, chapter_id: str) -> Screenplay:
        """Load raw Screenplay model (used by other services)."""
        return self._load_screenplay(chapter_id)

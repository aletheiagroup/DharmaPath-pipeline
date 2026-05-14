"""
dharmapath/models/screenplay.py

Complete Pydantic v2 screenplay schema.
All screenplay JSON files must validate against these models before any generation runs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, field_validator, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class Size(str, Enum):
    """Panel size on the canvas."""
    full = "full"        # 800px wide — full width
    half = "half"        # 400px wide — two side by side
    quarter = "quarter"  # 200px wide — four in a row (must appear in groups of 4)


class Beat(str, Enum):
    """Narrative beat for each panel."""
    hook = "hook"                # Opening — grabs attention. First panel (p01) must be this.
    escalation = "escalation"    # Rising tension or action
    impact = "impact"            # Climactic moment — exactly 1 per chapter, size=full, human_required=True
    quiet = "quiet"              # Emotional beat, low word count. Exactly 1 per chapter.
    close = "close"              # Final panel — chapter end. Last panel must be this.
    transition = "transition"    # Scene change


class ShotType(str, Enum):
    """Camera framing for the panel."""
    establishing = "establishing"      # Wide shot — sets location
    medium = "medium"                  # Waist-up
    close_face = "close_face"          # Face close-up
    pov = "pov"                        # Point of view shot
    overhead = "overhead"              # Bird's eye
    detail_insert = "detail_insert"    # Object or body-part detail


class DialogueType(str, Enum):
    """Type of text in a dialogue bubble."""
    speech = "speech"      # Spoken word — speech bubble
    thought = "thought"    # Inner thought — thought bubble (minimum 3 per chapter)
    caption = "caption"    # Narrator caption box (max 8 words)
    sfx = "sfx"           # Sound effect text


class Arc(str, Enum):
    """Thematic arc — determines colour palette applied in ComfyUI."""
    divine = "divine"        # Celestial, gods, cosmic events
    conflict = "conflict"    # Battle, confrontation, high tension
    domestic = "domestic"    # Home, family, village scenes
    lesson = "lesson"        # Teaching, wisdom, philosophical exchange


class Path(str, Enum):
    """Learning path the chapter belongs to."""
    itihaasa = "itihaasa"          # Mahabharata — V1 active
    leela = "leela"                # Krishna Leela — coming soon
    cosmological = "cosmological"  # Creation stories — coming soon


# ── Sub-models ────────────────────────────────────────────────────────────────

class Dialogue(BaseModel):
    """A single dialogue entry within a panel."""

    speaker: str
    """Character name, or 'NARRATOR' for caption boxes, or 'SFX' for sound effects."""

    type: DialogueType

    text: str
    """Dialogue text. Hard limit: 25 words. Quiet beat panels: 10 words total across all dialogue."""

    @field_validator("text")
    @classmethod
    def validate_word_count(cls, v: str) -> str:
        word_count = len(v.split())
        if word_count > 25:
            raise ValueError(
                f"Dialogue text exceeds 25-word limit ({word_count} words): '{v[:50]}...'"
            )
        return v

    @property
    def word_count(self) -> int:
        return len(self.text.split())


# ── Core Panel Model ──────────────────────────────────────────────────────────

class Panel(BaseModel):
    """A single manhwa panel — the atomic unit of a chapter."""

    panel_id: str
    """Unique identifier. Format: p01, p02 … p60. Must be zero-padded 2 digits."""

    size: Size
    """Canvas width allocation: full / half / quarter."""

    beat: Beat
    """Narrative beat this panel serves."""

    shot_type: ShotType
    """Camera framing."""

    characters: list[str] = []
    """List of character names present in this panel. Must all be approved in registry."""

    action: str
    """What is happening in this panel. Used in positive prompt construction."""

    environment: str
    """Location / setting description."""

    lighting: str
    """Lighting conditions for the panel."""

    camera: str
    """Camera angle and lens description (e.g. 'low angle, wide lens')."""

    mood: str
    """Emotional atmosphere (e.g. 'tense, foreboding')."""

    dialogue: list[Dialogue] = []
    """All dialogue, captions, and SFX in this panel."""

    pose_ref: str | None = None
    """Optional: path to a ControlNet pose skeleton PNG (relative to data/poses/)."""

    palette_override: Arc | None = None
    """Optional: override the chapter-level arc palette for this panel."""

    human_required: bool = False
    """If True, panel is pre-flagged for human review after generation.
    Impact panels must have this set to True."""

    @property
    def total_word_count(self) -> int:
        """Total words across all dialogue entries in this panel."""
        return sum(d.word_count for d in self.dialogue)

    @property
    def panel_number(self) -> int:
        """Numeric panel number extracted from panel_id."""
        return int(self.panel_id.lstrip("p"))


# ── Chapter Metadata ──────────────────────────────────────────────────────────

class Chapter(BaseModel):
    """Chapter-level metadata attached to every screenplay."""

    chapter_id: str
    """Unique chapter identifier. Format: {path_id}_ch{NN} e.g. 'itihaasa_ch01'."""

    path: Path
    """Which learning path this chapter belongs to."""

    arc: Arc
    """Dominant arc — sets the base colour palette for the whole chapter."""

    title: str
    """Human-readable chapter title."""

    description: str
    """Brief description of the chapter for dashboard display."""

    arc_number: int
    """Arc number within the path (1-indexed). Used to resolve character age states."""

    lesson_id: str | None = None
    """Optional: linked DharmaPath lesson ID this chapter was generated for."""


# ── Root Screenplay Model ─────────────────────────────────────────────────────

class Screenplay(BaseModel):
    """Root model for a complete screenplay JSON file."""

    version: str = "1.0"
    """Schema version. Bump when breaking changes are made."""

    chapter: Chapter
    panels: list[Panel]

    @model_validator(mode="after")
    def validate_panel_ids_sequential(self) -> "Screenplay":
        """Panel IDs must be sequential starting from p01."""
        for i, panel in enumerate(self.panels, start=1):
            expected = f"p{i:02d}"
            if panel.panel_id != expected:
                raise ValueError(
                    f"Panel ID out of sequence: expected '{expected}', got '{panel.panel_id}'"
                )
        return self

    @property
    def panel_count(self) -> int:
        return len(self.panels)

    @property
    def impact_panels(self) -> list[Panel]:
        return [p for p in self.panels if p.beat == Beat.impact]

    @property
    def quiet_panels(self) -> list[Panel]:
        return [p for p in self.panels if p.beat == Beat.quiet]

    @property
    def all_characters(self) -> set[str]:
        """All unique character names referenced across all panels."""
        names: set[str] = set()
        for panel in self.panels:
            names.update(panel.characters)
        return names

    def get_panel(self, panel_id: str) -> Panel | None:
        return next((p for p in self.panels if p.panel_id == panel_id), None)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

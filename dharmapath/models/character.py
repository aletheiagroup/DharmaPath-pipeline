"""
dharmapath/models/character.py

Pydantic v2 models for the character registry.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class AgeState(BaseModel):
    """
    A character's appearance at a specific life stage.
    Characters may have multiple age states (young, middle, elder)
    that appear at different arc numbers.
    """

    reference_image: str | None = None
    """Path to the approved face crop image (relative to data/characters/)."""

    reference_image_full: str | None = None
    """Path to the approved full-body image (relative to data/characters_full/)."""

    candidates: list[str] = Field(default_factory=list)
    """Paths to all generated candidate images (relative to data/candidates/)."""

    appears_arcs: list[int] = Field(default_factory=list)
    """Arc numbers where this age state is active (1-indexed)."""

    @property
    def is_approved(self) -> bool:
        return self.reference_image is not None


class CharacterEntry(BaseModel):
    """
    A single character in the registry.

    Status lifecycle:
        unregistered → pending_selection → approved

    - unregistered: character exists in screenplay but no candidates generated yet
    - pending_selection: 9 candidates generated, waiting for human click-to-approve
    - approved: face crop saved, character is cleared for use in generation
    """

    name: str
    """Canonical character name. Must match exactly how it appears in screenplay panel.characters."""

    description: str
    """Visual description used to generate candidate images. Passed to ComfyUI prompt."""

    status: Literal["unregistered", "pending_selection", "approved"] = "unregistered"

    first_appears: str
    """chapter_id of the chapter where this character first appears."""

    states: dict[str, AgeState] = Field(default_factory=dict)
    """
    Age states keyed by label (e.g. 'young', 'middle', 'elder').
    Most characters have only one state ('default').
    """

    default_state: str = "default"
    """The state label to use when no specific arc mapping applies."""

    ip_adapter_weight: float = 0.65
    """IP-Adapter face consistency weight for this character. Range: 0.0–1.0."""

    def get_state_for_arc(self, arc_number: int) -> AgeState | None:
        """
        Return the correct AgeState for a given arc number.
        Falls back to default_state if no specific state covers this arc.
        """
        for state in self.states.values():
            if arc_number in state.appears_arcs:
                return state
        return self.states.get(self.default_state)

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"

    @property
    def approved_state(self) -> AgeState | None:
        """Return the approved state (any state with a reference image)."""
        for state in self.states.values():
            if state.reference_image:
                return state
        return None

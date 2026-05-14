"""
dharmapath/registry/registry.py

CharacterRegistry — loads/saves the characters.json file and manages
the full lifecycle of character entries from unregistered → approved.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from dharmapath.models.character import AgeState, CharacterEntry
from dharmapath.models.screenplay import Screenplay

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent / "characters.json"


class CharacterRegistry:
    """
    Central store for all character entries.

    Load once at app start, mutate in memory, persist with save().
    Thread safety is not required — pipeline runs are sequential.
    """

    def __init__(self, registry_path: Path = REGISTRY_PATH) -> None:
        self._path = registry_path
        self._characters: dict[str, CharacterEntry] = {}

    # ── Persistence ───────────────────────────────────────────

    def load(self) -> None:
        """Read characters.json into memory. Call at startup."""
        if not self._path.exists():
            logger.warning(f"Registry file not found at {self._path} — starting empty.")
            self._characters = {}
            return

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._characters = {
            name: CharacterEntry.model_validate(entry)
            for name, entry in raw.items()
        }
        logger.info(f"Registry loaded: {len(self._characters)} characters from {self._path}")

    def save(self) -> None:
        """Write current in-memory registry back to characters.json."""
        data = {
            name: entry.model_dump()
            for name, entry in self._characters.items()
        }
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Registry saved: {len(self._characters)} characters → {self._path}")

    # ── Queries ───────────────────────────────────────────────

    def get(self, name: str) -> CharacterEntry | None:
        """Return a character entry by canonical name, or None if not found."""
        return self._characters.get(name)

    def all(self) -> list[CharacterEntry]:
        """Return all character entries."""
        return list(self._characters.values())

    def approved(self) -> list[CharacterEntry]:
        """Return only fully approved characters."""
        return [c for c in self._characters.values() if c.is_approved]

    # ── Mutations ─────────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        first_appears: str,
        default_state: str = "default",
    ) -> CharacterEntry:
        """
        Add a new character in 'unregistered' status.
        Creates a single default AgeState with no references yet.
        Skips silently if the character already exists.
        """
        if name in self._characters:
            logger.debug(f"Character '{name}' already in registry — skipping register.")
            return self._characters[name]

        entry = CharacterEntry(
            name=name,
            description=description,
            status="unregistered",
            first_appears=first_appears,
            default_state=default_state,
            states={default_state: AgeState()},
        )
        self._characters[name] = entry
        logger.info(f"Registered new character: '{name}' (status=unregistered)")
        return entry

    def set_candidates(
        self,
        name: str,
        state: str,
        candidate_paths: list[str],
    ) -> None:
        """
        Store candidate image paths and advance status to pending_selection.
        Called after CharacterDesigner generates the 9 candidate images.
        """
        entry = self._require(name)
        if state not in entry.states:
            entry.states[state] = AgeState()

        entry.states[state].candidates = candidate_paths
        entry.status = "pending_selection"
        logger.info(f"Set {len(candidate_paths)} candidates for '{name}' state='{state}'")

    def approve(
        self,
        name: str,
        state: str,
        face_crop_path: str,
        full_image_path: str,
    ) -> None:
        """
        Approve a character after human selection.
        Stores face crop + full image paths, sets status to approved.
        Called by CharacterDesigner.approve_candidate() after Pillow crop.
        """
        entry = self._require(name)
        if state not in entry.states:
            entry.states[state] = AgeState()

        entry.states[state].reference_image = face_crop_path
        entry.states[state].reference_image_full = full_image_path
        entry.status = "approved"
        logger.info(f"Approved character '{name}' state='{state}' → {face_crop_path}")

    def set_arc_range(
        self,
        name: str,
        state: str,
        arc_numbers: list[int],
    ) -> None:
        """Assign which arc numbers a given age state is active for."""
        entry = self._require(name)
        if state not in entry.states:
            entry.states[state] = AgeState()
        entry.states[state].appears_arcs = arc_numbers

    # ── Gate check ────────────────────────────────────────────

    def all_approved_for_chapter(self, screenplay: Screenplay) -> bool:
        """
        Return True only if every character named in the screenplay
        has status='approved' in the registry.

        This is the HARD GATE before pipeline generation can start.
        ChapterRunner checks this after validation passes.
        """
        missing: list[str] = []
        for character_name in screenplay.all_characters:
            entry = self._characters.get(character_name)
            if entry is None:
                missing.append(f"{character_name} (not in registry)")
            elif not entry.is_approved:
                missing.append(f"{character_name} (status={entry.status})")

        if missing:
            logger.warning(
                f"Characters not approved for chapter '{screenplay.chapter.chapter_id}': "
                + ", ".join(missing)
            )
            return False
        return True

    def get_state_for_arc(self, name: str, arc_number: int) -> AgeState | None:
        """Resolve the correct AgeState for a character at a given arc number."""
        entry = self._characters.get(name)
        if not entry:
            return None
        return entry.get_state_for_arc(arc_number)

    def unapproved_characters(self, screenplay: Screenplay) -> list[str]:
        """
        Return list of character names that need approval before generation.
        Used by the web UI to show which characters need candidate generation.
        """
        unapproved: list[str] = []
        for name in screenplay.all_characters:
            entry = self._characters.get(name)
            if entry is None or not entry.is_approved:
                unapproved.append(name)
        return unapproved

    # ── Internal ──────────────────────────────────────────────

    def _require(self, name: str) -> CharacterEntry:
        entry = self._characters.get(name)
        if not entry:
            raise KeyError(f"Character '{name}' not found in registry. Call register() first.")
        return entry

    def __len__(self) -> int:
        return len(self._characters)

    def __contains__(self, name: str) -> bool:
        return name in self._characters

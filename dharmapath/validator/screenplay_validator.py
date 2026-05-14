"""
dharmapath/validator/screenplay_validator.py

Validates a Screenplay against all 16 rules defined in the build spec.
Returns a list of ValidationError objects — each with panel_id, rule_name,
severity, and message.

Hard errors block generation entirely.
Warnings allow generation but flag panels for review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from dharmapath.models.screenplay import Beat, DialogueType, Panel, Screenplay, Size
from dharmapath.registry.registry import CharacterRegistry


# ── Result type ───────────────────────────────────────────────────────────────

Severity = Literal["error", "warning"]


@dataclass
class ValidationError:
    panel_id: str          # "chapter" if rule is chapter-level, else "p01" etc.
    rule_name: str         # short machine-readable rule identifier
    severity: Severity
    message: str

    def __str__(self) -> str:
        icon = "[ERROR]" if self.severity == "error" else "[WARN]"
        return f"{icon} [{self.rule_name}] {self.panel_id}: {self.message}"


@dataclass
class ValidationResult:
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def has_hard_errors(self) -> bool:
        return any(e.severity == "error" for e in self.errors)

    @property
    def hard_errors(self) -> list[ValidationError]:
        return [e for e in self.errors if e.severity == "error"]

    @property
    def warnings(self) -> list[ValidationError]:
        return [e for e in self.errors if e.severity == "warning"]

    def passed(self) -> bool:
        return not self.has_hard_errors

    def summary(self) -> str:
        hard = len(self.hard_errors)
        warn = len(self.warnings)
        if hard == 0 and warn == 0:
            return "SUCCESS: All validation rules passed."
        parts = []
        if hard:
            parts.append(f"{hard} hard error{'s' if hard != 1 else ''}")
        if warn:
            parts.append(f"{warn} warning{'s' if warn != 1 else ''}")
        status = "FAILED: BLOCKED" if hard else "PASSED WITH WARNINGS"
        return f"{status} -- {', '.join(parts)}"


# ── Validator ─────────────────────────────────────────────────────────────────

class ScreenplayValidator:
    """
    Validates a Screenplay against all defined rules.

    Usage:
        validator = ScreenplayValidator(registry)
        result = validator.validate(screenplay)
        if not result.passed():
            for err in result.hard_errors:
                print(err)
    """

    def __init__(self, registry: CharacterRegistry | None = None) -> None:
        """
        Args:
            registry: Optional CharacterRegistry for character approval checks.
                      If None, rule R10 (all characters approved) is skipped.
        """
        self._registry = registry

    def validate(self, screenplay: Screenplay) -> ValidationResult:
        """Run all 16 rules against the screenplay. Returns a ValidationResult."""
        result = ValidationResult()
        panels = screenplay.panels

        # ── Hard errors (block generation) ────────────────────
        result.errors += self._r01_first_panel_hook(panels)
        result.errors += self._r02_exactly_one_impact(panels)
        result.errors += self._r03_exactly_one_quiet(panels)
        result.errors += self._r04_last_panel_close(panels)
        result.errors += self._r05_dialogue_word_count(panels)
        result.errors += self._r06_quiet_panel_word_count(panels)
        result.errors += self._r07_quarter_panels_groups_of_four(panels)
        result.errors += self._r08_impact_panel_full_and_human_required(panels)
        result.errors += self._r09_panel_count_range(screenplay)
        if self._registry is not None:
            result.errors += self._r10_all_characters_approved(screenplay)

        # ── Warnings (allow generation, flag for review) ───────
        result.errors += self._w01_thought_bubble_minimum(panels)
        result.errors += self._w02_consecutive_half_panels(panels)
        result.errors += self._w03_too_many_full_panels(panels)
        result.errors += self._w04_silent_named_characters(panels)
        result.errors += self._w05_caption_word_count(panels)
        result.errors += self._w06_condition_without_follow_through(panels)

        return result

    # ── Hard Error Rules ──────────────────────────────────────────────────────

    def _r01_first_panel_hook(self, panels: list[Panel]) -> list[ValidationError]:
        """R01: p01 must have beat=hook."""
        if not panels:
            return []
        first = panels[0]
        if first.beat != Beat.hook:
            return [ValidationError(
                panel_id=first.panel_id,
                rule_name="R01_FIRST_PANEL_HOOK",
                severity="error",
                message=f"First panel must have beat=hook, got beat={first.beat.value}.",
            )]
        return []

    def _r02_exactly_one_impact(self, panels: list[Panel]) -> list[ValidationError]:
        """R02: Exactly one panel must have beat=impact."""
        impact_panels = [p for p in panels if p.beat == Beat.impact]
        count = len(impact_panels)
        if count == 1:
            return []
        if count == 0:
            return [ValidationError(
                panel_id="chapter",
                rule_name="R02_EXACTLY_ONE_IMPACT",
                severity="error",
                message="No impact panel found. Every chapter must have exactly one beat=impact panel.",
            )]
        ids = ", ".join(p.panel_id for p in impact_panels)
        return [ValidationError(
            panel_id="chapter",
            rule_name="R02_EXACTLY_ONE_IMPACT",
            severity="error",
            message=f"Found {count} impact panels ({ids}). Exactly one is required.",
        )]

    def _r03_exactly_one_quiet(self, panels: list[Panel]) -> list[ValidationError]:
        """R03: Exactly one panel must have beat=quiet."""
        quiet_panels = [p for p in panels if p.beat == Beat.quiet]
        count = len(quiet_panels)
        if count == 1:
            return []
        if count == 0:
            return [ValidationError(
                panel_id="chapter",
                rule_name="R03_EXACTLY_ONE_QUIET",
                severity="error",
                message="No quiet panel found. Every chapter must have exactly one beat=quiet panel.",
            )]
        ids = ", ".join(p.panel_id for p in quiet_panels)
        return [ValidationError(
            panel_id="chapter",
            rule_name="R03_EXACTLY_ONE_QUIET",
            severity="error",
            message=f"Found {count} quiet panels ({ids}). Exactly one is required.",
        )]

    def _r04_last_panel_close(self, panels: list[Panel]) -> list[ValidationError]:
        """R04: Last panel must have beat=close."""
        if not panels:
            return []
        last = panels[-1]
        if last.beat != Beat.close:
            return [ValidationError(
                panel_id=last.panel_id,
                rule_name="R04_LAST_PANEL_CLOSE",
                severity="error",
                message=f"Last panel must have beat=close, got beat={last.beat.value}.",
            )]
        return []

    def _r05_dialogue_word_count(self, panels: list[Panel]) -> list[ValidationError]:
        """R05: No dialogue entry may exceed 25 words."""
        # Note: Pydantic validator also catches this at parse time.
        # This rule catches any that slip through or are constructed programmatically.
        errors: list[ValidationError] = []
        for panel in panels:
            for dialogue in panel.dialogue:
                wc = dialogue.word_count
                if wc > 25:
                    errors.append(ValidationError(
                        panel_id=panel.panel_id,
                        rule_name="R05_DIALOGUE_WORD_COUNT",
                        severity="error",
                        message=(
                            f"Dialogue by '{dialogue.speaker}' has {wc} words "
                            f"(limit: 25): \"{dialogue.text[:40]}...\""
                        ),
                    ))
        return errors

    def _r06_quiet_panel_word_count(self, panels: list[Panel]) -> list[ValidationError]:
        """R06: Quiet beat panels may not exceed 10 total words across all dialogue."""
        errors: list[ValidationError] = []
        for panel in panels:
            if panel.beat == Beat.quiet:
                total = panel.total_word_count
                if total > 10:
                    errors.append(ValidationError(
                        panel_id=panel.panel_id,
                        rule_name="R06_QUIET_PANEL_WORD_COUNT",
                        severity="error",
                        message=(
                            f"Quiet beat panel has {total} total dialogue words "
                            f"(limit: 10). Quiet panels must breathe — reduce or remove dialogue."
                        ),
                    ))
        return errors

    def _r07_quarter_panels_groups_of_four(self, panels: list[Panel]) -> list[ValidationError]:
        """
        R07: Quarter panels must appear in groups of exactly 4 consecutive panels.
        A run of quarters that is not a multiple of 4 is a hard error.
        """
        errors: list[ValidationError] = []
        i = 0
        while i < len(panels):
            if panels[i].size == Size.quarter:
                run_start = i
                while i < len(panels) and panels[i].size == Size.quarter:
                    i += 1
                run_length = i - run_start
                if run_length % 4 != 0:
                    ids = ", ".join(
                        panels[j].panel_id for j in range(run_start, run_start + run_length)
                    )
                    errors.append(ValidationError(
                        panel_id=panels[run_start].panel_id,
                        rule_name="R07_QUARTER_PANELS_GROUPS_OF_FOUR",
                        severity="error",
                        message=(
                            f"Quarter panels must appear in groups of exactly 4. "
                            f"Found a run of {run_length} quarters ({ids}). "
                            f"Adjust to nearest multiple of 4."
                        ),
                    ))
            else:
                i += 1
        return errors

    def _r08_impact_panel_full_and_human_required(self, panels: list[Panel]) -> list[ValidationError]:
        """R08: Impact panel must have size=full AND human_required=True."""
        errors: list[ValidationError] = []
        for panel in panels:
            if panel.beat == Beat.impact:
                issues: list[str] = []
                if panel.size != Size.full:
                    issues.append(f"size={panel.size.value} (must be full)")
                if not panel.human_required:
                    issues.append("human_required=False (must be True)")
                if issues:
                    errors.append(ValidationError(
                        panel_id=panel.panel_id,
                        rule_name="R08_IMPACT_PANEL_REQUIREMENTS",
                        severity="error",
                        message=f"Impact panel fails requirements: {'; '.join(issues)}.",
                    ))
        return errors

    def _r09_panel_count_range(self, screenplay: Screenplay) -> list[ValidationError]:
        """R09: Panel count must be between 35 and 60 inclusive."""
        count = screenplay.panel_count
        if 35 <= count <= 60:
            return []
        if count < 35:
            return [ValidationError(
                panel_id="chapter",
                rule_name="R09_PANEL_COUNT_RANGE",
                severity="error",
                message=(
                    f"Chapter has only {count} panels. Minimum is 35. "
                    f"Add {35 - count} more panels before generating."
                ),
            )]
        return [ValidationError(
            panel_id="chapter",
            rule_name="R09_PANEL_COUNT_RANGE",
            severity="error",
            message=(
                f"Chapter has {count} panels. Maximum is 60. "
                f"Remove {count - 60} panels before generating."
            ),
        )]

    def _r10_all_characters_approved(self, screenplay: Screenplay) -> list[ValidationError]:
        """R10: All characters referenced in panels must be approved in the registry."""
        assert self._registry is not None
        errors: list[ValidationError] = []

        for panel in screenplay.panels:
            for character_name in panel.characters:
                entry = self._registry.get(character_name)
                if entry is None:
                    errors.append(ValidationError(
                        panel_id=panel.panel_id,
                        rule_name="R10_CHARACTER_NOT_IN_REGISTRY",
                        severity="error",
                        message=(
                            f"Character '{character_name}' is not in the registry. "
                            f"Use the web UI to generate and approve candidates first."
                        ),
                    ))
                elif not entry.is_approved:
                    errors.append(ValidationError(
                        panel_id=panel.panel_id,
                        rule_name="R10_CHARACTER_NOT_APPROVED",
                        severity="error",
                        message=(
                            f"Character '{character_name}' has status='{entry.status}'. "
                            f"Approval required before generation."
                        ),
                    ))
        return errors

    # ── Warning Rules ─────────────────────────────────────────────────────────

    def _w01_thought_bubble_minimum(self, panels: list[Panel]) -> list[ValidationError]:
        """W01: Fewer than 3 thought-bubble entries across the chapter."""
        thought_count = sum(
            1
            for panel in panels
            for d in panel.dialogue
            if d.type == DialogueType.thought
        )
        if thought_count >= 3:
            return []
        return [ValidationError(
            panel_id="chapter",
            rule_name="W01_THOUGHT_BUBBLE_MINIMUM",
            severity="warning",
            message=(
                f"Only {thought_count} thought bubble(s) found across the chapter. "
                f"Minimum 3 recommended for interiority and reader engagement."
            ),
        )]

    def _w02_consecutive_half_panels(self, panels: list[Panel]) -> list[ValidationError]:
        """W02: 5 or more consecutive half-panels with no size break."""
        warnings: list[ValidationError] = []
        i = 0
        while i < len(panels):
            if panels[i].size == Size.half:
                run_start = i
                while i < len(panels) and panels[i].size == Size.half:
                    i += 1
                run_length = i - run_start
                if run_length >= 5:
                    ids = ", ".join(
                        panels[j].panel_id for j in range(run_start, i)
                    )
                    warnings.append(ValidationError(
                        panel_id=panels[run_start].panel_id,
                        rule_name="W02_CONSECUTIVE_HALF_PANELS",
                        severity="warning",
                        message=(
                            f"Run of {run_length} consecutive half-panels ({ids}). "
                            f"Consider breaking rhythm with a full or quarter panel."
                        ),
                    ))
            else:
                i += 1
        return warnings

    def _w03_too_many_full_panels(self, panels: list[Panel]) -> list[ValidationError]:
        """W03: More than 5 full-width panels in a chapter."""
        full_panels = [p for p in panels if p.size == Size.full]
        count = len(full_panels)
        if count <= 5:
            return []
        ids = ", ".join(p.panel_id for p in full_panels)
        return [ValidationError(
            panel_id="chapter",
            rule_name="W03_TOO_MANY_FULL_PANELS",
            severity="warning",
            message=(
                f"{count} full-width panels found ({ids}). "
                f"More than 5 may dilute the impact of your key moments. "
                f"Consider converting some to half panels."
            ),
        )]

    def _w04_silent_named_characters(self, panels: list[Panel]) -> list[ValidationError]:
        """
        W04: A named character appears in panel.characters but has no speech
        or thought dialogue in any panel across the chapter.
        """
        warnings: list[ValidationError] = []

        # Collect all characters who appear in at least one panel
        appearing_characters: set[str] = set()
        for panel in panels:
            appearing_characters.update(panel.characters)

        # Collect all characters who have at least one speech or thought line
        speaking_characters: set[str] = set()
        for panel in panels:
            for d in panel.dialogue:
                if d.type in (DialogueType.speech, DialogueType.thought):
                    speaking_characters.add(d.speaker)

        # Characters who appear but never speak or think
        silent = appearing_characters - speaking_characters
        # Exclude generic non-character speakers
        silent -= {"NARRATOR", "SFX", "CROWD", "OFFSCREEN"}

        for name in sorted(silent):
            # Find which panels they appear in
            panel_ids = [p.panel_id for p in panels if name in p.characters]
            warnings.append(ValidationError(
                panel_id=", ".join(panel_ids[:3]),  # first 3 appearances
                rule_name="W04_SILENT_NAMED_CHARACTER",
                severity="warning",
                message=(
                    f"Character '{name}' appears in panels but has no speech or thought "
                    f"dialogue anywhere in the chapter. Intentional silent character?"
                ),
            ))

        return warnings

    def _w05_caption_word_count(self, panels: list[Panel]) -> list[ValidationError]:
        """W05: Caption text longer than 8 words."""
        warnings: list[ValidationError] = []
        for panel in panels:
            for d in panel.dialogue:
                if d.type == DialogueType.caption:
                    wc = d.word_count
                    if wc > 8:
                        warnings.append(ValidationError(
                            panel_id=panel.panel_id,
                            rule_name="W05_CAPTION_TOO_LONG",
                            severity="warning",
                            message=(
                                f"Caption by '{d.speaker}' has {wc} words (recommended max: 8): "
                                f"\"{d.text[:50]}\""
                            ),
                        ))
        return warnings

    def _w06_condition_without_follow_through(self, panels: list[Panel]) -> list[ValidationError]:
        """
        W06: Heuristic check — dialogue containing 'condition' or 'I have a condition'
        without the condition itself stated in the same or following panel.

        This is a heuristic; it flags for human review rather than blocking.
        """
        warnings: list[ValidationError] = []
        trigger_phrases = ["i have a condition", "one condition", "my condition is"]

        for idx, panel in enumerate(panels):
            for d in panel.dialogue:
                text_lower = d.text.lower()
                if any(phrase in text_lower for phrase in trigger_phrases):
                    # Check if current or next panel elaborates the condition
                    next_panel = panels[idx + 1] if idx + 1 < len(panels) else None
                    current_texts = " ".join(
                        x.text.lower() for x in panel.dialogue
                    )
                    next_texts = (
                        " ".join(x.text.lower() for x in next_panel.dialogue)
                        if next_panel
                        else ""
                    )
                    # If neither panel has enough elaborating text, flag it
                    combined = current_texts + " " + next_texts
                    elaboration_keywords = [
                        "must", "shall", "will", "requires", "demands",
                        "stipulates", "insists", "swear", "oath", "promise"
                    ]
                    has_elaboration = any(kw in combined for kw in elaboration_keywords)

                    if not has_elaboration:
                        warnings.append(ValidationError(
                            panel_id=panel.panel_id,
                            rule_name="W06_CONDITION_WITHOUT_FOLLOW_THROUGH",
                            severity="warning",
                            message=(
                                f"'{d.speaker}' states a condition (\"{d.text[:40]}...\") "
                                f"but the condition content is not clear in this or the following panel. "
                                f"Verify the condition is stated explicitly."
                            ),
                        ))
        return warnings

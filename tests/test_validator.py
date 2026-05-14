"""
tests/test_validator.py

Tests for ScreenplayValidator — covers all 16 rules (10 hard errors + 6 warnings).
Each test builds a minimal valid screenplay and then mutates one thing to trigger the rule.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from dharmapath.models.screenplay import (
    Arc, Beat, Chapter, Dialogue, DialogueType, Panel, Path as LearningPath,
    Screenplay, Size, ShotType,
)
from dharmapath.registry.registry import CharacterRegistry
from dharmapath.validator.screenplay_validator import ScreenplayValidator, ValidationResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_panel(
    panel_id: str,
    beat: Beat = Beat.escalation,
    size: Size = Size.half,
    characters: list[str] | None = None,
    dialogue: list[Dialogue] | None = None,
    human_required: bool = False,
    shot_type: ShotType = ShotType.medium,
) -> Panel:
    """Helper: build a minimal valid Panel."""
    return Panel(
        panel_id=panel_id,
        size=size,
        beat=beat,
        shot_type=shot_type,
        characters=characters or [],
        action="Characters stand in the scene.",
        environment="A stone courtyard.",
        lighting="Midday sun.",
        camera="Medium shot.",
        mood="Neutral.",
        dialogue=dialogue or [],
        human_required=human_required,
    )


def _make_chapter() -> Chapter:
    return Chapter(
        chapter_id="itihaasa_ch01",
        path=LearningPath.itihaasa,
        arc=Arc.conflict,
        title="Test Chapter",
        description="Test.",
        arc_number=1,
    )


def _build_valid_screenplay(panel_count: int = 40) -> Screenplay:
    """
    Build a screenplay that passes all hard-error rules.
    Structure:
      p01 = hook (full)
      p02..p(N-3) = escalation (half), with thought dialogues scattered
      p(N-2) = impact (full, human_required=True)
      p(N-1) = quiet (half, no dialogue)
      p(N)   = close (full)
    """
    panels: list[Panel] = []

    # p01: hook
    panels.append(_make_panel(
        "p01", beat=Beat.hook, size=Size.full,
        dialogue=[Dialogue(speaker="Narrator", type=DialogueType.thought,
                           text="In the beginning there was only silence.")],
    ))

    # p02 to p(N-3): escalation — scatter 3 thought bubbles across first panels
    for i in range(2, panel_count - 2):
        pid = f"p{i:02d}"
        dlg: list[Dialogue] = []
        if i == 3:
            dlg = [Dialogue(speaker="Arjuna", type=DialogueType.thought, text="Why must I fight?")]
        elif i == 5:
            dlg = [Dialogue(speaker="Krishna", type=DialogueType.thought,
                            text="He does not yet understand his duty.")]
        panels.append(_make_panel(pid, beat=Beat.escalation, size=Size.half, dialogue=dlg))

    # p(N-2): impact
    impact_id = f"p{panel_count - 2:02d}"
    panels.append(_make_panel(
        impact_id, beat=Beat.impact, size=Size.full, human_required=True,
    ))

    # p(N-1): quiet
    quiet_id = f"p{panel_count - 1:02d}"
    panels.append(_make_panel(quiet_id, beat=Beat.quiet, size=Size.half, dialogue=[]))

    # p(N): close
    close_id = f"p{panel_count:02d}"
    panels.append(_make_panel(close_id, beat=Beat.close, size=Size.full))

    return Screenplay(chapter=_make_chapter(), panels=panels)


@pytest.fixture
def validator() -> ScreenplayValidator:
    return ScreenplayValidator(registry=None)


@pytest.fixture
def valid_screenplay() -> Screenplay:
    return _build_valid_screenplay(40)


# ── Fixture file test ─────────────────────────────────────────────────────────

class TestFixtureFile:
    def test_sample_fixture_loads(self) -> None:
        """Ensure the fixture JSON parses against the Screenplay model."""
        data = json.loads((FIXTURES_DIR / "sample_screenplay.json").read_text())
        sp = Screenplay.model_validate(data)
        assert sp.panel_count == 14

    def test_sample_fixture_fails_r09_panel_count(self, validator: ScreenplayValidator) -> None:
        """Fixture has 14 panels — should fail R09 (35–60 range)."""
        data = json.loads((FIXTURES_DIR / "sample_screenplay.json").read_text())
        sp = Screenplay.model_validate(data)
        result = validator.validate(sp)
        rule_names = [e.rule_name for e in result.hard_errors]
        assert "R09_PANEL_COUNT_RANGE" in rule_names


# ── R01: First panel hook ─────────────────────────────────────────────────────

class TestR01FirstPanelHook:
    def test_passes_when_p01_is_hook(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R01_FIRST_PANEL_HOOK" for e in result.errors)

    def test_fails_when_p01_not_hook(self, validator, valid_screenplay):
        valid_screenplay.panels[0].beat = Beat.escalation
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R01_FIRST_PANEL_HOOK" and e.severity == "error"
                   for e in result.errors)

    def test_error_references_correct_panel_id(self, validator, valid_screenplay):
        valid_screenplay.panels[0].beat = Beat.escalation
        result = validator.validate(valid_screenplay)
        err = next(e for e in result.errors if e.rule_name == "R01_FIRST_PANEL_HOOK")
        assert err.panel_id == "p01"


# ── R02: Exactly one impact ───────────────────────────────────────────────────

class TestR02ExactlyOneImpact:
    def test_passes_with_one_impact(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R02_EXACTLY_ONE_IMPACT" for e in result.errors)

    def test_fails_with_no_impact(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.impact:
                panel.beat = Beat.escalation
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R02_EXACTLY_ONE_IMPACT" and e.severity == "error"
                   for e in result.errors)

    def test_fails_with_two_impacts(self, validator, valid_screenplay):
        # Add a second impact beat to panel 2
        valid_screenplay.panels[1].beat = Beat.impact
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R02_EXACTLY_ONE_IMPACT" for e in result.errors)


# ── R03: Exactly one quiet ────────────────────────────────────────────────────

class TestR03ExactlyOneQuiet:
    def test_passes_with_one_quiet(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R03_EXACTLY_ONE_QUIET" for e in result.errors)

    def test_fails_with_no_quiet(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.quiet:
                panel.beat = Beat.escalation
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R03_EXACTLY_ONE_QUIET" for e in result.errors)

    def test_fails_with_two_quiet_panels(self, validator, valid_screenplay):
        valid_screenplay.panels[1].beat = Beat.quiet
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R03_EXACTLY_ONE_QUIET" for e in result.errors)


# ── R04: Last panel close ─────────────────────────────────────────────────────

class TestR04LastPanelClose:
    def test_passes_when_last_is_close(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R04_LAST_PANEL_CLOSE" for e in result.errors)

    def test_fails_when_last_is_not_close(self, validator, valid_screenplay):
        valid_screenplay.panels[-1].beat = Beat.escalation
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R04_LAST_PANEL_CLOSE" and e.severity == "error"
                   for e in result.errors)


# ── R05: Dialogue word count ──────────────────────────────────────────────────

class TestR05DialogueWordCount:
    def test_passes_with_25_words(self, validator, valid_screenplay):
        valid_screenplay.panels[1].dialogue = [
            Dialogue(speaker="A", type=DialogueType.speech,
                     text="one two three four five six seven eight nine ten "
                          "eleven twelve thirteen fourteen fifteen sixteen seventeen "
                          "eighteen nineteen twenty twenty-one twenty-two twenty-three twenty-four twenty-five")
        ]
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R05_DIALOGUE_WORD_COUNT" for e in result.errors)

    def test_pydantic_blocks_26_words_at_parse(self):
        with pytest.raises(Exception):
            Dialogue(
                speaker="A",
                type=DialogueType.speech,
                text="one two three four five six seven eight nine ten "
                     "eleven twelve thirteen fourteen fifteen sixteen seventeen "
                     "eighteen nineteen twenty twenty-one twenty-two twenty-three "
                     "twenty-four twenty-five TWENTY_SIX",
            )


# ── R06: Quiet panel word count ───────────────────────────────────────────────

class TestR06QuietPanelWordCount:
    def test_passes_quiet_with_zero_words(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R06_QUIET_PANEL_WORD_COUNT" for e in result.errors)

    def test_passes_quiet_with_10_words(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.quiet:
                panel.dialogue = [
                    Dialogue(speaker="A", type=DialogueType.speech,
                             text="one two three four five six seven eight nine ten")
                ]
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R06_QUIET_PANEL_WORD_COUNT" for e in result.errors)

    def test_fails_quiet_with_11_words(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.quiet:
                panel.dialogue = [
                    Dialogue(speaker="A", type=DialogueType.speech,
                             text="one two three four five six seven eight nine ten eleven")
                ]
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R06_QUIET_PANEL_WORD_COUNT" and e.severity == "error"
                   for e in result.errors)


# ── R07: Quarter panels in groups of four ────────────────────────────────────

class TestR07QuarterPanelsGroupsOfFour:
    def test_passes_with_group_of_four(self, validator, valid_screenplay):
        # Replace panels 2-5 with quarters
        for i in range(1, 5):
            valid_screenplay.panels[i].size = Size.quarter
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R07_QUARTER_PANELS_GROUPS_OF_FOUR" for e in result.errors)

    def test_passes_with_group_of_eight(self, validator, valid_screenplay):
        for i in range(1, 9):
            valid_screenplay.panels[i].size = Size.quarter
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R07_QUARTER_PANELS_GROUPS_OF_FOUR" for e in result.errors)

    def test_fails_with_group_of_three(self, validator, valid_screenplay):
        for i in range(1, 4):
            valid_screenplay.panels[i].size = Size.quarter
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R07_QUARTER_PANELS_GROUPS_OF_FOUR" and e.severity == "error"
                   for e in result.errors)

    def test_fails_with_group_of_five(self, validator, valid_screenplay):
        for i in range(1, 6):
            valid_screenplay.panels[i].size = Size.quarter
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R07_QUARTER_PANELS_GROUPS_OF_FOUR" for e in result.errors)


# ── R08: Impact panel requirements ───────────────────────────────────────────

class TestR08ImpactPanelRequirements:
    def test_passes_full_and_human_required(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R08_IMPACT_PANEL_REQUIREMENTS" for e in result.errors)

    def test_fails_when_impact_not_full(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.impact:
                panel.size = Size.half
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R08_IMPACT_PANEL_REQUIREMENTS" for e in result.errors)

    def test_fails_when_impact_not_human_required(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.impact:
                panel.human_required = False
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "R08_IMPACT_PANEL_REQUIREMENTS" for e in result.errors)

    def test_fails_both_conditions_missing(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            if panel.beat == Beat.impact:
                panel.size = Size.half
                panel.human_required = False
        result = validator.validate(valid_screenplay)
        errors = [e for e in result.errors if e.rule_name == "R08_IMPACT_PANEL_REQUIREMENTS"]
        assert len(errors) == 1
        assert "size=half" in errors[0].message
        assert "human_required=False" in errors[0].message


# ── R09: Panel count ──────────────────────────────────────────────────────────

class TestR09PanelCount:
    def test_passes_at_40(self, validator, valid_screenplay):
        assert valid_screenplay.panel_count == 40
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "R09_PANEL_COUNT_RANGE" for e in result.errors)

    def test_passes_at_35(self, validator):
        sp = _build_valid_screenplay(35)
        result = validator.validate(sp)
        assert not any(e.rule_name == "R09_PANEL_COUNT_RANGE" for e in result.errors)

    def test_passes_at_60(self, validator):
        sp = _build_valid_screenplay(60)
        result = validator.validate(sp)
        assert not any(e.rule_name == "R09_PANEL_COUNT_RANGE" for e in result.errors)

    def test_fails_below_35(self, validator):
        sp = _build_valid_screenplay(34)
        result = validator.validate(sp)
        assert any(e.rule_name == "R09_PANEL_COUNT_RANGE" and e.severity == "error"
                   for e in result.errors)

    def test_fails_above_60(self, validator):
        sp = _build_valid_screenplay(61)
        result = validator.validate(sp)
        assert any(e.rule_name == "R09_PANEL_COUNT_RANGE" and e.severity == "error"
                   for e in result.errors)


# ── R10: Character approval (with registry) ───────────────────────────────────

class TestR10CharacterApproval:
    def _make_registry_with(self, approved: list[str], unapproved: list[str]) -> CharacterRegistry:
        reg = CharacterRegistry()
        reg.load()  # loads empty {}
        for name in approved:
            reg.register(name, f"{name} description", "itihaasa_ch01")
            reg.approve(name, "default",
                        f"data/characters/{name}_default.jpg",
                        f"data/characters_full/{name}_default.jpg")
        for name in unapproved:
            reg.register(name, f"{name} description", "itihaasa_ch01")
        return reg

    def test_passes_all_approved(self, valid_screenplay):
        valid_screenplay.panels[1].characters = ["Arjuna"]
        reg = self._make_registry_with(approved=["Arjuna"], unapproved=[])
        validator = ScreenplayValidator(registry=reg)
        result = validator.validate(valid_screenplay)
        assert not any("R10" in e.rule_name for e in result.errors)

    def test_fails_unapproved_character(self, valid_screenplay):
        valid_screenplay.panels[1].characters = ["Arjuna"]
        reg = self._make_registry_with(approved=[], unapproved=["Arjuna"])
        validator = ScreenplayValidator(registry=reg)
        result = validator.validate(valid_screenplay)
        assert any("R10_CHARACTER_NOT_APPROVED" in e.rule_name for e in result.errors)

    def test_fails_character_not_in_registry(self, valid_screenplay):
        valid_screenplay.panels[1].characters = ["UnknownHero"]
        reg = self._make_registry_with(approved=[], unapproved=[])
        validator = ScreenplayValidator(registry=reg)
        result = validator.validate(valid_screenplay)
        assert any("R10_CHARACTER_NOT_IN_REGISTRY" in e.rule_name for e in result.errors)


# ── W01: Thought bubble minimum ───────────────────────────────────────────────

class TestW01ThoughtBubbleMinimum:
    def test_passes_with_three_thoughts(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        # valid_screenplay has 3 thought bubbles (p01, p03, p05)
        assert not any(e.rule_name == "W01_THOUGHT_BUBBLE_MINIMUM" for e in result.errors)

    def test_warns_with_two_thoughts(self, validator, valid_screenplay):
        # Remove thought from p05
        valid_screenplay.panels[4].dialogue = []
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W01_THOUGHT_BUBBLE_MINIMUM" and e.severity == "warning"
                   for e in result.errors)

    def test_warns_with_zero_thoughts(self, validator, valid_screenplay):
        for panel in valid_screenplay.panels:
            panel.dialogue = [d for d in panel.dialogue if d.type != DialogueType.thought]
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W01_THOUGHT_BUBBLE_MINIMUM" for e in result.errors)


# ── W02: Consecutive half panels ─────────────────────────────────────────────

class TestW02ConsecutiveHalfPanels:
    def test_no_warning_with_four_consecutive(self, validator, valid_screenplay):
        # panels 1-4 are already half — 4 consecutive, no warning
        for i in range(1, 5):
            valid_screenplay.panels[i].size = Size.half
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "W02_CONSECUTIVE_HALF_PANELS" for e in result.errors)

    def test_warns_with_five_consecutive_halves(self, validator, valid_screenplay):
        for i in range(1, 6):
            valid_screenplay.panels[i].size = Size.half
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W02_CONSECUTIVE_HALF_PANELS" and e.severity == "warning"
                   for e in result.errors)


# ── W03: Too many full panels ─────────────────────────────────────────────────

class TestW03TooManyFullPanels:
    def test_no_warning_with_three_fulls(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        full_count = sum(1 for p in valid_screenplay.panels if p.size == Size.full)
        if full_count <= 5:
            assert not any(e.rule_name == "W03_TOO_MANY_FULL_PANELS" for e in result.errors)

    def test_warns_with_six_full_panels(self, validator, valid_screenplay):
        # Force 6 full panels
        count = 0
        for panel in valid_screenplay.panels:
            if count < 6 and panel.beat not in (Beat.impact, Beat.hook, Beat.close):
                panel.size = Size.full
                count += 1
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W03_TOO_MANY_FULL_PANELS" for e in result.errors)


# ── W04: Silent named characters ─────────────────────────────────────────────

class TestW04SilentNamedCharacters:
    def test_warns_for_character_with_no_dialogue(self, validator, valid_screenplay):
        valid_screenplay.panels[1].characters = ["Karna"]
        # Karna has no dialogue anywhere
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W04_SILENT_NAMED_CHARACTER" and "Karna" in e.message
                   for e in result.errors)

    def test_no_warning_when_character_has_speech(self, validator, valid_screenplay):
        valid_screenplay.panels[1].characters = ["Karna"]
        valid_screenplay.panels[1].dialogue.append(
            Dialogue(speaker="Karna", type=DialogueType.speech, text="I am here.")
        )
        result = validator.validate(valid_screenplay)
        assert not any("Karna" in e.message for e in result.errors
                       if e.rule_name == "W04_SILENT_NAMED_CHARACTER")


# ── W05: Caption word count ───────────────────────────────────────────────────

class TestW05CaptionWordCount:
    def test_passes_with_8_word_caption(self, validator, valid_screenplay):
        valid_screenplay.panels[1].dialogue = [
            Dialogue(speaker="NARRATOR", type=DialogueType.caption,
                     text="The battle had not yet begun today.")
        ]
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "W05_CAPTION_TOO_LONG" for e in result.errors)

    def test_warns_with_9_word_caption(self, validator, valid_screenplay):
        valid_screenplay.panels[1].dialogue = [
            Dialogue(speaker="NARRATOR", type=DialogueType.caption,
                     text="The long battle had not yet truly begun today.")
        ]
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W05_CAPTION_TOO_LONG" and e.severity == "warning"
                   for e in result.errors)


# ── W06: Condition without follow-through ────────────────────────────────────

class TestW06ConditionWithoutFollowThrough:
    def test_warns_when_condition_stated_without_detail(self, validator, valid_screenplay):
        valid_screenplay.panels[1].dialogue = [
            Dialogue(speaker="Shakuni", type=DialogueType.speech,
                     text="I have a condition before we begin.")
        ]
        result = validator.validate(valid_screenplay)
        assert any(e.rule_name == "W06_CONDITION_WITHOUT_FOLLOW_THROUGH"
                   for e in result.errors)

    def test_no_warning_when_condition_is_elaborated(self, validator, valid_screenplay):
        valid_screenplay.panels[1].dialogue = [
            Dialogue(speaker="Shakuni", type=DialogueType.speech,
                     text="I have a condition. You must swear on Dharma itself."),
        ]
        result = validator.validate(valid_screenplay)
        assert not any(e.rule_name == "W06_CONDITION_WITHOUT_FOLLOW_THROUGH"
                       for e in result.errors)


# ── ValidationResult helpers ──────────────────────────────────────────────────

class TestValidationResult:
    def test_passed_returns_true_when_no_hard_errors(self, validator, valid_screenplay):
        result = validator.validate(valid_screenplay)
        assert result.passed()

    def test_summary_shows_blocked_when_hard_errors(self, validator):
        sp = _build_valid_screenplay(40)
        sp.panels[0].beat = Beat.escalation  # trigger R01
        result = validator.validate(sp)
        assert "BLOCKED" in result.summary()

    def test_summary_shows_passed_with_only_warnings(self, validator, valid_screenplay):
        # Cause a warning by adding a silent character
        valid_screenplay.panels[1].characters = ["SilentKing"]
        result = validator.validate(valid_screenplay)
        if not result.has_hard_errors:
            assert "PASSED WITH WARNINGS" in result.summary() or "passed" in result.summary()

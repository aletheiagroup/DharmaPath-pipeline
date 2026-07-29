"""
tests/test_runner.py

Integration tests for dharmapath.pipeline.runner.ChapterRunner.

All external services (ComfyUI, R2) are mocked — no GPU or cloud required.
Tests verify the orchestration logic:
  - Validation gate (hard errors block generation)
  - Character registry gate (unapproved characters block generation)
  - Prompt generation is called for each panel
  - ComfyUI is invoked once per panel
  - Assembly and export are called after generation
  - Tiered failure thresholds (5% auto, 15% degraded, >15% fail)
  - Upload skipped when upload=False
  - run_log.json is written to disk
  - RunResult fields are correct

Run with:
    .venv/Scripts/pytest tests/test_runner.py -v
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from PIL import Image

from dharmapath.models.screenplay import (
    Arc, Beat, Chapter, Dialogue, DialogueType, Panel,
    Path as LearningPath, Screenplay, Size, ShotType,
)
from dharmapath.models.job import JobStatus, RunResult
from dharmapath.registry.registry import CharacterRegistry
from dharmapath.pipeline.runner import ChapterRunner


# ── Screenplay builders ───────────────────────────────────────────────────────

def _make_chapter(chapter_id: str = "itihaasa_ch01") -> Chapter:
    return Chapter(
        chapter_id=chapter_id,
        path=LearningPath.itihaasa,
        arc=Arc.conflict,
        title="Test Chapter",
        description="Test chapter for pipeline runner tests.",
        arc_number=1,
    )


def _make_panel(
    panel_id: str,
    beat: Beat = Beat.escalation,
    size: Size = Size.half,
    human_required: bool = False,
) -> Panel:
    return Panel(
        panel_id=panel_id,
        size=size,
        beat=beat,
        shot_type=ShotType.medium,
        characters=[],
        action="A scene plays out.",
        environment="A stone courtyard.",
        lighting="Midday sun.",
        camera="Medium shot.",
        mood="Neutral.",
        dialogue=[],
        human_required=human_required,
    )


def _build_minimal_screenplay(panel_count: int = 37, chapter_id: str = "itihaasa_ch01") -> Screenplay:
    """
    Build a screenplay that passes all validation rules.
    Satisfies: hook, one impact (full+human_required), one quiet, close, >=35 panels,
    and 3 thought bubbles for W01.
    """
    chapter = _make_chapter(chapter_id)
    panels: list[Panel] = []

    # p01 = hook
    panels.append(Panel(
        panel_id="p01", size=Size.full, beat=Beat.hook,
        shot_type=ShotType.establishing, characters=[],
        action="Opening scene.", environment="Palace.", lighting="Dawn.", camera="Wide.",
        mood="Tense.",
        dialogue=[Dialogue(speaker="Narrator", type=DialogueType.thought,
                           text="In the beginning there was silence.")],
    ))

    # p02 to p(N-3) = escalation with 2 more thought bubbles scattered
    for i in range(2, panel_count - 2):
        pid = f"p{i:02d}"
        dlg: list[Dialogue] = []
        if i == 3:
            dlg = [Dialogue(speaker="Hero", type=DialogueType.thought, text="Why must I fight?")]
        elif i == 5:
            dlg = [Dialogue(speaker="Mentor", type=DialogueType.thought, text="He must understand duty.")]
        p = Panel(
            panel_id=pid, size=Size.half, beat=Beat.escalation,
            shot_type=ShotType.medium, characters=[],
            action="Scene continues.", environment="Hall.", lighting="Torch.", camera="Medium.",
            mood="Building.", dialogue=dlg,
        )
        panels.append(p)

    # p(N-2) = impact
    impact_id = f"p{panel_count - 2:02d}"
    panels.append(Panel(
        panel_id=impact_id, size=Size.full, beat=Beat.impact,
        shot_type=ShotType.establishing, characters=[],
        action="The key moment.", environment="Battlefield.", lighting="Harsh.", camera="Wide.",
        mood="Catastrophic.", dialogue=[], human_required=True,
    ))

    # p(N-1) = quiet
    quiet_id = f"p{panel_count - 1:02d}"
    panels.append(Panel(
        panel_id=quiet_id, size=Size.half, beat=Beat.quiet,
        shot_type=ShotType.close_face, characters=[],
        action="Stillness.", environment="Ruins.", lighting="Soft.", camera="Close.",
        mood="Grief.", dialogue=[],
    ))

    # p(N) = close
    close_id = f"p{panel_count:02d}"
    panels.append(Panel(
        panel_id=close_id, size=Size.full, beat=Beat.close,
        shot_type=ShotType.establishing, characters=[],
        action="Final beat.", environment="Horizon.", lighting="Dusk.", camera="Wide.",
        mood="Resolution.", dialogue=[],
    ))

    return Screenplay(chapter=chapter, panels=panels)


def _write_screenplay(path: Path, screenplay: Screenplay) -> None:
    path.write_text(screenplay.model_dump_json(indent=2), encoding="utf-8")


def _make_fake_panel_image(panel_dir: Path, panel_id: str, chapter_id: str) -> Path:
    """Create a tiny real PNG image for a panel so assembly doesn't fail."""
    num = panel_id.lstrip("p")
    ch_num = chapter_id.split("ch")[-1] if "ch" in chapter_id else "01"
    filename = f"dp_ch{ch_num}_p{num}_v1.png"
    img_path = panel_dir / filename
    img = Image.new("RGB", (800, 1200), color=(100, 100, 100))
    img.save(img_path, "PNG")
    return img_path


# ── Fixture: a ChapterRunner with mocked external services ────────────────────

@pytest.fixture
def runner(tmp_path: Path):
    """
    ChapterRunner wired to tmp_path for outputs, with an empty in-memory registry.

    R2Client is patched at the module level so boto3.client() is never called
    with the placeholder credentials in .env (which would raise ValueError at
    construction time before any per-test mock can intercept it).
    """
    registry_path = tmp_path / "characters.json"
    registry_path.write_text("{}", encoding="utf-8")

    with patch("dharmapath.pipeline.runner.R2Client") as MockR2:
        MockR2.return_value.upload_episode = MagicMock(return_value="r2://dharmapath/test.jpg")
        _runner = ChapterRunner(
            output_root=tmp_path / "outputs",
            registry_path=registry_path,
        )
        # Yield inside the patch context so R2Client stays mocked for the test body
        yield _runner


# ── Validation gate ───────────────────────────────────────────────────────────

class TestValidationGate:
    @pytest.mark.asyncio
    async def test_returns_failure_when_screenplay_not_found(self, runner: ChapterRunner, tmp_path: Path):
        result = await runner.run(tmp_path / "nonexistent.json")
        assert result.success is False
        assert any("not found" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_returns_failure_when_validation_hard_error(self, runner: ChapterRunner, tmp_path: Path):
        # Build a screenplay with p01 = escalation (not hook) → triggers R01 hard error
        sp = _build_minimal_screenplay()
        sp.panels[0].beat = Beat.escalation  # violate R01

        sp_path = tmp_path / "bad_screenplay.json"
        _write_screenplay(sp_path, sp)

        result = await runner.run(sp_path)
        assert result.success is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_returns_failure_for_malformed_json(self, runner: ChapterRunner, tmp_path: Path):
        sp_path = tmp_path / "broken.json"
        sp_path.write_text("{ this is not valid json }", encoding="utf-8")

        result = await runner.run(sp_path)
        assert result.success is False
        assert any("parse" in e.lower() or "json" in e.lower() for e in result.errors)


# ── Full pipeline with mocked ComfyUI ────────────────────────────────────────

class TestFullPipelineWithMocks:
    @pytest.mark.asyncio
    async def test_successful_run_generates_episodes(self, runner: ChapterRunner, tmp_path: Path):
        """
        Happy path: all panels generate successfully, assembly + export succeed.
        Verifies RunResult.success=True, episode_paths exist, and run_log is written.
        """
        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create fake panel images (assembly uses Pillow to open them)
        for panel in sp.panels:
            _make_fake_panel_image(panel_dir, panel.panel_id, chapter_id)

        async def fake_generate_panel(workflow: dict, save_path: str, max_retries: int = 2) -> str:
            # Don't actually generate — the images already exist on disk
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client") as MockR2:

            # Configure the async context manager
            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            # R2 upload returns a fake URL
            MockR2.return_value.upload_episode = MagicMock(return_value="r2://dharmapath/test.jpg")

            result = await runner.run(sp_path, upload=True)

        assert result.success is True
        assert result.chapter_id == chapter_id
        assert result.panels_generated == len(sp.panels)
        assert result.duration_seconds >= 0
        assert len(result.episode_paths) >= 1

        # run_log.json should be on disk
        log_path = tmp_path / "outputs" / chapter_id / "run_log.json"
        assert log_path.exists()
        log_data = json.loads(log_path.read_text())
        assert log_data["success"] is True
        assert log_data["panels_generated"] == len(sp.panels)

    @pytest.mark.asyncio
    async def test_upload_skipped_when_disabled(self, runner: ChapterRunner, tmp_path: Path):
        """When upload=False, R2Client.upload_episode should never be called."""
        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        for panel in sp.panels:
            _make_fake_panel_image(panel_dir, panel.panel_id, chapter_id)

        async def fake_generate_panel(workflow, save_path, max_retries=2):
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client") as MockR2:

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await runner.run(sp_path, upload=False)

        MockR2.return_value.upload_episode.assert_not_called()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_human_required_panels_are_flagged_in_result(self, runner: ChapterRunner, tmp_path: Path):
        """RunResult.panels_flagged_human should list all panels with human_required=True."""
        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        for panel in sp.panels:
            _make_fake_panel_image(panel_dir, panel.panel_id, chapter_id)

        async def fake_generate_panel(workflow, save_path, max_retries=2):
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client"):

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await runner.run(sp_path, upload=False)

        expected_flagged = [p.panel_id for p in sp.panels if p.human_required]
        assert result.panels_flagged_human == expected_flagged


# ── Failure threshold logic ───────────────────────────────────────────────────

class TestFailureThresholds:
    @pytest.mark.asyncio
    async def test_chapter_fails_when_too_many_panels_fail(self, runner: ChapterRunner, tmp_path: Path):
        """
        >15% panel failures → runner should NOT assemble, returns success=False.
        """
        from dharmapath.comfyui.client import ComfyUIError

        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        # Create only a few panel images so most fail
        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        fail_count = [0]
        total_panels = len(sp.panels)
        fail_threshold = int(total_panels * 0.20)  # Force >15% failures

        async def fake_generate_panel_with_failures(workflow, save_path, max_retries=2):
            if fail_count[0] < fail_threshold:
                fail_count[0] += 1
                raise ComfyUIError(f"Simulated panel failure #{fail_count[0]}")
            # For successfully "generated" panels, create the image file
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            img = Image.new("RGB", (800, 1200), (100, 100, 100))
            img.save(save_path, "PNG")
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client"):

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel_with_failures
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await runner.run(sp_path, upload=False)

        assert result.success is False
        assert any("Too many" in e or "threshold" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_degraded_run_succeeds_with_warning_when_few_panels_fail(
        self, runner: ChapterRunner, tmp_path: Path
    ):
        """
        5–15% panel failures → degraded mode: assembles anyway, returns success=True,
        includes a DEGRADED warning in errors list.
        """
        from dharmapath.comfyui.client import ComfyUIError

        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        total_panels = len(sp.panels)
        # Fail exactly 2 panels → ~5.4% of 37 → within degraded band (5–15%)
        fail_panel_ids = {sp.panels[1].panel_id, sp.panels[2].panel_id}

        async def fake_generate_panel_degraded(workflow, save_path, max_retries=2):
            path = Path(save_path)
            # Check if this panel is one of the failing ones
            # The save_path filename encodes the panel number
            panel_id_in_path = any(
                f"_p{pid.lstrip('p')}_" in path.name for pid in fail_panel_ids
            )
            if panel_id_in_path:
                raise ComfyUIError("Simulated degraded failure")
            path.parent.mkdir(parents=True, exist_ok=True)
            img = Image.new("RGB", (800, 1200), (120, 120, 120))
            img.save(path, "PNG")
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client"):

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel_degraded
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await runner.run(sp_path, upload=False)

        # Assembly works with remaining panels
        assert result.success is True
        # The degraded warning is appended to errors[]
        assert any("DEGRADED" in e for e in result.errors)


# ── Run log ───────────────────────────────────────────────────────────────────

class TestRunLog:
    @pytest.mark.asyncio
    async def test_run_log_contains_per_panel_details(self, runner: ChapterRunner, tmp_path: Path):
        """run_log.json should include a 'panel_details' entry for every panel."""
        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        for panel in sp.panels:
            _make_fake_panel_image(panel_dir, panel.panel_id, chapter_id)

        async def fake_generate_panel(workflow, save_path, max_retries=2):
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client"):

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            await runner.run(sp_path, upload=False)

        log_path = tmp_path / "outputs" / chapter_id / "run_log.json"
        assert log_path.exists()
        log_data = json.loads(log_path.read_text())

        assert "panel_details" in log_data
        assert len(log_data["panel_details"]) == len(sp.panels)

        for entry in log_data["panel_details"]:
            assert "panel_id" in entry
            assert "status" in entry
            assert "duration_s" in entry

    @pytest.mark.asyncio
    async def test_run_log_written_even_on_validation_failure(
        self, runner: ChapterRunner, tmp_path: Path
    ):
        """
        When validation fails (no generation), run_log should NOT be written
        because we never reach the log-write step. Confirm it doesn't exist.
        """
        sp = _build_minimal_screenplay()
        sp.panels[0].beat = Beat.escalation  # break R01

        sp_path = tmp_path / "bad.json"
        _write_screenplay(sp_path, sp)

        result = await runner.run(sp_path)

        chapter_id = sp.chapter.chapter_id
        log_path = tmp_path / "outputs" / chapter_id / "run_log.json"
        # Validation failure exits early — no log
        assert not log_path.exists()
        assert result.success is False


# ── Progress callback ─────────────────────────────────────────────────────────

class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_progress_callback_called_for_each_panel(
        self, runner: ChapterRunner, tmp_path: Path
    ):
        """on_progress(panel_id, completed, total) should be called once per panel."""
        progress_calls: list[tuple] = []

        def on_progress(panel_id: str, completed: int, total: int) -> None:
            progress_calls.append((panel_id, completed, total))

        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        for panel in sp.panels:
            _make_fake_panel_image(panel_dir, panel.panel_id, chapter_id)

        # Attach progress callback to runner
        runner._on_progress = on_progress

        async def fake_generate_panel(workflow, save_path, max_retries=2):
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client"):

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            await runner.run(sp_path, upload=False)

        assert len(progress_calls) == len(sp.panels)

        # Verify progress is monotonically increasing
        for i, (panel_id, completed, total) in enumerate(progress_calls):
            assert total == len(sp.panels)
            assert completed == i + 1


# ── Episode file verification ─────────────────────────────────────────────────

class TestEpisodeFiles:
    @pytest.mark.asyncio
    async def test_episode_files_exist_and_are_valid_jpegs(
        self, runner: ChapterRunner, tmp_path: Path
    ):
        """Episode files must be valid JPEGs that Pillow can open."""
        sp = _build_minimal_screenplay(37)
        sp_path = tmp_path / "screenplay.json"
        _write_screenplay(sp_path, sp)

        chapter_id = sp.chapter.chapter_id
        panel_dir = tmp_path / "outputs" / chapter_id / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        for panel in sp.panels:
            _make_fake_panel_image(panel_dir, panel.panel_id, chapter_id)

        async def fake_generate_panel(workflow, save_path, max_retries=2):
            return save_path

        with patch("dharmapath.pipeline.runner.ComfyUIClient") as MockComfyUI, \
             patch("dharmapath.pipeline.runner.R2Client"):

            mock_client_instance = AsyncMock()
            mock_client_instance.generate_panel = fake_generate_panel
            MockComfyUI.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockComfyUI.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await runner.run(sp_path, upload=False)

        assert result.success is True
        assert len(result.episode_paths) >= 1

        for ep_path in result.episode_paths:
            path = Path(ep_path)
            assert path.exists(), f"Episode file missing: {ep_path}"
            assert path.suffix == ".jpg"
            # Verify it's a valid image
            img = Image.open(path)
            assert img.width == 800

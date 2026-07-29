"""
dharmapath/pipeline/runner.py

ChapterRunner — the main orchestrator for the DharmaPath pipeline.
Handles the end-to-end flow from screenplay JSON to cloud-hosted episodes.

Resilience features:
  - Tiered failure thresholds (0-5% auto, 5-15% degraded, >15% fail)
  - Per-panel retries (delegated to ComfyUI client)
  - Progress callbacks for UI/CLI integration
  - Structured run log (JSON) written per chapter for post-mortem analysis
  - Partial success — assembles available panels even if some failed
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dharmapath.assembler.assembler import ChapterAssembler
from dharmapath.assembler.exporter import ChapterExporter
from dharmapath.comfyui.client import ComfyUIClient, ComfyUIError
from dharmapath.comfyui.workflow_builder import WorkflowBuilder
from dharmapath.models.job import BatchJob, GenerationJob, JobStatus, RunResult
from dharmapath.models.screenplay import Screenplay
from dharmapath.prompt_generator.generator import PromptGenerator
from dharmapath.registry.registry import CharacterRegistry
from dharmapath.storage.r2_client import R2Client
from dharmapath.validator.screenplay_validator import ScreenplayValidator

logger = logging.getLogger(__name__)

# Config paths
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


# ── Run Log Entry ────────────────────────────────────────────────────────────

def _build_panel_log(
    panel_id: str,
    status: str,
    duration_s: float,
    retries: int = 0,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a structured log entry for a single panel generation."""
    entry: dict[str, Any] = {
        "panel_id": panel_id,
        "status": status,
        "duration_s": round(duration_s, 2),
        "retries": retries,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if error:
        entry["error"] = error
    return entry


class ChapterRunner:
    """
    Orchestrates the 9-step manhwa generation pipeline with production resilience.

    Failure thresholds (configurable):
      - 0-5% failures  → assemble automatically (success)
      - 5-15% failures → assemble but mark as degraded (success with warnings)
      - >15% failures  → fail the chapter (do not assemble)
    """

    def __init__(
        self,
        output_root: Path | str = "data/outputs",
        registry_path: Path | str = "dharmapath/registry/characters.json",
        max_failure_pct_auto: float = 5.0,
        max_failure_pct_degraded: float = 15.0,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """
        Args:
            output_root: Root directory for generated outputs.
            registry_path: Path to the character registry JSON.
            max_failure_pct_auto: Max failure % for clean assembly (default 5%).
            max_failure_pct_degraded: Max failure % for degraded assembly (default 15%).
            on_progress: Optional callback(panel_id, completed, total) for progress tracking.
        """
        self.output_root = Path(output_root)
        self.registry = CharacterRegistry(Path(registry_path))
        self.registry.load()

        self.validator = ScreenplayValidator(self.registry)
        self.prompt_gen = PromptGenerator()
        self.workflow_builder = WorkflowBuilder()
        self.assembler = ChapterAssembler()
        self.exporter = ChapterExporter()
        self.r2 = R2Client()

        self._max_failure_pct_auto = max_failure_pct_auto
        self._max_failure_pct_degraded = max_failure_pct_degraded
        self._on_progress = on_progress

        # Load style profiles for workflow building
        self._style_profiles = json.loads(
            (_CONFIG_DIR / "style_profiles.json").read_text(encoding="utf-8")
        )

    def _report_progress(self, panel_id: str, completed: int, total: int) -> None:
        """Report panel progress via callback if registered."""
        if self._on_progress:
            try:
                self._on_progress(panel_id, completed, total)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")

    def _write_run_log(
        self,
        chapter_id: str,
        output_dir: Path,
        panel_logs: list[dict],
        run_result: RunResult,
        start_time: float,
    ) -> None:
        """Write structured run log to data/outputs/{chapter_id}/run_log.json."""
        run_log = {
            "chapter_id": chapter_id,
            "started_at": datetime.utcfromtimestamp(start_time).isoformat() + "Z",
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "duration_s": round(run_result.duration_seconds, 2),
            "success": run_result.success,
            "panels_total": len(panel_logs),
            "panels_generated": run_result.panels_generated,
            "panels_failed": len(panel_logs) - run_result.panels_generated,
            "panels_flagged_human": run_result.panels_flagged_human,
            "failure_pct": round(
                ((len(panel_logs) - run_result.panels_generated) / max(len(panel_logs), 1)) * 100, 1
            ),
            "errors": run_result.errors,
            "panel_details": panel_logs,
        }

        log_path = output_dir / "run_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(run_log, indent=2), encoding="utf-8")
        logger.info(f"Run log written to {log_path}")

    async def run(
        self,
        screenplay_path: Path | str,
        upload: bool = True,
    ) -> RunResult:
        """
        Execute the full pipeline for a single screenplay.

        Steps:
        1. Load & Validate Screenplay
        2. Check Character Registry Approval
        3. Setup Job Tracking
        4. Generate Prompts (Jinja2)
        5. Build Workflows (LoRA/IP-Adapter/ControlNet injection)
        6. Queue & Poll ComfyUI (Async HTTP) — with per-panel retries
        7. Assess Failure Threshold — tiered decision
        8. Assemble Panels (Vertical Strip) — skipping failed panels
        9. Export Episodes (Split & JPG)
        10. Upload to Cloud Storage
        11. Write Run Log
        """
        start_time = time.time()
        path = Path(screenplay_path)
        panel_logs: list[dict] = []

        if not path.exists():
            return RunResult(
                chapter_id="unknown",
                success=False,
                panels_generated=0,
                errors=[f"Screenplay file not found: {path}"],
            )

        # ── Step 1: Load & Validate ──────────────────────────────
        try:
            screenplay = Screenplay.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.exception("Failed to parse screenplay")
            return RunResult(
                chapter_id="unknown",
                success=False,
                panels_generated=0,
                errors=[f"Failed to parse screenplay JSON: {e}"],
            )

        chapter_id = screenplay.chapter.chapter_id
        logger.info(f"🚀 Starting pipeline run for {chapter_id}")

        validation = self.validator.validate(screenplay)
        if not validation.passed():
            logger.error(f"Validation failed for {chapter_id}")
            return RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=0,
                errors=[str(err) for err in validation.hard_errors],
            )

        # ── Step 2: Setup ────────────────────────────────────────
        batch = BatchJob(
            chapter_id=chapter_id,
            screenplay_path=str(path),
            status=JobStatus.generating,
        )

        output_dir = self.output_root / chapter_id
        panel_dir = output_dir / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 3: Generate Panels ──────────────────────────────
        path_key = screenplay.chapter.path.value
        style_profile = self._style_profiles.get(
            path_key, self._style_profiles.get("itihaasa", {})
        )

        total_panels = len(screenplay.panels)
        completed_count = 0

        async with ComfyUIClient() as client:
            for panel in screenplay.panels:
                panel_start = time.time()

                job = GenerationJob(
                    panel_id=panel.panel_id,
                    chapter_id=chapter_id,
                    status=JobStatus.queued,
                    started_at=datetime.utcnow(),
                )
                batch.jobs.append(job)

                try:
                    # Generate prompt
                    prompt_data = self.prompt_gen.generate_panel_prompt(
                        panel, screenplay.chapter, self.registry
                    )

                    # Resolve character face references
                    character_face_paths = {}
                    character_ip_weights = {}
                    for char_name in panel.characters:
                        state = self.registry.get_state_for_arc(
                            char_name, screenplay.chapter.arc_number
                        )
                        if state and state.reference_image:
                            character_face_paths[char_name] = state.reference_image
                            character_ip_weights[char_name] = style_profile.get(
                                "ip_adapter_weight", 0.65
                            )

                    # Build workflow
                    workflow = self.workflow_builder.build_panel_workflow(
                        prompt=prompt_data,
                        panel=panel,
                        chapter=screenplay.chapter,
                        style_profile=style_profile,
                        character_face_paths=character_face_paths,
                        character_ip_weights=character_ip_weights,
                    )

                    # Generate (ComfyUI client handles retries internally)
                    job.status = JobStatus.generating
                    save_path = (
                        panel_dir
                        / f"dp_ch{chapter_id.split('ch')[-1]}_p{panel.panel_id.lstrip('p')}_v1.png"
                    )

                    logger.info(f"Generating panel {panel.panel_id} ({completed_count + 1}/{total_panels})...")
                    await client.generate_panel(workflow, str(save_path))

                    job.status = JobStatus.complete
                    job.output_path = str(save_path)
                    job.completed_at = datetime.utcnow()
                    completed_count += 1

                    panel_duration = time.time() - panel_start
                    panel_logs.append(_build_panel_log(
                        panel.panel_id, "success", panel_duration,
                    ))

                    self._report_progress(panel.panel_id, completed_count, total_panels)

                except ComfyUIError as e:
                    panel_duration = time.time() - panel_start
                    logger.error(f"❌ Panel {panel.panel_id} failed after retries: {e}")
                    job.status = JobStatus.failed
                    job.error = str(e)
                    job.completed_at = datetime.utcnow()

                    panel_logs.append(_build_panel_log(
                        panel.panel_id, "failed", panel_duration, error=str(e),
                    ))

                    self._report_progress(panel.panel_id, completed_count, total_panels)

                except Exception as e:
                    panel_duration = time.time() - panel_start
                    logger.error(f"❌ Unexpected error on panel {panel.panel_id}: {e}")
                    job.status = JobStatus.failed
                    job.error = str(e)
                    job.completed_at = datetime.utcnow()

                    panel_logs.append(_build_panel_log(
                        panel.panel_id, "failed", panel_duration, error=str(e),
                    ))

        # ── Step 4: Assess Failure Threshold ─────────────────────
        failed_count = total_panels - completed_count
        failure_pct = (failed_count / max(total_panels, 1)) * 100

        if failure_pct > self._max_failure_pct_degraded:
            # >15% — fail the chapter entirely
            logger.error(
                f"💀 Chapter {chapter_id} FAILED: {failure_pct:.1f}% panels failed "
                f"({failed_count}/{total_panels}). Exceeds {self._max_failure_pct_degraded}% threshold."
            )
            result = RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=completed_count,
                errors=[
                    f"Too many panel failures: {failed_count}/{total_panels} "
                    f"({failure_pct:.1f}%) — threshold is {self._max_failure_pct_degraded}%."
                ],
                duration_seconds=time.time() - start_time,
                panels_flagged_human=[
                    p.panel_id for p in screenplay.panels if p.human_required
                ],
            )
            self._write_run_log(chapter_id, output_dir, panel_logs, result, start_time)
            return result

        is_degraded = failure_pct > self._max_failure_pct_auto
        if is_degraded:
            logger.warning(
                f"⚠️ Chapter {chapter_id} DEGRADED: {failure_pct:.1f}% panels failed "
                f"({failed_count}/{total_panels}). Assembling with available panels."
            )
        elif failed_count > 0:
            logger.info(
                f"✅ Chapter {chapter_id}: {failed_count} panel(s) failed "
                f"({failure_pct:.1f}%) — within auto-assembly threshold."
            )

        # ── Step 5: Assembly ─────────────────────────────────────
        logger.info(f"📦 Assembling {completed_count} panels into vertical strip for {chapter_id}")
        batch.status = JobStatus.assembling
        try:
            strip_path = self.assembler.assemble_chapter(screenplay, panel_dir)
        except Exception as e:
            logger.exception("Assembly failed")
            result = RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=completed_count,
                errors=[f"Assembly failed: {e}"],
                duration_seconds=time.time() - start_time,
            )
            self._write_run_log(chapter_id, output_dir, panel_logs, result, start_time)
            return result

        # ── Step 6: Export ───────────────────────────────────────
        logger.info(f"✂️  Splitting strip into Webtoon episodes for {chapter_id}")
        batch.status = JobStatus.exporting
        try:
            episode_paths = self.exporter.split_episodes(strip_path, chapter_id)
        except Exception as e:
            logger.exception("Export failed")
            result = RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=completed_count,
                errors=[f"Export failed: {e}"],
                duration_seconds=time.time() - start_time,
            )
            self._write_run_log(chapter_id, output_dir, panel_logs, result, start_time)
            return result

        # ── Step 7: Upload ──────────────────────────────────────
        cloud_urls: list[str] = []
        if upload:
            logger.info(f"☁️  Uploading {len(episode_paths)} episodes to storage")
            batch.status = JobStatus.uploading
            try:
                for ep_path in episode_paths:
                    url = self.r2.upload_episode(ep_path, chapter_id)
                    cloud_urls.append(url)
            except Exception as e:
                logger.warning(f"⚠️  Upload failed (non-fatal): {e}")
                # Non-fatal — panels are still on disk

        # ── Step 8: Finalise ────────────────────────────────────
        batch.status = JobStatus.complete
        batch.completed_at = datetime.utcnow()

        duration = time.time() - start_time
        status_emoji = "⚠️" if is_degraded else "✅"
        logger.info(f"{status_emoji} Chapter {chapter_id} complete! Duration: {duration:.1f}s")

        # Build errors list
        errors: list[str] = []
        if is_degraded:
            errors.append(
                f"DEGRADED: {failed_count}/{total_panels} panels failed "
                f"({failure_pct:.1f}%). Manual review recommended."
            )

        result = RunResult(
            chapter_id=chapter_id,
            success=True,
            panels_generated=completed_count,
            episode_paths=[str(p) for p in episode_paths],
            r2_urls=cloud_urls,
            duration_seconds=duration,
            panels_flagged_human=[
                p.panel_id for p in screenplay.panels if p.human_required
            ],
            errors=errors,
        )

        self._write_run_log(chapter_id, output_dir, panel_logs, result, start_time)
        return result

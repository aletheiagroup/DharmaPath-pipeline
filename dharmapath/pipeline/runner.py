"""
dharmapath/pipeline/runner.py

ChapterRunner — the main orchestrator for the DharmaPath pipeline.
Handles the end-to-end flow from screenplay JSON to R2-hosted episodes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from dharmapath.assembler.assembler import ChapterAssembler
from dharmapath.assembler.exporter import ChapterExporter
from dharmapath.comfyui.client import ComfyUIClient
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

class ChapterRunner:
    """
    Orchestrates the 9-step manhwa generation pipeline.
    """

    def __init__(
        self,
        output_root: Path | str = "data/outputs",
        registry_path: Path | str = "dharmapath/registry/characters.json",
    ):
        self.output_root = Path(output_root)
        self.registry = CharacterRegistry(Path(registry_path))
        self.registry.load()
        
        self.validator = ScreenplayValidator(self.registry)
        self.prompt_gen = PromptGenerator()
        self.workflow_builder = WorkflowBuilder()
        self.assembler = ChapterAssembler()
        self.exporter = ChapterExporter()
        self.r2 = R2Client()
        
        # Load style profiles for workflow building
        self._style_profiles = json.loads(
            (_CONFIG_DIR / "style_profiles.json").read_text(encoding="utf-8")
        )

    async def run(self, screenplay_path: Path | str, upload: bool = True) -> RunResult:
        """
        Executes the full pipeline for a single screenplay.
        
        Steps:
        1. Load & Validate Screenplay
        2. Check Character Registry Approval
        3. Setup Job Tracking
        4. Generate Prompts (Jinja2)
        5. Build Workflows (LoRA/IP-Adapter/ControlNet injection)
        6. Queue & Poll ComfyUI (Async HTTP)
        7. Assemble Panels (Vertical Strip)
        8. Export Episodes (Split & JPG)
        9. Upload to R2 (Persistence)
        """
        start_time = datetime.utcnow()
        path = Path(screenplay_path)
        
        if not path.exists():
            return RunResult(
                chapter_id="unknown",
                success=False,
                panels_generated=0,
                errors=[f"Screenplay file not found: {path}"]
            )

        # 1. Load & Validate Screenplay
        try:
            screenplay = Screenplay.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.exception("Failed to parse screenplay")
            return RunResult(
                chapter_id="unknown",
                success=False,
                panels_generated=0,
                errors=[f"Failed to parse screenplay JSON: {e}"]
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
                errors=[str(err) for err in validation.hard_errors]
            )

        # 2. Setup batch job tracking
        batch = BatchJob(
            chapter_id=chapter_id,
            screenplay_path=str(path),
            status=JobStatus.generating
        )
        
        output_dir = self.output_root / chapter_id
        panel_dir = output_dir / "panels"
        panel_dir.mkdir(parents=True, exist_ok=True)

        # 3. Generate and Run ComfyUI Jobs
        # We get the style profile for the chapter's path
        path_key = screenplay.chapter.path.value
        style_profile = self._style_profiles.get(path_key, self._style_profiles.get("itihaasa", {}))

        async with ComfyUIClient() as client:
            for panel in screenplay.panels:
                job = GenerationJob(
                    panel_id=panel.panel_id,
                    chapter_id=chapter_id,
                    status=JobStatus.queued,
                    started_at=datetime.utcnow()
                )
                batch.jobs.append(job)

                try:
                    # Step 4: Generate Prompts
                    prompt_data = self.prompt_gen.generate_panel_prompt(
                        panel, screenplay.chapter, self.registry
                    )
                    
                    # Resolve character face references from registry
                    character_face_paths = {}
                    character_ip_weights = {}
                    for char_name in panel.characters:
                        state = self.registry.get_state_for_arc(char_name, screenplay.chapter.arc_number)
                        if state and state.reference_image:
                            character_face_paths[char_name] = state.reference_image
                            # Use default weight if not specified in registry (which it currently isn't in models)
                            character_ip_weights[char_name] = style_profile.get("ip_adapter_weight", 0.65)

                    # Step 5: Build Workflow
                    workflow = self.workflow_builder.build_panel_workflow(
                        prompt=prompt_data,
                        panel=panel,
                        chapter=screenplay.chapter,
                        style_profile=style_profile,
                        character_face_paths=character_face_paths,
                        character_ip_weights=character_ip_weights
                    )

                    # Step 6: Queue & Poll ComfyUI
                    job.status = JobStatus.generating
                    save_path = panel_dir / f"dp_ch{chapter_id.split('ch')[-1]}_p{panel.panel_id.lstrip('p')}_v1.png"
                    
                    logger.info(f"Generating panel {panel.panel_id}...")
                    await client.generate_panel(workflow, str(save_path))
                    
                    job.status = JobStatus.complete
                    job.output_path = str(save_path)
                    job.completed_at = datetime.utcnow()
                    
                except Exception as e:
                    logger.error(f"❌ Failed to generate panel {panel.panel_id}: {e}")
                    job.status = JobStatus.failed
                    job.error = str(e)
                    job.completed_at = datetime.utcnow()

        # Check if we should proceed to assembly
        if batch.failed_panels > 0:
             return RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=batch.completed_panels,
                errors=[f"Generation failed for {batch.failed_panels}/{batch.total_panels} panels."]
            )

        # 7. Assembly
        logger.info(f"📦 Assembling panels into vertical strip for {chapter_id}")
        batch.status = JobStatus.assembling
        try:
            strip_path = self.assembler.assemble_chapter(screenplay, panel_dir)
        except Exception as e:
            logger.exception("Assembly failed")
            return RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=batch.completed_panels,
                errors=[f"Assembly failed: {e}"]
            )

        # 8. Export
        logger.info(f"✂️  Splitting strip into Webtoon episodes for {chapter_id}")
        batch.status = JobStatus.exporting
        try:
            episode_paths = self.exporter.split_episodes(strip_path, chapter_id)
        except Exception as e:
            logger.exception("Export failed")
            return RunResult(
                chapter_id=chapter_id,
                success=False,
                panels_generated=batch.completed_panels,
                errors=[f"Export failed: {e}"]
            )

        # 9. Upload (Optional)
        r2_urls = []
        if upload:
            logger.info(f"☁️  Uploading {len(episode_paths)} episodes to R2")
            batch.status = JobStatus.uploading
            try:
                for ep_path in episode_paths:
                    url = self.r2.upload_episode(ep_path, chapter_id)
                    r2_urls.append(url)
            except Exception as e:
                logger.warning(f"⚠️  R2 Upload failed (non-fatal): {e}")
                # We don't fail the whole run if upload fails, but we log it.

        batch.status = JobStatus.complete
        batch.completed_at = datetime.utcnow()
        
        duration = (batch.completed_at - start_time).total_seconds()
        logger.info(f"✅ Chapter {chapter_id} complete! Duration: {duration:.1f}s")
        
        return RunResult(
            chapter_id=chapter_id,
            success=True,
            panels_generated=batch.completed_panels,
            episode_paths=[str(p) for p in episode_paths],
            r2_urls=r2_urls,
            duration_seconds=duration,
            panels_flagged_human=[p.panel_id for p in screenplay.panels if p.human_required]
        )

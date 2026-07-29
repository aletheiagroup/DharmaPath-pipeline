"""
web/services/generation_service.py

Orchestrates pipeline runs:
  - Starts ChapterRunner as a background task via TaskManager
  - Updates JobStore progress on every panel callback
  - Emits typed RunEvents for SSE streaming
  - Enqueues regeneration requests via RegenerationQueue

Route → GenerationService → [TaskManager, JobStore, ChapterRunner]
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from dharmapath.pipeline.runner import ChapterRunner
from web.exceptions import NotFoundError, PipelineBusyError
from web.schemas.generation import RunRequest, RunSummary
from web.store.job_store import JobStore, MemoryJobStore, RunEvent, RunRecord, RunStatus
from web.store.task_manager import TaskManager

logger = logging.getLogger(__name__)

# SSE keepalive comment
_KEEPALIVE = ": keepalive\n\n"


class GenerationService:
    def __init__(
        self,
        job_store: JobStore,
        task_manager: TaskManager,
        registry_path: Path,
        outputs_dir: Path,
    ) -> None:
        self._job_store = job_store
        self._task_manager = task_manager
        self._registry_path = registry_path
        self._outputs_dir = outputs_dir

    # ── Public API ────────────────────────────────────────────────────────────

    async def start_run(self, request: RunRequest, screenplay_path: Path) -> RunRecord:
        """
        Validates there's no active run, then submits a background generation task.
        Returns immediately with the RunRecord (status=queued).
        """
        active = await self._job_store.get_active_run_for_chapter(request.chapter_id)
        if active:
            raise PipelineBusyError(request.chapter_id)

        # Load screenplay to get panel count
        from dharmapath.models.screenplay import Screenplay
        sp = Screenplay.model_validate_json(screenplay_path.read_text(encoding="utf-8"))

        run = await self._job_store.create_run(
            chapter_id=request.chapter_id,
            screenplay_path=str(screenplay_path),
            panels_total=sp.panel_count,
        )

        # Build and submit coroutine
        coro = self._execute_run(
            run_id=run.run_id,
            screenplay_path=screenplay_path,
            upload=request.upload,
            max_failure_pct_auto=request.max_failure_pct_auto,
            max_failure_pct_degraded=request.max_failure_pct_degraded,
        )
        await self._task_manager.submit(coro, task_id=run.run_id)
        logger.info("Submitted generation run %s for chapter %s", run.run_id, request.chapter_id)
        return run

    async def get_run(self, run_id: str) -> RunRecord:
        record = await self._job_store.get_run(run_id)
        if not record:
            raise NotFoundError("Run", run_id)
        return record

    async def list_runs(self, limit: int = 50) -> list[RunRecord]:
        return await self._job_store.list_runs(limit=limit)

    async def get_runs_for_chapter(self, chapter_id: str) -> list[RunRecord]:
        return await self._job_store.get_runs_for_chapter(chapter_id)

    async def cancel_run(self, run_id: str) -> RunRecord:
        record = await self.get_run(run_id)
        cancelled = await self._task_manager.cancel(run_id)
        if cancelled:
            record = await self._job_store.update_run(
                run_id,
                status=RunStatus.cancelled,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        return record

    async def stream_events(
        self,
        run_id: str,
        keepalive_interval: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """
        SSE generator: streams RunEvents for a specific run.
        Yields typed events as they arrive, then terminates when run is done.
        """
        record = await self._job_store.get_run(run_id)
        if not record:
            yield f"data: {{'type': 'error', 'message': 'Run {run_id} not found'}}\n\n"
            return

        sent_index = 0
        terminal_statuses = {RunStatus.complete, RunStatus.failed, RunStatus.cancelled}
        last_keepalive = time.monotonic()

        while True:
            events = await self._job_store.get_events(run_id)
            # Emit any new events
            while sent_index < len(events):
                yield events[sent_index].to_sse()
                sent_index += 1

            record = await self._job_store.get_run(run_id)

            # Terminal — emit final status and stop
            if record and record.status in terminal_statuses:
                import json
                final = json.dumps({
                    "type": "run_complete",
                    "status": record.status.value,
                    "panels_completed": record.panels_completed,
                    "panels_failed": record.panels_failed,
                    "errors": record.errors,
                })
                yield f"data: {final}\n\n"
                return

            # Keepalive
            now = time.monotonic()
            if now - last_keepalive > keepalive_interval:
                yield _KEEPALIVE
                last_keepalive = now

            await asyncio.sleep(0.5)

    # ── Internal pipeline coroutine ───────────────────────────────────────────

    async def _execute_run(
        self,
        run_id: str,
        screenplay_path: Path,
        upload: bool,
        max_failure_pct_auto: float,
        max_failure_pct_degraded: float,
    ) -> None:
        """
        The actual pipeline coroutine. Runs inside TaskManager.
        Updates JobStore on every panel completion.
        """
        await self._job_store.update_run(run_id, status=RunStatus.running)
        start_time = time.monotonic()

        async def on_progress(panel_id: str, completed: int, total: int) -> None:
            elapsed = time.monotonic() - start_time
            event = RunEvent(
                run_id=run_id,
                event_type="panel_completed",
                data={
                    "panel_id": panel_id,
                    "completed": completed,
                    "total": total,
                    "elapsed_s": round(elapsed, 1),
                    "progress_pct": round((completed / max(total, 1)) * 100, 1),
                },
            )
            await self._job_store.add_event(run_id, event)
            await self._job_store.update_run(
                run_id,
                panels_completed=completed,
            )

        try:
            runner = ChapterRunner(
                output_root=self._outputs_dir,
                registry_path=self._registry_path,
                max_failure_pct_auto=max_failure_pct_auto,
                max_failure_pct_degraded=max_failure_pct_degraded,
                on_progress=lambda panel_id, completed, total: asyncio.ensure_future(
                    on_progress(panel_id, completed, total)
                ),
            )

            result = await runner.run(screenplay_path, upload=upload)

            final_status = RunStatus.complete if result.success else RunStatus.failed
            await self._job_store.update_run(
                run_id,
                status=final_status,
                panels_completed=result.panels_generated,
                panels_failed=0 if result.success else max(0, (await self._job_store.get_run(run_id)).panels_total - result.panels_generated),
                errors=result.errors,
                completed_at=datetime.now(timezone.utc).isoformat(),
                result=result.to_dict(),
            )

        except Exception as e:
            logger.exception("Run %s failed with unhandled exception", run_id)
            await self._job_store.update_run(
                run_id,
                status=RunStatus.failed,
                errors=[str(e)],
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            await self._job_store.add_event(
                run_id,
                RunEvent(
                    run_id=run_id,
                    event_type="run_failed",
                    data={"error": str(e)},
                ),
            )

"""web/routes/generation.py — Generation runs and SSE event stream."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from web.dependencies import get_chapter_service, get_generation_service, require_api_key
from web.schemas.common import MessageResponse, SuccessResponse
from web.schemas.generation import RunDetail, RunRequest, RunSummary
from web.services.chapter_service import ChapterService
from web.services.generation_service import GenerationService
from web.store.job_store import RunRecord
from pathlib import Path

router = APIRouter(prefix="/generate", tags=["Generation"])
_auth = Depends(require_api_key)


def _run_to_summary(record: RunRecord) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        chapter_id=record.chapter_id,
        status=record.status.value,
        started_at=record.started_at,
        completed_at=record.completed_at,
        panels_total=record.panels_total,
        panels_completed=record.panels_completed,
        panels_failed=record.panels_failed,
        progress_pct=record.progress_pct,
        errors=record.errors,
    )


@router.post(
    "/run",
    response_model=SuccessResponse[RunSummary],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a full generation run",
    description="""
Submits a chapter screenplay to the generation pipeline.

Returns **202 Accepted** immediately with a `run_id`.
Connect to `GET /generate/runs/{run_id}/events` (SSE) to stream progress.

**Note**: Only one run may be active per chapter at a time.
If a run is already in progress, returns **409 Conflict**.
    """,
)
async def start_run(
    request: RunRequest,
    generation_svc: GenerationService = Depends(get_generation_service),
    chapter_svc: ChapterService = Depends(get_chapter_service),
    _: str = _auth,
) -> SuccessResponse[RunSummary]:
    sp = chapter_svc.get_screenplay(request.chapter_id)
    screenplay_path = Path(chapter_svc._screenplay_path(request.chapter_id))
    record = await generation_svc.start_run(request, screenplay_path)
    return SuccessResponse(data=_run_to_summary(record))


@router.get(
    "/runs",
    response_model=SuccessResponse[list[RunSummary]],
    summary="List all generation runs",
)
async def list_runs(
    limit: int = 50,
    generation_svc: GenerationService = Depends(get_generation_service),
    _: str = _auth,
) -> SuccessResponse[list[RunSummary]]:
    records = await generation_svc.list_runs(limit=limit)
    return SuccessResponse(data=[_run_to_summary(r) for r in records])


@router.get(
    "/runs/{run_id}",
    response_model=SuccessResponse[RunDetail],
    summary="Get run detail",
)
async def get_run(
    run_id: str,
    generation_svc: GenerationService = Depends(get_generation_service),
    _: str = _auth,
) -> SuccessResponse[RunDetail]:
    record = await generation_svc.get_run(run_id)
    detail = RunDetail(
        **_run_to_summary(record).model_dump(),
        panel_logs=[e.data for e in record.events],
        result=record.result,
    )
    return SuccessResponse(data=detail)


@router.get(
    "/runs/{run_id}/events",
    summary="SSE: stream run progress events",
    description="""
Server-Sent Events stream for a specific generation run.

**Event types:**
- `panel_completed` — a panel finished generating
- `panel_failed` — a panel failed (included in run but not assembly)
- `run_complete` — run finished (success or failure)
- `error` — internal error

Connect with `EventSource` in the browser or `curl -N` in CLI.
    """,
    response_class=StreamingResponse,
)
async def stream_run_events(
    run_id: str,
    generation_svc: GenerationService = Depends(get_generation_service),
    _: str = _auth,
) -> StreamingResponse:
    return StreamingResponse(
        generation_svc.stream_events(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=SuccessResponse[RunSummary],
    summary="Cancel an active run",
)
async def cancel_run(
    run_id: str,
    generation_svc: GenerationService = Depends(get_generation_service),
    _: str = _auth,
) -> SuccessResponse[RunSummary]:
    record = await generation_svc.cancel_run(run_id)
    return SuccessResponse(data=_run_to_summary(record))

"""web/routes/system.py — System health and SSE activity stream."""
from __future__ import annotations

import json
import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from web.dependencies import get_system_service, require_api_key
from web.schemas.system import SystemHealthResponse
from web.schemas.common import SuccessResponse
from web.services.system_service import SystemService

router = APIRouter(prefix="/system", tags=["System"])


@router.get(
    "/health",
    response_model=SuccessResponse[SystemHealthResponse],
    summary="Health check",
    description="Returns health status of all external services. Does not require auth.",
)
async def health_check(
    system_svc: SystemService = Depends(get_system_service),
) -> SuccessResponse[SystemHealthResponse]:
    health = await system_svc.health_check()
    return SuccessResponse(data=health)


@router.get(
    "/activity",
    summary="SSE activity stream",
    description="Server-Sent Events stream of live pipeline events.",
    response_class=StreamingResponse,
)
async def activity_stream(
    request: Request,
    _: str = Depends(require_api_key),
) -> StreamingResponse:
    """
    Long-lived SSE connection that emits events from all active runs.
    Frontend connects once and gets all updates.
    """
    async def event_generator():
        job_store = request.app.state.job_store
        sent: dict[str, int] = {}  # run_id → last sent index

        while True:
            if await request.is_disconnected():
                break

            runs = await job_store.list_runs(limit=20)
            for run in runs:
                idx = sent.get(run.run_id, 0)
                events = await job_store.get_events(run.run_id)
                while idx < len(events):
                    yield events[idx].to_sse()
                    idx += 1
                sent[run.run_id] = idx

            yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

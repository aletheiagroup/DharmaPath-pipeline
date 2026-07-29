"""
web/routes/workspace.py

Dedicated workspace endpoints for high-performance frontend loads:
  - Review workspace (all-in-one state for review screen)
  - Unified search across chapters, panels, assets, and runs
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Query

from web.dependencies import (
    get_chapter_service,
    get_review_service,
    get_asset_service,
    get_generation_service,
    require_api_key,
)
from web.schemas.common import SuccessResponse
from web.services.chapter_service import ChapterService
from web.services.review_service import ReviewService
from web.services.asset_service import AssetService
from web.services.generation_service import GenerationService

router = APIRouter(tags=["Workspace"])
_auth = Depends(require_api_key)


@router.get(
    "/review/{chapter_id}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Get complete review workspace for a chapter",
    description="""
Consolidates chapter detail, panel states, approved counts, active runs,
and asset references in a single request to minimize frontend network overhead.
    """,
)
async def get_review_workspace(
    chapter_id: str,
    chapter_svc: ChapterService = Depends(get_chapter_service),
    review_svc: ReviewService = Depends(get_review_service),
    asset_svc: AssetService = Depends(get_asset_service),
    generation_svc: GenerationService = Depends(get_generation_service),
    _: str = _auth,
) -> SuccessResponse[dict[str, Any]]:
    # 1. Get chapter details (screenplay panels)
    chapter_detail = await chapter_svc.get_chapter(chapter_id)
    
    # 2. Get panels review states (which merges screenplay panel metadata with active versions)
    panels_review = await review_svc.list_panels(chapter_id, chapter_detail.panels)
    
    # 3. Get approved count & total progress
    approved_count = await review_svc.get_approved_count(chapter_id)
    progress_pct = (
        round((approved_count / chapter_detail.total_panels) * 100, 1)
        if chapter_detail.total_panels > 0
        else 0.0
    )
    
    # 4. Get active run if any
    active_run = None
    runs = await generation_svc.get_runs_for_chapter(chapter_id)
    active_statuses = {"queued", "running", "assembling", "exporting", "uploading"}
    for run in runs:
        if run.status.value in active_statuses:
            active_run = {
                "run_id": run.run_id,
                "status": run.status.value,
                "progress_pct": run.progress_pct,
                "panels_completed": run.panels_completed,
                "panels_failed": run.panels_failed,
            }
            break

    # 5. Fetch associated character assets
    characters = set()
    for panel in chapter_detail.panels:
        characters.update(panel.get("characters", []))
    
    assets = []
    for char_name in sorted(characters):
        try:
            asset = await asset_svc.get_asset(char_name)
            assets.append(asset.model_dump())
        except Exception:
            # Character might not be registered yet
            pass

    workspace_data = {
        "chapter": chapter_detail.model_dump(),
        "panels": [p.model_dump() for p in panels_review],
        "assets": assets,
        "approved_count": approved_count,
        "progress_pct": progress_pct,
        "active_run": active_run,
    }
    
    return SuccessResponse(data=workspace_data)


@router.get(
    "/search",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Unified search",
    description="Performs text query matching across chapters, assets, runs, and panel actions.",
)
async def unified_search(
    q: str = Query(..., min_length=1, description="Search query"),
    chapter_svc: ChapterService = Depends(get_chapter_service),
    asset_svc: AssetService = Depends(get_asset_service),
    generation_svc: GenerationService = Depends(get_generation_service),
    _: str = _auth,
) -> SuccessResponse[dict[str, Any]]:
    query_lower = q.lower()
    
    # Search Chapters
    chapters = await chapter_svc.list_chapters()
    matched_chapters = [
        c.model_dump() for c in chapters
        if query_lower in c.title.lower() or query_lower in c.description.lower()
    ]
    
    # Search Assets
    assets_search = await asset_svc.search_assets(q)
    matched_assets = [a.model_dump() for a in assets_search.results]
    
    # Search Runs
    runs = await generation_svc.list_runs(limit=100)
    matched_runs = [
        {
            "run_id": r.run_id,
            "chapter_id": r.chapter_id,
            "status": r.status.value,
            "started_at": r.started_at,
        }
        for r in runs
        if query_lower in r.chapter_id.lower() or query_lower in r.run_id.lower()
    ]
    
    # Search Panels (Scan screenplays to find panel actions/dialogues matching the query)
    matched_panels = []
    for c in chapters:
        try:
            sp = chapter_svc.get_screenplay(c.chapter_id)
            for p in sp.panels:
                match = False
                if query_lower in p.action.lower() or query_lower in p.environment.lower():
                    match = True
                else:
                    for d in p.dialogue:
                        if query_lower in d.text.lower() or query_lower in d.speaker.lower():
                            match = True
                            break
                if match:
                    matched_panels.append({
                        "chapter_id": c.chapter_id,
                        "chapter_title": c.title,
                        "panel_id": p.panel_id,
                        "action": p.action,
                        "characters": p.characters,
                    })
        except Exception:
            pass

    return SuccessResponse(data={
        "query": q,
        "chapters": matched_chapters,
        "assets": matched_assets,
        "runs": matched_runs,
        "panels": matched_panels[:50],  # Limit to top 50 matches
    })

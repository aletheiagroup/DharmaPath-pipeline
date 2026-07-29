"""web/routes/panels.py — Panel review, approval, versioning."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from web.dependencies import get_chapter_service, get_review_service, require_api_key
from web.schemas.common import MessageResponse, SuccessResponse
from web.schemas.panels import (
    ApproveRequest,
    BulkApproveRequest,
    BulkRegenerateRequest,
    PanelReviewSchema,
    PanelVersionSchema,
    RegenerateRequest,
    RejectRequest,
    RestoreVersionRequest,
)
from web.services.chapter_service import ChapterService
from web.services.review_service import ReviewService

router = APIRouter(tags=["Panels"])
_auth = Depends(require_api_key)


@router.get(
    "/chapters/{chapter_id}/panels",
    response_model=SuccessResponse[list[PanelReviewSchema]],
    summary="List all panels for a chapter with review state",
)
async def list_panels(
    chapter_id: str,
    chapter_svc: ChapterService = Depends(get_chapter_service),
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[list[PanelReviewSchema]]:
    sp = chapter_svc.get_screenplay(chapter_id)
    panels_dict = [p.model_dump() for p in sp.panels]
    reviews = await review_svc.list_panels(chapter_id, panels_dict)
    return SuccessResponse(data=reviews)


@router.get(
    "/chapters/{chapter_id}/panels/{panel_id}",
    response_model=SuccessResponse[PanelReviewSchema],
    summary="Get single panel review detail",
)
async def get_panel(
    chapter_id: str,
    panel_id: str,
    chapter_svc: ChapterService = Depends(get_chapter_service),
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[PanelReviewSchema]:
    sp = chapter_svc.get_screenplay(chapter_id)
    panel = sp.get_panel(panel_id)
    panel_dict = panel.model_dump() if panel else None
    review = await review_svc.get_panel(chapter_id, panel_id, panel_dict)
    return SuccessResponse(data=review)


@router.post(
    "/chapters/{chapter_id}/panels/{panel_id}/approve",
    response_model=SuccessResponse[PanelReviewSchema],
    summary="Approve a panel",
)
async def approve_panel(
    chapter_id: str,
    panel_id: str,
    body: ApproveRequest,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[PanelReviewSchema]:
    review = await review_svc.approve_panel(chapter_id, panel_id, body.reviewer_note)
    return SuccessResponse(data=review)


@router.post(
    "/chapters/{chapter_id}/panels/{panel_id}/reject",
    response_model=SuccessResponse[PanelReviewSchema],
    summary="Reject a panel",
)
async def reject_panel(
    chapter_id: str,
    panel_id: str,
    body: RejectRequest,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[PanelReviewSchema]:
    review = await review_svc.reject_panel(chapter_id, panel_id, body.reviewer_note)
    return SuccessResponse(data=review)


@router.post(
    "/chapters/{chapter_id}/panels/{panel_id}/regenerate",
    response_model=SuccessResponse[PanelReviewSchema],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request regeneration of a panel with a suggestion",
    description="""
Marks the panel as `needs_regen` and stores the suggestion.
Returns 202 immediately — actual regeneration is submitted to the background queue.
Poll the run events stream to track progress.
    """,
)
async def request_regeneration(
    chapter_id: str,
    panel_id: str,
    body: RegenerateRequest,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[PanelReviewSchema]:
    review = await review_svc.request_regeneration(
        chapter_id, panel_id, body.suggestion, body.reviewer_note
    )
    return SuccessResponse(data=review)


@router.post(
    "/chapters/{chapter_id}/panels/bulk-approve",
    response_model=SuccessResponse[dict],
    summary="Bulk approve multiple panels",
)
async def bulk_approve(
    chapter_id: str,
    body: BulkApproveRequest,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[dict]:
    count = await review_svc.bulk_approve(chapter_id, body.panel_ids)
    return SuccessResponse(data={"approved_count": count, "panel_ids": body.panel_ids})


@router.post(
    "/chapters/{chapter_id}/panels/bulk-regenerate",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request regeneration of multiple panels",
)
async def bulk_regenerate(
    chapter_id: str,
    body: BulkRegenerateRequest,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[dict]:
    queued = []
    for panel_id in body.panel_ids:
        await review_svc.request_regeneration(chapter_id, panel_id, body.suggestion)
        queued.append(panel_id)
    return SuccessResponse(data={"queued": queued, "suggestion": body.suggestion})


# ── Version management ────────────────────────────────────────────────────────

@router.get(
    "/chapters/{chapter_id}/panels/{panel_id}/versions",
    response_model=SuccessResponse[list[PanelVersionSchema]],
    summary="List all versions of a panel",
)
async def list_versions(
    chapter_id: str,
    panel_id: str,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[list[PanelVersionSchema]]:
    versions = await review_svc.list_versions(chapter_id, panel_id)
    return SuccessResponse(data=versions)


@router.post(
    "/chapters/{chapter_id}/panels/{panel_id}/versions/{version_number}/restore",
    response_model=SuccessResponse[PanelReviewSchema],
    summary="Restore a previous panel version as the active version",
)
async def restore_version(
    chapter_id: str,
    panel_id: str,
    version_number: int,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> SuccessResponse[PanelReviewSchema]:
    review = await review_svc.restore_version(chapter_id, panel_id, version_number)
    return SuccessResponse(data=review)


@router.delete(
    "/chapters/{chapter_id}/panels/{panel_id}/versions/{version_number}",
    response_model=MessageResponse,
    summary="Delete a specific panel version",
)
async def delete_version(
    chapter_id: str,
    panel_id: str,
    version_number: int,
    review_svc: ReviewService = Depends(get_review_service),
    _: str = _auth,
) -> MessageResponse:
    await review_svc.delete_version(chapter_id, panel_id, version_number)
    return MessageResponse(message=f"Version {version_number} deleted from panel {panel_id}.")

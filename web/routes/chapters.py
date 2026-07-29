"""web/routes/chapters.py — Chapter CRUD and validation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from web.dependencies import get_chapter_service, require_api_key
from web.schemas.chapters import (
    ChapterDetail,
    ChapterSummary,
    CreateChapterRequest,
    ValidateScreenplayResponse,
)
from web.schemas.common import MessageResponse, PaginatedResponse, SuccessResponse
from web.services.chapter_service import ChapterService

router = APIRouter(prefix="/chapters", tags=["Chapters"])
_auth = Depends(require_api_key)


@router.get(
    "",
    response_model=SuccessResponse[list[ChapterSummary]],
    summary="List all chapters",
)
async def list_chapters(
    chapter_svc: ChapterService = Depends(get_chapter_service),
    _: str = _auth,
) -> SuccessResponse[list[ChapterSummary]]:
    chapters = await chapter_svc.list_chapters()
    return SuccessResponse(data=chapters)


@router.get(
    "/{chapter_id}",
    response_model=SuccessResponse[ChapterDetail],
    summary="Get full chapter detail",
)
async def get_chapter(
    chapter_id: str,
    chapter_svc: ChapterService = Depends(get_chapter_service),
    _: str = _auth,
) -> SuccessResponse[ChapterDetail]:
    chapter = await chapter_svc.get_chapter(chapter_id)
    return SuccessResponse(data=chapter)


@router.post(
    "",
    response_model=SuccessResponse[ChapterSummary],
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new screenplay",
)
async def create_chapter(
    request: CreateChapterRequest,
    chapter_svc: ChapterService = Depends(get_chapter_service),
    _: str = _auth,
) -> SuccessResponse[ChapterSummary]:
    chapter = await chapter_svc.create_chapter(request.screenplay)
    return SuccessResponse(data=chapter)


@router.post(
    "/validate",
    response_model=SuccessResponse[ValidateScreenplayResponse],
    summary="Validate a screenplay without saving",
)
async def validate_screenplay(
    request: CreateChapterRequest,
    chapter_svc: ChapterService = Depends(get_chapter_service),
    _: str = _auth,
) -> SuccessResponse[ValidateScreenplayResponse]:
    result = await chapter_svc.validate_screenplay(request.screenplay)
    return SuccessResponse(data=result)

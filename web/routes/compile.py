"""web/routes/compile.py — Compile, slice, publish, and download."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from web.dependencies import get_chapter_service, get_compile_service, require_api_key
from web.schemas.common import SuccessResponse
from web.schemas.compile import CompileResult, PublishResult, SliceResult
from web.services.chapter_service import ChapterService
from web.services.compile_service import CompileService

router = APIRouter(tags=["Compile"])
_auth = Depends(require_api_key)


@router.post(
    "/chapters/{chapter_id}/compile",
    response_model=SuccessResponse[CompileResult],
    summary="Assemble panels into vertical strip",
)
async def compile_chapter(
    chapter_id: str,
    compile_svc: CompileService = Depends(get_compile_service),
    chapter_svc: ChapterService = Depends(get_chapter_service),
    _: str = _auth,
) -> SuccessResponse[CompileResult]:
    sp = chapter_svc.get_screenplay(chapter_id)
    result = await compile_svc.compile_chapter(chapter_id, sp)
    return SuccessResponse(data=result)


@router.post(
    "/chapters/{chapter_id}/slice",
    response_model=SuccessResponse[SliceResult],
    summary="Slice assembled strip into Webtoon episodes",
)
async def slice_chapter(
    chapter_id: str,
    compile_svc: CompileService = Depends(get_compile_service),
    _: str = _auth,
) -> SuccessResponse[SliceResult]:
    result = await compile_svc.slice_chapter(chapter_id)
    return SuccessResponse(data=result)


@router.post(
    "/chapters/{chapter_id}/publish",
    response_model=SuccessResponse[PublishResult],
    summary="Upload sliced episodes to cloud storage",
    description="Uploads all episode JPG files to R2/GCS. Run `slice` first.",
)
async def publish_chapter(
    chapter_id: str,
    compile_svc: CompileService = Depends(get_compile_service),
    _: str = _auth,
) -> SuccessResponse[PublishResult]:
    result = await compile_svc.publish_chapter(chapter_id)
    return SuccessResponse(data=result)


@router.get(
    "/chapters/{chapter_id}/download",
    summary="Download all episodes as a ZIP file",
    response_class=Response,
)
async def download_episodes(
    chapter_id: str,
    compile_svc: CompileService = Depends(get_compile_service),
    _: str = _auth,
) -> Response:
    zip_bytes = compile_svc.build_download_zip(chapter_id)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{chapter_id}_episodes.zip"'},
    )

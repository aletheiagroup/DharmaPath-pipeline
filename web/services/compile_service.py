"""
web/services/compile_service.py

CompileService — owns all assembly, slicing, and publishing operations.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from dharmapath.assembler.assembler import ChapterAssembler
from dharmapath.assembler.exporter import ChapterExporter
from dharmapath.storage.r2_client import R2Client
from dharmapath.models.screenplay import Screenplay
from web.exceptions import NotFoundError
from web.schemas.compile import CompileResult, PublishResult, SliceResult

logger = logging.getLogger(__name__)


class CompileService:
    def __init__(self, outputs_dir: Path) -> None:
        self._outputs_dir = outputs_dir
        self._assembler = ChapterAssembler()
        self._exporter = ChapterExporter()

    def _panel_dir(self, chapter_id: str) -> Path:
        return self._outputs_dir / chapter_id / "panels"

    def _episode_dir(self, chapter_id: str) -> Path:
        return self._outputs_dir / chapter_id / "episodes"

    def _strip_path(self, chapter_id: str) -> Path:
        return self._outputs_dir / chapter_id / f"{chapter_id}_assembled.png"

    async def compile_chapter(self, chapter_id: str, screenplay: Screenplay) -> CompileResult:
        """Assemble individual panel images into a single vertical strip."""
        panel_dir = self._panel_dir(chapter_id)
        if not panel_dir.exists():
            raise NotFoundError("Panel directory", chapter_id)

        try:
            strip_path = self._assembler.assemble_chapter(screenplay, panel_dir)
        except Exception as e:
            from web.exceptions import DharmaPathError, ErrorCode
            raise DharmaPathError(
                f"Assembly failed for '{chapter_id}': {e}",
                code=ErrorCode.COMPILE_FAILED,
                status_code=500,
            )

        from PIL import Image
        with Image.open(strip_path) as img:
            w, h = img.size

        panels_dir_count = len(list(panel_dir.glob("*.png")))
        panels_skipped = max(0, screenplay.panel_count - panels_dir_count)

        return CompileResult(
            chapter_id=chapter_id,
            strip_path=str(strip_path),
            strip_height_px=h,
            strip_width_px=w,
            panels_included=panels_dir_count,
            panels_skipped=panels_skipped,
        )

    async def slice_chapter(self, chapter_id: str) -> SliceResult:
        """Slice the assembled strip into mobile episodes."""
        strip_path = self._strip_path(chapter_id)
        if not strip_path.exists():
            raise NotFoundError("Assembled strip", chapter_id)

        try:
            episode_paths = self._exporter.split_episodes(strip_path, chapter_id)
        except Exception as e:
            from web.exceptions import DharmaPathError, ErrorCode
            raise DharmaPathError(
                f"Episode slicing failed for '{chapter_id}': {e}",
                code=ErrorCode.EXPORT_FAILED,
                status_code=500,
            )

        from PIL import Image
        episodes = []
        for i, ep_path in enumerate(episode_paths):
            with Image.open(ep_path) as img:
                _, h = img.size
            episodes.append({
                "episode_number": i + 1,
                "path": str(ep_path),
                "filename": ep_path.name,
                "height_px": h,
            })

        return SliceResult(
            chapter_id=chapter_id,
            episodes=episodes,
            total_episodes=len(episodes),
        )

    async def publish_chapter(self, chapter_id: str) -> PublishResult:
        """Upload sliced episodes to cloud storage."""
        ep_dir = self._episode_dir(chapter_id)
        if not ep_dir.exists():
            raise NotFoundError("Episode directory (run slice first)", chapter_id)

        episode_files = sorted(ep_dir.glob("*.jpg"))
        if not episode_files:
            raise NotFoundError("Episode files (run slice first)", chapter_id)

        try:
            r2 = R2Client()
            cloud_urls = []
            for ep_path in episode_files:
                url = r2.upload_episode(ep_path, chapter_id)
                cloud_urls.append(url)
        except Exception as e:
            from web.exceptions import DharmaPathError, ErrorCode
            raise DharmaPathError(
                f"Upload failed for '{chapter_id}': {e}",
                code=ErrorCode.UPLOAD_FAILED,
                status_code=502,
            )

        return PublishResult(
            chapter_id=chapter_id,
            cloud_urls=cloud_urls,
            storage_provider="r2",
        )

    def build_download_zip(self, chapter_id: str) -> bytes:
        """Create an in-memory ZIP of all episode files."""
        ep_dir = self._episode_dir(chapter_id)
        if not ep_dir.exists():
            raise NotFoundError("Episode directory", chapter_id)

        episode_files = sorted(ep_dir.glob("*.jpg"))
        if not episode_files:
            raise NotFoundError("Episode files", chapter_id)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for ep in episode_files:
                zf.write(ep, arcname=ep.name)
        buffer.seek(0)
        return buffer.read()

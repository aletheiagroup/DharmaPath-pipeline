"""
web/dependencies.py

Shared FastAPI dependencies — injected via Depends().

All services are created once in the app lifespan and stored on
app.state to avoid re-initialisation on each request.
"""

from __future__ import annotations

import logging
import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from web.services import (
    AssetService,
    ChapterService,
    CompileService,
    GenerationService,
    ReviewService,
    SettingsService,
    SystemService,
)

logger = logging.getLogger(__name__)

# ── API Key Auth ──────────────────────────────────────────────────────────────

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request,
    api_key: str | None = Depends(_API_KEY_HEADER),
) -> str:
    """
    Validate the X-API-Key header or query parameter against API_SECRET_KEY env var.

    If API_SECRET_KEY is not set (development mode), auth is skipped
    and a warning is emitted on every request.
    """
    secret = os.environ.get("API_SECRET_KEY", "")
    if not secret:
        logger.warning("API_SECRET_KEY not set — running in unauthenticated mode.")
        return "dev-mode"

    # Fallback to query param for EventSource / SSE
    resolved_key = api_key or request.query_params.get("api_key")

    if not resolved_key or resolved_key != secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Invalid or missing X-API-Key header or query parameter.",
                },
            },
        )
    return resolved_key


# ── Service accessors (thin wrappers around app.state) ────────────────────────

def get_chapter_service(request: Request) -> ChapterService:
    return request.app.state.chapter_service


def get_generation_service(request: Request) -> GenerationService:
    return request.app.state.generation_service


def get_review_service(request: Request) -> ReviewService:
    return request.app.state.review_service


def get_compile_service(request: Request) -> CompileService:
    return request.app.state.compile_service


def get_asset_service(request: Request) -> AssetService:
    return request.app.state.asset_service


def get_settings_service(request: Request) -> SettingsService:
    return request.app.state.settings_service


def get_system_service(request: Request) -> SystemService:
    return request.app.state.system_service

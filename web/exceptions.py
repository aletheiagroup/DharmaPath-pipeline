"""
web/exceptions.py

Custom HTTP exceptions and the global exception handler registered
on the FastAPI app. Every error surface returns the same ErrorResponse shape.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── Error codes ──────────────────────────────────────────────────────────────

class ErrorCode:
    # Generic
    INTERNAL_ERROR     = "INTERNAL_ERROR"
    NOT_FOUND          = "NOT_FOUND"
    VALIDATION_FAILED  = "VALIDATION_FAILED"
    CONFLICT           = "CONFLICT"
    UNPROCESSABLE      = "UNPROCESSABLE"

    # Auth
    UNAUTHORIZED       = "UNAUTHORIZED"
    FORBIDDEN          = "FORBIDDEN"
    RATE_LIMITED       = "RATE_LIMITED"

    # Domain
    SCREENPLAY_INVALID = "SCREENPLAY_INVALID"
    CHAPTER_NOT_FOUND  = "CHAPTER_NOT_FOUND"
    PANEL_NOT_FOUND    = "PANEL_NOT_FOUND"
    RUN_NOT_FOUND      = "RUN_NOT_FOUND"
    ASSET_NOT_FOUND    = "ASSET_NOT_FOUND"
    PIPELINE_BUSY      = "PIPELINE_BUSY"
    COMFYUI_UNREACHABLE = "COMFYUI_UNREACHABLE"
    COMPILE_FAILED     = "COMPILE_FAILED"
    EXPORT_FAILED      = "EXPORT_FAILED"
    UPLOAD_FAILED      = "UPLOAD_FAILED"


# ── Custom exceptions ────────────────────────────────────────────────────────

class DharmaPathError(Exception):
    """Base exception for all DharmaPath domain errors."""

    def __init__(
        self,
        message: str,
        code: str = ErrorCode.INTERNAL_ERROR,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail


class NotFoundError(DharmaPathError):
    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(
            message=f"{resource} '{identifier}' not found.",
            code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ConflictError(DharmaPathError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message=message,
            code=ErrorCode.CONFLICT,
            status_code=status.HTTP_409_CONFLICT,
        )


class ValidationError(DharmaPathError):
    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(
            message=message,
            code=ErrorCode.VALIDATION_FAILED,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )


class PipelineBusyError(DharmaPathError):
    def __init__(self, chapter_id: str) -> None:
        super().__init__(
            message=f"A generation run for '{chapter_id}' is already in progress.",
            code=ErrorCode.PIPELINE_BUSY,
            status_code=status.HTTP_409_CONFLICT,
        )


class ComfyUIUnreachableError(DharmaPathError):
    def __init__(self, url: str) -> None:
        super().__init__(
            message=f"ComfyUI server at '{url}' is not reachable.",
            code=ErrorCode.COMFYUI_UNREACHABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class ScreenplayInvalidError(DharmaPathError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(
            message="Screenplay failed validation.",
            code=ErrorCode.SCREENPLAY_INVALID,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors,
        )


# ── Error response payload ───────────────────────────────────────────────────

def _error_body(
    code: str,
    message: str,
    detail: Any = None,
) -> dict:
    body: dict[str, Any] = {"success": False, "error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return body


# ── Global exception handler ─────────────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to a FastAPI app instance."""

    @app.exception_handler(DharmaPathError)
    async def dharmapath_error_handler(
        request: Request, exc: DharmaPathError
    ) -> JSONResponse:
        logger.warning(
            "DharmaPathError [%s]: %s — %s %s",
            exc.code, exc.message, request.method, request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=_error_body(ErrorCode.NOT_FOUND, f"Route not found: {request.url.path}"),
        )

    @app.exception_handler(405)
    async def method_not_allowed_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=405,
            content=_error_body("METHOD_NOT_ALLOWED", f"Method {request.method} not allowed."),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(
                ErrorCode.INTERNAL_ERROR,
                "An unexpected internal error occurred. Check server logs.",
            ),
        )

"""web/routes/settings.py — Runtime settings (non-secret) and connection status."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web.dependencies import get_settings_service, require_api_key
from web.schemas.common import SuccessResponse
from web.schemas.settings import (
    ConnectionStatusResponse,
    RuntimeSettingsResponse,
    UpdateRuntimeSettingsRequest,
)
from web.services.settings_service import SettingsService

router = APIRouter(prefix="/settings", tags=["Settings"])
_auth = Depends(require_api_key)


@router.get(
    "",
    response_model=SuccessResponse[RuntimeSettingsResponse],
    summary="Get current runtime settings",
    description="Returns editable operational settings from `config/runtime_settings.json`. Secret values are never included.",
)
async def get_settings(
    settings_svc: SettingsService = Depends(get_settings_service),
    _: str = _auth,
) -> SuccessResponse[RuntimeSettingsResponse]:
    s = settings_svc.load()
    return SuccessResponse(data=s)


@router.put(
    "",
    response_model=SuccessResponse[RuntimeSettingsResponse],
    summary="Update runtime settings",
    description="Persists non-None fields to `config/runtime_settings.json`. Omit fields you do not want to change.",
)
async def update_settings(
    request: UpdateRuntimeSettingsRequest,
    settings_svc: SettingsService = Depends(get_settings_service),
    _: str = _auth,
) -> SuccessResponse[RuntimeSettingsResponse]:
    s = settings_svc.update(request)
    return SuccessResponse(data=s)


@router.get(
    "/connections",
    response_model=SuccessResponse[ConnectionStatusResponse],
    summary="Get external connection configuration status",
    description="Read-only view of which external services are configured. Does not return any secret values.",
)
async def connection_status(
    settings_svc: SettingsService = Depends(get_settings_service),
    _: str = _auth,
) -> SuccessResponse[ConnectionStatusResponse]:
    status = settings_svc.connection_status()
    return SuccessResponse(data=status)

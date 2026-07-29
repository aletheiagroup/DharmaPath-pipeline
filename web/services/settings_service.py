"""
web/services/settings_service.py

Manages runtime_settings.json (non-secret, editable parameters).
.env is NEVER touched — it holds secrets only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import settings as env_settings
from web.schemas.settings import (
    ConnectionStatusResponse,
    RuntimeSettingsResponse,
    UpdateRuntimeSettingsRequest,
)

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path(__file__).parent.parent.parent / "config" / "runtime_settings.json"


class SettingsService:
    def load(self) -> RuntimeSettingsResponse:
        """Read config/runtime_settings.json."""
        try:
            raw = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            # Strip comments key
            raw.pop("_comment", None)
            return RuntimeSettingsResponse(**raw)
        except Exception as e:
            logger.error("Failed to load runtime settings: %s", e)
            raise

    def update(self, request: UpdateRuntimeSettingsRequest) -> RuntimeSettingsResponse:
        """Merge non-None fields from request into runtime_settings.json."""
        current = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        current.pop("_comment", None)

        updated = request.model_dump(exclude_none=True)
        current.update(updated)

        _SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info("Runtime settings updated: %s", list(updated.keys()))
        return RuntimeSettingsResponse(**current)

    def connection_status(self) -> ConnectionStatusResponse:
        """Read-only view of external integrations — no secret values."""
        return ConnectionStatusResponse(
            comfyui_url=env_settings.comfyui_base_url,
            gemini_model=env_settings.gemini_model,
            gcs_bucket=env_settings.gcs_bucket_name,
            r2_bucket=env_settings.r2_bucket_name,
            r2_configured=bool(env_settings.r2_access_key_id),
            gcs_configured=bool(env_settings.gcp_project_id),
            gemini_configured=bool(env_settings.google_api_key or env_settings.gcp_project_id),
        )

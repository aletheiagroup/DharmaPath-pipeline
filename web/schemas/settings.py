"""web/schemas/settings.py"""
from __future__ import annotations
from pydantic import BaseModel, Field


class RuntimeSettingsResponse(BaseModel):
    """Editable runtime settings. Secrets are never included."""
    max_failure_pct_auto: float
    max_failure_pct_degraded: float
    r2_batch_upload_interval: int
    default_generation_steps: int
    default_cfg: float
    default_sampler: str
    default_compile_quality: str
    max_panel_regen_retries: int
    sse_keepalive_interval_s: int
    rate_limit_per_minute: int


class UpdateRuntimeSettingsRequest(BaseModel):
    max_failure_pct_auto: float | None = Field(None, ge=0.0, le=50.0)
    max_failure_pct_degraded: float | None = Field(None, ge=0.0, le=50.0)
    r2_batch_upload_interval: int | None = Field(None, ge=1, le=100)
    default_generation_steps: int | None = Field(None, ge=10, le=150)
    default_cfg: float | None = Field(None, ge=1.0, le=30.0)
    default_sampler: str | None = None
    default_compile_quality: str | None = None
    max_panel_regen_retries: int | None = Field(None, ge=1, le=10)
    sse_keepalive_interval_s: int | None = Field(None, ge=5, le=60)
    rate_limit_per_minute: int | None = Field(None, ge=10, le=1000)


class ConnectionStatusResponse(BaseModel):
    """Read-only view of external connection status — no secret values."""
    comfyui_url: str
    gemini_model: str
    gcs_bucket: str
    r2_bucket: str
    r2_configured: bool
    gcs_configured: bool
    gemini_configured: bool

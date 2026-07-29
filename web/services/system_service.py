"""
web/services/system_service.py

SystemService — checks health of external dependencies.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import httpx

from config.settings import settings
from web.schemas.system import ServiceStatus, SystemHealthResponse

logger = logging.getLogger(__name__)


class SystemService:
    def __init__(self, outputs_dir: Path) -> None:
        self._outputs_dir = outputs_dir

    async def health_check(self) -> SystemHealthResponse:
        services = []
        overall = "healthy"

        # ComfyUI
        comfy_status = await self._check_comfyui()
        services.append(comfy_status)
        if comfy_status.status != "healthy":
            overall = "degraded"

        # Gemini (simple connectivity check)
        gemini_status = self._check_gemini()
        services.append(gemini_status)

        # Storage
        storage_status = self._check_storage()
        services.append(storage_status)

        # GPU (from ComfyUI /system_stats)
        gpu_usage, vram_used, vram_total = await self._get_gpu_stats()
        if gpu_usage is not None and gpu_usage > 95:
            overall = "degraded"

        # Disk
        storage_free_gb = self._get_free_disk_gb()

        return SystemHealthResponse(
            overall=overall,
            services=services,
            gpu_usage_pct=gpu_usage,
            vram_used_gb=vram_used,
            vram_total_gb=vram_total,
            storage_free_gb=storage_free_gb,
        )

    async def _check_comfyui(self) -> ServiceStatus:
        url = settings.comfyui_system_stats_url
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
            latency = round((time.monotonic() - t0) * 1000, 1)
            if r.status_code == 200:
                return ServiceStatus(name="comfyui", status="healthy", latency_ms=latency)
            return ServiceStatus(name="comfyui", status="degraded", latency_ms=latency, detail=f"HTTP {r.status_code}")
        except Exception as e:
            return ServiceStatus(name="comfyui", status="unreachable", detail=str(e)[:120])

    def _check_gemini(self) -> ServiceStatus:
        configured = bool(settings.google_api_key or settings.gcp_project_id)
        return ServiceStatus(
            name="gemini",
            status="healthy" if configured else "degraded",
            detail=f"Model: {settings.gemini_model}" if configured else "No API key or project configured",
        )

    def _check_storage(self) -> ServiceStatus:
        r2_ok = bool(settings.r2_access_key_id and settings.r2_secret_access_key)
        gcs_ok = bool(settings.gcp_project_id)
        if r2_ok or gcs_ok:
            providers = []
            if r2_ok: providers.append("R2")
            if gcs_ok: providers.append("GCS")
            return ServiceStatus(name="storage", status="healthy", detail=f"Configured: {', '.join(providers)}")
        return ServiceStatus(name="storage", status="degraded", detail="No cloud storage credentials configured")

    async def _get_gpu_stats(self) -> tuple[float | None, float | None, float | None]:
        """Try to pull GPU stats from ComfyUI /system_stats endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(settings.comfyui_system_stats_url)
            if r.status_code == 200:
                data = r.json()
                devices = data.get("devices", [])
                if devices:
                    d = devices[0]
                    vram_total = round(d.get("vram_total", 0) / 1024**3, 2)
                    vram_free = round(d.get("vram_free", 0) / 1024**3, 2)
                    vram_used = round(vram_total - vram_free, 2)
                    return None, vram_used, vram_total
        except Exception:
            pass
        return None, None, None

    def _get_free_disk_gb(self) -> float | None:
        try:
            usage = shutil.disk_usage(self._outputs_dir)
            return round(usage.free / 1024**3, 2)
        except Exception:
            return None

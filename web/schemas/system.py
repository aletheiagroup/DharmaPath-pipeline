"""web/schemas/system.py"""
from __future__ import annotations
from pydantic import BaseModel


class ServiceStatus(BaseModel):
    name: str
    status: str          # "healthy" | "degraded" | "unreachable"
    latency_ms: float | None = None
    detail: str | None = None


class SystemHealthResponse(BaseModel):
    overall: str         # "healthy" | "degraded" | "critical"
    services: list[ServiceStatus]
    gpu_usage_pct: float | None = None
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    storage_free_gb: float | None = None
    queue_length: int = 0


class ActivityEvent(BaseModel):
    event_type: str
    timestamp: str
    data: dict

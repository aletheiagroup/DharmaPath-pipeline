"""
web/store/job_store.py

Abstract JobStore interface + MemoryJobStore implementation.

Design:
  - JobStore is the interface
  - MemoryJobStore is the default (single-process, no persistence)
  - DatabaseJobStore can be dropped in later (SQLite / Postgres)
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Job models ────────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    queued      = "queued"
    running     = "running"
    assembling  = "assembling"
    exporting   = "exporting"
    uploading   = "uploading"
    complete    = "complete"
    failed      = "failed"
    cancelled   = "cancelled"


@dataclass
class RunEvent:
    """A single SSE-style event emitted by the pipeline."""
    run_id: str
    event_type: str          # e.g. "panel_completed", "panel_failed", "run_complete"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Serialise to SSE wire format."""
        import json
        payload = json.dumps({"type": self.event_type, "timestamp": self.timestamp, **self.data})
        return f"data: {payload}\n\n"


@dataclass
class RunRecord:
    run_id: str
    chapter_id: str
    screenplay_path: str
    status: RunStatus = RunStatus.queued
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    panels_total: int = 0
    panels_completed: int = 0
    panels_failed: int = 0
    errors: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    events: list[RunEvent] = field(default_factory=list)

    @property
    def progress_pct(self) -> float:
        if self.panels_total == 0:
            return 0.0
        return round((self.panels_completed / self.panels_total) * 100, 1)

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "chapter_id": self.chapter_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "panels_total": self.panels_total,
            "panels_completed": self.panels_completed,
            "panels_failed": self.panels_failed,
            "progress_pct": self.progress_pct,
            "errors": self.errors,
        }


# ── Abstract interface ────────────────────────────────────────────────────────

class JobStore(ABC):
    """Abstract interface for run tracking storage."""

    @abstractmethod
    async def create_run(self, chapter_id: str, screenplay_path: str, panels_total: int) -> RunRecord:
        ...

    @abstractmethod
    async def get_run(self, run_id: str) -> RunRecord | None:
        ...

    @abstractmethod
    async def get_runs_for_chapter(self, chapter_id: str) -> list[RunRecord]:
        ...

    @abstractmethod
    async def list_runs(self, limit: int = 50) -> list[RunRecord]:
        ...

    @abstractmethod
    async def update_run(self, run_id: str, **kwargs: Any) -> RunRecord:
        ...

    @abstractmethod
    async def add_event(self, run_id: str, event: RunEvent) -> None:
        ...

    @abstractmethod
    async def get_events(self, run_id: str) -> list[RunEvent]:
        ...

    @abstractmethod
    async def get_active_run_for_chapter(self, chapter_id: str) -> RunRecord | None:
        ...


# ── MemoryJobStore ────────────────────────────────────────────────────────────

class MemoryJobStore(JobStore):
    """
    In-process, in-memory implementation of JobStore.

    Sufficient for single-user internal tool. Replace with
    DatabaseJobStore for multi-user or persistent history.
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, chapter_id: str, screenplay_path: str, panels_total: int) -> RunRecord:
        run_id = str(uuid.uuid4())
        record = RunRecord(
            run_id=run_id,
            chapter_id=chapter_id,
            screenplay_path=screenplay_path,
            panels_total=panels_total,
        )
        async with self._lock:
            self._runs[run_id] = record
        return record

    async def get_run(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    async def get_runs_for_chapter(self, chapter_id: str) -> list[RunRecord]:
        return [r for r in self._runs.values() if r.chapter_id == chapter_id]

    async def list_runs(self, limit: int = 50) -> list[RunRecord]:
        runs = sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    async def update_run(self, run_id: str, **kwargs: Any) -> RunRecord:
        async with self._lock:
            record = self._runs[run_id]
            for key, value in kwargs.items():
                setattr(record, key, value)
        return record

    async def add_event(self, run_id: str, event: RunEvent) -> None:
        async with self._lock:
            record = self._runs.get(run_id)
            if record:
                record.events.append(event)

    async def get_events(self, run_id: str) -> list[RunEvent]:
        record = self._runs.get(run_id)
        return record.events if record else []

    async def get_active_run_for_chapter(self, chapter_id: str) -> RunRecord | None:
        active_statuses = {RunStatus.queued, RunStatus.running, RunStatus.assembling, RunStatus.exporting, RunStatus.uploading}
        for run in self._runs.values():
            if run.chapter_id == chapter_id and run.status in active_statuses:
                return run
        return None

"""
web/store/task_manager.py

Abstract TaskManager + AsyncTaskManager.

Isolates background task execution so Celery / Dramatiq can be
dropped in later without touching any route or service code.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


class TaskManager(ABC):
    """Abstract task execution interface."""

    @abstractmethod
    async def submit(self, coro: Coroutine[Any, Any, Any], task_id: str) -> None:
        """Submit a coroutine for background execution."""
        ...

    @abstractmethod
    async def cancel(self, task_id: str) -> bool:
        """Cancel a running task by ID. Returns True if cancelled."""
        ...

    @abstractmethod
    def is_running(self, task_id: str) -> bool:
        """Return True if the task_id is currently running."""
        ...


class AsyncTaskManager(TaskManager):
    """
    asyncio.create_task-based implementation.
    Suitable for single-process deployments.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def submit(self, coro: Coroutine[Any, Any, Any], task_id: str) -> None:
        if task_id in self._tasks and not self._tasks[task_id].done():
            logger.warning("Task %s already running — ignoring duplicate submit.", task_id)
            return

        task = asyncio.create_task(self._run_with_logging(coro, task_id))
        self._tasks[task_id] = task

    async def _run_with_logging(self, coro: Coroutine[Any, Any, Any], task_id: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("Task %s was cancelled.", task_id)
        except Exception:
            logger.exception("Task %s raised an unhandled exception.", task_id)
        finally:
            self._tasks.pop(task_id, None)

    async def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def is_running(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return task is not None and not task.done()

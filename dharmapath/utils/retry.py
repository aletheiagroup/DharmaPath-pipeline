"""
dharmapath/utils/retry.py

Centralised retry logic for all external service calls in the DharmaPath pipeline.

Provides:
  - retry_async() — for async clients (ComfyUI, Gemini)
  - retry_sync()  — for sync clients (R2, GCS)
  - CircuitBreaker — prevents hammering a failing service

Design principles:
  - Exponential backoff with jitter (prevents thundering herd)
  - Per-exception retry filtering (only retry transient errors)
  - Structured JSON logging (machine-parseable for Cloud Logging)
  - Explicit timeout awareness (callers set timeout budgets)
  - No decorator magic — plain functions, easy to test and debug
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── Structured Retry Logging ─────────────────────────────────────────────────

def _log_retry(
    service: str,
    operation: str,
    attempt: int,
    max_retries: int,
    delay: float,
    error: Exception,
    elapsed_ms: float,
    context: dict[str, Any] | None = None,
) -> None:
    """Emit a structured JSON log entry for a retry event."""
    log_data = {
        "event": "retry",
        "service": service,
        "operation": operation,
        "attempt": attempt,
        "max_retries": max_retries,
        "delay_s": round(delay, 2),
        "error_type": type(error).__name__,
        "error_msg": str(error)[:200],
        "elapsed_ms": round(elapsed_ms, 1),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if context:
        log_data["context"] = context

    logger.warning(json.dumps(log_data))


def _log_exhausted(
    service: str,
    operation: str,
    attempts: int,
    error: Exception,
    total_elapsed_ms: float,
    context: dict[str, Any] | None = None,
) -> None:
    """Emit a structured JSON log entry when all retries are exhausted."""
    log_data = {
        "event": "retry_exhausted",
        "service": service,
        "operation": operation,
        "total_attempts": attempts,
        "final_error_type": type(error).__name__,
        "final_error_msg": str(error)[:300],
        "total_elapsed_ms": round(total_elapsed_ms, 1),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if context:
        log_data["context"] = context

    logger.error(json.dumps(log_data))


# ── Backoff Calculation ──────────────────────────────────────────────────────

def _compute_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    backoff_factor: float,
    jitter: bool,
) -> float:
    """
    Compute the delay before the next retry attempt.

    Uses exponential backoff capped at max_delay, with optional ±25% jitter.
    """
    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
    if jitter:
        # ±25% randomness to prevent thundering herd
        jitter_range = delay * 0.25
        delay = delay + random.uniform(-jitter_range, jitter_range)
        delay = max(0.1, delay)  # never negative or zero
    return delay


# ── Async Retry ──────────────────────────────────────────────────────────────

async def retry_async(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    service: str = "unknown",
    operation: str = "unknown",
    context: dict[str, Any] | None = None,
    circuit_breaker: "CircuitBreaker | None" = None,
    **kwargs: Any,
) -> Any:
    """
    Retry an async callable with exponential backoff.

    Args:
        fn: The async function to call.
        *args: Positional arguments for fn.
        max_retries: Maximum number of retry attempts (total calls = max_retries + 1).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier for exponential growth.
        jitter: If True, adds ±25% randomness to delay.
        retryable_exceptions: Tuple of exception types to retry on.
        service: Service name for structured logging (e.g. "comfyui", "gemini").
        operation: Operation name for logging (e.g. "queue_prompt", "refine_prompt").
        context: Optional dict of extra context for log entries.
        circuit_breaker: Optional CircuitBreaker instance.
        **kwargs: Keyword arguments for fn.

    Returns:
        The return value of fn on success.

    Raises:
        The last exception after all retries are exhausted, or
        CircuitOpenError if the circuit breaker is open.
    """
    if circuit_breaker and circuit_breaker.is_open:
        raise CircuitOpenError(
            f"Circuit breaker for '{circuit_breaker.service}' is open. "
            f"Will retry after {circuit_breaker.reset_after_s:.0f}s cooldown."
        )

    last_exception: Exception | None = None
    start_time = time.monotonic()

    for attempt in range(max_retries + 1):
        attempt_start = time.monotonic()
        try:
            result = await fn(*args, **kwargs)

            # Success — record with circuit breaker
            if circuit_breaker:
                circuit_breaker.record_success()

            return result

        except retryable_exceptions as e:
            last_exception = e
            elapsed_ms = (time.monotonic() - attempt_start) * 1000

            # Record failure with circuit breaker
            if circuit_breaker:
                circuit_breaker.record_failure()
                if circuit_breaker.is_open:
                    _log_exhausted(
                        service, operation, attempt + 1, e,
                        (time.monotonic() - start_time) * 1000, context,
                    )
                    raise CircuitOpenError(
                        f"Circuit breaker opened for '{circuit_breaker.service}' "
                        f"after {circuit_breaker.failure_count} consecutive failures."
                    ) from e

            if attempt < max_retries:
                delay = _compute_delay(attempt, base_delay, max_delay, backoff_factor, jitter)
                _log_retry(service, operation, attempt + 1, max_retries, delay, e, elapsed_ms, context)
                await asyncio.sleep(delay)
            else:
                total_elapsed_ms = (time.monotonic() - start_time) * 1000
                _log_exhausted(service, operation, attempt + 1, e, total_elapsed_ms, context)

        except Exception:
            # Non-retryable exception — fail immediately
            raise

    # Should only reach here if all retries exhausted
    assert last_exception is not None
    raise last_exception


# ── Sync Retry ───────────────────────────────────────────────────────────────

def retry_sync(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    service: str = "unknown",
    operation: str = "unknown",
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """
    Retry a synchronous callable with exponential backoff.

    Same interface as retry_async but for sync functions (R2/GCS uploads).
    See retry_async docstring for parameter details.
    """
    last_exception: Exception | None = None
    start_time = time.monotonic()

    for attempt in range(max_retries + 1):
        attempt_start = time.monotonic()
        try:
            return fn(*args, **kwargs)
        except retryable_exceptions as e:
            last_exception = e
            elapsed_ms = (time.monotonic() - attempt_start) * 1000

            if attempt < max_retries:
                delay = _compute_delay(attempt, base_delay, max_delay, backoff_factor, jitter)
                _log_retry(service, operation, attempt + 1, max_retries, delay, e, elapsed_ms, context)
                time.sleep(delay)
            else:
                total_elapsed_ms = (time.monotonic() - start_time) * 1000
                _log_exhausted(service, operation, attempt + 1, e, total_elapsed_ms, context)
        except Exception:
            raise

    assert last_exception is not None
    raise last_exception


# ── Circuit Breaker ──────────────────────────────────────────────────────────

class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and calls are being rejected."""


@dataclass
class CircuitBreaker:
    """
    Simple circuit breaker to prevent hammering a failing service.

    States:
      CLOSED  — normal operation, requests flow through
      OPEN    — too many failures, requests are rejected for `reset_after_s` seconds
      HALF-OPEN — after cooldown, one request is allowed through to test recovery

    Usage:
        gemini_breaker = CircuitBreaker(service="gemini", failure_threshold=5)

        result = await retry_async(
            fn, circuit_breaker=gemini_breaker, ...
        )
    """

    service: str
    failure_threshold: int = 5
    """Number of consecutive failures before the circuit opens."""

    reset_after_s: float = 60.0
    """Seconds to wait before attempting to close the circuit."""

    # Internal state
    failure_count: int = field(default=0, repr=False)
    _last_failure_time: float | None = field(default=None, repr=False)
    _state: str = field(default="closed", repr=False)

    @property
    def state(self) -> str:
        if self._state == "open":
            # Check if cooldown has elapsed → transition to half-open
            if self._last_failure_time is not None:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.reset_after_s:
                    self._state = "half-open"
                    return "half-open"
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def record_success(self) -> None:
        """Record a successful call — resets the breaker to closed."""
        if self._state in ("half-open", "open"):
            logger.info(
                json.dumps({
                    "event": "circuit_closed",
                    "service": self.service,
                    "previous_failures": self.failure_count,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            )
        self.failure_count = 0
        self._state = "closed"
        self._last_failure_time = None

    def record_failure(self) -> None:
        """Record a failed call. Opens the circuit if threshold is reached."""
        self.failure_count += 1
        self._last_failure_time = time.monotonic()

        if self.failure_count >= self.failure_threshold:
            self._state = "open"
            logger.error(
                json.dumps({
                    "event": "circuit_opened",
                    "service": self.service,
                    "failure_count": self.failure_count,
                    "cooldown_s": self.reset_after_s,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            )

    def reset(self) -> None:
        """Manually reset the circuit breaker to closed state."""
        self.failure_count = 0
        self._state = "closed"
        self._last_failure_time = None

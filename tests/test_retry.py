"""
tests/test_retry.py

Unit tests for dharmapath.utils.retry — retry_async, retry_sync, CircuitBreaker.
Tests are isolated (no external services, no network calls).
"""

import asyncio
import pytest
import time
from unittest.mock import MagicMock

from dharmapath.utils.retry import (
    retry_async,
    retry_sync,
    CircuitBreaker,
    CircuitOpenError,
    _compute_delay,
)


# ── Test helpers ─────────────────────────────────────────────────────────────

class TransientError(Exception):
    """Simulates a retryable error."""


class PermanentError(Exception):
    """Simulates a non-retryable error."""


# ── _compute_delay ───────────────────────────────────────────────────────────

class TestComputeDelay:
    def test_exponential_growth(self):
        """Delays should grow exponentially."""
        d0 = _compute_delay(0, base_delay=1.0, max_delay=60.0, backoff_factor=2.0, jitter=False)
        d1 = _compute_delay(1, base_delay=1.0, max_delay=60.0, backoff_factor=2.0, jitter=False)
        d2 = _compute_delay(2, base_delay=1.0, max_delay=60.0, backoff_factor=2.0, jitter=False)

        assert d0 == 1.0
        assert d1 == 2.0
        assert d2 == 4.0

    def test_max_delay_cap(self):
        """Delay should be capped at max_delay."""
        delay = _compute_delay(10, base_delay=1.0, max_delay=30.0, backoff_factor=2.0, jitter=False)
        assert delay == 30.0

    def test_jitter_adds_variance(self):
        """With jitter, delays should vary within ±25%."""
        delays = set()
        for _ in range(100):
            d = _compute_delay(1, base_delay=4.0, max_delay=60.0, backoff_factor=2.0, jitter=True)
            delays.add(round(d, 2))

        # Should not all be the same value
        assert len(delays) > 1

        # Should be within ±25% of 8.0 (= 4.0 * 2^1)
        for d in delays:
            assert 5.9 <= d <= 10.1  # 8.0 ± 25% with a bit of margin


# ── retry_sync ───────────────────────────────────────────────────────────────

class TestRetrySync:
    def test_success_on_first_try(self):
        """Should return immediately if no error."""
        fn = MagicMock(return_value="ok")
        result = retry_sync(
            fn,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(TransientError,),
            service="test",
            operation="test_op",
        )
        assert result == "ok"
        assert fn.call_count == 1

    def test_success_after_transient_errors(self):
        """Should retry and succeed after transient errors."""
        call_count = 0

        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError(f"fail #{call_count}")
            return "recovered"

        result = retry_sync(
            flaky_fn,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(TransientError,),
            service="test",
            operation="test_op",
        )
        assert result == "recovered"
        assert call_count == 3

    def test_exhaustion_raises_last_error(self):
        """Should raise the last error after all retries exhausted."""
        fn = MagicMock(side_effect=TransientError("always fails"))

        with pytest.raises(TransientError, match="always fails"):
            retry_sync(
                fn,
                max_retries=2,
                base_delay=0.01,
                retryable_exceptions=(TransientError,),
                service="test",
                operation="test_op",
            )

        assert fn.call_count == 3  # 1 initial + 2 retries

    def test_non_retryable_exception_fails_immediately(self):
        """Non-retryable exceptions should not be retried."""
        fn = MagicMock(side_effect=PermanentError("bad input"))

        with pytest.raises(PermanentError, match="bad input"):
            retry_sync(
                fn,
                max_retries=3,
                base_delay=0.01,
                retryable_exceptions=(TransientError,),  # PermanentError NOT included
                service="test",
                operation="test_op",
            )

        assert fn.call_count == 1  # No retries

    def test_passes_args_and_kwargs(self):
        """Should pass positional and keyword args to the function."""
        fn = MagicMock(return_value="result")
        retry_sync(
            fn, "arg1", "arg2",
            max_retries=1,
            base_delay=0.01,
            retryable_exceptions=(TransientError,),
            service="test",
            operation="test_op",
            key1="val1",
        )
        fn.assert_called_once_with("arg1", "arg2", key1="val1")


# ── retry_async ──────────────────────────────────────────────────────────────

class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Async: should return immediately if no error."""
        call_count = 0

        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_async(
            fn,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(TransientError,),
            service="test",
            operation="test_op",
        )
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_transient_errors(self):
        """Async: should retry and succeed."""
        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError(f"fail #{call_count}")
            return "recovered"

        result = await retry_async(
            flaky_fn,
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(TransientError,),
            service="test",
            operation="test_op",
        )
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exhaustion_raises(self):
        """Async: should raise after all retries exhausted."""
        async def always_fails():
            raise TransientError("nope")

        with pytest.raises(TransientError, match="nope"):
            await retry_async(
                always_fails,
                max_retries=2,
                base_delay=0.01,
                retryable_exceptions=(TransientError,),
                service="test",
                operation="test_op",
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_integration(self):
        """Circuit breaker should open after threshold failures."""
        breaker = CircuitBreaker(service="test", failure_threshold=2, reset_after_s=0.1)

        async def always_fails():
            raise TransientError("fail")

        # First call exhausts retries and records failures
        with pytest.raises(CircuitOpenError):
            await retry_async(
                always_fails,
                max_retries=1,
                base_delay=0.01,
                retryable_exceptions=(TransientError,),
                service="test",
                operation="test_op",
                circuit_breaker=breaker,
            )

        # Circuit should now be open (2 failures = threshold)
        assert breaker.is_open

        # Next call should fail immediately with CircuitOpenError
        with pytest.raises(CircuitOpenError):
            await retry_async(
                always_fails,
                max_retries=1,
                base_delay=0.01,
                retryable_exceptions=(TransientError,),
                service="test",
                operation="test_op",
                circuit_breaker=breaker,
            )


# ── CircuitBreaker ───────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_starts_closed(self):
        breaker = CircuitBreaker(service="test", failure_threshold=3)
        assert breaker.state == "closed"
        assert not breaker.is_open

    def test_opens_after_threshold(self):
        breaker = CircuitBreaker(service="test", failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        assert not breaker.is_open

        breaker.record_failure()
        assert breaker.is_open
        assert breaker.state == "open"

    def test_success_resets_count(self):
        breaker = CircuitBreaker(service="test", failure_threshold=3)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()

        assert breaker.failure_count == 0
        assert breaker.state == "closed"

    def test_half_open_after_cooldown(self):
        breaker = CircuitBreaker(service="test", failure_threshold=2, reset_after_s=0.05)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open

        # Wait for cooldown
        time.sleep(0.1)
        assert breaker.state == "half-open"
        assert not breaker.is_open

    def test_reset_clears_state(self):
        breaker = CircuitBreaker(service="test", failure_threshold=2)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open

        breaker.reset()
        assert breaker.state == "closed"
        assert breaker.failure_count == 0

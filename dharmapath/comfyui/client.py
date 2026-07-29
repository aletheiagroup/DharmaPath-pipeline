"""
dharmapath/comfyui/client.py

Async ComfyUI REST API client using httpx.
Handles: queue → poll → download → save flow.
Runs against a remote GCE ComfyUI instance (no local GPU).

Resilience features:
  - Retries with exponential backoff on transient HTTP failures
  - Distinct transient vs permanent error types
  - Explicit timeout budgets per operation
  - Circuit breaker support (optional)
  - Full pipeline retry on generate_panel()
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from config.settings import settings
from dharmapath.utils.retry import retry_async, CircuitBreaker

logger = logging.getLogger(__name__)

# ── Timeout Budgets ───────────────────────────────────────────────────────────

QUEUE_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
POLL_TIMEOUT_S = 1200.0       # 20 minutes max for a single generation
POLL_INTERVAL = 2.0           # seconds between /history polls
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
HEALTH_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# ── Retry Policies ───────────────────────────────────────────────────────────

# Retryable HTTP status codes (transient server errors)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Exceptions that indicate transient failures
_TRANSIENT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


# ── Error Types ──────────────────────────────────────────────────────────────

class ComfyUIError(Exception):
    """Permanent error — will NOT be retried (bad workflow, missing model, etc.)."""


class ComfyUITransientError(ComfyUIError):
    """Transient error — WILL be retried (network, 5xx, rate limit)."""


def _classify_http_error(response: httpx.Response) -> ComfyUIError:
    """Classify an HTTP error response as transient or permanent."""
    if response.status_code in _RETRYABLE_STATUS_CODES:
        return ComfyUITransientError(
            f"HTTP {response.status_code} — {response.text[:200]}"
        )
    # 4xx (except 429) = permanent
    return ComfyUIError(
        f"HTTP {response.status_code} — {response.text[:300]}"
    )


# ── Client ───────────────────────────────────────────────────────────────────

class ComfyUIClient:
    """
    Async client for the ComfyUI REST API on GCE.

    All methods are async — use inside an async context or
    with asyncio.run() from synchronous code.

    Usage:
        async with ComfyUIClient() as client:
            path = await client.generate_panel(workflow, "data/outputs/ch01/p01.png")
    """

    def __init__(
        self,
        base_url: str | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._base_url = (base_url or settings.comfyui_base_url).rstrip("/")
        self._headers: dict[str, str] = {}
        if settings.runpod_api_key:
            self._headers["Authorization"] = f"Bearer {settings.runpod_api_key}"
        self._client: httpx.AsyncClient | None = None
        self._circuit_breaker = circuit_breaker

    async def __aenter__(self) -> "ComfyUIClient":
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=QUEUE_TIMEOUT,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError(
                "ComfyUIClient must be used as an async context manager. "
                "Use: async with ComfyUIClient() as client: ..."
            )
        return self._client

    # ── Core API methods ──────────────────────────────────────

    async def queue_prompt(self, workflow: dict) -> str:
        """
        POST /prompt — queue a workflow for generation.
        Returns the prompt_id string assigned by ComfyUI.

        Retries: 3 attempts, 2s base backoff on transient HTTP errors.
        """
        async def _do_queue() -> str:
            url = f"{self._base_url}/prompt"
            clean_workflow = {k: v for k, v in workflow.items() if k.isdigit()}
            payload = {"prompt": clean_workflow}

            response = await self._http().post(url, json=payload, timeout=QUEUE_TIMEOUT)

            if response.status_code != 200:
                raise _classify_http_error(response)

            data = response.json()
            prompt_id = data.get("prompt_id")
            if not prompt_id:
                raise ComfyUIError(f"No prompt_id in response: {data}")

            logger.info(f"Queued prompt — prompt_id={prompt_id}")
            return prompt_id

        return await retry_async(
            _do_queue,
            max_retries=3,
            base_delay=2.0,
            backoff_factor=2.0,
            retryable_exceptions=(ComfyUITransientError, *_TRANSIENT_EXCEPTIONS),
            service="comfyui",
            operation="queue_prompt",
            circuit_breaker=self._circuit_breaker,
        )

    async def poll_status(self, prompt_id: str) -> dict:
        """
        Poll GET /history/{prompt_id} until the generation completes.
        Returns the full history entry for the prompt.

        Individual poll HTTP calls retry on transient errors.
        Overall polling has a 20-minute timeout budget.
        """
        url = f"{self._base_url}/history/{prompt_id}"
        start_time = time.monotonic()

        logger.debug(f"Polling status for prompt_id={prompt_id}")

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > POLL_TIMEOUT_S:
                raise ComfyUIError(
                    f"Timed out waiting for prompt_id={prompt_id} "
                    f"after {POLL_TIMEOUT_S:.0f}s"
                )

            try:
                response = await self._http().get(url, timeout=QUEUE_TIMEOUT)

                if response.status_code == 200:
                    data = response.json()
                    if prompt_id in data:
                        history_entry = data[prompt_id]
                        # Check for execution errors in the history
                        status_data = history_entry.get("status", {})
                        if status_data.get("status_str") == "error":
                            msgs = status_data.get("messages", [])
                            error_detail = str(msgs)[:300] if msgs else "Unknown error"
                            raise ComfyUIError(
                                f"ComfyUI execution error for {prompt_id}: {error_detail}"
                            )
                        if history_entry.get("outputs"):
                            logger.info(f"Prompt {prompt_id} complete in {elapsed:.1f}s")
                            return history_entry

                elif response.status_code not in (404,):
                    # Log but don't fail on transient poll errors
                    logger.warning(
                        f"Unexpected status {response.status_code} polling {prompt_id}"
                    )

            except (_TRANSIENT_EXCEPTIONS) as e:
                # Transient network errors during polling — log and retry on next interval
                logger.warning(
                    f"Transient error polling {prompt_id}: {type(e).__name__}: {e}"
                )

            await asyncio.sleep(POLL_INTERVAL)

    async def _download_image(self, image_info: dict) -> bytes | None:
        """Download a single output image with retries."""
        filename = image_info.get("filename")
        subfolder = image_info.get("subfolder", "")
        folder_type = image_info.get("type", "output")

        params = {"filename": filename, "type": folder_type}
        if subfolder:
            params["subfolder"] = subfolder

        async def _do_download() -> bytes:
            view_url = f"{self._base_url}/view"
            response = await self._http().get(view_url, params=params, timeout=DOWNLOAD_TIMEOUT)

            if response.status_code != 200:
                raise _classify_http_error(response)

            return response.content

        try:
            image_bytes = await retry_async(
                _do_download,
                max_retries=3,
                base_delay=1.0,
                retryable_exceptions=(ComfyUITransientError, *_TRANSIENT_EXCEPTIONS),
                service="comfyui",
                operation="download_image",
                context={"filename": filename},
            )
            logger.debug(f"Downloaded {filename} ({len(image_bytes)} bytes)")
            return image_bytes
        except Exception as e:
            logger.error(f"Failed to download image {filename} after retries: {e}")
            return None

    async def get_output_images(self, prompt_id: str) -> list[bytes]:
        """
        Download all output images for a completed prompt_id.
        Returns a list of raw image bytes (one per output node image).

        Each image download retries independently (3 attempts).
        """
        history = await self.poll_status(prompt_id)
        images: list[bytes] = []

        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            for image_info in node_output.get("images", []):
                image_bytes = await self._download_image(image_info)
                if image_bytes:
                    images.append(image_bytes)

        return images

    async def generate_panel(
        self,
        workflow: dict,
        save_path: str,
        max_retries: int = 2,
    ) -> str:
        """
        Full generation flow: queue → poll → download → save to disk.

        Retries the ENTIRE flow up to max_retries times on failure.
        This catches cases like GPU OOM, generation producing no output,
        or transient ComfyUI crashes mid-generation.

        Args:
            workflow: ComfyUI workflow dict (from WorkflowBuilder)
            save_path: Local file path to save the output image
            max_retries: Number of full-pipeline retries (default: 2)

        Returns:
            The save_path string on success.

        Raises:
            ComfyUIError if generation fails after all retries.
        """
        async def _do_generate() -> str:
            prompt_id = await self.queue_prompt(workflow)
            images = await self.get_output_images(prompt_id)

            if not images:
                raise ComfyUITransientError(
                    f"No output images returned for prompt_id={prompt_id}"
                )

            # Take the last image (ESRGAN upscale node is last output)
            image_data = images[-1]

            out_path = Path(save_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(image_data)

            logger.info(f"Panel saved to {save_path} ({len(image_data)} bytes)")
            return save_path

        return await retry_async(
            _do_generate,
            max_retries=max_retries,
            base_delay=5.0,
            max_delay=30.0,
            retryable_exceptions=(ComfyUITransientError,),
            service="comfyui",
            operation="generate_panel",
            context={"save_path": save_path},
        )

    async def health_check(self) -> bool:
        """
        GET /system_stats — returns True if ComfyUI is reachable.

        Retries: 2 attempts, 5s base backoff.
        """
        async def _do_health() -> bool:
            url = f"{self._base_url}/system_stats"
            response = await self._http().get(url, timeout=HEALTH_TIMEOUT)
            if response.status_code == 200:
                logger.info(f"ComfyUI health check passed at {self._base_url}")
                return True
            raise ComfyUITransientError(f"Health check HTTP {response.status_code}")

        try:
            return await retry_async(
                _do_health,
                max_retries=2,
                base_delay=5.0,
                retryable_exceptions=(ComfyUITransientError, *_TRANSIENT_EXCEPTIONS),
                service="comfyui",
                operation="health_check",
            )
        except Exception as e:
            logger.error(f"ComfyUI not reachable at {self._base_url}: {e}")
            return False

    async def get_queue_status(self) -> dict:
        """GET /queue — returns current queue size."""
        url = f"{self._base_url}/queue"
        response = await self._http().get(url)
        response.raise_for_status()
        return response.json()

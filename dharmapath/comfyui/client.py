"""
dharmapath/comfyui/client.py

Async ComfyUI REST API client using httpx.
Handles: queue → poll → download → save flow.
Runs against a remote RunPod ComfyUI instance (no local GPU).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

# How often to poll /history for completion (seconds)
POLL_INTERVAL = 2.0
# Max time to wait for a single generation before giving up
POLL_TIMEOUT = 600.0  # 10 minutes


class ComfyUIError(Exception):
    """Raised when ComfyUI returns an unexpected response."""


class ComfyUIClient:
    """
    Async client for the ComfyUI REST API on RunPod.

    All methods are async — use inside an async context or
    with asyncio.run() from synchronous code.

    Usage:
        async with ComfyUIClient() as client:
            path = await client.generate_panel(workflow, "data/outputs/ch01/p01.png")
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.comfyui_base_url).rstrip("/")
        self._headers: dict[str, str] = {}
        if settings.runpod_api_key:
            self._headers["Authorization"] = f"Bearer {settings.runpod_api_key}"
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ComfyUIClient":
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(30.0, read=60.0),
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
        """
        url = f"{self._base_url}/prompt"
        payload = {"prompt": workflow}

        logger.debug(f"Queueing prompt at {url}")
        response = await self._http().post(url, json=payload)

        if response.status_code != 200:
            raise ComfyUIError(
                f"queue_prompt failed: HTTP {response.status_code} — {response.text[:300]}"
            )

        data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"No prompt_id in response: {data}")

        logger.info(f"Queued prompt — prompt_id={prompt_id}")
        return prompt_id

    async def poll_status(self, prompt_id: str) -> dict:
        """
        Poll GET /history/{prompt_id} until the generation completes.
        Returns the full history entry for the prompt.

        Raises ComfyUIError if polling times out.
        """
        url = f"{self._base_url}/history/{prompt_id}"
        start_time = time.monotonic()

        logger.debug(f"Polling status for prompt_id={prompt_id}")

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > POLL_TIMEOUT:
                raise ComfyUIError(
                    f"Timed out waiting for prompt_id={prompt_id} "
                    f"after {POLL_TIMEOUT:.0f}s"
                )

            response = await self._http().get(url)
            if response.status_code == 200:
                data = response.json()
                if prompt_id in data:
                    history_entry = data[prompt_id]
                    # ComfyUI marks completion with "outputs" present
                    if history_entry.get("outputs"):
                        logger.info(
                            f"Prompt {prompt_id} complete in {elapsed:.1f}s"
                        )
                        return history_entry
            elif response.status_code != 404:
                logger.warning(
                    f"Unexpected status {response.status_code} polling {prompt_id}"
                )

            await asyncio.sleep(POLL_INTERVAL)

    async def get_output_images(self, prompt_id: str) -> list[bytes]:
        """
        Download all output images for a completed prompt_id.
        Returns a list of raw image bytes (one per output node image).
        """
        history = await self.poll_status(prompt_id)
        images: list[bytes] = []

        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            for image_info in node_output.get("images", []):
                filename = image_info.get("filename")
                subfolder = image_info.get("subfolder", "")
                folder_type = image_info.get("type", "output")

                params = {"filename": filename, "type": folder_type}
                if subfolder:
                    params["subfolder"] = subfolder

                view_url = f"{self._base_url}/view"
                logger.debug(f"Downloading image: {filename} from {view_url}")

                response = await self._http().get(view_url, params=params)
                if response.status_code == 200:
                    images.append(response.content)
                    logger.debug(f"Downloaded {filename} ({len(response.content)} bytes)")
                else:
                    logger.error(
                        f"Failed to download image {filename}: "
                        f"HTTP {response.status_code}"
                    )

        return images

    async def generate_panel(self, workflow: dict, save_path: str) -> str:
        """
        Full generation flow: queue → poll → download → save to disk.

        Args:
            workflow: ComfyUI workflow dict (from WorkflowBuilder)
            save_path: Local file path to save the output image

        Returns:
            The save_path string on success.

        Raises:
            ComfyUIError if generation fails or produces no output.
        """
        prompt_id = await self.queue_prompt(workflow)
        images = await self.get_output_images(prompt_id)

        if not images:
            raise ComfyUIError(
                f"No output images returned for prompt_id={prompt_id}"
            )

        # Take the first image (ESRGAN upscale node is last, so last image is best)
        image_data = images[-1]

        out_path = Path(save_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_data)

        logger.info(f"Panel saved to {save_path} ({len(image_data)} bytes)")
        return save_path

    async def health_check(self) -> bool:
        """
        GET /system_stats — returns True if ComfyUI is reachable.
        Used by scripts/check_runpod.py and the CLI check-runpod command.
        """
        url = f"{self._base_url}/system_stats"
        try:
            response = await self._http().get(url)
            ok = response.status_code == 200
            if ok:
                logger.info(f"ComfyUI health check passed at {self._base_url}")
            else:
                logger.warning(
                    f"ComfyUI health check failed: HTTP {response.status_code}"
                )
            return ok
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"ComfyUI not reachable at {self._base_url}: {e}")
            return False

    async def get_queue_status(self) -> dict:
        """
        GET /queue — returns current queue size.
        Useful for monitoring RunPod load before queueing a large batch.
        """
        url = f"{self._base_url}/queue"
        response = await self._http().get(url)
        response.raise_for_status()
        return response.json()

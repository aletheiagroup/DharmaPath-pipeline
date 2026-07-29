"""
tests/test_live_comfyui.py

Live smoke test for a real ComfyUI server.

SKIPPED AUTOMATICALLY unless a live ComfyUI instance is configured.

Skip conditions (any one of these causes all tests in this file to skip):
  - COMFYUI_BASE_URL is not set or is the placeholder value
  - The /system_stats endpoint is unreachable within 5 seconds

This test is intentionally lightweight. Its purpose is NOT to verify
model quality — it verifies that:
  1. ComfyUI is reachable
  2. /system_stats returns valid data
  3. A minimal workflow can be queued and polled
  4. An image can be downloaded from /view
  5. The image is non-empty (>0 bytes)

One generated image is sufficient. The test uses steps=1 and cfg=1.0
to minimise GPU time and cost.

Run manually:
    .venv/Scripts/pytest tests/test_live_comfyui.py -v -s

Force-run even if marked slow:
    .venv/Scripts/pytest tests/test_live_comfyui.py -v -s -m live
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

# ── Availability check — runs at collection time ─────────────────────────────

def _comfyui_is_live() -> bool:
    """
    Return True only if:
      1. COMFYUI_BASE_URL is set and is not the placeholder value
      2. The /system_stats endpoint responds within 5 seconds
    """
    try:
        from config.settings import settings
        url = settings.comfyui_base_url
    except Exception:
        return False

    placeholder_values = {"", "http://localhost:8188", "http://YOUR_GCE_VM_IP:8188"}
    if url in placeholder_values:
        return False

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{url.rstrip('/')}/system_stats")
            return resp.status_code == 200
    except Exception:
        return False


# Evaluate once at module load — skip all tests if no live server
_LIVE = _comfyui_is_live()
_SKIP_REASON = (
    "No live ComfyUI server detected. "
    "Set COMFYUI_BASE_URL in .env to a reachable ComfyUI instance to run these tests."
)

pytestmark = [
    pytest.mark.skipif(not _LIVE, reason=_SKIP_REASON),
    pytest.mark.live,   # custom marker — use `-m live` to select
]


# ── Minimal workflow for smoke testing ────────────────────────────────────────

def _build_smoke_workflow() -> dict:
    """
    A minimal ComfyUI workflow that generates a tiny image.
    Uses 1 step and cfg=1.0 to minimise compute time.
    Requires: a base checkpoint model named in the 'ckpt_name' field.

    If Illustrious XL is not available, change 'ckpt_name' to any checkpoint
    present in your ComfyUI models/checkpoints directory.
    """
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "_meta": {"title": "Checkpoint Loader"},
            "inputs": {
                "ckpt_name": "illustrious_xl.safetensors"  # adjust if needed
            }
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Positive Prompt"},
            "inputs": {
                "text": "a simple test image, solid colour, no detail",
                "clip": ["1", 1]
            }
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Negative Prompt"},
            "inputs": {
                "text": "nsfw, ugly, bad quality",
                "clip": ["1", 1]
            }
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "_meta": {"title": "Latent"},
            "inputs": {
                "width": 64,   # tiny — just to verify pipeline, not quality
                "height": 64,
                "batch_size": 1
            }
        },
        "5": {
            "class_type": "KSampler",
            "_meta": {"title": "KSampler"},
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": 12345,
                "steps": 1,       # absolute minimum — just verify it runs
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0
            }
        },
        "6": {
            "class_type": "VAEDecode",
            "_meta": {"title": "VAE Decode"},
            "inputs": {
                "samples": ["5", 0],
                "vae": ["1", 2]
            }
        },
        "7": {
            "class_type": "SaveImage",
            "_meta": {"title": "Save Image"},
            "inputs": {
                "images": ["6", 0],
                "filename_prefix": "dharmapath_smoke_test"
            }
        }
    }


# ── Live tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_live_system_stats():
    """
    Verify /system_stats returns valid JSON with expected fields.
    This is the most basic liveness check.
    """
    from dharmapath.comfyui.client import ComfyUIClient

    async with ComfyUIClient() as client:
        result = await client.health_check()

    assert result is True, (
        "health_check() returned False. "
        "ComfyUI may be unreachable or returning a non-200 status."
    )


@pytest.mark.asyncio
async def test_live_system_stats_fields():
    """
    Verify /system_stats returns the expected top-level fields.
    ComfyUI should report: system, devices, and possibly python_version.
    """
    from config.settings import settings

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{settings.comfyui_base_url.rstrip('/')}/system_stats")

    assert resp.status_code == 200
    data = resp.json()

    # ComfyUI always returns these top-level keys
    assert "system" in data or "devices" in data, (
        f"/system_stats response missing expected fields. Got: {list(data.keys())}"
    )


@pytest.mark.asyncio
async def test_live_queue_minimal_workflow():
    """
    Queue a minimal workflow and verify ComfyUI assigns a prompt_id.
    Does NOT wait for completion — just checks queueing works.
    """
    from dharmapath.comfyui.client import ComfyUIClient

    workflow = _build_smoke_workflow()

    async with ComfyUIClient() as client:
        prompt_id = await client.queue_prompt(workflow)

    assert prompt_id, "queue_prompt() returned empty or None prompt_id"
    assert isinstance(prompt_id, str)
    assert len(prompt_id) > 0


@pytest.mark.asyncio
async def test_live_full_generation_smoke(tmp_path: Path):
    """
    Full smoke test: queue a minimal workflow, poll until done, download the image.

    This test verifies the complete /prompt → /history → /view flow.
    Uses steps=1, 64x64 to minimise GPU time.

    Expected outcome:
      - A real PNG file is saved to disk
      - The file is > 0 bytes
      - Pillow can open it without errors
    """
    from PIL import Image
    from dharmapath.comfyui.client import ComfyUIClient

    workflow = _build_smoke_workflow()
    save_path = tmp_path / "smoke_test_panel.png"

    async with ComfyUIClient() as client:
        result = await client.generate_panel(workflow, str(save_path), max_retries=1)

    # File should exist and be non-empty
    assert save_path.exists(), f"Expected generated image at {save_path} but file not found."
    assert save_path.stat().st_size > 0, "Generated image file is empty (0 bytes)."

    # Verify Pillow can open and read it
    img = Image.open(save_path)
    assert img.width > 0
    assert img.height > 0

    print(f"\n[smoke test] Generated image: {save_path} ({save_path.stat().st_size} bytes)")
    print(f"[smoke test] Image dimensions: {img.width}x{img.height} px")
    print(f"[smoke test] Image mode: {img.mode}")


@pytest.mark.asyncio
async def test_live_queue_status_endpoint():
    """
    Verify /queue returns a valid response with queue_running and queue_pending.
    """
    from dharmapath.comfyui.client import ComfyUIClient

    async with ComfyUIClient() as client:
        result = await client.get_queue_status()

    assert "queue_running" in result, f"/queue missing 'queue_running'. Got: {list(result.keys())}"
    assert "queue_pending" in result, f"/queue missing 'queue_pending'. Got: {list(result.keys())}"


@pytest.mark.asyncio
async def test_live_prompt_id_roundtrip():
    """
    Verify that after queuing a workflow, the same prompt_id appears
    in either /history or the /queue response.
    Confirms the server is correctly tracking submissions.
    """
    from config.settings import settings
    from dharmapath.comfyui.client import ComfyUIClient

    workflow = _build_smoke_workflow()

    async with ComfyUIClient() as client:
        prompt_id = await client.queue_prompt(workflow)
        queue_data = await client.get_queue_status()

    # prompt_id should be somewhere in queue_pending or queue_running
    all_queued_ids = set()
    for item in queue_data.get("queue_pending", []):
        if isinstance(item, list) and len(item) > 1:
            all_queued_ids.add(str(item[1]))
    for item in queue_data.get("queue_running", []):
        if isinstance(item, list) and len(item) > 1:
            all_queued_ids.add(str(item[1]))

    # Also check /history directly
    async with httpx.AsyncClient(timeout=10.0) as http:
        hist = await http.get(
            f"{settings.comfyui_base_url.rstrip('/')}/history/{prompt_id}"
        )
        in_history = prompt_id in hist.json()

    assert prompt_id in all_queued_ids or in_history, (
        f"prompt_id '{prompt_id}' not found in /queue or /history. "
        "The job may have completed very quickly, or there's a server issue."
    )

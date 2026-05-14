"""
scripts/check_runpod.py

Verify that the RunPod ComfyUI instance is reachable before running the pipeline.
Usage: python scripts/check_runpod.py
"""

import asyncio
import sys
import httpx
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings


async def check_comfyui() -> bool:
    """Ping the ComfyUI /system_stats endpoint and print results."""
    url = settings.comfyui_system_stats_url
    print(f"🔍 Checking ComfyUI at: {settings.comfyui_base_url}")
    print(f"   Endpoint: {url}")
    print()

    headers = {}
    if settings.runpod_api_key:
        headers["Authorization"] = f"Bearer {settings.runpod_api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

        print("✅ ComfyUI is reachable!")
        print()

        # Print system info if available
        if "system" in data:
            sys_info = data["system"]
            print(f"  OS:      {sys_info.get('os', 'unknown')}")
            print(f"  Python:  {sys_info.get('python_version', 'unknown')}")
            print(f"  CUDA:    {sys_info.get('cuda_version', 'N/A')}")
            print(f"  GPU:     {sys_info.get('accelerate', {}).get('device', 'unknown')}")

        if "devices" in data:
            for device in data["devices"]:
                vram_total = device.get("vram_total", 0) / (1024 ** 3)
                vram_free = device.get("vram_free", 0) / (1024 ** 3)
                print(f"  VRAM:    {vram_free:.1f} GB free / {vram_total:.1f} GB total")

        return True

    except httpx.ConnectError:
        print(f"❌ Connection refused — is the RunPod instance running?")
        print(f"   Check your COMFYUI_BASE_URL in .env: {settings.comfyui_base_url}")
        return False
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP {e.response.status_code}: {e.response.text[:200]}")
        if e.response.status_code == 401:
            print("   Check your RUNPOD_API_KEY in .env")
        return False
    except httpx.TimeoutException:
        print(f"❌ Timeout — ComfyUI took too long to respond (>10s)")
        print(f"   The pod may be starting up. Wait 30 seconds and try again.")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


if __name__ == "__main__":
    ok = asyncio.run(check_comfyui())
    sys.exit(0 if ok else 1)

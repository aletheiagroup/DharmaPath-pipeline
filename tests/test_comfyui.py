"""
tests/test_comfyui.py

Unit tests for dharmapath.comfyui.client.ComfyUIClient.

All HTTP calls are mocked — no live ComfyUI server required.
Tests verify:
  - Request structure and headers
  - Retry behaviour on transient errors (5xx, network failures)
  - Polling loop (handles empty history, then completion)
  - Error classification (transient vs permanent)
  - generate_panel() full flow: queue → poll → download → save
  - health_check() true/false paths
  - Circuit breaker integration

Run with:
    .venv/Scripts/pytest tests/test_comfyui.py -v
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest

from dharmapath.comfyui.client import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUITransientError,
    _classify_http_error,
    POLL_INTERVAL,
)
from dharmapath.utils.retry import CircuitBreaker, CircuitOpenError


# ── Helpers ───────────────────────────────────────────────────────────────────

FAKE_PROMPT_ID = "abc-123-def"
FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 512   # fake PNG header + padding

# A minimal ComfyUI /history response that signals completion
def _history_response(prompt_id: str = FAKE_PROMPT_ID) -> dict:
    return {
        prompt_id: {
            "status": {"status_str": "success"},
            "outputs": {
                "35": {
                    "images": [
                        {"filename": "dp_ch01_p01_v1_00001.png", "subfolder": "", "type": "output"}
                    ]
                }
            }
        }
    }


def _mock_http_response(status_code: int, json_body: dict | None = None, content: bytes = b"") -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(json_body) if json_body else ""
    resp.content = content
    resp.json = MagicMock(return_value=json_body or {})
    return resp


# ── _classify_http_error ──────────────────────────────────────────────────────

class TestClassifyHttpError:
    def test_5xx_is_transient(self):
        resp = _mock_http_response(500)
        err = _classify_http_error(resp)
        assert isinstance(err, ComfyUITransientError)

    def test_429_is_transient(self):
        resp = _mock_http_response(429)
        err = _classify_http_error(resp)
        assert isinstance(err, ComfyUITransientError)

    def test_503_is_transient(self):
        resp = _mock_http_response(503)
        err = _classify_http_error(resp)
        assert isinstance(err, ComfyUITransientError)

    def test_400_is_permanent(self):
        resp = _mock_http_response(400)
        err = _classify_http_error(resp)
        assert type(err) is ComfyUIError  # NOT transient

    def test_404_is_permanent(self):
        resp = _mock_http_response(404)
        err = _classify_http_error(resp)
        assert type(err) is ComfyUIError

    def test_error_message_contains_status_code(self):
        resp = _mock_http_response(502, {"error": "bad gateway"})
        err = _classify_http_error(resp)
        assert "502" in str(err)


# ── health_check ──────────────────────────────────────────────────────────────

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
        ok_response = _mock_http_response(200, {"system": {}, "devices": []})
        mock_get = AsyncMock(return_value=ok_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_connect_error(self):
        mock_get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            result = await client.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_500(self):
        err_response = _mock_http_response(500)
        mock_get = AsyncMock(return_value=err_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            result = await client.health_check()

        assert result is False


# ── queue_prompt ──────────────────────────────────────────────────────────────

class TestQueuePrompt:
    @pytest.mark.asyncio
    async def test_returns_prompt_id_on_success(self):
        ok_response = _mock_http_response(200, {"prompt_id": FAKE_PROMPT_ID})
        mock_post = AsyncMock(return_value=ok_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = mock_post
            prompt_id = await client.queue_prompt({"3": {"class_type": "KSampler", "inputs": {}}})

        assert prompt_id == FAKE_PROMPT_ID

    @pytest.mark.asyncio
    async def test_raises_on_missing_prompt_id(self):
        bad_response = _mock_http_response(200, {"error": "no id returned"})
        mock_post = AsyncMock(return_value=bad_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = mock_post
            with pytest.raises(ComfyUIError, match="No prompt_id"):
                await client.queue_prompt({"3": {}})

    @pytest.mark.asyncio
    async def test_raises_comfyui_transient_on_503(self):
        err_response = _mock_http_response(503, {})
        mock_post = AsyncMock(return_value=err_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = mock_post
            with pytest.raises((ComfyUITransientError, ComfyUIError)):
                await client.queue_prompt({"3": {}})

    @pytest.mark.asyncio
    async def test_payload_wraps_workflow_under_prompt_key(self):
        """queue_prompt should POST {"prompt": {...workflow...}} to /prompt."""
        ok_response = _mock_http_response(200, {"prompt_id": FAKE_PROMPT_ID})
        mock_post = AsyncMock(return_value=ok_response)
        workflow = {"3": {"class_type": "KSampler", "inputs": {"seed": 42}}}

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = mock_post
            await client.queue_prompt(workflow)

        call_kwargs = mock_post.call_args
        sent_json = call_kwargs.kwargs["json"]
        assert "prompt" in sent_json
        # Non-digit keys (like "_comment") are stripped; digit keys are kept
        assert "3" in sent_json["prompt"]

    @pytest.mark.asyncio
    async def test_strips_non_digit_keys_from_workflow(self):
        """Metadata keys like '_comment' should not be sent to ComfyUI."""
        ok_response = _mock_http_response(200, {"prompt_id": FAKE_PROMPT_ID})
        mock_post = AsyncMock(return_value=ok_response)
        workflow = {
            "_comment": "This is metadata",
            "3": {"class_type": "KSampler", "inputs": {}},
        }

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = mock_post
            await client.queue_prompt(workflow)

        sent_json = mock_post.call_args.kwargs["json"]
        assert "_comment" not in sent_json["prompt"]
        assert "3" in sent_json["prompt"]


# ── poll_status ───────────────────────────────────────────────────────────────

class TestPollStatus:
    @pytest.mark.asyncio
    async def test_returns_history_on_first_poll_if_complete(self):
        history = _history_response()
        ok_response = _mock_http_response(200, history)
        mock_get = AsyncMock(return_value=ok_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            result = await client.poll_status(FAKE_PROMPT_ID)

        assert "outputs" in result
        assert "35" in result["outputs"]

    @pytest.mark.asyncio
    async def test_polls_multiple_times_before_completing(self):
        """Should poll repeatedly, returning empty history until generation completes."""
        empty = _mock_http_response(200, {})  # not complete yet
        complete = _mock_http_response(200, _history_response())

        # First 2 polls return empty, 3rd returns complete
        responses = [empty, empty, complete]
        call_index = 0

        async def fake_get(*args, **kwargs):
            nonlocal call_index
            resp = responses[min(call_index, len(responses) - 1)]
            call_index += 1
            return resp

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = fake_get
            with patch("dharmapath.comfyui.client.asyncio.sleep", new_callable=AsyncMock):
                result = await client.poll_status(FAKE_PROMPT_ID)

        assert result["outputs"] is not None
        assert call_index == 3

    @pytest.mark.asyncio
    async def test_raises_on_execution_error_status(self):
        """ComfyUI signals execution errors via status_str='error' in history."""
        error_history = {
            FAKE_PROMPT_ID: {
                "status": {
                    "status_str": "error",
                    "messages": [["execution_error", {"exception_message": "model not found"}]]
                },
                "outputs": {}
            }
        }
        err_response = _mock_http_response(200, error_history)
        mock_get = AsyncMock(return_value=err_response)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            with pytest.raises(ComfyUIError, match="execution error"):
                await client.poll_status(FAKE_PROMPT_ID)

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        """Should raise ComfyUIError after the polling timeout budget expires."""
        # Always return empty (never completes)
        never_complete = _mock_http_response(200, {})
        mock_get = AsyncMock(return_value=never_complete)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            # Patch POLL_TIMEOUT_S to something tiny so the test runs fast
            with patch("dharmapath.comfyui.client.POLL_TIMEOUT_S", 0.01):
                with patch("dharmapath.comfyui.client.asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(ComfyUIError, match="Timed out"):
                        await client.poll_status(FAKE_PROMPT_ID)

    @pytest.mark.asyncio
    async def test_survives_transient_network_errors_during_poll(self):
        """Transient errors during polling should log and retry, not crash."""
        network_err = httpx.ReadTimeout("timeout")
        complete_response = _mock_http_response(200, _history_response())

        call_index = 0

        async def fake_get(*args, **kwargs):
            nonlocal call_index
            call_index += 1
            if call_index == 1:
                raise network_err
            return complete_response

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = fake_get
            with patch("dharmapath.comfyui.client.asyncio.sleep", new_callable=AsyncMock):
                result = await client.poll_status(FAKE_PROMPT_ID)

        assert "outputs" in result


# ── get_output_images ─────────────────────────────────────────────────────────

class TestGetOutputImages:
    @pytest.mark.asyncio
    async def test_returns_image_bytes_for_completed_prompt(self):
        history_resp = _mock_http_response(200, _history_response())
        image_resp = _mock_http_response(200, content=FAKE_IMAGE_BYTES)

        call_count = 0

        async def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return history_resp   # /history poll
            return image_resp         # /view download

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = fake_get
            images = await client.get_output_images(FAKE_PROMPT_ID)

        assert len(images) == 1
        assert images[0] == FAKE_IMAGE_BYTES

    @pytest.mark.asyncio
    async def test_returns_empty_list_if_no_images_in_output(self):
        no_images_history = {
            FAKE_PROMPT_ID: {
                "status": {"status_str": "success"},
                "outputs": {"35": {"images": []}}
            }
        }
        history_resp = _mock_http_response(200, no_images_history)
        mock_get = AsyncMock(return_value=history_resp)

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.get = mock_get
            images = await client.get_output_images(FAKE_PROMPT_ID)

        assert images == []


# ── generate_panel (full flow) ────────────────────────────────────────────────

class TestGeneratePanel:
    @pytest.mark.asyncio
    async def test_full_flow_saves_image_to_disk(self, tmp_path: Path):
        """
        Happy path: queue → poll (complete) → download → save.
        Verifies the image file is created with the expected bytes.
        """
        queue_resp = _mock_http_response(200, {"prompt_id": FAKE_PROMPT_ID})
        history_resp = _mock_http_response(200, _history_response())
        image_resp = _mock_http_response(200, content=FAKE_IMAGE_BYTES)

        call_count = 0

        async def fake_post(*args, **kwargs):
            return queue_resp

        async def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return history_resp
            return image_resp

        save_path = tmp_path / "test_panel.png"
        workflow = {"3": {"class_type": "KSampler", "inputs": {}}}

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = fake_post
            client._client.get = fake_get
            result = await client.generate_panel(workflow, str(save_path))

        assert result == str(save_path)
        assert save_path.exists()
        assert save_path.read_bytes() == FAKE_IMAGE_BYTES

    @pytest.mark.asyncio
    async def test_raises_when_no_images_returned(self, tmp_path: Path):
        """
        If ComfyUI completes but produces no images, should raise ComfyUITransientError
        (so the full-pipeline retry logic can retry it).
        """
        queue_resp = _mock_http_response(200, {"prompt_id": FAKE_PROMPT_ID})
        empty_history = {
            FAKE_PROMPT_ID: {
                "status": {"status_str": "success"},
                "outputs": {"35": {"images": []}}
            }
        }
        history_resp = _mock_http_response(200, empty_history)

        async def fake_post(*args, **kwargs):
            return queue_resp

        async def fake_get(*args, **kwargs):
            return history_resp

        save_path = tmp_path / "panel.png"
        workflow = {"3": {"class_type": "KSampler", "inputs": {}}}

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = fake_post
            client._client.get = fake_get
            # max_retries=0 so we don't wait forever
            with pytest.raises((ComfyUITransientError, ComfyUIError)):
                await client.generate_panel(workflow, str(save_path), max_retries=0)

    @pytest.mark.asyncio
    async def test_creates_parent_directory_if_needed(self, tmp_path: Path):
        """generate_panel should create missing parent directories automatically."""
        queue_resp = _mock_http_response(200, {"prompt_id": FAKE_PROMPT_ID})
        history_resp = _mock_http_response(200, _history_response())
        image_resp = _mock_http_response(200, content=FAKE_IMAGE_BYTES)

        call_count = 0

        async def fake_post(*args, **kwargs):
            return queue_resp

        async def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return history_resp
            return image_resp

        nested_path = tmp_path / "chapter01" / "panels" / "p01.png"
        assert not nested_path.parent.exists()

        workflow = {"3": {"class_type": "KSampler", "inputs": {}}}

        async with ComfyUIClient(base_url="http://fake-comfyui:8188") as client:
            client._client.post = fake_post
            client._client.get = fake_get
            await client.generate_panel(workflow, str(nested_path))

        assert nested_path.exists()


# ── Authorization header ──────────────────────────────────────────────────────

class TestAuthorizationHeader:
    @pytest.mark.asyncio
    async def test_adds_bearer_token_when_api_key_configured(self):
        """Client should include Authorization: Bearer <key> when RUNPOD_API_KEY is set."""
        with patch("dharmapath.comfyui.client.settings") as mock_settings:
            mock_settings.comfyui_base_url = "http://fake:8188"
            mock_settings.runpod_api_key = "test-api-key-xyz"

            client = ComfyUIClient(base_url="http://fake:8188")

        assert client._headers.get("Authorization") == "Bearer test-api-key-xyz"

    @pytest.mark.asyncio
    async def test_no_authorization_header_when_no_api_key(self):
        """Client should not include Authorization header when API key is empty."""
        with patch("dharmapath.comfyui.client.settings") as mock_settings:
            mock_settings.comfyui_base_url = "http://fake:8188"
            mock_settings.runpod_api_key = ""

            client = ComfyUIClient(base_url="http://fake:8188")

        assert "Authorization" not in client._headers


# ── Context manager ───────────────────────────────────────────────────────────

class TestContextManager:
    @pytest.mark.asyncio
    async def test_raises_if_used_without_context_manager(self):
        """Calling queue_prompt without 'async with' should raise RuntimeError."""
        client = ComfyUIClient(base_url="http://fake:8188")
        with pytest.raises(RuntimeError, match="context manager"):
            await client.queue_prompt({"3": {}})

    @pytest.mark.asyncio
    async def test_cleans_up_http_client_on_exit(self):
        """Ensure the httpx.AsyncClient is closed on __aexit__."""
        ok_response = _mock_http_response(200, {"system": {}})
        async with ComfyUIClient(base_url="http://fake:8188") as client:
            mock_aclose = AsyncMock()
            client._client.aclose = mock_aclose

        mock_aclose.assert_called_once()


# ── get_queue_status ──────────────────────────────────────────────────────────

class TestGetQueueStatus:
    @pytest.mark.asyncio
    async def test_returns_queue_json(self):
        queue_data = {"queue_running": [], "queue_pending": []}
        queue_resp = _mock_http_response(200, queue_data)
        mock_get = AsyncMock(return_value=queue_resp)

        async with ComfyUIClient(base_url="http://fake:8188") as client:
            client._client.get = mock_get
            result = await client.get_queue_status()

        assert "queue_running" in result
        assert result == queue_data

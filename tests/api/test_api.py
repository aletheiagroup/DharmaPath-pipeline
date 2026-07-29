"""
tests/api/test_api.py

API routes unit and integration tests.
Tests all endpoints using FastAPI's TestClient and dependency overrides.
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from web.app import app
from web.dependencies import require_api_key
from web.schemas.chapters import ChapterDetail, ChapterSummary
from web.store import MemoryJobStore, MemoryReviewStore, AsyncTaskManager


# Mock API key verification to bypass headers in tests
async def mock_require_api_key() -> str:
    return "test-key"


@pytest.fixture
def client() -> TestClient:
    # Use dependency override to skip API key check
    app.dependency_overrides[require_api_key] = mock_require_api_key
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_endpoint(client: TestClient) -> None:
    """Test health endpoint (does not require auth)."""
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert "overall" in json_data["data"]
    assert "services" in json_data["data"]


def test_list_chapters(client: TestClient) -> None:
    """Test list chapters endpoint returns a valid response."""
    response = client.get("/api/v1/chapters")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert isinstance(json_data["data"], list)


def test_get_settings(client: TestClient) -> None:
    """Test retrieving runtime settings."""
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert "max_failure_pct_auto" in json_data["data"]
    assert "max_failure_pct_degraded" in json_data["data"]


def test_update_settings(client: TestClient) -> None:
    """Test updating runtime settings."""
    payload = {
        "max_failure_pct_auto": 6.5,
        "default_generation_steps": 40,
    }
    response = client.put("/api/v1/settings", json=payload)
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert json_data["data"]["max_failure_pct_auto"] == 6.5
    assert json_data["data"]["default_generation_steps"] == 40


def test_settings_connections(client: TestClient) -> None:
    """Test connection config status endpoint."""
    response = client.get("/api/v1/settings/connections")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert "comfyui_url" in json_data["data"]
    assert "gemini_model" in json_data["data"]


def test_list_assets(client: TestClient) -> None:
    """Test list assets endpoint returns character list."""
    response = client.get("/api/v1/assets")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert isinstance(json_data["data"], list)


def test_list_categories(client: TestClient) -> None:
    """Test categories list and check character count is present."""
    response = client.get("/api/v1/assets/categories")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    categories = json_data["data"]
    assert any(c["category"] == "characters" for c in categories)


def test_asset_search(client: TestClient) -> None:
    """Test searching assets."""
    response = client.get("/api/v1/assets/search?q=test")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    assert "results" in json_data["data"]
    assert json_data["data"]["query"] == "test"


def test_unified_search(client: TestClient) -> None:
    """Test unified workspace search endpoint."""
    response = client.get("/api/v1/search?q=test")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["success"] is True
    data = json_data["data"]
    assert "chapters" in data
    assert "assets" in data
    assert "runs" in data
    assert "panels" in data

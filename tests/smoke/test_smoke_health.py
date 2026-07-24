"""Smoke tests for health and status endpoints.

These tests verify that the server is running and all components report
healthy before a deployment proceeds.
"""

from __future__ import annotations

import pytest
import requests

from tests.smoke.conftest import get_server_url, smoke_log


@pytest.fixture(scope="session")
def server_url(pytestconfig) -> str:
    """Return the server base URL (without /api/v1)."""
    return get_server_url(pytestconfig)


def test_root_endpoint_returns_healthy(server_url: str) -> None:
    """GET / returns status information."""
    resp = requests.get(f"{server_url}/", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "healthy"
    assert "version" in data
    assert "environment" in data
    smoke_log("root_healthy", environment=data.get("environment"))


def test_health_endpoint_returns_up(server_url: str) -> None:
    """GET /health returns component status."""
    resp = requests.get(f"{server_url}/health", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] in ("healthy", "degraded"), f"Unexpected status: {data['status']}"
    assert "components" in data
    assert data["components"].get("api") == "healthy"
    smoke_log("health_check_success", db_status=data["components"].get("database"))


def test_api_v1_health_returns_ok(api_base: str) -> None:
    """GET /api/v1/health returns API version info."""
    resp = requests.get(f"{api_base}/health", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data.get("status") == "healthy"
    smoke_log("api_v1_healthy")

"""Tests for the FastAPI health endpoint."""

from fastapi.testclient import TestClient

from health_api import app


client = TestClient(app)


def test_health_endpoint_returns_200() -> None:
    """Verify that the health endpoint returns HTTP 200."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

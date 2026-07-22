"""Smoke tests for authentication and session management.

These tests verify the auth flow end-to-end against a live server.
They are designed to run as a pre-deploy gate — if any fail, deployment blocks.

Run:  pytest tests/smoke/ -v --server-url=http://localhost:8000
"""

from __future__ import annotations

import os
from typing import Any, Generator

import pytest
import requests

from tests.smoke.conftest import (
    get_api_base,
    SmokeTestError,
    random_email,
    strong_password,
    smoke_log,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_user_returns_201(api_base: str) -> None:
    """POST /auth/register with valid data returns 201 and a token."""
    payload = {"email": random_email(), "password": strong_password()}
    resp = requests.post(f"{api_base}/auth/register", json=payload, timeout=10)
    smoke_log("register_response", status=resp.status_code)
    assert resp.status_code == 201 or resp.status_code == 200, (
        f"Expected 200/201, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert "id" in data, "Missing 'id' in register response"
    assert "token" in data, "Missing 'token' in register response"
    assert "access_token" in data["token"], "Missing access_token in token object"
    assert data["token"]["token_type"] == "bearer"
    smoke_log("register_success", user_id=data["id"])


def test_register_duplicate_email_returns_400(api_base: str) -> None:
    """POST /auth/register with an existing email returns 400."""
    email = random_email()
    password = strong_password()
    payload = {"email": email, "password": password}
    requests.post(f"{api_base}/auth/register", json=payload, timeout=10)

    resp = requests.post(f"{api_base}/auth/register", json=payload, timeout=10)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert "already registered" in resp.text.lower()
    smoke_log("duplicate_email_rejected")


def test_register_weak_password_returns_422(api_base: str) -> None:
    """POST /auth/register with a weak password returns 422."""
    payload = {"email": random_email(), "password": "short"}
    resp = requests.post(f"{api_base}/auth/register", json=payload, timeout=10)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    smoke_log("weak_password_rejected")


def test_register_invalid_email_returns_422(api_base: str) -> None:
    """POST /auth/register with an invalid email returns 422."""
    payload = {"email": "not-an-email", "password": strong_password()}
    resp = requests.post(f"{api_base}/auth/register", json=payload, timeout=10)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    smoke_log("invalid_email_rejected")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_valid_credentials_returns_token(api_base: str) -> None:
    """POST /auth/login with valid credentials returns an access token."""
    email = random_email()
    password = strong_password()
    requests.post(f"{api_base}/auth/register", json={"email": email, "password": password}, timeout=10)

    resp = requests.post(
        f"{api_base}/auth/login",
        data={"email": email, "password": password, "grant_type": "password"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_at" in data
    smoke_log("login_success")


def test_login_wrong_password_returns_401(api_base: str) -> None:
    """POST /auth/login with wrong password returns 401."""
    email = random_email()
    password = strong_password()
    requests.post(f"{api_base}/auth/register", json={"email": email, "password": password}, timeout=10)

    resp = requests.post(
        f"{api_base}/auth/login",
        data={"email": email, "password": "WrongPass123!", "grant_type": "password"},
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
    smoke_log("wrong_password_rejected")


# ---------------------------------------------------------------------------
# Session Management
# ---------------------------------------------------------------------------


@pytest.fixture
def user_token(api_base: str) -> Generator[str, None, None]:
    """Register a user and return the access token."""
    email = random_email()
    password = strong_password()
    resp = requests.post(
        f"{api_base}/auth/register",
        json={"email": email, "password": password},
        timeout=10,
    )
    data = resp.json()
    yield data["token"]["access_token"]


def test_create_session_returns_session_token(user_token: str, api_base: str) -> None:
    """POST /auth/session creates a new chat session."""
    resp = requests.post(
        f"{api_base}/auth/session",
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "session_id" in data
    assert "token" in data
    assert "access_token" in data["token"]
    smoke_log("session_created", session_id=data["session_id"])


def test_list_sessions_returns_user_sessions(user_token: str, api_base: str) -> None:
    """GET /auth/sessions returns the user's sessions."""
    # Create two sessions
    for _ in range(2):
        requests.post(
            f"{api_base}/auth/session",
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=10,
        )

    resp = requests.get(
        f"{api_base}/auth/sessions",
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 2, f"Expected at least 2 sessions, got {len(data)}"
    smoke_log("sessions_listed", count=len(data))


# ---------------------------------------------------------------------------
# Guardrails (token validation)
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(api_base: str) -> None:
    """Endpoints requiring auth return 401 when no token is provided."""
    resp = requests.get(f"{api_base}/auth/sessions", timeout=10)
    assert resp.status_code == 401 or resp.status_code == 403, (
        f"Expected 401/403, got {resp.status_code}: {resp.text}"
    )
    smoke_log("missing_token_rejected")


def test_invalid_token_returns_401(api_base: str) -> None:
    """Endpoints with a malformed token return 401."""
    resp = requests.get(
        f"{api_base}/auth/sessions",
        headers={"Authorization": "Bearer invalid-token-here"},
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
    smoke_log("invalid_token_rejected")

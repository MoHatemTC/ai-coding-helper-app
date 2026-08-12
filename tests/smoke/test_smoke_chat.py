"""Smoke tests for chat and message endpoints.

These tests verify the core chat flow against a live server:
- Sending a message (multipart/form-data with a required ``message`` field)
- Selecting an agent mode (``reasoning`` default, ``fast``)
- Streaming responses (SSE ``data:`` JSON events)
- Retrieving message history
- Clearing chat history

The chat endpoints accept multipart/form-data (``message``, optional ``mode``,
optional ``files``), NOT the legacy JSON ``{"messages": [...]}`` contract.
"""

from __future__ import annotations

import json
from typing import Generator

import pytest
import requests

from tests.smoke.conftest import random_email, smoke_log, strong_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def session_token(api_base: str) -> Generator[str, None, None]:
    """Register a user, create a session, and return the session token."""
    email = random_email()
    password = strong_password()

    # Register
    reg = requests.post(
        f"{api_base}/auth/register",
        json={"email": email, "password": password},
        timeout=10,
    )
    user_token = reg.json()["token"]["access_token"]

    # Create session
    sess = requests.post(
        f"{api_base}/auth/session",
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=10,
    )
    yield sess.json()["token"]["access_token"]


def _chat_headers(session_token: str) -> dict[str, str]:
    """Return the headers used for authenticated chat requests."""
    return {"Authorization": f"Bearer {session_token}"}


def _post_chat(api_base: str, data: dict, session_token: str, timeout: int = 30) -> requests.Response:
    """POST to /chatbot/chat with multipart form data."""
    return requests.post(
        f"{api_base}/chatbot/chat",
        headers=_chat_headers(session_token),
        data=data,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Chat non-streaming
# ---------------------------------------------------------------------------


def test_chat_basic_message(session_token: str, api_base: str) -> None:
    """POST /chat sends a message and receives a response."""
    resp = _post_chat(api_base, {"message": "Say hello in one word."}, session_token)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data, "Missing 'messages' in chat response"
    assert len(data["messages"]) >= 1, f"Expected at least 1 assistant message, got {len(data['messages'])}"
    # The response is the assistant message for this request.
    last_msg = data["messages"][-1]
    assert last_msg["role"] == "assistant"
    assert last_msg["content"], "Assistant response content is empty"
    smoke_log("chat_basic_success", response_length=len(last_msg["content"]))


def test_chat_with_code_in_message(session_token: str, api_base: str) -> None:
    """POST /chat with code in the message text returns a response."""
    resp = _post_chat(
        api_base,
        {"message": "Review this code briefly: def divide(a, b): return a / b"},
        session_token,
        timeout=60,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) >= 1
    assert data["messages"][-1]["role"] == "assistant"
    smoke_log("chat_code_in_message_success")


def test_chat_missing_message_returns_422(session_token: str, api_base: str) -> None:
    """POST /chat without the required message field returns 422."""
    resp = _post_chat(api_base, {}, session_token, timeout=10)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    smoke_log("chat_missing_message_rejected")


def test_chat_invalid_mode_returns_400(session_token: str, api_base: str) -> None:
    """POST /chat with an unknown agent mode returns 400."""
    resp = _post_chat(api_base, {"message": "Hello", "mode": "bogus"}, session_token, timeout=10)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    assert "invalid_mode" in resp.text, f"Expected invalid_mode detail, got: {resp.text}"
    smoke_log("chat_invalid_mode_rejected")


def test_chat_fast_mode_returns_response(session_token: str, api_base: str) -> None:
    """POST /chat with mode=fast routes to the workflow agent."""
    resp = _post_chat(api_base, {"message": "Count to three.", "mode": "fast"}, session_token, timeout=120)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) >= 1
    assert data["messages"][-1]["role"] == "assistant"
    smoke_log("chat_fast_mode_success")


def test_chat_without_auth_returns_403(api_base: str) -> None:
    """POST /chat without a token returns 403 (HTTPBearer)."""
    resp = requests.post(
        f"{api_base}/chatbot/chat",
        data={"message": "Hello"},
        timeout=10,
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    smoke_log("chat_unauthorized_rejected")


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------


def test_chat_stream_returns_events(session_token: str, api_base: str) -> None:
    """POST /chat/stream returns SSE events ending with done=true."""
    resp = requests.post(
        f"{api_base}/chatbot/chat/stream",
        headers=_chat_headers(session_token),
        data={"message": "Count to three."},
        timeout=60,
        stream=True,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    events = []
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            events.append(json.loads(line[6:]))
        if events and events[-1].get("done"):
            break

    resp.close()

    assert len(events) > 0, "No SSE events received from stream endpoint"
    assert events[-1]["done"] is True, f"Expected final done=true event, got: {events[-1]}"
    smoke_log("chat_stream_events", event_count=len(events))


# ---------------------------------------------------------------------------
# Message retrieval & persistence
# ---------------------------------------------------------------------------


def test_get_messages_returns_history(session_token: str, api_base: str) -> None:
    """GET /messages returns the conversation history."""
    # Send a message first so there's history
    _post_chat(api_base, {"message": "What is 2+2?"}, session_token, timeout=30)

    resp = requests.get(
        f"{api_base}/chatbot/messages",
        headers=_chat_headers(session_token),
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) >= 2, f"Expected at least 2 messages in history, got {len(data['messages'])}"
    assert "has_more" in data, "Missing 'has_more' in messages response"
    smoke_log("messages_retrieved", message_count=len(data["messages"]))


def test_clear_messages_removes_history(session_token: str, api_base: str) -> None:
    """DELETE /messages clears the conversation history."""
    # Verify history exists
    before = requests.get(
        f"{api_base}/chatbot/messages",
        headers=_chat_headers(session_token),
        timeout=10,
    )
    smoke_log("messages_before_clear", count=len(before.json().get("messages", [])))

    resp = requests.delete(
        f"{api_base}/chatbot/messages",
        headers=_chat_headers(session_token),
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "message" in data
    assert "cleared" in data["message"].lower()
    smoke_log("messages_cleared")

    after = requests.get(
        f"{api_base}/chatbot/messages",
        headers=_chat_headers(session_token),
        timeout=10,
    )
    assert after.status_code == 200, f"Expected 200, got {after.status_code}: {after.text}"
    assert len(after.json().get("messages", [])) == 0, "History not empty after clearing"
    smoke_log("messages_empty_after_clear")

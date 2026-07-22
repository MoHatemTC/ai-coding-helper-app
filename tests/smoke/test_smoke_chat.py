"""Smoke tests for chat and message endpoints.

These tests verify the core chat flow against a live server:
- Sending a message
- Retrieving message history
- Streaming responses
- Clearing chat history
"""

from __future__ import annotations

from typing import Any, Generator

import pytest
import requests

from tests.smoke.conftest import get_api_base, random_email, smoke_log, strong_password


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


# ---------------------------------------------------------------------------
# Chat (non-streaming)
# ---------------------------------------------------------------------------


def test_chat_basic_message(session_token: str, api_base: str) -> None:
    """POST /chat sends a message and receives a response."""
    payload = {
        "messages": [{"role": "user", "content": "Say hello in one word."}],
    }
    resp = requests.post(
        f"{api_base}/chatbot/chat",
        headers={
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data, "Missing 'messages' in chat response"
    assert len(data["messages"]) >= 2, (
        f"Expected at least 2 messages (user + assistant), got {len(data['messages'])}"
    )
    # Last message should be from the assistant
    last_msg = data["messages"][-1]
    assert last_msg["role"] == "assistant"
    assert last_msg["content"], "Assistant response content is empty"
    smoke_log("chat_basic_success", response_length=len(last_msg["content"]))


def test_chat_with_code_review(session_token: str, api_base: str) -> None:
    """POST /chat with code context returns a review response."""
    payload = {
        "messages": [{"role": "user", "content": "Review this code briefly."}],
        "code": "def divide(a, b):\n    return a / b",
        "language": "python",
    }
    resp = requests.post(
        f"{api_base}/chatbot/chat",
        headers={
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) >= 2
    smoke_log("chat_code_review_success")


def test_chat_with_empty_messages_returns_422(session_token: str, api_base: str) -> None:
    """POST /chat with an empty messages list returns 422."""
    payload = {"messages": []}
    resp = requests.post(
        f"{api_base}/chatbot/chat",
        headers={
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    smoke_log("chat_empty_messages_rejected")


def test_chat_without_auth_returns_401(api_base: str) -> None:
    """POST /chat without a token returns 401."""
    payload = {"messages": [{"role": "user", "content": "Hello"}]}
    resp = requests.post(
        f"{api_base}/chatbot/chat",
        json=payload,
        timeout=10,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
    smoke_log("chat_unauthorized_rejected")


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------


def test_chat_stream_returns_events(session_token: str, api_base: str) -> None:
    """POST /chat/stream returns SSE events."""
    payload = {
        "messages": [{"role": "user", "content": "Count to three."}],
    }
    resp = requests.post(
        f"{api_base}/chatbot/chat/stream",
        headers={
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
        stream=True,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    # Read SSE events
    events = []
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            if line.startswith("data: "):
                events.append(line[6:])
        # Read at most 50 lines to avoid infinite loops
        if len(events) > 50:
            break
    resp.close()

    assert len(events) > 0, "No SSE events received from stream endpoint"
    smoke_log("chat_stream_events", event_count=len(events))


# ---------------------------------------------------------------------------
# Message retrieval & persistence
# ---------------------------------------------------------------------------


def test_get_messages_returns_history(session_token: str, api_base: str) -> None:
    """GET /messages returns the conversation history."""
    # Send a message first so there's history
    requests.post(
        f"{api_base}/chatbot/chat",
        headers={
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
        },
        json={"messages": [{"role": "user", "content": "What is 2+2?"}]},
        timeout=30,
    )

    resp = requests.get(
        f"{api_base}/chatbot/messages",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) >= 2, (
        f"Expected at least 2 messages in history, got {len(data['messages'])}"
    )
    smoke_log("messages_retrieved", message_count=len(data["messages"]))


def test_clear_messages_removes_history(session_token: str, api_base: str) -> None:
    """DELETE /messages clears the conversation history."""
    # Verify history exists
    before = requests.get(
        f"{api_base}/chatbot/messages",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=10,
    )
    smoke_log("messages_before_clear", count=len(before.json().get("messages", [])))

    resp = requests.delete(
        f"{api_base}/chatbot/messages",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=10,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "message" in data
    assert "cleared" in data["message"].lower()
    smoke_log("messages_cleared")
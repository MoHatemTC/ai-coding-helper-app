"""Tests for chat request code intake (F0.2)."""

import pytest
from pydantic import ValidationError

from app.schemas.chat import ChatRequest, Message


def test_chat_request_accepts_code_and_language():
    """ChatRequest should accept optional code and language fields."""
    request = ChatRequest(
        messages=[Message(role="user", content="Please review this")],
        code="def add(a, b):\n    return a + b",
        language="python",
    )
    assert request.code == "def add(a, b):\n    return a + b"
    assert request.language == "python"


def test_chat_request_code_and_language_default_to_none():
    """code/language should be optional and default to None."""
    request = ChatRequest(messages=[Message(role="user", content="hello")])
    assert request.code is None
    assert request.language is None


def test_chat_request_rejects_null_byte_in_code():
    """Code containing a null byte should be rejected."""
    with pytest.raises(ValidationError):
        ChatRequest(
            messages=[Message(role="user", content="review")],
            code="def add(a, b):\0\n    return a + b",
        )

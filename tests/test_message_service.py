"""Tests for MessageService — verifies store, get (pagination), and delete operations."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlmodel import Session, col, select

from app.models.message import Message
from app.services.message import MessageService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(db_engine) -> MessageService:
    """Create a MessageService wired to the test database."""
    svc = MessageService.__new__(MessageService)
    svc._engine = db_engine
    return svc


def _seed_messages(db_session: Session, session_id: str, user_id: int, count: int) -> list[Message]:
    """Insert N messages into the database."""
    messages = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msg = Message(
            user_id=user_id,
            session_id=session_id,
            role=role,
            message=f"Message {i}",
        )
        db_session.add(msg)
        messages.append(msg)
    db_session.commit()
    for msg in messages:
        db_session.refresh(msg)
    return messages


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestStoreMessages:
    """Verify batch message storage."""

    @pytest.mark.asyncio
    async def test_stores_messages(self, db_engine, db_session, test_user, test_session):
        """Should insert messages into the database."""
        svc = _make_service(db_engine)
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        await svc.store_messages(
            user_id=test_user.id,
            session_id=test_session.id,
            messages=messages,
        )

        stored = list(db_session.exec(select(Message).where(col(Message.session_id) == test_session.id)).all())
        assert len(stored) == 2
        assert stored[0].role == "user"
        assert stored[0].message == "Hello"
        assert stored[1].role == "assistant"
        assert stored[1].message == "Hi there!"

    @pytest.mark.asyncio
    async def test_stores_empty_list_noop(self, db_engine, test_user, test_session):
        """Empty message list should not cause errors."""
        svc = _make_service(db_engine)
        await svc.store_messages(user_id=test_user.id, session_id=test_session.id, messages=[])
        # No exception means success

    @pytest.mark.asyncio
    async def test_skips_messages_without_content(self, db_engine, db_session, test_user, test_session):
        """Messages with empty content should be skipped."""
        svc = _make_service(db_engine)
        messages = [
            {"role": "user", "content": "Valid message"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": ""},
        ]

        await svc.store_messages(
            user_id=test_user.id,
            session_id=test_session.id,
            messages=messages,
        )

        stored = list(db_session.exec(select(Message).where(col(Message.session_id) == test_session.id)).all())
        assert len(stored) == 1
        assert stored[0].message == "Valid message"


# ---------------------------------------------------------------------------
# Get (pagination) tests
# ---------------------------------------------------------------------------


class TestGetMessages:
    """Verify cursor-based pagination."""

    @pytest.mark.asyncio
    async def test_get_messages_returns_chronological_order(self, db_engine, db_session, test_user, test_session):
        """Messages should be returned in the order the service returns them (DESC by created_at)."""
        svc = _make_service(db_engine)
        _seed_messages(db_session, test_session.id, test_user.id, count=5)

        messages, has_more = await svc.get_messages(session_id=test_session.id, limit=10)

        assert len(messages) == 5
        assert has_more is False
        contents = [m.message for m in messages]
        assert contents == ["Message 4", "Message 3", "Message 2", "Message 1", "Message 0"]

    @pytest.mark.asyncio
    async def test_get_messages_respects_limit(self, db_engine, db_session, test_user, test_session):
        """Should return at most `limit` messages."""
        svc = _make_service(db_engine)
        _seed_messages(db_session, test_session.id, test_user.id, count=10)

        messages, has_more = await svc.get_messages(session_id=test_session.id, limit=3)

        assert len(messages) == 3
        assert has_more is True

    @pytest.mark.asyncio
    async def test_get_messages_after_cursor(self, db_engine, db_session, test_user, test_session):
        """Should return messages older than the cursor (created_at < cursor.created_at)."""
        svc = _make_service(db_engine)
        seeded = _seed_messages(db_session, test_session.id, test_user.id, count=5)

        # Use the 3rd message (index 2, which is "Message 2") as cursor
        cursor_id = str(seeded[2].id)
        messages, has_more = await svc.get_messages(session_id=test_session.id, limit=10, after=cursor_id)

        # Should return messages older than "Message 2" (i.e. Message 1 and Message 0)
        assert len(messages) == 2
        assert messages[0].message == "Message 1"
        assert messages[1].message == "Message 0"

    @pytest.mark.asyncio
    async def test_get_messages_empty_session(self, db_engine, test_user, test_session):
        """Should return empty list for session with no messages."""
        svc = _make_service(db_engine)
        messages, has_more = await svc.get_messages(session_id=test_session.id)

        assert messages == []
        assert has_more is False

    @pytest.mark.asyncio
    async def test_get_messages_capped_at_100(self, db_engine, db_session, test_user, test_session):
        """Limit should be capped at 100."""
        svc = _make_service(db_engine)
        _seed_messages(db_session, test_session.id, test_user.id, count=105)

        messages, has_more = await svc.get_messages(session_id=test_session.id, limit=200)

        assert len(messages) == 100


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------


class TestDeleteMessages:
    """Verify message deletion."""

    @pytest.mark.asyncio
    async def test_deletes_all_messages_for_session(self, db_engine, db_session, test_user, test_session):
        """Should delete all messages belonging to the session."""
        svc = _make_service(db_engine)
        _seed_messages(db_session, test_session.id, test_user.id, count=5)

        await svc.delete_messages(session_id=test_session.id)

        remaining = list(db_session.exec(select(Message).where(col(Message.session_id) == test_session.id)).all())
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_delete_only_affects_target_session(self, db_engine, db_session, test_user, test_session):
        """Should not delete messages from other sessions."""
        svc = _make_service(db_engine)

        # Seed messages for two sessions
        _seed_messages(db_session, test_session.id, test_user.id, count=3)
        other_session_id = str(uuid4())
        _seed_messages(db_session, other_session_id, test_user.id, count=2)

        await svc.delete_messages(session_id=test_session.id)

        # Target session should be empty
        target = list(db_session.exec(select(Message).where(col(Message.session_id) == test_session.id)).all())
        assert len(target) == 0

        # Other session should be untouched
        other = list(db_session.exec(select(Message).where(col(Message.session_id) == other_session_id)).all())
        assert len(other) == 2

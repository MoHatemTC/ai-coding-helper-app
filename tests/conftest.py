"""Shared test fixtures for memory service tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from app.models.session import Session as ChatSession

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models.user import User
from app.services.memory import MemoryService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_A = "user-alice-001"
USER_B = "user-bob-002"

SAMPLE_MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": "Can you review this Python function?"},
    {
        "role": "assistant",
        "content": "Sure! I found a potential SQL injection vulnerability.",
    },
]


# ---------------------------------------------------------------------------
# Mock mem0 (AsyncMemory)
# ---------------------------------------------------------------------------


class FakeMem0:
    """In-memory substitute for ``mem0.AsyncMemory``.

    Stores entries keyed by ``user_id`` so tests can verify isolation
    without a real pgvector instance.
    """

    def __init__(self) -> None:
        """Initialize the fake mem0."""
        self._store: dict[str, list[dict[str, Any]]] = {}
        self._next_id = 0

    # -- mem0 API surface used by MemoryService --------------------------------

    async def add(
        self,
        content: Any,
        user_id: str,
        metadata: dict | None = None,
        run_id: str | None = None,
        infer: bool = True,
    ) -> dict:
        """Add a new memory entry."""
        self._next_id += 1
        entry = {
            "id": f"mem-{self._next_id}",
            "memory": content if isinstance(content, str) else str(content),
            "user_id": user_id,
            "metadata": metadata or {},
            "run_id": run_id,
        }
        self._store.setdefault(user_id, []).append(entry)
        return {"results": [entry]}

    async def search(self, user_id: str, query: str) -> dict:
        """Search for relevant memories."""
        entries = self._store.get(user_id, [])
        return {"results": [e for e in entries if e.get("run_id") is None]}

    async def get_all(
        self,
        user_id: str,
        run_id: str | None = None,
        filters: dict | None = None,
    ) -> dict:
        """Get all memories for a user."""
        entries = self._store.get(user_id, [])
        if run_id is not None:
            entries = [e for e in entries if e.get("run_id") == run_id]
        if filters:
            filtered = []
            for e in entries:
                meta = e.get("metadata", {})
                if all(meta.get(k) == v for k, v in filters.items()):
                    filtered.append(e)
            entries = filtered
        return {"results": entries}

    async def delete(self, memory_id: str) -> None:
        """Delete a memory entry."""
        for uid, entries in self._store.items():
            self._store[uid] = [e for e in entries if e["id"] != memory_id]


@pytest.fixture
def fake_mem0() -> FakeMem0:
    """Provide a fresh ``FakeMem0`` instance per test."""
    return FakeMem0()


@pytest.fixture
def mock_cache() -> AsyncMock:
    """No-op cache service that always returns misses."""
    cache = AsyncMock()
    cache.get.return_value = None
    cache.set.return_value = None
    cache.delete.return_value = None
    return cache


@pytest.fixture
def memory_service(
    fake_mem0: FakeMem0,
    mock_cache: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> MemoryService:
    """Return a ``MemoryService`` wired to fakes — no external services.

    Patches the module-level ``cache_service`` reference so that
    ``search()`` uses the mock cache instead of the real one.
    """
    monkeypatch.setattr("app.services.memory.cache_service", mock_cache)
    svc = MemoryService()
    svc._memory = fake_mem0  # type: ignore[attr-defined]
    return svc


@pytest.fixture
def sample_messages() -> list[dict[str, Any]]:
    """Return a list of sample messages."""
    return list(SAMPLE_MESSAGES)


# ---------------------------------------------------------------------------
# SQLite in-memory database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def db_engine():
    """Create a fresh SQLite in-memory engine per test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Provide a transactional DB session that rolls back after each test."""
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def test_user(db_session: Session) -> User:
    """Create a test user in the database."""
    user = User(
        id=1,
        email="test",
        hashed_password="",
        username="testuser",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_session(db_session: Session, test_user: User) -> ChatSession:
    """Create a test session in the database."""
    session_id = str(uuid4())
    chat_session = ChatSession(
        id=session_id,
        user_id=test_user.id,
        name="Test Session",
        username=test_user.username,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)
    return chat_session

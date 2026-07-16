"""Unit tests for MemoryService — user-scoped CRUD operations."""

from __future__ import annotations
from typing import Any
from unittest.mock import AsyncMock
import pytest
from app.services.memory import MemoryService
from .conftest import USER_A, SAMPLE_MESSAGES


@pytest.mark.asyncio
async def test_add_stores_with_user_id(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """add() must pass user_id through to mem0."""
    await memory_service.add(USER_A, SAMPLE_MESSAGES)

    entries = fake_mem0._store.get(USER_A, [])
    assert len(entries) == 1
    assert entries[0]["user_id"] == USER_A


@pytest.mark.asyncio
async def test_search_filters_by_user_id(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """search() must query mem0 with the correct user_id."""
    await memory_service.add(USER_A, SAMPLE_MESSAGES)

    result = await memory_service.search(USER_A, "SQL injection")
    assert "SQL injection" in result


@pytest.mark.asyncio
async def test_add_none_user_is_noop(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """add(None, ...) must never touch mem0."""
    await memory_service.add(None, SAMPLE_MESSAGES)

    assert len(fake_mem0._store) == 0


@pytest.mark.asyncio
async def test_search_none_user_returns_empty(
    memory_service: MemoryService,
) -> None:
    """search(None, ...) must return empty string without querying mem0."""
    result = await memory_service.search(None, "anything")
    assert result == ""


@pytest.mark.asyncio
async def test_search_caches_result(
    memory_service: MemoryService,
    mock_cache: AsyncMock,
) -> None:
    """search() must cache successful results and return from cache on hit."""
    await memory_service.add(USER_A, SAMPLE_MESSAGES)

    # First call — cache miss, hits mem0
    await memory_service.search(USER_A, "SQL injection")
    mock_cache.get.assert_called_once()
    mock_cache.set.assert_called_once()

    # Simulate cache hit on second call
    mock_cache.get.return_value = "cached memory text"
    result2 = await memory_service.search(USER_A, "SQL injection")
    assert result2 == "cached memory text"


@pytest.mark.asyncio
async def test_add_exception_does_not_crash(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """add() must log but not propagate mem0 exceptions."""
    fake_mem0.add = AsyncMock(side_effect=RuntimeError("mem0 unavailable"))

    # Should not raise
    await memory_service.add(USER_A, SAMPLE_MESSAGES)

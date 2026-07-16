"""User isolation tests — prove User A cannot see User B's memories."""

from __future__ import annotations
from typing import Any
import pytest
from app.services.memory import MemoryService
import re
import inspect
import textwrap
from tests.conftest import USER_A, USER_B

USER_A_MESSAGES = [
    {"role": "user", "content": "Help me fix this SQL query"},
    {"role": "assistant", "content": "I found a SQL injection vulnerability in your query."},
]

USER_B_MESSAGES = [
    {"role": "user", "content": "Review this Python function"},
    {"role": "assistant", "content": "Your function has a performance issue with N+1 queries."},
]


@pytest.mark.asyncio
async def test_user_a_memory_not_visible_to_user_b(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """Memories stored for User A must not appear when searching as User B."""
    await memory_service.add(USER_A, USER_A_MESSAGES)
    await memory_service.add(USER_B, USER_B_MESSAGES)

    # Search as User B — should NOT find User A's SQL injection memory
    result_b = await memory_service.search(USER_B, "SQL injection")
    assert "SQL injection" not in result_b

    # Search as User A — should find it
    result_a = await memory_service.search(USER_A, "SQL injection")
    assert "SQL injection" in result_a


@pytest.mark.asyncio
async def test_user_b_memory_not_visible_to_user_a(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """Memories stored for User B must not appear when searching as User A."""
    await memory_service.add(USER_A, USER_A_MESSAGES)
    await memory_service.add(USER_B, USER_B_MESSAGES)

    # Search as User A — should NOT find User B's N+1 query memory
    result_a = await memory_service.search(USER_A, "N+1 queries")
    assert "N+1" not in result_a

    # Search as User B — should find it
    result_b = await memory_service.search(USER_B, "N+1 queries")
    assert "N+1" in result_b


@pytest.mark.asyncio
async def test_search_with_wrong_user_id_returns_empty(
    memory_service: MemoryService,
    fake_mem0: Any,
) -> None:
    """Searching with a user_id that has no stored memories returns empty."""
    await memory_service.add(USER_A, USER_A_MESSAGES)

    result = await memory_service.search("user-charlie-999", "SQL injection")
    assert result == ""


@pytest.mark.asyncio
async def test_no_unscoped_search_exists() -> None:
    """Static assertion: every mem0.search() and mem0.get_all() call includes user_id.

    This is a code-level check — it reads the source and verifies the pattern.
    It does NOT execute any mem0 calls.
    """
    source = textwrap.dedent(inspect.getsource(MemoryService))

    # Join continuation lines so multi-line calls become one string
    joined = " ".join(source.splitlines())

    # Check every .search( call has user_id
    for match in re.finditer(r"\.search\([^)]+\)", joined):
        call_text = match.group()
        assert "user_id" in call_text, f".search() call missing user_id filter: {call_text}"

    # Check every .get_all( call has user_id
    for match in re.finditer(r"\.get_all\([^)]+\)", joined):
        call_text = match.group()
        assert "user_id" in call_text, f".get_all() call missing user_id filter: {call_text}"

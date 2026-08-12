"""Unit tests for cache services and state snapshot caching helpers."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import pytest
from langgraph.types import StateSnapshot

from app.core.cache import InMemoryCacheService, StateSnapshotCache


# ---------------------------------------------------------------------------
# InMemoryCacheService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_missing_key_returns_none() -> None:
    cache = InMemoryCacheService(default_ttl=60)
    assert await cache.get("missing") is None


@pytest.mark.asyncio
async def test_cache_set_get_roundtrip() -> None:
    cache = InMemoryCacheService(default_ttl=60)
    await cache.set("key", "value")
    assert await cache.get("key") == "value"


@pytest.mark.asyncio
async def test_cache_expired_entry_returns_none() -> None:
    cache = InMemoryCacheService(default_ttl=60)
    await cache.set("key", "value")
    cache._cache["key"] = (time.monotonic() - 1, "value")
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_cache_custom_ttl_respected() -> None:
    cache = InMemoryCacheService(default_ttl=60)
    await cache.set("key", "value", ttl=0.2)
    assert await cache.get("key") == "value"
    await asyncio.sleep(0.3)
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_cache_max_entries_evicts_oldest_fifo() -> None:
    cache = InMemoryCacheService(default_ttl=60, max_entries=2)
    await cache.set("a", "1")
    await cache.set("b", "2")
    await cache.set("c", "3")
    assert await cache.get("a") is None
    assert await cache.get("b") == "2"
    assert await cache.get("c") == "3"


@pytest.mark.asyncio
async def test_cache_delete_removes_key() -> None:
    cache = InMemoryCacheService(default_ttl=60)
    await cache.set("key", "value")
    await cache.delete("key")
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_cache_close_clears_all() -> None:
    cache = InMemoryCacheService(default_ttl=60)
    await cache.set("key", "value")
    await cache.close()
    assert await cache.get("key") is None


def test_cache_thread_safety_smoke() -> None:
    cache = InMemoryCacheService(default_ttl=60, max_entries=100)

    def run_one(idx: int) -> None:
        async def inner() -> None:
            await cache.set(f"key-{idx}", f"value-{idx}")
            await cache.get(f"key-{idx}")

        asyncio.run(inner())

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run_one, range(64)))

    async def verify() -> None:
        assert await cache.get("key-0") == "value-0"

    asyncio.run(verify())


# ---------------------------------------------------------------------------
# StateSnapshotCache
# ---------------------------------------------------------------------------


class _FakeSnapshot:
    """Minimal stand-in for a langgraph StateSnapshot."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.next: list = []


def _snapshot(marker: str) -> StateSnapshot:
    return cast(StateSnapshot, _FakeSnapshot(marker))


def test_snapshot_missing_key_returns_none() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    assert cache.get("missing") is None


def test_snapshot_set_get_roundtrip() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    cache.set("s1", _snapshot("a"))
    assert cache.get("s1") is not None
    assert cache.get("s1").marker == "a"  # pyright: ignore[reportAttributeAccessIssue]


def test_snapshot_expired_entry_returns_none() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    cache.set("s1", _snapshot("a"))
    cache._cache["s1"] = (time.monotonic() - 1, cache._cache["s1"][1])
    assert cache.get("s1") is None


def test_snapshot_custom_ttl_respected() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    cache.set("s1", _snapshot("a"), ttl=0.2)
    assert cache.get("s1") is not None
    time.sleep(0.3)
    assert cache.get("s1") is None


def test_snapshot_max_entries_evicts_oldest_fifo() -> None:
    cache = StateSnapshotCache(default_ttl=60, max_entries=2)
    cache.set("a", _snapshot("1"))
    cache.set("b", _snapshot("2"))
    cache.set("c", _snapshot("3"))
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_snapshot_delete_removes_key() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    cache.set("s1", _snapshot("a"))
    cache.delete("s1")
    assert cache.get("s1") is None


def test_snapshot_clear_removes_all() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    cache.set("a", _snapshot("1"))
    cache.set("b", _snapshot("2"))
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_snapshot_isolation_between_sessions() -> None:
    cache = StateSnapshotCache(default_ttl=60)
    cache.set("s1", _snapshot("a"))
    cache.set("s2", _snapshot("b"))
    assert cache.get("s1").marker == "a"  # pyright: ignore[reportAttributeAccessIssue]
    assert cache.get("s2").marker == "b"  # pyright: ignore[reportAttributeAccessIssue]


def test_snapshot_thread_safety_smoke() -> None:
    cache = StateSnapshotCache(default_ttl=60, max_entries=100)

    def run_one(idx: int) -> None:
        cache.set(f"key-{idx}", _snapshot(f"value-{idx}"))
        cache.get(f"key-{idx}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run_one, range(64)))

    assert cache.get("key-0") is not None
    assert cache.get("key-0").marker == "value-0"  # pyright: ignore[reportAttributeAccessIssue]

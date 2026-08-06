"""Smoke tests for the file-upload + chunking pipeline.

These tests verify the mentor-mandated end-to-end guarantee: every chunk stored
by the document pipeline carries ``file_id``, ``session_id``, and a ``created_at``
timestamp. They run against a live server (``--server-url``) and assert directly
on the Postgres ``code_chunk`` table, using the same credentials the server uses.

The chunking path is gated by an LLM intent judge (``inbound_intent_node``), so
each test retries the upload against a fresh session when the judge does not
route to the document pipeline; the assertion then proves the pipeline's
metadata contract whenever it runs.

Tests:
- test_multi_chunk_metadata: uploading one Python file produces >=2 chunks,
  each with non-null file_id/session_id/user_id/created_at/content.
- test_same_name_two_files: two files with the SAME basename (graph.py) coexist
  in one session under distinct file_ids (no demotion/collision).
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import requests

from tests.smoke.conftest import random_email, smoke_log, strong_password

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _session_id_from_token(token: str) -> str:
    """Decode the JWT payload (segment 2) and return the ``sub`` claim.

    The app stores the session id in ``sub`` (see ``app/utils/auth.py``).
    """
    segment = token.split(".")[1]
    padding = "=" * (-len(segment) % 4)
    payload = json.loads(base64.urlsafe_b64decode(segment + padding))
    return payload["sub"]


def _new_session(api_base: str) -> tuple[str, str]:
    """Register a user, create a session; return (token, session_id)."""
    reg = requests.post(
        f"{api_base}/auth/register",
        json={"email": random_email(), "password": strong_password()},
        timeout=15,
    )
    assert reg.status_code == 200, f"register failed: {reg.status_code} {reg.text}"
    sess = requests.post(
        f"{api_base}/auth/session",
        headers={"Authorization": f"Bearer {reg.json()['token']['access_token']}"},
        timeout=15,
    )
    assert sess.status_code == 200, f"session create failed: {sess.status_code} {sess.text}"
    token = sess.json()["token"]["access_token"]
    return token, _session_id_from_token(token)


def _load_db_env() -> None:
    """Load ``.env.development`` from the repo root so DB creds match the server."""
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env.development", override=True)


def _db_params() -> dict[str, Any]:
    """Return Postgres connection params used by the running server."""
    _load_db_env()
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "food_order_db"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    }


def _fetch_chunks(session_id: str) -> list[dict[str, Any]]:
    """Query all ``code_chunk`` rows for a session, or [] on connection error."""
    try:
        with psycopg.connect(**_db_params(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, session_id, file_id, file_name,
                           language, content, created_at
                    FROM code_chunk
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 - smoke test must not fail on env noise
        smoke_log("db_connection_failed", error=str(exc))
        return []


def _wait_for_chunks(session_id: str, min_chunks: int, timeout_s: int = 90) -> list[dict[str, Any]]:
    """Poll the DB until at least ``min_chunks`` exist for the session."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunks = _fetch_chunks(session_id)
        if len(chunks) >= min_chunks:
            return chunks
        time.sleep(2)
    return chunks


def _upload_and_wait(
    api_base: str,
    files: list[tuple[str, tuple[str, bytes, str]]],
    message: str,
    min_chunks: int,
    attempts: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    """Upload files to a fresh session and return (session_id, chunks).

    The document pipeline is gated by an LLM intent judge, so retry with a new
    session when the judge does not route the upload to the pipeline.
    """
    chunks: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        token, session_id = _new_session(api_base)
        resp = requests.post(
            f"{api_base}/chatbot/chat",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
            data={"message": message},
            timeout=240,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        chunks = _wait_for_chunks(session_id, min_chunks=min_chunks)
        if len(chunks) >= min_chunks:
            return session_id, chunks
        smoke_log("upload_attempt_no_chunks", attempt=attempt, chunk_count=len(chunks))
    return session_id, chunks


# A Python file large enough (multiple >50-line regions) that the AST splitter
# produces at least 2 chunks (chunk_lines=50, max_chars=1500).
_CALCULATOR_PY = b"""
'''A calculator module for the chunking smoke test.'''

MATH_CONSTANTS = {"pi": 3.14159, "e": 2.71828, "phi": 1.61803}


def add(a: int, b: int) -> int:
    '''Return the sum of two numbers.'''
    return a + b


def subtract(a: int, b: int) -> int:
    '''Return the difference of two numbers.'''
    return a - b


def multiply(a: int, b: int) -> int:
    '''Return the product of two numbers.'''
    return a * b


def divide(a: int, b: int) -> float:
    '''Return the quotient of two numbers.'''
    if b == 0:
        raise ValueError('division by zero is not allowed')
    return a / b


def power(a: int, b: int) -> int:
    '''Return a raised to the power b.'''
    return a ** b


def remainder(a: int, b: int) -> int:
    '''Return the remainder of a divided by b.'''
    return a % b


def floor_divide(a: int, b: int) -> int:
    '''Return the integer quotient of two numbers.'''
    if b == 0:
        raise ValueError('division by zero is not allowed')
    return a // b


def absolute(value: int) -> int:
    '''Return the absolute value of a number.'''
    return -value if value < 0 else value


def is_even(value: int) -> bool:
    '''Return True when the value is even.'''
    return value % 2 == 0


def is_odd(value: int) -> bool:
    '''Return True when the value is odd.'''
    return value % 2 != 0


def square(value: int) -> int:
    '''Return the square of a number.'''
    return value * value


def cube(value: int) -> int:
    '''Return the cube of a number.'''
    return value ** 3


def sign(value: int) -> int:
    '''Return -1, 0, or 1 depending on the value sign.'''
    if value < 0:
        return -1
    if value > 0:
        return 1
    return 0


def clamp(value: int, low: int, high: int) -> int:
    '''Constrain a value to the closed interval [low, high].'''
    return max(low, min(value, high))


def gcd(a: int, b: int) -> int:
    '''Return the greatest common divisor of two numbers.'''
    while b:
        a, b = b, a % b
    return a


def lcm(a: int, b: int) -> int:
    '''Return the least common multiple of two numbers.'''
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // gcd(a, b)


def mean(values: list[int]) -> float:
    '''Return the arithmetic mean of a list of numbers.'''
    return sum(values) / len(values)


def median(values: list[int]) -> float:
    '''Return the median of a sorted list of numbers.'''
    n = len(values)
    mid = n // 2
    if n % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


class Calculator:
    '''A class holding calculator helpers.'''

    def __init__(self) -> None:
        self.operations = [add, subtract, multiply, divide, power, remainder]

    def run(self, name: str, a: int, b: int) -> int | float:
        '''Dispatch to the named operation.'''
        operation = getattr(self, name)
        return operation(a, b)

    def stats(self, values: list[int]) -> dict[str, float]:
        '''Return common summary statistics for a list.'''
        return {
            'mean': mean(values),
            'median': median(values),
            'count': len(values),
        }
"""

_GRAPH_A = b"""
# marker_alpha
GRAPH_A_NAME = 'alpha'


def build_graph_a() -> None:
    '''First same-named file.'''
    return None


def helper_a() -> str:
    '''Alpha-specific helper.'''
    return 'alpha'


def shape_a() -> str:
    '''Another alpha helper.'''
    return 'square'
"""

_GRAPH_B = b"""
# marker_beta
GRAPH_B_NAME = 'beta'


def build_graph_b() -> None:
    '''Second same-named file.'''
    return None


def helper_b() -> str:
    '''Beta-specific helper.'''
    return 'beta'


def shape_b() -> str:
    '''Another beta helper.'''
    return 'circle'
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_multi_chunk_metadata(api_base: str) -> None:
    """Every chunk of an uploaded file carries file_id/session_id/timestamp."""
    session_id, chunks = _upload_and_wait(
        api_base,
        files=[("files", ("calculator.py", _CALCULATOR_PY, "text/x-python"))],
        message="Please review the code I uploaded and help me improve it.",
        min_chunks=2,
    )
    assert len(chunks) >= 2, (
        f"Expected >=2 chunks for calculator.py, found {len(chunks)} "
        "(is the document pipeline reaching the vector DB?)"
    )

    now = datetime.now(UTC)
    for chunk in chunks:
        assert chunk["file_id"], f"chunk missing file_id: {chunk['id']}"
        assert chunk["session_id"] == session_id, f"chunk session_id mismatch: {chunk['id']}"
        assert chunk["user_id"] is not None and chunk["user_id"] > 0, f"chunk missing user_id: {chunk['id']}"
        assert chunk["file_name"] == "calculator.py", f"unexpected file_name: {chunk['file_name']}"
        assert chunk["content"], f"chunk has empty content: {chunk['id']}"
        assert chunk["created_at"] is not None, f"chunk missing created_at: {chunk['id']}"
        created = chunk["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        assert (now - created).total_seconds() < 3600, f"chunk created_at not fresh: {chunk['id']}"

    smoke_log("multi_chunk_metadata_ok", session_id=session_id, chunk_count=len(chunks))


def test_same_name_two_files(api_base: str) -> None:
    """Two same-named files coexist under distinct file_ids (no demotion)."""
    session_id, chunks = _upload_and_wait(
        api_base,
        files=[
            ("files", ("graph.py", _GRAPH_A, "text/x-python")),
            ("files", ("graph.py", _GRAPH_B, "text/x-python")),
        ],
        message="Please review the two files I uploaded and help me improve them.",
        min_chunks=2,
    )
    assert len(chunks) >= 2, f"Expected >=2 chunks for two graph.py files, found {len(chunks)}"

    file_ids = {chunk["file_id"] for chunk in chunks}
    assert len(file_ids) >= 2, (
        f"Expected two distinct file_ids for the same-named files, found {len(file_ids)} "
        "(a newer upload may be demoting the older file)"
    )

    for chunk in chunks:
        assert chunk["file_name"] == "graph.py", f"unexpected file_name: {chunk['file_name']}"
        assert chunk["session_id"] == session_id
        assert chunk["file_id"] in file_ids

    smoke_log("same_name_two_files_ok", session_id=session_id, file_id_count=len(file_ids))

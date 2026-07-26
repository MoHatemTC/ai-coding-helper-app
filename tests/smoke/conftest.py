"""Shared fixtures and helpers for the smoke test suite.

Usage:
    pytest tests/smoke/ -v --server-url=http://localhost:8000
"""

from __future__ import annotations

import os
import random
import string
import sys
from typing import Any

import pytest
import structlog


# ---------------------------------------------------------------------------
# Helpers — resolve the base URL lazily via the active pytest Config
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def api_base(pytestconfig: pytest.Config) -> str:
    """Return the API base URL."""
    return get_api_base(pytestconfig)


def get_api_base(config: pytest.Config | None = None) -> str:
    """Return the ``/api/v1`` base URL from the CLI option or env var.

    If ``config`` is ``None`` (e.g. imported at module level before the test
    session starts), falls back to the env var or default.
    """
    if config is not None:
        server_url = config.getoption("--server-url", default=None)
        if server_url:
            return f"{server_url}/api/v1"
    server_url = os.getenv("SMOKE_SERVER_URL", "http://localhost:8000")
    return f"{server_url}/api/v1"


def get_server_url(config: pytest.Config | None = None) -> str:
    """Return the server base URL (without ``/api/v1`` suffix).

    Used by health tests that hit ``/`` and ``/health`` directly.
    """
    if config is not None:
        server_url = config.getoption("--server-url", default=None)
        if server_url:
            return server_url
    return os.getenv("SMOKE_SERVER_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# CLI option: --server-url
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add a ``--server-url`` CLI option to point smoke tests at a target host."""
    parser.addoption(
        "--server-url",
        action="store",
        default=os.getenv("SMOKE_SERVER_URL", "http://localhost:8000"),
        help="Base URL of the running server (default: http://localhost:8000)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the 'smoke' marker."""
    config.addinivalue_line("markers", "smoke: pre-deploy smoke test.")
    url = get_api_base(config)
    print(f"\n🔍 Smoke tests targeting: {url}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SmokeTestError(AssertionError):
    """A smoke test assertion failed — deployment should block."""


def random_email() -> str:
    """Generate a unique email for each test run."""
    suffix = "".join(random.choices(string.ascii_lowercase, k=8))
    return f"smoke-test-{suffix}@example.com"


def strong_password() -> str:
    """Generate a password that passes the app's strength checks."""
    return f"SmokeTest{random.randint(1000, 9999)}!"


def smoke_log(event: str, **kwargs: Any) -> None:
    """Emit a structured log line visible in the test output."""
    logger = structlog.get_logger("smoke_test")
    logger.info(event, **kwargs)

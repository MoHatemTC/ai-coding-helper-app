"""Standalone configuration for the MCP server.

This module is intentionally independent of ``app.*`` so the MCP server can
run as its own process without importing or executing any application code.

Environment variables are loaded from the same ``.env.{ENV}`` files the
application uses (via python-dotenv), then read from ``os.environ``.
``MCP_USER_ID`` (optional) enables identity-bound mode: when set, the
``search_code`` / ``memory_search`` tools only operate for that user.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ENV = os.getenv("MCP_ENV", os.getenv("APP_ENV", "development")).lower()


def _load_env_file() -> None:
    """Load the environment-specific dotenv files into ``os.environ``.

    Existing variables are never overridden so a parent process can pass
    values explicitly (e.g. the ReAct agent spawning this server).
    """
    base_dir = Path(__file__).resolve().parents[1]
    env_files = [
        base_dir / f".env.{_ENV}.local",
        base_dir / f".env.{_ENV}",
        base_dir / ".env.local",
        base_dir / ".env",
    ]
    for env_file in env_files:
        if env_file.is_file():
            load_dotenv(dotenv_path=str(env_file), override=False)
            return


_load_env_file()


def _str(env_key: str, default: str) -> str:
    """Read a string setting from the environment."""
    return os.getenv(env_key, default)


def _int(env_key: str, default: int) -> int:
    """Read an integer setting from the environment."""
    try:
        return int(os.getenv(env_key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class MCPConfig:
    """Settings consumed by the standalone MCP server."""

    environment: str
    tavily_api_key: str
    groq_api_key: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    memory_collection_name: str
    embedding_model_name: str
    top_k_retrieval: int
    mcp_user_id: str | None
    guardrails_enabled: bool


config = MCPConfig(
    environment=_ENV,
    tavily_api_key=_str("TAVILY_API_KEY", ""),
    groq_api_key=_str("GROQ_API_KEY", ""),
    postgres_host=_str("POSTGRES_HOST", "localhost"),
    postgres_port=_int("POSTGRES_PORT", 5432),
    postgres_db=_str("POSTGRES_DB", "food_order_db"),
    postgres_user=_str("POSTGRES_USER", "postgres"),
    postgres_password=_str("POSTGRES_PASSWORD", "postgres"),
    memory_collection_name=_str("LONG_TERM_MEMORY_COLLECTION_NAME", "longterm_memory"),
    embedding_model_name=_str("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"),
    top_k_retrieval=_int("TOP_K_RETRIEVAL", 5),
    mcp_user_id=_str("MCP_USER_ID", "") or None,
    guardrails_enabled=_str("MCP_GUARDRAILS_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
)

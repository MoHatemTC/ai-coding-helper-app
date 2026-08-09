"""Standalone long-term memory search backed by mem0 + pgvector.

Kept deliberately thin: no caching, no consolidation, no LLM registry. It
only needs to search a user's memories, using the same pgvector collection
and LiteLLM LLM configuration as the application.
"""

from mem0 import AsyncMemory

from mcp_server.config import config


class MemoryService:
    """Search long-term memories for a user."""

    def __init__(self) -> None:
        """Initialize the memory service."""
        self._memory: AsyncMemory | None = None

    async def _get_memory(self) -> AsyncMemory:
        """Build the mem0 AsyncMemory client on first use."""
        if self._memory is None:
            self._memory = await AsyncMemory.from_config(
                config_dict={
                    "vector_store": {
                        "provider": "pgvector",
                        "config": {
                            "collection_name": config.memory_collection_name,
                            "dbname": config.postgres_db,
                            "user": config.postgres_user,
                            "password": config.postgres_password,
                            "host": config.postgres_host,
                            "port": config.postgres_port,
                            "embedding_model_dims": 384,
                        },
                    },
                    "llm": {
                        "provider": "openai",
                        "config": {
                            "model": config.memory_llm_model,
                            "api_key": config.litellm_api_key,
                            "openai_base_url": config.litellm_base_url,
                            "max_tokens": 400,
                        },
                    },
                    "embedder": {
                        "provider": "huggingface",
                        "config": {"model": "sentence-transformers/all-MiniLM-L6-v2"},
                    },
                }
            )
        return self._memory

    async def search(self, user_id: str, query: str) -> str:
        """Search memories for a user and return a formatted string.

        Args:
            user_id: The user whose memories to search.
            query: The search query describing what to look for.

        Returns:
            Formatted memory entries (one per line), or an empty string when
            no user_id is supplied. Exceptions propagate so the caller can
            surface a tool error.
        """
        if not user_id:
            return ""
        memory = await self._get_memory()
        results = await memory.search(user_id=user_id, query=query, limit=5)
        return "\n".join(f"* {result['memory']}" for result in results["results"])


memory_service = MemoryService()

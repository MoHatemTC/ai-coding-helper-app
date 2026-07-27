"""Long-term memory service using mem0 and pgvector with optional cache layer."""

import asyncio
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from mem0 import AsyncMemory
from sqlalchemy import text
from sqlmodel import Session

from app.core.cache import (
    cache_key,
    cache_service,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.prompts.memory import (
    CONSOLIDATION_PROMPT,
    FACT_EXTRACTION_PROMPT,
    CUSTOM_UPDATE_PROMPT,
)
from app.schemas.memory import ConsolidatedFacts
from app.services.database import database_service
from app.services.llm.registry import LLMRegistry

load_dotenv()


class MemoryService:
    """Service for managing long-term memory using mem0 and pgvector."""

    def __init__(self):
        """Initialize the memory service."""
        self._memory: AsyncMemory | None = None

    async def _get_memory(self) -> AsyncMemory:
        if self._memory is None:
            self._memory = await AsyncMemory.from_config(
                config_dict={
                    "vector_store": {
                        "provider": "pgvector",
                        "config": {
                            "collection_name": settings.LONG_TERM_MEMORY_COLLECTION_NAME,
                            "dbname": settings.POSTGRES_DB,
                            "user": settings.POSTGRES_USER,
                            "password": settings.POSTGRES_PASSWORD,
                            "host": settings.POSTGRES_HOST,
                            "port": settings.POSTGRES_PORT,
                            "embedding_model_dims": 384,
                        },
                    },
                    "llm": {
                        "provider": "groq",
                        "config": {
                            "model": "llama-3.3-70b-versatile",
                            "api_key": os.getenv("GROQ_API_KEY"),
                            "max_tokens": 400,
                        },
                    },
                    "embedder": {
                        "provider": "huggingface",
                        "config": {"model": "sentence-transformers/all-MiniLM-L6-v2"},
                    },
                    "custom_fact_extraction_prompt": FACT_EXTRACTION_PROMPT,
                    "custom_update_memory_prompt": CUSTOM_UPDATE_PROMPT,
                }
            )
        return self._memory

    async def initialize(self) -> None:
        """Pre-warm the mem0 AsyncMemory instance and its pgvector connection pool.

        Call once at startup so the first search() or add() doesn't pay the
        ~130ms from_config + pgvector.list_cols() cold-init cost.
        """
        await self._get_memory()
        logger.info("memory_service_initialized")

    async def count_memories(self, user_id: str) -> int:
        """Count memories for a user via SQL COUNT — no data transfer."""
        try:
            table_name = settings.LONG_TERM_MEMORY_COLLECTION_NAME
            with Session(database_service.engine) as session:
                result = session.execute(
                    text(f"SELECT COUNT(*) FROM {table_name} WHERE payload->>'user_id' = :uid"),
                    {"uid": user_id},
                )
                return result.scalar() or 0
        except Exception as e:
            logger.error("failed_to_count_memories", user_id=user_id, error=str(e))
            return 0

    async def search(self, user_id: str | None, query: str) -> str:
        """Search relevant memories for a user.

        Checks cache first; on miss, queries mem0 and caches the result.

        Returns formatted memory string, or empty string on failure or when
        no user_id is supplied (anonymous sessions skip long-term memory
        rather than pooling under a shared partition).
        """
        if user_id is None:
            return ""
        try:
            # Check cache first
            key = cache_key("memory", str(user_id), query)
            cached = await cache_service.get(key)
            if cached is not None:
                logger.debug("memory_search_cache_hit", user_id=user_id)
                return cached

            memory = await self._get_memory()
            results = await memory.search(user_id=str(user_id), query=query, limit=5)
            result = "\n".join([f"* {r['memory']}" for r in results["results"]])

            # Cache successful results
            if result:
                await cache_service.set(key, result)

            return result
        except Exception as e:
            logger.error("failed_to_get_relevant_memory", error=str(e), user_id=user_id, query=query)
            return ""

    async def add(self, user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        """Add messages to long-term memory for a user.

        After adding, checks memory count via SQL COUNT. If it exceeds
        the consolidation threshold, triggers background consolidation.

        No-op when ``user_id`` is ``None`` (see ``search`` for rationale).
        """
        if user_id is None:
            return
        try:
            memory = await self._get_memory()
            await memory.add(messages, user_id=str(user_id), metadata=metadata)
            logger.info("long_term_memory_updated_successfully", user_id=user_id)

            # Check count via SQL (fast, no data transfer)
            count = await self.count_memories(user_id)
            threshold = settings.MEMORY_CONSOLIDATION_THRESHOLD

            if count > threshold:
                logger.info("memory_consolidation_scheduled", user_id=user_id, count=count, threshold=threshold)
                asyncio.create_task(self._consolidate(user_id))
        except Exception as e:
            logger.exception("failed_to_update_long_term_memory", user_id=user_id, error=str(e))

    async def _consolidate(self, user_id: str) -> None:
        """Merge similar memories to reduce count while preserving key facts.

        Fetches all memories, uses structured LLM output to consolidate,
        deletes old memories, and adds the merged result.
        """
        try:
            memory = await self._get_memory()

            # Fetch all memories for this user
            all_memories = await memory.get_all(user_id=user_id)
            memories = all_memories.get("results", [])
            if not memories:
                return

            # Build facts list for the LLM
            facts = "\n".join(f"- {m['memory']}" for m in memories)
            target = settings.MEMORY_CONSOLIDATION_TARGET

            # Use structured output for deterministic parsing
            llm = LLMRegistry.get("llama-3.3-70b-versatile", temperature=0, max_tokens=500)
            structured_llm = llm.with_structured_output(ConsolidatedFacts)
            raw_result = await structured_llm.ainvoke(
                [
                    HumanMessage(content=CONSOLIDATION_PROMPT.format(facts=facts, target=target)),
                ]
            )
            result = ConsolidatedFacts.model_validate(raw_result)

            # Hard cap: LLM may ignore target — truncate to enforce it
            if len(result.facts) > target:
                result = ConsolidatedFacts(facts=result.facts[:target])

            if not result.facts:
                logger.warning("memory_consolidation_produced_no_facts", user_id=user_id)
                return

            # Add consolidated facts FIRST so we never lose data if add fails
            merged_messages = [{"role": "user", "content": fact} for fact in result.facts]
            await memory.add(merged_messages, user_id=user_id, infer=False)

            # Only delete old memories after successful add
            await asyncio.gather(*(memory.delete(m["id"]) for m in memories))

            logger.info(
                "memory_consolidation_completed",
                user_id=user_id,
                before_count=len(memories),
                after_count=len(result.facts),
            )
        except Exception as e:
            logger.exception("memory_consolidation_failed", user_id=user_id, error=str(e))


memory_service = MemoryService()

"""Long-term memory service using mem0 and pgvector with optional cache layer."""

from mem0 import AsyncMemory

from app.core.cache import (
    cache_key,
    cache_service,
)
from app.core.config import settings
from app.core.logging import logger
from app.schemas.review import Finding, Severity, Category
import os
from dotenv import load_dotenv

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
                            "model": "llama-3.1-8b-instant",
                            "api_key": os.getenv("GROQ_API_KEY"),
                        },
                    },
                    "embedder": {
                        "provider": "huggingface",
                        "config": {"model": "sentence-transformers/all-MiniLM-L6-v2"},
                    },
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
            results = await memory.search(user_id=str(user_id), query=query)
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

        No-op when ``user_id`` is ``None`` (see ``search`` for rationale).
        """
        if user_id is None:
            return
        try:
            memory = await self._get_memory()
            await memory.add(messages, user_id=str(user_id), metadata=metadata)
            logger.info("long_term_memory_updated_successfully", user_id=user_id)
        except Exception as e:
            logger.exception("failed_to_update_long_term_memory", user_id=user_id, error=str(e))

    async def store_finding(self, user_id: str, session_id: str, finding: Finding) -> None:
        """Store a finding in long-term memory."""
        if user_id is None or session_id is None:
            return
        try:
            memory = await self._get_memory()
            await memory.add(
                f"[{finding.severity.value.upper()}] {finding.category.value} finding: {finding.message} — {finding.rationale}",
                user_id=user_id,
                metadata={
                    "type": "code_finding",
                    "category": finding.category.value,
                    "severity": finding.severity.value,
                    "message": finding.message,
                    "rationale": finding.rationale,
                },
                run_id=session_id,
                infer=False,
            )
            logger.info("store_finding_in_long_term_memory_successfully", user_id=user_id)
        except Exception as e:
            logger.exception("failed_to_store_finding_in_long_term_memory", user_id=user_id, error=str(e))

    async def get_all_session_finding(self, user_id: str, session_id: str):
        """Get all findings for a session."""
        if user_id is None or session_id is None:
            return []
        try:
            memory = await self._get_memory()
            results = await memory.get_all(
                user_id=user_id,
                run_id=session_id,
                filters={"type": "code_finding"},
            )
            results = results.get("results", [])
            final_finding = []
            for f in results:
                print(f["metadata"])
                final_finding.append(f["metadata"])
            return final_finding
        except Exception as e:
            logger.error("failed_to_get_findings", error=str(e), user_id=user_id)
            return []

    async def get_skill_profile(self, user_id: str | None) -> dict | None:
        """Retrieve user's skill profile from memory."""
        if user_id is None:
            return None
        try:
            memory = await self._get_memory()
            results = await memory.get_all(
                user_id=str(user_id),
                filters={"type": "skill_profile"},
            )
            # Find the skill profile entry (filtered by metadata)
            return results.get("results", [])
        except Exception as e:
            logger.error("failed_to_get_skill_profile", error=str(e), user_id=user_id)
            return None

    async def upsert_skill_profile(self, user_id: str, profile: dict) -> None:
        """Create or update a user's skill profile memory.

        If a skill_profile memory already exists for this user, it is overwritten
        in place (same memory id). Otherwise a new one is created.
        """
        if user_id is None:
            return
        memory = await self._get_memory()

        existing_result = await memory.get_all(
            user_id=user_id,
            filters={"type": "skill_profile"},
        )
        existing_result = existing_result.get("results", [])
        content = self._profile_to_text(profile)

        metadata = {
            "type": "skill_profile",
            "skill_level": profile["skill_level"].value,
            "weaknesses": profile["weaknesses"],
            "all_searched_topics": profile["all_searched_topics"],
        }

        if existing_result not in (None, []):
            print("Skill profile already exists. Updating...")
            memory_id = existing_result[0]["id"]
            await memory.delete(memory_id=memory_id)
        await memory.add(content, user_id=user_id, metadata=metadata, infer=False)

    def _profile_to_text(self, profile: dict) -> str:
        print("From profile to text...")
        print(profile)
        weakness_summary = (
            "; ".join(f"{w['topic']}: {w['description']}" for w in profile["weaknesses"]) or "none identified"
        )
        topics = ", ".join(profile["all_searched_topics"]) or "none yet"
        profile_text = (
            f"Skill level: {profile['skill_level'].value}. Weaknesses: {weakness_summary}. Topics explored: {topics}."
        )
        print(profile_text)
        return profile_text

    def code_review(self) -> list[Finding]:
        """Create dummy findings for code review. Returns a list of Finding objects with detailed issue information."""
        return [
            Finding(
                file_path="main.py",
                line=3,
                severity=Severity.HIGH,
                category=Category.SECURITY,
                message="SQL injection vulnerability - unvalidated user input",
                rationale="User input is directly concatenated into SQL query",
            ),
            Finding(
                file_path="main.py",
                line=7,
                severity=Severity.MEDIUM,
                category=Category.CORRECTNESS,
                message="Off-by-one error in loop boundary",
                rationale="Loop iterates to len(items) instead of len(items) - 1",
            ),
            Finding(
                file_path="main.py",
                line=12,
                severity=Severity.LOW,
                category=Category.STYLE,
                message="Missing type hints on function parameters",
                rationale="Type hints improve code readability",
            ),
        ]


memory_service = MemoryService()

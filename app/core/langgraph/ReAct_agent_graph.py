"""ReAct agent workflow built with LangChain's create_agent.

Everything the graph needs lives in this file: the chat model, the
create_agent subgraph, the document pipeline / store / summarization nodes,
the checkpointer, and the public ReActAgent API.
"""

import asyncio
from typing import (
    AsyncGenerator,
    Optional,
)
from urllib.parse import quote_plus

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    SystemMessage,
)
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import (
    END,
    StateGraph,
)
from langgraph.graph.state import CompiledStateGraph
from psycopg import (
    AsyncConnection,
    sql,
)
from psycopg.rows import (
    DictRow,
    dict_row,
)
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from app.core.config import (
    Environment,
    settings,
)
from app.core.langgraph.nodes.document_pipeline import document_pipeline_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.core.langgraph.nodes.inbound_first_stage import secret_guardrail_node
from app.core.langgraph.nodes.outbound import outbound_node
from app.core.langgraph.nodes.store_messages import store_messages_node
from app.core.langgraph.nodes.summarization import summarization_node
from app.core.langgraph.tools.code_search import (
    search_code,
    set_session_id,
    set_user_id,
)
from app.core.langgraph.tools.duckduckgo_search import duckduckgo_search_tool
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.core.prompts import load_system_prompt
from app.schemas import (
    GraphState,
    Message,
)
from app.services.memory import memory_service
from app.services.skill_profile import skill_profile_service

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

_chat_model = ChatOpenAI(
    model=settings.DEFAULT_LLM_MODEL,
    base_url=settings.LITELLM_BASE_URL,
    api_key=SecretStr(settings.LITELLM_API_KEY),
    temperature=settings.DEFAULT_LLM_TEMPERATURE,
    max_retries=settings.MAX_LLM_CALL_RETRIES,
    timeout=settings.LLM_TOTAL_TIMEOUT,
)

_agent = create_agent(
    model=_chat_model,
    tools=[duckduckgo_search_tool, search_code],
    name="agent",
)


def _last_ai_message_content(messages: list) -> str:
    """Return the content of the last non-empty AIMessage in the list, if any."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
            return message.content
    return ""


class ReActAgent:
    """Manages the ReAct agent graph and interactions with the LLM."""

    def __init__(self):
        """Initialize the ReAct agent with the shared components."""
        self.model_name = settings.DEFAULT_LLM_MODEL
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        logger.info(
            "react_agent_initialized",
            model=self.model_name,
            environment=settings.ENVIRONMENT.value,
        )

    async def _get_connection_pool(self) -> Optional[PostgresConnPool]:
        """Get a PostgreSQL connection pool using environment-specific settings."""
        if self._connection_pool is None:
            try:
                max_size = settings.POSTGRES_POOL_SIZE

                connection_url = (
                    "postgresql://"
                    f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                )

                self._connection_pool = AsyncConnectionPool(
                    connection_url,
                    open=False,
                    max_size=max_size,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 5,
                        "prepare_threshold": None,
                        "row_factory": dict_row,
                    },
                )
                await self._connection_pool.open()
                logger.info("connection_pool_created", max_size=max_size, environment=settings.ENVIRONMENT.value)
            except Exception as e:
                logger.error("connection_pool_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_connection_pool", environment=settings.ENVIRONMENT.value)
                    return None
                raise e
        return self._connection_pool

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Create and configure the ReAct agent workflow."""
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)

                # Register all nodes first
                graph_builder.add_node("secret_guardrail", secret_guardrail_node)
                graph_builder.add_node("inbound_intent", inbound_intent_node)
                graph_builder.add_node("document_pipeline", document_pipeline_node)
                graph_builder.add_node("agent", _agent)
                graph_builder.add_node("outbound", outbound_node)
                graph_builder.add_node("store_messages", store_messages_node)
                graph_builder.add_node("summarization", summarization_node)
                # Then edges
                graph_builder.set_entry_point("secret_guardrail")
                graph_builder.add_edge("secret_guardrail", "inbound_intent")
                graph_builder.add_edge("document_pipeline", "agent")
                graph_builder.add_edge("agent", "outbound")
                graph_builder.add_edge("store_messages", "summarization")
                graph_builder.add_edge("summarization", END)

                connection_pool = await self._get_connection_pool()
                if connection_pool:
                    checkpointer = AsyncPostgresSaver(connection_pool)
                    await checkpointer.setup()
                else:
                    checkpointer = None
                    if settings.ENVIRONMENT != Environment.PRODUCTION:
                        raise Exception("Connection pool initialization failed")

                self._graph = graph_builder.compile(
                    checkpointer=checkpointer, name=f"{settings.PROJECT_NAME} Agent ({settings.ENVIRONMENT.value})"
                )

                logger.info(
                    "graph_created",
                    graph_name=f"{settings.PROJECT_NAME} Agent",
                    environment=settings.ENVIRONMENT.value,
                    has_checkpointer=checkpointer is not None,
                )
            except Exception as e:
                logger.error("graph_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_graph")
                    return None
                raise e

        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        """Return the compiled graph, creating it on first access."""
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("graph initialization failed")
        return self._graph

    async def _prepare_turn(
        self,
        message: Message,
        session_id: str,
        user_id: Optional[str],
        username: Optional[str],
        pending_files: Optional[list],
    ) -> tuple[CompiledStateGraph, RunnableConfig, dict]:
        """Build the config and graph input for one conversation turn."""
        graph = await self._get_graph()
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "callbacks": callbacks,
            "metadata": {
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
            },
        }

        set_session_id(session_id)
        if user_id:
            set_user_id(int(user_id))

        state, relevant_memory, skill_profile = await asyncio.gather(
            graph.aget_state(config),
            memory_service.search(user_id, message.content),
            skill_profile_service.render_for_prompt_async(int(user_id)) if user_id else asyncio.sleep(0, result=""),
        )

        existing_messages = state.values.get("messages", []) if state.values else []
        summary = state.values.get("summary", "") if state.values else ""

        relevant_memory = relevant_memory or "No relevant memory found."
        skill_profile = skill_profile or ""

        system_prompt = load_system_prompt(
            username=username,
            long_term_memory=relevant_memory,
            summary=summary,
            skill_profile=skill_profile or "No skill profile yet.",
        )

        conversation_context = [msg for msg in existing_messages if not isinstance(msg, SystemMessage)]
        graph_input = {
            "messages": [
                SystemMessage(content=system_prompt, id="system-prompt"),
                *conversation_context,
                message.model_dump(),
            ],
            "long_term_memory": relevant_memory,
            "skill_profile": skill_profile,
            "last_message_index": len(existing_messages),
            "pending_files": pending_files or [],
            "outbound_attempts": 0,
        }
        return graph, config, graph_input

    async def get_response(
        self,
        message: Message,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        pending_files: Optional[list] = None,
    ) -> list[Message]:
        """Get a response from the LLM.

        Args:
            message (Message): The user message for this turn.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            code (Optional[str]): The code snippet submitted for review.
            language (Optional[str]): The programming language of the submitted code.
            pending_files (Optional[list]): FileAttachments uploaded but not yet processed.

        Returns:
            list[Message]: The assistant message for this turn.
        """
        try:
            graph, config, graph_input = await self._prepare_turn(
                message, session_id, user_id, username, pending_files
            )

            response = await graph.ainvoke(graph_input, config=config)

            final_response = response.get("final_response", "") or _last_ai_message_content(
                response.get("messages", [])
            )
            return [Message(role="assistant", content=final_response)] if final_response else []
        except Exception as e:
            logger.exception("get_response_failed", error=str(e), session_id=session_id)
            raise

    async def get_stream_response(
        self,
        message: Message,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        pending_files: Optional[list] = None,
    ) -> AsyncGenerator[str, None]:
        """Get a stream response from the LLM.

        Args:
            message (Message): The user message for this turn.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            code (Optional[str]): The code snippet submitted for review.
            language (Optional[str]): The programming language of the submitted code.
            pending_files (Optional[list]): FileAttachments uploaded but not yet processed.

        Yields:
            str: Tokens of the LLM response.
        """
        try:
            graph, config, graph_input = await self._prepare_turn(
                message, session_id, user_id, username, pending_files
            )

            await graph.ainvoke(graph_input, config=config)
            state = await graph.aget_state(config)
            final_response = state.values.get("final_response", "") or _last_ai_message_content(
                state.values.get("messages", [])
            )
            for word in final_response.split(" "):
                yield word + " "
                await asyncio.sleep(0.02)
        except Exception as e:
            logger.exception("stream_processing_failed", error=str(e), session_id=session_id)
            raise

    async def clear_chat_history(self, session_id: str) -> None:
        """Clear all chat history for a given thread ID."""
        try:
            conn_pool = await self._get_connection_pool()
            if conn_pool is None:
                raise RuntimeError("connection pool unavailable; cannot clear chat history")

            async with conn_pool.connection() as conn:
                async with conn.pipeline():
                    for table in settings.CHECKPOINT_TABLES:
                        await conn.execute(
                            sql.SQL("DELETE FROM {} WHERE thread_id = %s").format(sql.Identifier(table)),
                            (session_id,),
                        )
                logger.info(
                    "checkpoint_tables_cleared_for_session",
                    tables=settings.CHECKPOINT_TABLES,
                    session_id=session_id,
                )
        except Exception as e:
            logger.exception(
                "clear_chat_history_operation_failed",
                session_id=session_id,
                error=str(e),
            )
            raise

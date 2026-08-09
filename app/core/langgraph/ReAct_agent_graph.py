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
    AIMessageChunk,
    SystemMessage,
)
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import (
    END,
    StateGraph,
)
from langgraph.graph.state import CompiledStateGraph
from psycopg import (
    AsyncConnection,
    InterfaceError,
    OperationalError,
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
from app.core.langgraph.mcp_tool_connection import MCPToolConnection
from app.core.langgraph.tools.ask_human import ask_human
from app.core.langgraph.tools.code_search import (
    search_code,
    set_session_id,
    set_user_id,
)
from app.core.langgraph.tools.duckduckgo_search import duckduckgo_search_tool
from app.core.logging import logger
from app.core.observability import new_langfuse_callback_handler
from app.core.prompts import load_system_prompt
from app.schemas import (
    GraphState,
    Message,
)
from app.services.memory import memory_service
from app.services.skill_profile import skill_profile_service

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]


class AgentDatabaseUnavailableError(RuntimeError):
    """Raised when the agent's PostgreSQL backend (pool/checkpointer) is unreachable.

    Callers (e.g. the MCP server) use this to return a user-friendly error and
    to decide whether to retry graph/pool initialization once the DB recovers.
    """


_chat_model = ChatOpenAI(
    model=settings.DEFAULT_LLM_MODEL,
    base_url=settings.LITELLM_BASE_URL,
    api_key=SecretStr(settings.LITELLM_API_KEY),
    temperature=settings.DEFAULT_LLM_TEMPERATURE,
    max_retries=settings.MAX_LLM_CALL_RETRIES,
    timeout=settings.LLM_TOTAL_TIMEOUT,
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
        self._agent: Optional[CompiledStateGraph] = None
        self.mcp_tools = MCPToolConnection()
        logger.info(
            "react_agent_initialized",
            model=self.model_name,
            environment=settings.ENVIRONMENT.value,
        )

    async def _connect_mcp_tools(self) -> list[BaseTool]:
        """Load the standalone MCP server's tools as LangChain tools.

        Delegates to the shared persistent connection, which spawns the server
        once as a stdio subprocess with the full parent environment but with
        ``MCP_USER_ID`` forced empty, so the server runs unbound and
        user/session scope is injected per-call by the scope-forcing
        interceptor instead of the model choosing it.
        """
        return await self.mcp_tools.get_tools()

    async def start_mcp(self) -> None:
        """Pre-warm the shared MCP tool connection at app startup."""
        if settings.MCP_AGENT_TOOLS_ENABLED:
            await self.mcp_tools.start()
            logger.info("mcp_tools_started")

    async def stop_mcp(self) -> None:
        """Tear down the shared MCP tool connection at app shutdown."""
        if self.mcp_tools.is_running:
            await self.mcp_tools.stop()

    async def _create_agent(self) -> CompiledStateGraph:
        """Build the ReAct sub-agent, using MCP server tools when enabled."""
        if self._agent is not None:
            return self._agent
        if settings.MCP_AGENT_TOOLS_ENABLED:
            tools: list[BaseTool] = [*await self._connect_mcp_tools(), ask_human]
        else:
            tools = [duckduckgo_search_tool, search_code, ask_human]
        self._agent = create_agent(model=_chat_model, tools=tools, name="agent")
        return self._agent

    async def _get_connection_pool(self) -> Optional[PostgresConnPool]:
        """Get a PostgreSQL connection pool using environment-specific settings.

        A pool left in a closed/broken state by a previous failed attempt is
        dropped so it gets rebuilt on the next call, making the agent
        self-healing after a PostgreSQL outage.
        """
        if self._connection_pool is not None:
            if not self._connection_pool.closed:
                return self._connection_pool
            self._connection_pool = None

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
                timeout=settings.POSTGRES_CONNECT_TIMEOUT,
                kwargs={
                    "autocommit": True,
                    "connect_timeout": settings.POSTGRES_CONNECT_TIMEOUT,
                    "prepare_threshold": None,
                    "row_factory": dict_row,
                },
            )
            await self._connection_pool.open(timeout=settings.POSTGRES_CONNECT_TIMEOUT)
            logger.info("connection_pool_created", max_size=max_size, environment=settings.ENVIRONMENT.value)
            return self._connection_pool
        except Exception as e:
            logger.exception("connection_pool_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
            # Drop the broken pool so the next attempt builds a fresh one.
            if self._connection_pool is not None:
                try:
                    await self._connection_pool.close()
                except Exception:
                    logger.warning("connection_pool_cleanup_failed")
                self._connection_pool = None
            if settings.ENVIRONMENT == Environment.PRODUCTION:
                logger.warning("continuing_without_connection_pool", environment=settings.ENVIRONMENT.value)
                return None
            raise AgentDatabaseUnavailableError(f"could not connect to postgres: {e}") from e

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Create and configure the ReAct agent workflow."""
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)

                # Register all nodes first
                graph_builder.add_node("secret_guardrail", secret_guardrail_node)
                graph_builder.add_node("inbound_intent", inbound_intent_node)
                graph_builder.add_node("document_pipeline", document_pipeline_node)
                agent = await self._create_agent()
                graph_builder.add_node("agent", agent)
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
                        raise AgentDatabaseUnavailableError("connection pool initialization failed")

                self._graph = graph_builder.compile(
                    checkpointer=checkpointer, name=f"{settings.PROJECT_NAME} Agent ({settings.ENVIRONMENT.value})"
                )

                logger.info(
                    "graph_created",
                    graph_name=f"{settings.PROJECT_NAME} Agent",
                    environment=settings.ENVIRONMENT.value,
                    has_checkpointer=checkpointer is not None,
                )
            except AgentDatabaseUnavailableError:
                self._graph = None
                raise
            except Exception as e:
                self._graph = None
                logger.error("graph_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_graph")
                    return None
                raise

        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        """Return the compiled graph, creating it on first access."""
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise AgentDatabaseUnavailableError("graph initialization failed: database unavailable")
        return self._graph

    async def reset_graph(self) -> None:
        """Drop the graph, pool, and MCP session so the next call rebuilds them.

        Call this after a PostgreSQL outage so that when the DB comes back the
        pool, checkpointer, compiled graph, and MCP tool session are re-created
        on the next turn.
        """
        self._graph = None
        self._agent = None
        if self._connection_pool is not None:
            try:
                await self._connection_pool.close()
            except Exception:
                logger.warning("connection_pool_close_during_reset_failed")
            self._connection_pool = None
        await self.mcp_tools.restart()
        logger.info("agent_graph_reset")

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
        handler = new_langfuse_callback_handler()
        callbacks: list[BaseCallbackHandler] = [handler] if handler else []
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "callbacks": callbacks,
            "metadata": {
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
                "langfuse_session_id": session_id,
                "langfuse_user_id": user_id,
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
        except (OperationalError, InterfaceError) as e:
            logger.warning("get_response_db_error", error=str(e), session_id=session_id)
            await self.reset_graph()
            raise AgentDatabaseUnavailableError(f"database unavailable during agent run: {e}") from e
        except AgentDatabaseUnavailableError:
            raise
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
        """Get a token stream from the LLM.

        Uses LangGraph ``astream(stream_mode="messages")`` so tokens are
        yielded as the model generates them, instead of buffering the full
        response and replaying it (which delayed the first token).

        Only the agent's model output is streamed — internal guardrail /
        intent / outbound LLM calls are filtered out via the node name.

        Args:
            message (Message): The user message for this turn.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            code (Optional[str]): The code snippet submitted for review.
            language (Optional[str]): The programming language of the submitted code.
            pending_files (Optional[list]): FileAttachments uploaded but not yet processed.

        Yields:
            str: Content tokens of the agent's response, as they are produced.
        """
        try:
            graph, config, graph_input = await self._prepare_turn(
                message, session_id, user_id, username, pending_files
            )

            async for event in graph.astream(graph_input, config=config, stream_mode="messages", subgraphs=True):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue
                message_chunk, metadata = event
                if not isinstance(message_chunk, AIMessageChunk):
                    continue
                if metadata.get("langgraph_node") != "model":
                    continue
                content = message_chunk.content
                if isinstance(content, str) and content:
                    yield content
        except (OperationalError, InterfaceError) as e:
            logger.warning("get_stream_response_db_error", error=str(e), session_id=session_id)
            await self.reset_graph()
            raise AgentDatabaseUnavailableError(f"database unavailable during agent stream: {e}") from e
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

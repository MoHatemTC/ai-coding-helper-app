"""This file contains the LangGraph Progressive Hint Agent and its execution pipeline."""

import asyncio
from typing import (
    AsyncGenerator,
    Optional,
    cast,
)
from urllib.parse import quote_plus

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    convert_to_openai_messages,
)
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import (
    END,
    StateGraph,
)
from langgraph.graph.state import (
    Command,
    CompiledStateGraph,
)
from langgraph.types import (
    StateSnapshot,
)
from psycopg import (
    AsyncConnection,
    sql,
)
from psycopg.rows import (
    DictRow,
    dict_row,
)
from psycopg_pool import AsyncConnectionPool

from app.core.config import (
    Environment,
    settings,
)
from app.core.langgraph.nodes.document_pipeline import document_pipeline_node
from app.core.langgraph.nodes.hints import generate_hint_node
from app.core.langgraph.nodes.inbound_first_stage import secret_guardrail_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.core.langgraph.nodes.outbound import (
    MAX_OUTBOUND_ATTEMPTS,
    SAFE_TIMEOUT_RESPONSE,
)
from app.core.langgraph.nodes.store_messages import store_messages_node
from app.core.langgraph.nodes.summarization import summarization_node
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.schemas import (
    GraphState,
    Message,
)
from app.schemas.review import OutboundJudgeOutput, OutboundTriggerReason
from app.services.guardrails import check_outbound_guardrails
from app.services.memory import memory_service
from app.services.skill_profile import skill_profile_service
from app.services.vector_store import vector_store_service
from app.utils import extract_text_content

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]


def _last_human_query(state: GraphState) -> str:
    """Return the content of the last HumanMessage or user dict in state, if any."""
    for message in reversed(state.messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
        if isinstance(message, dict):
            role = message.get("role") or message.get("type")
            if role in ("user", "human") and isinstance(message.get("content"), str):
                return str(message["content"])
    return ""


class LangGraphAgent:
    """Manages the LangGraph Progressive Hint Agent and interactions with the LLM."""

    def __init__(self):
        """Initialize the LangGraph Agent with necessary components."""
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        logger.info(
            "langgraph_hint_agent_initialized",
            model=settings.HINT_LLM_MODEL,
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

    def _build_config(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> RunnableConfig:
        """Construct standard RunnableConfig metadata payload."""
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        return {
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

    def _build_graph_input(
        self,
        message: Message,
        code: Optional[str] = None,
        language: Optional[str] = None,
        pending_files: Optional[list] = None,
    ) -> dict:
        """Construct standard GraphState entry dictionary."""
        return {
            "messages": [message.model_dump()],
            "pending_files": pending_files or [],
            "code": code,
            "language": language,
            "outbound_attempts": 0,
        }

    # ------------------------------------------------------------------
    # Context Retrieval Node
    # ------------------------------------------------------------------
    async def _context_retrieval_node(self, state: GraphState, config: RunnableConfig) -> Command:
        """Fetch code vector chunks, memory, and skill profile concurrently."""
        user_id = config.get("metadata", {}).get("user_id")
        session_id = config.get("configurable", {}).get("thread_id")

        messages = state.messages
        user_query = ""
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, BaseMessage):
                user_query = str(last_msg.content)
            elif isinstance(last_msg, dict):
                user_query = str(last_msg.get("content", ""))
            else:
                user_query = str(last_msg)

        parsed_user_id = int(user_id) if user_id and str(user_id).isdigit() else 0

        # Concurrent vector search, long-term memory retrieval, and skill profile rendering
        code_chunks, relevant_memory, skill_profile = await asyncio.gather(
            asyncio.to_thread(
                vector_store_service.search,
                query=user_query,
                session_id=str(session_id or ""),
                user_id=parsed_user_id,
                top_k=15,
            ),
            memory_service.search(user_id, user_query),
            skill_profile_service.render_for_prompt_async(parsed_user_id)
            if parsed_user_id
            else asyncio.sleep(0, result=""),
        )

        code_context = (
            "\n\n".join([c.content for c in code_chunks]) if code_chunks else "No specific code chunks found."
        )

        return Command(
            update={
                "code_context": code_context,
                "long_term_memory": relevant_memory or "No memory recorded.",
                "skill_profile": skill_profile or "No skill profile recorded.",
            },
            goto="generate_hint",
        )

    # ------------------------------------------------------------------
    # Node Wrappers
    # ------------------------------------------------------------------
    async def _document_pipeline_wrapper(self, state: GraphState, config: RunnableConfig) -> Command:
        """Wrap document_pipeline_node, redirecting its hardcoded ``goto="agent"`` to context_retrieval."""
        result = await document_pipeline_node(state, config)
        if isinstance(result, Command):
            return Command(update=result.update, goto="context_retrieval")
        return Command(update=result if isinstance(result, dict) else {}, goto="context_retrieval")

    async def _outbound_wrapper(self, state: GraphState, config: RunnableConfig) -> Command:
        """Judge the hint draft and route to delivery or regeneration."""
        draft = state.draft_response
        user_query = _last_human_query(state)
        code_context = getattr(state, "code_context", "") or ""

        if not draft:
            logger.warning("outbound_missing_draft")
            return Command(
                update={
                    "is_safe_output": False,
                    "outbound_trigger_reason": OutboundTriggerReason.EVALUATOR_ERROR.value,
                    "constructive_redirect": None,
                    "final_response": SAFE_TIMEOUT_RESPONSE,
                    "messages": [AIMessage(content=SAFE_TIMEOUT_RESPONSE)],
                },
                goto="store_messages",
            )

        try:
            decision = await check_outbound_guardrails(draft, user_query, code_context, config)
        except Exception:
            logger.exception("outbound_evaluator_error")
            decision = OutboundJudgeOutput(
                is_safe_output=False,
                outbound_trigger_reason=OutboundTriggerReason.EVALUATOR_ERROR,
            )

        attempts_used = state.outbound_attempts + 1
        trigger_reason = decision.outbound_trigger_reason.value if decision.outbound_trigger_reason else None

        if decision.is_safe_output:
            logger.info("outbound_primary_completed", is_safe_output=True, attempts_used=attempts_used)
            return Command(
                update={
                    "is_safe_output": True,
                    "outbound_trigger_reason": None,
                    "constructive_redirect": None,
                    "final_response": draft,
                    "messages": [AIMessage(content=draft)],
                },
                goto="store_messages",
            )

        if attempts_used < MAX_OUTBOUND_ATTEMPTS:
            logger.info("outbound_regenerating", attempts_used=attempts_used, outbound_trigger_reason=trigger_reason)
            return Command(
                update={
                    "is_safe_output": False,
                    "outbound_trigger_reason": trigger_reason,
                    "constructive_redirect": decision.constructive_redirect,
                    "outbound_attempts": attempts_used,
                },
                goto="generate_hint",
            )

        redirect = decision.constructive_redirect or SAFE_TIMEOUT_RESPONSE
        logger.info("outbound_max_attempts_reached", attempts_used=attempts_used)
        return Command(
            update={
                "is_safe_output": False,
                "outbound_trigger_reason": trigger_reason,
                "constructive_redirect": decision.constructive_redirect,
                "final_response": redirect,
                "messages": [AIMessage(content=redirect)],
            },
            goto="store_messages",
        )

    # ------------------------------------------------------------------
    # Graph Compilation
    # ------------------------------------------------------------------
    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Build and compile the hint pipeline graph using node modules directly."""
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)

                # Add nodes directly from /app/core/langgraph/nodes
                graph_builder.add_node(
                    "secret_guardrail",
                    secret_guardrail_node,
                    destinations=("inbound_intent",),
                )
                graph_builder.add_node(
                    "inbound_intent",
                    inbound_intent_node,
                    destinations=("document_pipeline", "store_messages"),
                )
                graph_builder.add_node(
                    "document_pipeline", self._document_pipeline_wrapper, destinations=("context_retrieval",)
                )
                graph_builder.add_node(
                    "context_retrieval", self._context_retrieval_node, destinations=("generate_hint",)
                )
                graph_builder.add_node("generate_hint", generate_hint_node, destinations=("outbound",))
                graph_builder.add_node(
                    "outbound",
                    self._outbound_wrapper,
                    destinations=("store_messages", "generate_hint"),
                )
                graph_builder.add_node("store_messages", store_messages_node, destinations=("summarization",))
                graph_builder.add_node("summarization", summarization_node, destinations=(END,))

                # Set execution entry & finish points
                graph_builder.set_entry_point("secret_guardrail")
                graph_builder.set_finish_point("summarization")

                # Nodes returning plain state dicts (no Command) need explicit
                # edges; `destinations=` only declares valid Command targets
                # and does not route non-Command returns.
                graph_builder.add_edge("secret_guardrail", "inbound_intent")
                graph_builder.add_edge("generate_hint", "outbound")
                graph_builder.add_edge("store_messages", "summarization")

                connection_pool = await self._get_connection_pool()
                if connection_pool:
                    checkpointer = AsyncPostgresSaver(connection_pool)
                    await checkpointer.setup()
                else:
                    checkpointer = None
                    if settings.ENVIRONMENT != Environment.PRODUCTION:
                        raise Exception("Connection pool initialization failed")

                self._graph = graph_builder.compile(
                    checkpointer=checkpointer,
                    name=f"{settings.PROJECT_NAME} Hint Agent ({settings.ENVIRONMENT.value})",
                )

                logger.info(
                    "graph_created",
                    graph_name=f"{settings.PROJECT_NAME} Hint Agent",
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
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("graph initialization failed")
        return self._graph

    # ------------------------------------------------------------------
    # API Entrypoints
    # ------------------------------------------------------------------
    async def get_response(
        self,
        message: Message,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        code: Optional[str] = None,
        language: Optional[str] = None,
        pending_files: Optional[list] = None,
    ) -> list[Message]:
        """Get non-streamed assistant hint response."""
        graph = await self._get_graph()
        config = self._build_config(session_id, user_id, username)

        try:
            state = await graph.aget_state(config)

            if state.next:
                logger.info("resuming_interrupted_graph", session_id=session_id, next_nodes=state.next)
                response = await graph.ainvoke(Command(resume=message.content), config=config)
            else:
                graph_input = self._build_graph_input(message, code, language, pending_files)
                response = await graph.ainvoke(input=graph_input, config=config)

            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                return [Message(role="assistant", content=str(interrupt_value))]

            final_response = response.get("final_response") if isinstance(response, dict) else None
            if final_response:
                return [Message(role="assistant", content=str(final_response))]

            openai_msgs = cast(list[dict], convert_to_openai_messages(response["messages"]))
            assistant_msgs = [
                Message(role=msg["role"], content=str(msg["content"]))
                for msg in openai_msgs
                if msg["role"] == "assistant" and msg["content"]
            ]
            return [assistant_msgs[-1]] if assistant_msgs else []
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            return [Message(role="assistant", content=str(interrupt_value))]
        except Exception as e:
            logger.exception("get_response_failed", error=str(e), session_id=session_id)
            raise

    async def get_stream_response(
        self,
        message: Message,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        code: Optional[str] = None,
        language: Optional[str] = None,
        pending_files: Optional[list] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream assistant hint response tokens."""
        graph = await self._get_graph()
        config = self._build_config(session_id, user_id, username)

        try:
            state = await graph.aget_state(config)

            if state.next:
                logger.info("resuming_interrupted_graph_stream", session_id=session_id, next_nodes=state.next)
                graph_input = Command(resume=message.content)
            else:
                graph_input = self._build_graph_input(message, code, language, pending_files)

            async for token, chunk_metadata in graph.astream(
                graph_input,
                config,
                stream_mode="messages",
            ):
                if not isinstance(token, (AIMessage, AIMessageChunk)):
                    continue
                if not isinstance(chunk_metadata, dict) or chunk_metadata.get("langgraph_node") != "generate_hint":
                    continue

                text = extract_text_content(token.content)
                if text:
                    yield text

            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                yield str(interrupt_value)
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            yield str(interrupt_value)
        except Exception as stream_error:
            logger.exception("stream_processing_failed", error=str(stream_error), session_id=session_id)
            raise stream_error

    async def get_chat_history(self, session_id: str) -> list[Message]:
        """Get the chat history for a given thread ID."""
        graph = await self._get_graph()
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        state: StateSnapshot = await graph.aget_state(config=config)

        if state.values:
            messages = state.values.get("messages", [])
            return self._process_messages(messages)
        return []

    def _process_messages(self, messages: list[BaseMessage]) -> list[Message]:
        """Filter and convert internal BaseMessages to API Message objects."""
        openai_style_messages = convert_to_openai_messages(messages)
        return [
            Message(role=message["role"], content=str(message["content"]))
            for message in openai_style_messages
            if message["role"] in ["assistant", "user"] and message["content"]
        ]

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
            logger.error(
                "clear_chat_history_operation_failed",
                session_id=session_id,
                error=str(e),
            )
            raise

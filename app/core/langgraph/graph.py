"""This file contains the LangGraph Agent/workflow and interactions with the LLM."""

import asyncio
import hashlib
from typing import (
    Any,
    AsyncGenerator,
    Optional,
    cast,
)
from urllib.parse import quote_plus

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolCall,
    ToolMessage,
    convert_to_openai_messages,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import (
    END,
    StateGraph,
)
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import (
    Command,
    CompiledStateGraph,
)
from langgraph.types import (
    RetryPolicy,
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
from app.core.langgraph.nodes.correctness import correctness_node
from app.core.langgraph.nodes.hints import generate_hint_node
from app.core.langgraph.nodes.inbound_first_stage import inbound_dlp_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.core.langgraph.nodes.outbound import SAFE_TIMEOUT_RESPONSE, outbound_node
from app.core.langgraph.nodes.performance_node import performance_review_node
from app.core.langgraph.nodes.security_review import security_review_node
from app.core.langgraph.tools import tools
from app.core.logging import logger
from app.core.metrics import llm_inference_duration_seconds
from app.core.observability import langfuse_callback_handler
from app.core.prompts import load_system_prompt
from app.schemas import (
    GraphState,
    Message,
)
from app.schemas.review import InboundTriggerReason
from app.services.llm import llm_service
from app.services.memory import memory_service
from app.utils import (
    dump_messages,
    extract_text_content,
    prepare_messages,
    process_llm_response,
)

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

# Deterministic, non-preachy fallback redirects for a blocked inbound turn.
# Used verbatim for the Stage 1 (DLP) block, which never produces a
# `constructive_redirect`; used as a fallback for Stage 2 (Intent) blocks
# only when the judge itself failed to produce one (e.g. EVALUATOR_ERROR).
_INBOUND_REDIRECT_TEMPLATES: dict[InboundTriggerReason, str] = {
    InboundTriggerReason.SENSITIVE_DATA_EXPOSURE: (
        "I found what looks like a real credential in your message, so I didn't save or process it. "
        "Remove the secret (for example, move it into an environment variable) and send your code again — "
        "I'm ready to help once it's out."
    ),
    InboundTriggerReason.DLP_SCANNER_ERROR: (
        "I couldn't finish a safety check on your message just now, so I'm not processing it. "
        "Please try sending it again in a moment."
    ),
    InboundTriggerReason.SOLUTION_EXTRACTION: (
        "I won't hand over a complete, ready-to-submit solution — that's not how you'll actually learn this. "
        "Show me what you've tried so far and I'll help you find the next step."
    ),
    InboundTriggerReason.OFF_TOPIC: (
        "I'm built to help with programming, software engineering, and tech-career questions. "
        "Try rephrasing this as a coding or engineering question and I'm happy to dig in."
    ),
    InboundTriggerReason.HARMFUL_ILLEGAL: (
        "I can't help with that request. If you have a legitimate coding or security question, "
        "I'm glad to help with that instead."
    ),
    InboundTriggerReason.EVALUATOR_ERROR: SAFE_TIMEOUT_RESPONSE,
}


def _route_after_dlp(state: GraphState) -> str:
    """Route after Stage 1 (DLP). Fail closed if is_safe_sensitive is missing."""
    return "inbound_intent" if state.get("is_safe_sensitive", False) else "guardrail_redirect"


def _route_after_intent(state: GraphState) -> list[str] | str:
    """Route after Stage 2 (Intent). Fail closed if is_safe_intent is missing.

    Fans out to all three review nodes at once on the safe path -- they
    append to the shared `findings` list via operator.add, so running them
    concurrently is safe (see GraphState.findings).
    """
    if state.get("is_safe_intent", False):
        return ["correctness", "security", "performance"]
    return "guardrail_redirect"


class LangGraphAgent:
    """Manages the LangGraph Agent/workflow and interactions with the LLM.

    This class handles the creation and management of the LangGraph workflow,
    including LLM interactions, database connections, and response processing.
    """

    def __init__(self):
        """Initialize the LangGraph Agent with necessary components."""
        # Use the LLM service with tools bound
        self.llm_service = llm_service
        self.llm_service.bind_tools(tools)
        self.tools_by_name = {tool.name: tool for tool in tools}
        self._connection_pool: Optional[PostgresConnPool] = None
        self._graph: Optional[CompiledStateGraph] = None
        logger.info(
            "langgraph_agent_initialized",
            model=settings.DEFAULT_LLM_MODEL,
            environment=settings.ENVIRONMENT.value,
        )

    async def _get_connection_pool(self) -> Optional[PostgresConnPool]:
        """Get a PostgreSQL connection pool using environment-specific settings.

        Returns:
            AsyncConnectionPool or None when the pool fails to initialise in
            production (the app keeps running in a degraded mode).
        """
        if self._connection_pool is None:
            try:
                # Configure pool size based on environment
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
                # In production, we might want to degrade gracefully
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_connection_pool", environment=settings.ENVIRONMENT.value)
                    return None
                raise e
        return self._connection_pool

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    async def _guardrail_redirect(self, state: GraphState) -> Command:
        """Produce a deterministic, non-preachy redirect for an inbound-blocked turn.

        This is the single narrow-responsibility node both inbound stages
        route to when they block -- it never calls an LLM itself; it either
        uses the judge's own `constructive_redirect` (Stage 2) or a fixed
        template keyed by `inbound_trigger_reason` (Stage 1, or Stage 2 when
        the judge failed to produce one).

        Reads: inbound_trigger_reason, constructive_redirect
        Writes: messages (one AIMessage), final_response
        """
        reason = state.get("inbound_trigger_reason")
        template = (
            _INBOUND_REDIRECT_TEMPLATES[reason]
            if reason is not None and reason in _INBOUND_REDIRECT_TEMPLATES
            else _INBOUND_REDIRECT_TEMPLATES[InboundTriggerReason.EVALUATOR_ERROR]
        )
        text = state.get("constructive_redirect") or template
        return Command(update={"messages": [AIMessage(content=text)], "final_response": text}, goto=END)

    async def _hints(self, state: GraphState) -> dict[str, Any]:
        """Wrap generate_hint_node to skip hint generation when there's nothing to hint about.

        Per the design doc: "Hints run after findings exist — you can't give
        a useful hint about a problem you haven't identified yet." A turn
        with no submitted code and no findings (e.g. a general question) has
        nothing for the hint model to escalate, so this skips the LLM call
        entirely rather than generating a hint about nothing.

        Reads: code, findings (short-circuit check only -- generate_hint_node
            reads the full contract when it actually runs)
        Writes: latest_hint, hint_state (only when hint generation runs)
        """
        if not state.get("code") and not state.get("findings"):
            return {}
        # generate_hint_node predates the TypedDict rebuild and is typed against
        # Dict[str, Any]; GraphState (TypedDict(total=False)) *is* a plain dict
        # at runtime, so this cast is structurally sound, not a behavior change.
        return await generate_hint_node(cast(dict[str, Any], state))

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        """Compose the mentor's draft reply from the shared persona, this turn's code, review findings, and prepared hint.

        Reads: messages, code, language, long_term_memory, skill_profile,
            findings, latest_hint
        Writes: messages (AI message), draft_response
        Routes to: "tool_call" if the model asked for a tool, else "outbound"
            (the draft is never delivered directly -- it always passes
            through the outbound guardrail first).
        """
        # Get the current LLM instance for metrics
        current_llm = self.llm_service.get_llm()
        model_name = (
            current_llm.model_name
            if current_llm and hasattr(current_llm, "model_name")
            else settings.DEFAULT_LLM_MODEL
        )

        username = config.get("metadata", {}).get("username")
        thread_id = config.get("configurable", {}).get("thread_id")

        code = state.get("code")
        language = state.get("language")
        code_context = ""
        if code:
            code_context = (
                f"# Code submitted for review\nLanguage: {language or 'unknown'}\n```{language or ''}\n{code}\n```\n"
            )

        findings = state.get("findings") or []
        findings_context = ""
        if findings:
            rendered = "\n".join(
                f"- [{f.get('severity', '?')}/{f.get('category', '?')}] line {f.get('line', '?')}: {f.get('message', '')}"
                for f in findings
            )
            findings_context = f"# Review findings for this submission\n{rendered}\n"

        latest_hint = state.get("latest_hint")
        hint_context = ""
        if latest_hint:
            hint_context = (
                "# Progressive hint already prepared for this turn\n"
                "Ground your reply in this -- do not contradict it or reveal more than it does:\n"
                f"{latest_hint}\n"
            )

        SYSTEM_PROMPT = load_system_prompt(
            username=username,
            long_term_memory=state.get("long_term_memory", ""),
            code_context=code_context + findings_context + hint_context,
            skill_profile=state.get("skill_profile", "No Skill Profile for this user"),
        )

        # Prepare messages with system prompt.
        # prepare_messages predates this rebuild and is typed against the API's
        # Message schema (role/content), while the checkpointed graph state holds
        # LangChain BaseMessage objects (list[AnyMessage]). Both are pydantic
        # models whose dumped shape LangChain's own message conversion accepts
        # (it reads either "role" or "type"), so this is a pre-existing,
        # type-only mismatch -- not a behavior change introduced here.
        raw_messages = state.get("messages") or []
        messages = prepare_messages(cast(list[Message], raw_messages), SYSTEM_PROMPT)

        try:
            # Use LLM service with automatic retries and circular fallback
            with llm_inference_duration_seconds.labels(model=model_name).time():
                response_message = await self.llm_service.call(dump_messages(messages))

            # Process response to handle structured content blocks
            response_message = process_llm_response(response_message)

            logger.info(
                "llm_response_generated",
                session_id=thread_id,
                model=model_name,
                environment=settings.ENVIRONMENT.value,
            )

            # Determine next node based on whether there are tool calls
            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                return Command(update={"messages": [response_message]}, goto="tool_call")

            draft_text = extract_text_content(response_message.content)
            return Command(update={"messages": [response_message], "draft_response": draft_text}, goto="outbound")
        except Exception as e:
            logger.error(
                "llm_call_failed_all_models",
                session_id=thread_id,
                error=str(e),
                environment=settings.ENVIRONMENT.value,
            )
            raise Exception(f"failed to get llm response after trying all models: {str(e)}")

    async def _tool_call(self, state: GraphState) -> Command:
        """Process tool calls from the last message.

        Reads: messages (last message's tool_calls)
        Writes: messages (one ToolMessage per call)
        Routes to: "chat" (always -- the model gets a turn to use the result)
        """
        existing_messages = state.get("messages") or []
        last_message = existing_messages[-1] if existing_messages else None
        tool_calls: list[ToolCall] = last_message.tool_calls if isinstance(last_message, AIMessage) else []

        async def _execute_tool(tool_call: ToolCall) -> ToolMessage:
            tool_result = await self.tools_by_name[tool_call["name"]].ainvoke(tool_call["args"])
            return ToolMessage(
                content=tool_result,
                name=tool_call["name"],
                tool_call_id=tool_call["id"],
            )

        # Execute tool calls concurrently when multiple are requested
        if len(tool_calls) == 1:
            outputs = [await _execute_tool(tool_calls[0])]
        else:
            outputs = list(await asyncio.gather(*[_execute_tool(tc) for tc in tool_calls]))

        return Command(update={"messages": outputs}, goto="chat")

    async def _outbound(self, state: GraphState) -> dict[str, Any]:
        """Run the outbound guardrail, then repair the persisted transcript if it blocks.

        outbound_node itself only decides what text should be *delivered*
        (`final_response`); it doesn't touch `messages`. Without this
        wrapper, a blocked draft (e.g. a full-solution leak) would still sit
        in `state["messages"]` forever -- checkpointed, fed back to the LLM
        as context on the next turn, and pushed to long-term memory. This
        replaces that message in place (same message `id`, so the
        `add_messages` reducer overwrites rather than appends) whenever the
        draft is blocked.

        Reads: draft_response, sanitized_query, sanitized_code (via outbound_node)
        Writes: is_safe_output, outbound_trigger_reason, constructive_redirect,
            final_response, and -- only when blocked -- messages
        """
        # outbound_node predates the TypedDict rebuild and is typed against
        # dict[str, Any]; GraphState *is* a plain dict at runtime, so this cast
        # is structurally sound, not a behavior change.
        result = await outbound_node(cast(dict[str, Any], state))
        if not result.get("is_safe_output", True):
            existing_messages = state.get("messages") or []
            draft_ai_message = existing_messages[-1] if existing_messages else None
            safe_text = result.get("final_response") or SAFE_TIMEOUT_RESPONSE
            message_id = getattr(draft_ai_message, "id", None)
            result["messages"] = [
                AIMessage(content=safe_text, id=message_id) if message_id else AIMessage(content=safe_text)
            ]
        return result

    # ------------------------------------------------------------------
    # Graph assembly
    # ------------------------------------------------------------------

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Create and configure the LangGraph workflow.

        Shape (see docs/graph_state_contract.md for the full diagram):

            inbound_dlp --(safe)--> inbound_intent --(safe)--> [correctness, security, performance]
                |--(blocked)--> guardrail_redirect --> END          |--(blocked)--> guardrail_redirect --> END
                                                                     v
                                                                   hints --> chat --(tool call)--> tool_call --> chat
                                                                              |--(draft ready)--> outbound --> END

        Returns:
            Optional[CompiledStateGraph]: The configured LangGraph instance or None if init fails
        """
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)

                # Inbound guardrail perimeter (Stage 1: DLP, Stage 2: Intent)
                # inbound_dlp_node/inbound_intent_node/security_review_node/
                # performance_review_node predate the TypedDict rebuild and are
                # typed against dict[str, Any]; GraphState *is* a plain dict at
                # runtime, so LangGraph invokes them identically either way --
                # the cast below only satisfies add_node's static signature
                # check, it changes no behavior.
                graph_builder.add_node("inbound_dlp", cast(Any, inbound_dlp_node))
                graph_builder.add_node("inbound_intent", cast(Any, inbound_intent_node))
                graph_builder.add_node("guardrail_redirect", self._guardrail_redirect, destinations=(END,))

                # Parallel review lanes -- each appends to `findings` (operator.add)
                graph_builder.add_node("correctness", correctness_node)
                graph_builder.add_node("security", cast(Any, security_review_node))
                graph_builder.add_node("performance", cast(Any, performance_review_node))

                # Hint escalation
                graph_builder.add_node("hints", self._hints)

                # Chat (single user-facing voice) + tool loop + outbound guardrail
                graph_builder.add_node("chat", self._chat, destinations=("tool_call", "outbound"))
                graph_builder.add_node(
                    "tool_call",
                    self._tool_call,
                    destinations=("chat",),
                    retry_policy=RetryPolicy(max_attempts=3),
                )
                graph_builder.add_node("outbound", self._outbound)

                graph_builder.set_entry_point("inbound_dlp")

                graph_builder.add_conditional_edges("inbound_dlp", _route_after_dlp)
                graph_builder.add_conditional_edges("inbound_intent", _route_after_intent)
                # Fan-in: "hints" only runs once all three review lanes have completed.
                graph_builder.add_edge(["correctness", "security", "performance"], "hints")
                graph_builder.add_edge("hints", "chat")
                graph_builder.add_edge("outbound", END)

                # Get connection pool (may be None in production if DB unavailable)
                connection_pool = await self._get_connection_pool()
                if connection_pool:
                    checkpointer = AsyncPostgresSaver(connection_pool)
                    await checkpointer.setup()
                else:
                    # In production, proceed without checkpointer if needed
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
                # In production, we don't want to crash the app
                if settings.ENVIRONMENT == Environment.PRODUCTION:
                    logger.warning("continuing_without_graph")
                    return None
                raise e

        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        """Return the compiled graph, creating it on first access.

        Raises:
            RuntimeError: When ``create_graph()`` swallowed an init failure
                (production-only path) and returned ``None``. Callers can
                rely on the return being non-``None``.
        """
        if self._graph is None:
            self._graph = await self.create_graph()
        if self._graph is None:
            raise RuntimeError("graph initialization failed")
        return self._graph

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _build_graph_input(
        messages: list[Message],
        relevant_memory: str,
        code: Optional[str],
        language: Optional[str],
        skill_profile_text: str,
    ) -> dict[str, Any]:
        """Build the initial GraphState input dict shared by both entry points."""
        latest_user_text = messages[-1].content if messages else ""
        problem_id = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16] if code else None
        return {
            "messages": dump_messages(messages),
            "long_term_memory": relevant_memory or "No relevant memory found.",
            "code": code,
            "language": language,
            "skill_profile": skill_profile_text,
            "user_query": latest_user_text,
            "problem_id": problem_id,
        }

    async def get_response(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        code: Optional[str] = None,
        language: Optional[str] = None,
    ) -> list[Message]:
        """Get a response from the LLM.

        Args:
            messages (list[Message]): The messages to send to the LLM.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            code (Optional[str]): The code snippet submitted for review.
            language (Optional[str]): The programming language of the submitted code.

        Returns:
            list[Message]: The response from the LLM.
        """
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

        try:
            # Run state check and memory search concurrently to save 200-500ms
            state, relevant_memory, skill_profile = await asyncio.gather(
                graph.aget_state(config),
                memory_service.search(user_id, messages[-1].content),
                memory_service.get_skill_profile(user_id),
            )

            if state.next:
                logger.info("resuming_interrupted_graph", session_id=session_id, next_nodes=state.next)
                graph_input = Command(resume=messages[-1].content)
            else:
                graph_input = self._build_graph_input(
                    messages, relevant_memory, code, language, memory_service._profile_to_text(skill_profile)
                )

            response = await graph.ainvoke(graph_input, config=config)

            # Check if the graph was interrupted during this invocation
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
                return [Message(role="assistant", content=str(interrupt_value))]

            # A raw-credential (DLP) block never reaches long-term memory --
            # the whole point of the short-circuit is that the secret is
            # never given a chance to be embedded/searchable in mem0.
            if response.get("is_safe_sensitive") is not False and "messages" in response:
                openai_msgs = cast(list[dict], convert_to_openai_messages(response["messages"]))
                asyncio.create_task(memory_service.add(user_id, openai_msgs, config.get("metadata")))
            return self.__process_messages(response["messages"])
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
            return [Message(role="assistant", content=str(interrupt_value))]
        except Exception as e:
            logger.exception("get_response_failed", error=str(e), session_id=session_id)
            raise

    @staticmethod
    async def _chunk_text(text: str, *, delay_seconds: float = 0.02) -> AsyncGenerator[str, None]:
        """Yield already-approved text as incremental word chunks.

        Used instead of forwarding raw provider tokens so that (a) the
        outbound guardrail can veto a full-solution leak *before* any of it
        reaches the client, and (b) internal judge/review LLM calls (intent,
        outbound, security, performance, hints) never leak their own
        token streams into the user-facing channel -- only the graph's
        final, approved `final_response` is ever sent. See
        docs/graph_state_contract.md, "Streaming vs. the outbound guardrail",
        for the full rationale.
        """
        if not text:
            return
        words = text.split(" ")
        last_index = len(words) - 1
        for index, word in enumerate(words):
            yield word if index == last_index else f"{word} "
            await asyncio.sleep(delay_seconds)

    async def get_stream_response(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        code: Optional[str] = None,
        language: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Get a stream response from the LLM.

        Args:
            messages (list[Message]): The messages to send to the LLM.
            session_id (str): The session ID for the conversation.
            user_id (Optional[str]): The user ID for the conversation.
            username (Optional[str]): The display name of the user.
            code (Optional[str]): The code snippet submitted for review.
            language (Optional[str]): The programming language of the submitted code.

        Yields:
            str: Incremental chunks of the final, outbound-approved response.
        """
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
        graph = await self._get_graph()

        try:
            # Run state check and memory search concurrently to save 200-500ms
            state, relevant_memory, skill_profile = await asyncio.gather(
                graph.aget_state(config),
                memory_service.search(user_id, messages[-1].content),
                memory_service.get_skill_profile(user_id),
            )

            if state.next:
                logger.info("resuming_interrupted_graph_stream", session_id=session_id, next_nodes=state.next)
                graph_input = Command(resume=messages[-1].content)
            else:
                graph_input = self._build_graph_input(
                    messages, relevant_memory, code, language, memory_service._profile_to_text(skill_profile)
                )

            # The graph runs to completion rather than forwarding raw
            # provider tokens -- see _chunk_text's docstring.
            response = await graph.ainvoke(graph_input, config=config)

            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
                async for chunk in self._chunk_text(str(interrupt_value)):
                    yield chunk
                return

            final_text = response.get("final_response")
            if not final_text:
                # Fallback for any path that exits without setting final_response.
                ai_messages = [m for m in response.get("messages", []) if isinstance(m, AIMessage)]
                final_text = extract_text_content(ai_messages[-1].content) if ai_messages else ""

            async for chunk in self._chunk_text(final_text):
                yield chunk

            if response.get("is_safe_sensitive") is not False and "messages" in response:
                openai_msgs = cast(list[dict], convert_to_openai_messages(response["messages"]))
                asyncio.create_task(memory_service.add(user_id, openai_msgs, config.get("metadata")))
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
            async for chunk in self._chunk_text(str(interrupt_value)):
                yield chunk
        except Exception as stream_error:
            logger.exception("stream_processing_failed", error=str(stream_error), session_id=session_id)
            raise stream_error

    async def get_chat_history(self, session_id: str) -> list[Message]:
        """Get the chat history for a given thread ID.

        Args:
            session_id (str): The session ID for the conversation.

        Returns:
            list[Message]: The chat history.
        """
        graph = await self._get_graph()

        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        state: StateSnapshot = await graph.aget_state(config=config)
        return self.__process_messages(state.values["messages"]) if state.values else []

    def __process_messages(self, messages: list[BaseMessage]) -> list[Message]:
        openai_style_messages = convert_to_openai_messages(messages)
        # keep just assistant and user messages
        return [
            Message(role=message["role"], content=str(message["content"]))
            for message in openai_style_messages
            if message["role"] in ["assistant", "user"] and message["content"]
        ]

    async def clear_chat_history(self, session_id: str) -> None:
        """Clear all chat history for a given thread ID.

        Args:
            session_id: The ID of the session to clear history for.

        Raises:
            Exception: If there's an error clearing the chat history.
        """
        try:
            # Make sure the pool is initialized in the current event loop
            conn_pool = await self._get_connection_pool()
            if conn_pool is None:
                raise RuntimeError("connection pool unavailable; cannot clear chat history")

            # Batch all DELETEs in a single pipeline round-trip
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

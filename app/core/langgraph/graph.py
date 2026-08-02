"""This file contains the LangGraph Agent/workflow and interactions with the LLM."""

import asyncio
import hashlib
import json
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
from app.core.langgraph.nodes.inbound_first_stage import inbound_dlp_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.core.langgraph.nodes.outbound import SAFE_TIMEOUT_RESPONSE, outbound_node
from app.core.langgraph.nodes.correctness import correctness_node
from app.core.langgraph.nodes.performance_node import performance_review_node
from app.core.langgraph.nodes.security_review import security_review_node
from app.core.langgraph.subagent import summarize_tool_output
from app.core.langgraph.tools import tools
from app.core.langgraph.tools import agent_tools
from app.core.logging import logger
from app.core.metrics import llm_inference_duration_seconds
from app.core.observability import get_langfuse_callback_handler
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

_INBOUND_REDIRECT_TEMPLATES: dict[InboundTriggerReason, str] = {
    InboundTriggerReason.SENSITIVE_DATA_EXPOSURE: (
        "Please remove API keys, passwords, tokens, and other credentials from the code before sharing it. "
        "I can then help you review the sanitized version."
    ),
    InboundTriggerReason.DLP_SCANNER_ERROR: (
        "I couldn't safely inspect that submission. Please remove any credentials and try again with a sanitized "
        "code snippet."
    ),
    InboundTriggerReason.SOLUTION_EXTRACTION: (
        "Share your current attempt or the part you understand least, and I can guide you without providing a "
        "ready-to-submit solution."
    ),
    InboundTriggerReason.OFF_TOPIC: "Please keep your question focused on software engineering or computer science.",
    InboundTriggerReason.HARMFUL_ILLEGAL: (
        "I can help with safe, defensive software engineering questions, debugging, and secure design."
    ),
    InboundTriggerReason.EVALUATOR_ERROR: (
        "I couldn't safely classify that request. Please rephrase it as a specific software engineering or "
        "debugging question."
    ),
}


def _route_after_dlp(state: GraphState) -> str:
    """Send DLP-safe requests to intent evaluation and block unsafe requests."""
    return "inbound_intent" if state.get("is_safe_sensitive", False) else "guardrail_redirect"


def _route_after_intent(state: GraphState) -> str | list[str]:
    """Fan out to all review lanes only after the inbound intent check passes."""
    if not state.get("is_safe_intent", False):
        return "guardrail_redirect"
    return ["review_correctness", "review_security", "review_performance"]


_CATEGORY_PRIORITY = {"correctness": 0, "security": 1, "performance": 2, "style": 3}
_SEVERITY_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _review_priority(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _merge_review_findings(state: GraphState) -> dict[str, Any]:
    """Merge parallel review lanes into ordered context for the agent."""
    findings = list(state.get("findings") or [])
    ordered_findings = sorted(
        findings,
        key=lambda finding: (
            _CATEGORY_PRIORITY.get(_review_priority(finding.get("category")), 99),
            _SEVERITY_PRIORITY.get(_review_priority(finding.get("severity")), 99),
            finding.get("line", 0),
        ),
    )
    return {"review_findings": ordered_findings, "review_complete": True}


async def _correctness_review_lane(state: GraphState) -> dict[str, Any]:
    """Run synchronous correctness analysis without blocking the event loop."""
    return await asyncio.to_thread(correctness_node, state)


class LangGraphAgent:
    """Manages the LangGraph Agent/workflow and interactions with the LLM.

    This class handles the creation and management of the LangGraph workflow,
    including LLM interactions, database connections, and response processing.
    """

    def __init__(self):
        """Initialize the LangGraph Agent with necessary components."""
        self.llm_service = llm_service
        # Tool binding belongs to this agent runtime. The shared LLM service
        # only supplies the current unbound model instance.
        self.agent_tools = list(agent_tools)
        self.tools_by_name = {tool.name: tool for tool in self.agent_tools}
        self._agent_model: Any = None
        self._agent_model_source: Any = None
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

    @staticmethod
    def _sanitize_messages_for_agent(messages: list[Any], sanitized_query: str) -> list[Any]:
        """Replace the latest human message with the DLP-sanitized query."""
        sanitized_messages = list(messages)
        for index in range(len(sanitized_messages) - 1, -1, -1):
            message = sanitized_messages[index]
            if isinstance(message, BaseMessage):
                is_human = message.type in {"human", "user"}
                if is_human:
                    sanitized_messages[index] = message.model_copy(update={"content": sanitized_query})
                    break
            elif isinstance(message, dict):
                role = message.get("role", message.get("type"))
                if role in {"human", "user"}:
                    sanitized_message = dict(message)
                    sanitized_message["content"] = sanitized_query
                    sanitized_messages[index] = sanitized_message
                    break
        return sanitized_messages

    @staticmethod
    def _ensure_syntax_blockers_in_response(
        response_message: BaseMessage, state_messages: list[Any], review_findings: Optional[list[dict[str, Any]]] = None
    ) -> BaseMessage:
        """Preserve syntax blockers when the final model summary omits them."""
        if not isinstance(response_message, AIMessage):
            return response_message

        for message in reversed(state_messages):
            if not isinstance(message, ToolMessage) or message.name != "review_code":
                continue
            try:
                review_payload = json.loads(extract_text_content(message.content))
            except (TypeError, ValueError):
                return response_message

            syntax_blockers = review_payload.get("syntax_blockers", [])
            if not syntax_blockers:
                return response_message

            response_text = extract_text_content(response_message.content)
            if "syntax" in response_text.lower():
                return response_message

            blocker_lines = "\n".join(
                f"- Line {finding.get('line', '?')}: {finding.get('message', 'Syntax error detected.')}"
                for finding in syntax_blockers
            )
            response_message.content = (
                "Syntax blockers (the program cannot run until these are addressed):\n"
                f"{blocker_lines}\n\n{response_text}"
            )
            return response_message

        lane_blockers = [
            finding
            for finding in (review_findings or [])
            if _review_priority(finding.get("category")) == "correctness"
            and "syntax" in str(finding.get("message", "")).lower()
        ]
        if lane_blockers:
            response_text = extract_text_content(response_message.content)
            if "syntax" not in response_text.lower():
                blocker_lines = "\n".join(
                    f"- Line {finding.get('line', '?')}: {finding.get('message', 'Syntax error detected.')}"
                    for finding in lane_blockers
                )
                response_message.content = (
                    "Syntax blockers (the program cannot run until these are addressed):\n"
                    f"{blocker_lines}\n\n{response_text}"
                )
            return response_message

        return response_message

    async def _guardrail_redirect(self, state: GraphState) -> Command:
        """Return a safe, deterministic response for an inbound-blocked turn."""
        reason = state.get("inbound_trigger_reason")
        template = _INBOUND_REDIRECT_TEMPLATES.get(
            reason or InboundTriggerReason.EVALUATOR_ERROR,
            _INBOUND_REDIRECT_TEMPLATES[InboundTriggerReason.EVALUATOR_ERROR],
        )
        response_text = state.get("constructive_redirect") or template
        message_updates: list[Any] = []
        existing_messages = list(state.get("messages") or [])
        sanitized_query = state.get("sanitized_query")
        if isinstance(sanitized_query, str) and existing_messages:
            sanitized_messages = self._sanitize_messages_for_agent(existing_messages, sanitized_query)
            for original, sanitized in zip(existing_messages, sanitized_messages):
                if sanitized is not original:
                    message_updates.append(sanitized)
                    break
        message_updates.append(AIMessage(content=response_text))
        return Command(
            update={"messages": message_updates, "final_response": response_text},
            goto=END,
        )

    async def _outbound(self, state: GraphState) -> dict[str, Any]:
        """Validate the draft and replace blocked text before checkpointing it."""
        result = await outbound_node(cast(dict[str, Any], state))
        if not result.get("is_safe_output", True):
            existing_messages = state.get("messages") or []
            draft_message = existing_messages[-1] if existing_messages else None
            safe_text = result.get("final_response") or SAFE_TIMEOUT_RESPONSE
            message_id = getattr(draft_message, "id", None)
            result["messages"] = [
                AIMessage(content=safe_text, id=message_id) if message_id else AIMessage(content=safe_text)
            ]
        return result

    def _get_agent_model(self) -> Any:
        """Return the current model with this agent's tools bound locally."""
        source_model = self.llm_service.get_llm()
        if source_model is None:
            return None
        if source_model is not self._agent_model_source:
            bind_tools = getattr(source_model, "bind_tools", None)
            self._agent_model = bind_tools(self.agent_tools) if callable(bind_tools) else source_model
            self._agent_model_source = source_model
        return self._agent_model

    async def _call_agent_model(self, messages: Any) -> BaseMessage:
        """Invoke the locally tool-bound model, with a test-service fallback."""
        agent_model = self._get_agent_model()
        if agent_model is not None and hasattr(agent_model, "ainvoke"):
            return cast(BaseMessage, await agent_model.ainvoke(messages))
        return cast(BaseMessage, await self.llm_service.call(messages))

    async def _inbound_memory_query(self, messages: list[Message], code: Optional[str]) -> str:
        """Return a DLP-sanitized query for memory search before graph execution."""
        latest_user_text = messages[-1].content if messages else ""
        problem_id = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16] if code else None
        dlp_result = await inbound_dlp_node(
            {"user_query": latest_user_text, "code": code, "problem_id": problem_id}
        )
        if not dlp_result.get("is_safe_sensitive", False):
            return ""
        sanitized_query = dlp_result.get("sanitized_query")
        return sanitized_query if isinstance(sanitized_query, str) else ""

    async def _agent(self, state: GraphState | dict[str, Any], config: RunnableConfig) -> Command:
        """Run one ReAct decision step owned by this agent.

        The graph intentionally has only this node. A model response containing
        tool calls is committed first and routes back to this node; the next
        invocation executes those pending calls and routes back again for the
        model's observation/reasoning step. Keeping that checkpoint boundary is
        important for ``ask_human`` interrupts to resume safely.

        Args:
            state (GraphState): The current state of the conversation.
            config (RunnableConfig): The runnable configuration for this invocation.

        Returns:
            Command: Command object with updated state and next node to execute.
        """
        # GraphState is a TypedDict, so LangGraph supplies a normal mapping at
        # runtime. Keep the node independent of any schema implementation
        # details and use mapping access throughout.
        state_values = cast(dict[str, Any], state)
        state_messages = state_values.get("messages") or []
        last_message = state_messages[-1] if state_messages else None
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            async def _execute_tool(tool_call: dict) -> ToolMessage:
                tool = self.tools_by_name.get(tool_call["name"])
                if tool is None:
                    raise ValueError(f"unknown tool requested: {tool_call['name']}")

                tool_args = dict(tool_call["args"])
                if tool_call["name"] == "review_code" and not tool_args.get("language"):
                    request_language = state_values.get("language")
                    if isinstance(request_language, str) and request_language:
                        tool_args["language"] = request_language

                tool_result = await tool.ainvoke(tool_args)
                return ToolMessage(
                    content=tool_result,
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )

            # Execute independent tool calls concurrently, preserving the
            # existing behavior while keeping execution under agent control.
            if len(last_message.tool_calls) == 1:
                outputs = [await _execute_tool(last_message.tool_calls[0])]
            else:
                outputs = list(await asyncio.gather(*[_execute_tool(tc) for tc in last_message.tool_calls]))

            return Command(update={"messages": outputs}, goto="agent")

        # Get the current LLM instance for metrics
        current_llm = self.llm_service.get_llm()
        model_name = (
            current_llm.model_name
            if current_llm and hasattr(current_llm, "model_name")
            else settings.DEFAULT_LLM_MODEL
        )

        username = config.get("metadata", {}).get("username")
        thread_id = config.get("configurable", {}).get("thread_id")
        # func chat wasnt reading except state.messeges
        code_context = ""
        code = state_values.get("sanitized_code") or state_values.get("code")
        language = state_values.get("language")
        if code:
            code_context = (
                f"# Code submitted for review\n"
                f"Language: {language or 'unknown'}\n"
                f"```{language or ''}\n{code}\n```\n"
            )
        review_findings = state_values.get("review_findings") or []
        if review_findings:
            code_context += f"Review findings from parallel lanes:\n{json.dumps(review_findings)}\n"

        SYSTEM_PROMPT = load_system_prompt(
            username=username,
            long_term_memory=state_values.get("long_term_memory", ""),
            code_context=code_context,
            skill_profile=state_values.get("skill_profile", "No Skill Profile for this user"),
        )

        # Prepare messages with system prompt. The inbound DLP result is the
        # only user content allowed to reach the agent after the perimeter.
        sanitized_query = state_values.get("sanitized_query")
        agent_messages = (
            self._sanitize_messages_for_agent(state_messages, sanitized_query)
            if isinstance(sanitized_query, str)
            else state_messages
        )
        messages = prepare_messages(cast(list[Message], agent_messages), SYSTEM_PROMPT)

        try:
            # Invoke the locally tool-bound agent model.
            with llm_inference_duration_seconds.labels(model=model_name).time():
                response_message = await self._call_agent_model(dump_messages(messages))

            # Process response to handle structured content blocks
            response_message = process_llm_response(response_message)

            logger.info(
                "llm_response_generated",
                session_id=thread_id,
                model=model_name,
                environment=settings.ENVIRONMENT.value,
            )

            # Keep reasoning and tool use inside the same graph node. The
            # model message is checkpointed before the next agent step runs.
            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                return Command(update={"messages": [response_message]}, goto="agent")

            response_message = self._ensure_syntax_blockers_in_response(
                response_message,
                state_messages,
                state_values.get("review_findings"),
            )
            draft_response = extract_text_content(response_message.content)
            return Command(
                update={"messages": [response_message], "draft_response": draft_response},
                goto="outbound",
            )
        except Exception as e:
            logger.error(
                "llm_call_failed_all_models",
                session_id=thread_id,
                error=str(e),
                environment=settings.ENVIRONMENT.value,
            )
            raise Exception(f"failed to get llm response after trying all models: {str(e)}")

    async def create_graph(self) -> Optional[CompiledStateGraph]:
        """Create and configure the LangGraph workflow.

        The graph has an inbound safety perimeter, an agent-owned ReAct loop,
        and an outbound safety perimeter:

            inbound_dlp -> inbound_intent -> parallel reviews -> agent <-> agent -> outbound -> END
                  |              |                  |                  |
                  +--------------+------------------+--> redirect -----+

        Returns:
            Optional[CompiledStateGraph]: The configured LangGraph instance or None if init fails
        """
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)

                graph_builder.add_node("inbound_dlp", cast(Any, inbound_dlp_node))
                graph_builder.add_node("inbound_intent", cast(Any, inbound_intent_node))
                graph_builder.add_node("guardrail_redirect", self._guardrail_redirect, destinations=(END,))
                graph_builder.add_node("review_correctness", _correctness_review_lane)
                graph_builder.add_node("review_security", security_review_node)
                graph_builder.add_node("review_performance", performance_review_node)
                graph_builder.add_node("merge_reviews", _merge_review_findings)
                graph_builder.add_node(
                    "agent",
                    self._agent,
                    destinations=("agent", "outbound"),
                    retry_policy=RetryPolicy(max_attempts=3),
                )
                graph_builder.add_node("outbound", self._outbound)
                graph_builder.set_entry_point("inbound_dlp")
                graph_builder.add_conditional_edges("inbound_dlp", _route_after_dlp)
                graph_builder.add_conditional_edges("inbound_intent", _route_after_intent)
                graph_builder.add_edge("review_correctness", "merge_reviews")
                graph_builder.add_edge("review_security", "merge_reviews")
                graph_builder.add_edge("review_performance", "merge_reviews")
                graph_builder.add_edge("merge_reviews", "agent")
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

    @staticmethod
    def _build_graph_input(
        messages: list[Message],
        relevant_memory: str,
        code: Optional[str],
        language: Optional[str],
        skill_profile_text: str,
    ) -> dict[str, Any]:
        """Build the initial state shared by the response entry points."""
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
        callbacks: list[BaseCallbackHandler] = (
            [get_langfuse_callback_handler()] if settings.LANGFUSE_TRACING_ENABLED else []
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            # Each tool round uses two agent steps, plus inbound DLP/intent and
            # outbound validation. Bound the loop so tool-happy behavior cannot
            # run indefinitely.
            "recursion_limit": max(5, settings.AGENT_MAX_STEPS * 2 + 4),
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
            safe_memory_query = await self._inbound_memory_query(messages, code)
            # Run state check and memory search concurrently to save 200-500ms
            state, relevant_memory, skill_profile = await asyncio.gather(
                graph.aget_state(config),
                memory_service.search(user_id, safe_memory_query)
                if safe_memory_query
                else asyncio.sleep(0, result=""),
                memory_service.get_skill_profile(user_id),
            )

            if state.next:
                logger.info("resuming_interrupted_graph", session_id=session_id, next_nodes=state.next)
                response = await graph.ainvoke(
                    Command(resume=messages[-1].content),
                    config=config,
                )
            else:
                relevant_memory = relevant_memory or "No relevant memory found."
                response = await graph.ainvoke(
                    input=self._build_graph_input(
                        messages,
                        relevant_memory,
                        code,
                        language,
                        memory_service._profile_to_text(skill_profile),
                    ),
                    config=config,
                )

            # Check if the graph was interrupted during this invocation
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
                return [Message(role="assistant", content=str(interrupt_value))]

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
            str: Tokens of the LLM response.
        """
        callbacks: list[BaseCallbackHandler] = (
            [get_langfuse_callback_handler()] if settings.LANGFUSE_TRACING_ENABLED else []
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": max(5, settings.AGENT_MAX_STEPS * 2 + 4),
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
            safe_memory_query = await self._inbound_memory_query(messages, code)
            # Run state check and memory search concurrently to save 200-500ms
            state, relevant_memory, skill_profile = await asyncio.gather(
                graph.aget_state(config),
                memory_service.search(user_id, safe_memory_query)
                if safe_memory_query
                else asyncio.sleep(0, result=""),
                memory_service.get_skill_profile(user_id),
            )

            if state.next:
                logger.info("resuming_interrupted_graph_stream", session_id=session_id, next_nodes=state.next)
                graph_input = Command(resume=messages[-1].content)
            else:
                relevant_memory = relevant_memory or "No relevant memory found."
                graph_input = self._build_graph_input(
                    messages,
                    relevant_memory,
                    code,
                    language,
                    memory_service._profile_to_text(skill_profile),
                )

            # Invoke to completion before yielding anything. The outbound
            # guardrail must approve the complete draft before a single token
            # reaches the client; otherwise a blocked draft could leak through
            # the SSE stream. Re-chunk the approved response for compatibility
            # with the streaming API.
            response = await graph.ainvoke(graph_input, config=config)

            # After invocation, check for interrupt or update memory.
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
                yield str(interrupt_value)
            elif state.values and "messages" in state.values:
                final_response = response.get("final_response") or state.values.get("final_response")
                if final_response:
                    for start in range(0, len(str(final_response)), 256):
                        yield str(final_response)[start : start + 256]
                openai_msgs = cast(list[dict], convert_to_openai_messages(response["messages"]))
                asyncio.create_task(memory_service.add(user_id, openai_msgs, config.get("metadata")))
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
            yield str(interrupt_value)
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

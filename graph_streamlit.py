r"""Visual smoke test for the public graph APIs without external services.

Run with:

    .venv\\Scripts\\python.exe -m streamlit run graph_streamlit.py

The app uses the real ``LangGraphAgent.get_response`` and
``get_stream_response`` methods, while replacing PostgreSQL checkpointing,
the LLM, and mem0 with deterministic in-memory fakes.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

import app.core.langgraph.graph as graph_module
from app.core.langgraph.graph import LangGraphAgent
from app.schemas import Message
from app.schemas.graph import GraphState


class MockMemoryService:
    """Small deterministic memory replacement used by the visual smoke test."""

    def __init__(self) -> None:
        """Seed the mock with one relevant user memory."""
        self.entries: list[dict[str, Any]] = [
            {
                "user_id": "streamlit-user",
                "content": "The user is learning Python performance optimization.",
            }
        ]

    async def search(self, user_id: str | None, query: str) -> str:
        """Return deterministic memories for the demo user."""
        if user_id != "streamlit-user":
            return ""
        return "\n".join(f"* {entry['content']}" for entry in self.entries)

    async def get_skill_profile(self, user_id: str | None) -> None:
        """Return no persisted skill profile for the demo."""
        return None

    async def add(self, user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        """Record the latest assistant message as a mock memory entry."""
        if user_id is None:
            return
        assistant_messages = [message for message in messages if message.get("role") == "assistant"]
        if assistant_messages:
            self.entries.append({"user_id": user_id, "content": str(assistant_messages[-1]["content"])})

    def _profile_to_text(self, _profile: object) -> str:
        return "Skill level: intermediate. Weaknesses: none identified. Topics explored: Python performance."


class FakeTool:
    """Deterministic search tool exposed to the agent."""

    name = "fake_search"

    async def ainvoke(self, args: dict[str, Any]) -> str:
        """Return a deterministic result for the requested query."""
        return f"Mock result for: {args['query']}"


class FakeLLMService:
    """Deterministic model that demonstrates a tool decision and observation."""

    def __init__(self, use_tool: bool):
        """Configure whether the first model turn emits a tool call."""
        self.calls = 0
        self.use_tool = use_tool

    def get_llm(self) -> SimpleNamespace:
        """Return model metadata used by the agent metrics path."""
        return SimpleNamespace(model_name="streamlit-fake-model")

    async def call(self, _messages: object) -> AIMessage:
        """Return a tool request once, followed by a final answer."""
        self.calls += 1
        if self.use_tool and self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fake_search",
                        "args": {"query": "Python performance"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="Final answer generated after the agent observed the tool result.")


def build_agent(use_tool: bool) -> tuple[LangGraphAgent, MockMemoryService, FakeLLMService]:
    """Build the real agent with only external dependencies replaced."""
    mock_memory = MockMemoryService()
    graph_module.memory_service = mock_memory

    agent = LangGraphAgent()
    fake_llm = FakeLLMService(use_tool)
    agent.llm_service = fake_llm
    agent.tools_by_name = {"fake_search": FakeTool()}

    builder = StateGraph(GraphState)
    builder.add_node("agent", agent._agent, destinations=("agent", END))
    builder.set_entry_point("agent")
    agent._graph = builder.compile(checkpointer=InMemorySaver())
    return agent, mock_memory, fake_llm


async def run_agent(prompt: str, use_tool: bool, stream: bool) -> dict[str, Any]:
    """Run one public API call and collect its checkpointed trace."""
    agent, mock_memory, fake_llm = build_agent(use_tool)
    request = [Message(role="user", content=prompt)]
    session_id = "streamlit-test"

    if stream:
        chunks = [
            chunk
            async for chunk in agent.get_stream_response(
                request,
                session_id=session_id,
                user_id="streamlit-user",
            )
        ]
        response = None
    else:
        response = await agent.get_response(
            request,
            session_id=session_id,
            user_id="streamlit-user",
        )
        chunks = []

    # Public methods enqueue memory writes in the background. Give the local
    # task one scheduling turn so the mock can display the recorded response.
    await asyncio.sleep(0)
    checkpoint = await agent._graph.aget_state({"configurable": {"thread_id": session_id}})
    return {
        "response": response,
        "chunks": chunks,
        "trace": checkpoint.values.get("messages", []) if checkpoint.values else [],
        "memory": mock_memory.entries,
        "llm_calls": fake_llm.calls,
    }


st.set_page_config(page_title="Agent Workflow Tester", page_icon="AG")
st.title("Agent workflow tester")
st.caption("Real LangGraphAgent APIs with an in-memory checkpointer, mock LLM, and mock memory")

prompt = st.text_area("User message", "Search for Python performance information.")
use_tool = st.checkbox("Let the agent call a tool", value=True)
stream = st.checkbox("Use streaming API", value=True)

if st.button("Run agent", type="primary"):
    with st.spinner("Running agent workflow..."):
        result = asyncio.run(run_agent(prompt, use_tool, stream))

    st.subheader("User-facing response")
    if stream:
        st.write("".join(result["chunks"]))
    else:
        for message in result["response"] or []:
            with st.chat_message(message.role):
                st.write(message.content)

    st.subheader("Checkpointed execution trace")
    for message in result["trace"]:
        if isinstance(message, HumanMessage):
            with st.chat_message("user"):
                st.write(message.content)
        elif isinstance(message, AIMessage) and message.tool_calls:
            with st.chat_message("assistant"):
                st.info("Agent requested a tool")
                st.json(message.tool_calls)
        elif isinstance(message, ToolMessage):
            with st.chat_message("tool"):
                st.success("Tool executed")
                st.write(message.content)
        elif isinstance(message, AIMessage):
            with st.chat_message("assistant"):
                st.write(message.content)

    st.subheader("Mock memory")
    st.json(
        {
            "llm_calls": result["llm_calls"],
            "checkpoint": "InMemorySaver",
            "database_required": False,
            "entries": result["memory"],
        }
    )

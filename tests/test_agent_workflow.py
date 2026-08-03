"""Integration tests for the LangGraph agent workflow and safety perimeter."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.core.langgraph.graph import (
    LangGraphAgent,
    _merge_review_findings,
    _route_after_dlp,
    _route_after_intent,
)
from app.core.langgraph.nodes.inbound_first_stage import inbound_dlp_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.core.langgraph.nodes.outbound import outbound_node
from app.schemas.graph import GraphState
from app.schemas.review import InboundIntentJudgeOutput, OutboundJudgeOutput


class FakeTool:
    """Stand-in search tool that records calls for assertions."""

    name = "fake_search"

    def __init__(self):
        """Initialize the fake tool with an empty call log."""
        self.calls = []

    async def ainvoke(self, args):
        """Record the call and return a canned search result."""
        self.calls.append(args)
        return "fake search result"


class FakeReviewTool:
    """Stand-in review tool that captures the args it was called with."""

    name = "review_code"

    def __init__(self):
        """Initialize the fake review tool with no recorded args."""
        self.args = None

    async def ainvoke(self, args):
        """Record the call args and return canned review findings."""
        self.args = args
        return "review findings"


class FakeLLMService:
    """Stand-in LLM service that returns scripted tool-call then final answer."""

    def __init__(self):
        """Initialize the fake LLM service with zero calls."""
        self.calls = 0

    def bind_tools(self, _tools):
        """Return self to simulate tool binding without a real model."""
        return self

    def get_llm(self):
        """Return a lightweight stand-in exposing a model name."""
        return SimpleNamespace(model_name="test-model")

    async def call(self, _messages):
        """Return a scripted response, incrementing the call counter."""
        self.calls += 1

        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fake_search",
                        "args": {"query": "Python"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )

        return AIMessage(content="Final answer based on the tool result.")


class SafeIntentJudge:
    """Allow a safe request without making a network call."""

    def with_structured_output(self, _schema):
        """Return self to simulate structured output binding."""
        return self

    async def ainvoke(self, _messages):
        """Return a safe intent verdict without a network call."""
        return InboundIntentJudgeOutput(is_safe_intent=True)


class SafeOutboundJudge:
    """Allow a safe draft without making a network call."""

    def with_structured_output(self, _schema):
        """Return self to simulate structured output binding."""
        return self

    async def ainvoke(self, _messages):
        """Return a safe output verdict without a network call."""
        return OutboundJudgeOutput(is_safe_output=True)


@pytest.mark.asyncio
async def test_single_agent_owns_reasoning_and_tool_loop():
    """Verify the agent node owns the ReAct reasoning and tool-call loop."""
    agent = LangGraphAgent()
    fake_llm = FakeLLMService()
    fake_tool = FakeTool()

    agent.llm_service = fake_llm
    agent.tools_by_name = {"fake_search": fake_tool}

    builder = StateGraph(GraphState)
    builder.add_node(
        "agent",
        agent._agent,
        destinations=("agent", "outbound"),
    )
    builder.add_node("outbound", lambda state: state)
    builder.add_edge("outbound", END)
    builder.set_entry_point("agent")

    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Search for Python information.")]},
        config={
            "configurable": {"thread_id": "agent-test"},
            "recursion_limit": 10,
        },
    )

    assert fake_llm.calls == 2
    assert len(fake_tool.calls) == 1
    assert fake_tool.calls[0] == {"query": "Python"}
    assert result["messages"][-1].content == "Final answer based on the tool result."


@pytest.mark.asyncio
async def test_integrated_graph_runs_guardrails_agent_and_outbound_check():
    """Run a safe request through the complete perimeter and agent loop."""
    agent = LangGraphAgent()
    fake_llm = FakeLLMService()
    agent.llm_service = fake_llm
    agent.tools_by_name = {}
    safe_intent = SafeIntentJudge()
    safe_outbound = SafeOutboundJudge()

    async def intent_node(state):
        return await inbound_intent_node(state, safe_intent, safe_intent)

    async def outbound_guardrail(state):
        return await outbound_node(state, safe_outbound, safe_outbound)

    builder = StateGraph(GraphState)
    builder.add_node("inbound_dlp", inbound_dlp_node)
    builder.add_node("inbound_intent", intent_node)
    builder.add_node("guardrail_redirect", agent._guardrail_redirect, destinations=(END,))
    builder.add_node("review_correctness", lambda _state: {"findings": []})
    builder.add_node("review_security", lambda _state: {"findings": []})
    builder.add_node("review_performance", lambda _state: {"findings": []})
    builder.add_node("merge_reviews", _merge_review_findings)
    builder.add_node("agent", agent._agent, destinations=("agent", "outbound"))
    builder.add_node("outbound", outbound_guardrail)
    builder.set_entry_point("inbound_dlp")
    builder.add_conditional_edges("inbound_dlp", _route_after_dlp)
    builder.add_conditional_edges("inbound_intent", _route_after_intent)
    builder.add_edge("review_correctness", "merge_reviews")
    builder.add_edge("review_security", "merge_reviews")
    builder.add_edge("review_performance", "merge_reviews")
    builder.add_edge("merge_reviews", "agent")
    builder.add_edge("outbound", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Explain why this loop stops early.")],
            "user_query": "Explain why this loop stops early.",
            "code": "for item in items:\n    break",
            "language": "python",
        },
        config={"configurable": {"thread_id": "integrated-agent-test"}, "recursion_limit": 10},
    )

    assert result["is_safe_sensitive"] is True
    assert result["is_safe_intent"] is True
    assert result["is_safe_output"] is True
    assert result["final_response"] == "Final answer based on the tool result."


@pytest.mark.asyncio
async def test_integrated_graph_blocks_dlp_before_agent():
    """Do not call the agent when inbound DLP detects a credential."""
    agent = LangGraphAgent()
    fake_llm = FakeLLMService()
    agent.llm_service = fake_llm

    builder = StateGraph(GraphState)
    builder.add_node("inbound_dlp", inbound_dlp_node)
    builder.add_node("inbound_intent", lambda state: state)
    builder.add_node("guardrail_redirect", agent._guardrail_redirect, destinations=(END,))
    builder.add_node("agent", agent._agent, destinations=("agent", "outbound"))
    builder.add_node("outbound", lambda state: state)
    builder.set_entry_point("inbound_dlp")
    builder.add_conditional_edges("inbound_dlp", _route_after_dlp)
    builder.add_conditional_edges("inbound_intent", _route_after_intent)
    builder.add_edge("outbound", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="Review this configuration.")],
            "user_query": "Review this configuration.",
            "code": 'api_key = "sk-abcdefghijklmnopqrstuvwx"',
            "language": "python",
        },
        config={"configurable": {"thread_id": "dlp-agent-test"}, "recursion_limit": 10},
    )

    assert result["is_safe_sensitive"] is False
    assert result["final_response"]
    assert "sk-abcdefghijklmnopqrstuvwx" not in result["messages"][0].content
    assert fake_llm.calls == 0


@pytest.mark.asyncio
async def test_review_tool_receives_language_from_graph_state():
    """Supply the request language when the model omits it from a review call."""
    agent = LangGraphAgent()
    review_tool = FakeReviewTool()
    agent.tools_by_name = {review_tool.name: review_tool}

    result = await agent._agent(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "review_code",
                            "args": {"code": "def broken(:\n    pass"},
                            "id": "review-call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            "language": "python",
        },
        {},
    )

    assert review_tool.args == {"code": "def broken(:\n    pass", "language": "python"}
    assert result.goto == "agent"


def test_final_response_preserves_syntax_blockers_from_review_tool():
    """Inject omitted syntax blockers into the final diagnosis deterministically."""
    agent = LangGraphAgent()
    response = AIMessage(content="The cache is stale.")
    review_message = ToolMessage(
        name="review_code",
        tool_call_id="review-call-2",
        content=json.dumps(
            {
                "syntax_blockers": [
                    {"line": 3, "message": "The code contains a syntax error."},
                ]
            }
        ),
    )

    result = agent._ensure_syntax_blockers_in_response(response, [review_message])

    assert "Syntax blockers" in result.content
    assert "Line 3" in result.content

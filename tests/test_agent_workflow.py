from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.core.langgraph.graph import LangGraphAgent
from app.schemas.graph import GraphState


class FakeTool:
    name = "fake_search"

    def __init__(self):
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return "fake search result"


class FakeLLMService:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    def get_llm(self):
        return SimpleNamespace(model_name="test-model")

    async def call(self, _messages):
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


@pytest.mark.asyncio
async def test_single_agent_owns_reasoning_and_tool_loop():
    agent = LangGraphAgent()
    fake_llm = FakeLLMService()
    fake_tool = FakeTool()

    agent.llm_service = fake_llm
    agent.tools_by_name = {"fake_search": fake_tool}

    builder = StateGraph(GraphState)
    builder.add_node(
        "agent",
        agent._agent,
        destinations=("agent", END),
    )
    builder.set_entry_point("agent")

    graph = builder.compile(checkpointer=InMemorySaver())

    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="Search for Python information.")
            ]
        },
        config={
            "configurable": {"thread_id": "agent-test"},
            "recursion_limit": 10,
        },
    )

    assert fake_llm.calls == 2
    assert len(fake_tool.calls) == 1
    assert fake_tool.calls[0] == {"query": "Python"}
    assert result["messages"][-1].content == "Final answer based on the tool result."
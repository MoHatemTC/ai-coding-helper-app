# app/core/agent.py
from app.core.langgraph.graph import LangGraphAgent

# single shared agent instance
_agent: LangGraphAgent | None = None

def get_agent() -> LangGraphAgent:
    global _agent
    if _agent is None:
        _agent = LangGraphAgent()
    return _agent

# ReAct Agent Streamlit System Test

Run this from the repository root:

```bash
uv run streamlit run tests/frontend_agent/app.py
```

Choose an existing database session in the sidebar, then send a message. The frontend calls `ReActAgent` directly, so every request exercises the current LangGraph graph, PostgreSQL checkpointer, SQL message persistence, and long-term memory service.

Use a disposable development session. This is a system test: it writes the selected user's messages and approved responses to the configured database and memory store. It deliberately does not expose a delete/reset action.

The **Guardrail state** expander shows the final state persisted by the graph, including inbound redaction and outbound safety decisions.

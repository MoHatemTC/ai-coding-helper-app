"""Streamlit system-test frontend for the current ReAct LangGraph agent.

Run from the repository root with:
    uv run streamlit run tests/frontend_agent/app.py
"""

import asyncio
import importlib
import os
import sys
from pathlib import Path
from typing import Any
from typing import TypedDict

import streamlit as st
from dotenv import load_dotenv
from sqlmodel import Session as DatabaseSession
from sqlmodel import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
for key in list(sys.modules.keys()):
    if key.startswith("app"):
        sys.modules.pop(key)
load_dotenv(PROJECT_ROOT / ".env")

st.set_page_config(page_title="ReAct Agent System Test", page_icon="🧪", layout="wide")


@st.cache_resource
def get_agent() -> Any:
    """Create one agent instance for the Streamlit process."""
    react_agent_module = importlib.import_module("app.core.langgraph.ReAct_agent_graph")
    return react_agent_module.ReActAgent()


class SessionOption(TypedDict):
    """Database identity needed to run a persisted system-test turn."""

    id: str
    user_id: int
    username: str | None
    name: str


def load_sessions() -> list[SessionOption]:
    """Load database-backed sessions that can safely receive persisted messages."""
    database_service = importlib.import_module("app.services.database").database_service
    chat_session = importlib.import_module("app.models.session").Session
    with DatabaseSession(database_service.engine) as database_session:
        sessions = database_session.exec(select(chat_session).order_by(chat_session.id)).all()

    return [
        {
            "id": session.id,
            "user_id": session.user_id,
            "username": session.username,
            "name": session.name,
        }
        for session in sessions
    ]


async def run_turn(
    agent: Any,
    content: str,
    session_id: str,
    user_id: int,
    username: str | None,
    stream: bool,
) -> tuple[str, dict[str, Any]]:
    """Run one full graph turn and return the approved response and guardrail state."""
    message_schema = importlib.import_module("app.schemas").Message
    message = message_schema(role="user", content=content)
    if stream:
        chunks = [
            chunk
            async for chunk in agent.get_stream_response(
                message,
                session_id,
                user_id=str(user_id),
                username=username,
            )
        ]
        response = "".join(chunks).rstrip()
    else:
        result = await agent.get_response(
            message,
            session_id,
            user_id=str(user_id),
            username=username,
        )
        response = result[0].content if result else ""

    graph = await agent._get_graph()
    state = await graph.aget_state({"configurable": {"thread_id": session_id}})
    values = state.values
    guardrail_state = {
        "is_safe_intent": values.get("is_safe_intent"),
        "user_query_redacted": values.get("user_query_redacted"),
        "inbound_trigger_reason": values.get("inbound_trigger_reason"),
        "is_safe_output": values.get("is_safe_output"),
        "outbound_trigger_reason": values.get("outbound_trigger_reason"),
        "final_response_length": len(values.get("final_response", "")),
    }
    return response, guardrail_state


def display_history() -> None:
    """Render messages sent during this Streamlit browser session."""
    for item in st.session_state.history:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])


st.title("ReAct Agent System Test")
st.caption("Directly exercises the current LangGraph agent, PostgreSQL checkpoint/message storage, and mem0 memory.")

missing_credentials = [
    credential
    for credential in ("LITELLM_API_KEY",)
    if not os.getenv(credential)
]
if missing_credentials:
    st.error("This system test requires the configured LLM and memory credentials before it can start.")
    st.code("Missing: " + ", ".join(missing_credentials))
    st.stop()
sessions: list[SessionOption] = []
try:
    sessions = load_sessions()
except Exception as error:
    st.error(f"Could not load database sessions: {error}")
    st.stop()

if not sessions:
    st.warning("No database sessions exist. Create a user and session through the API before running this system test.")
    st.stop()

with st.sidebar:
    st.header("Test target")
    if st.button("Refresh sessions"):
        st.rerun()

    selected_id = st.selectbox(
        "Existing database session",
        options=[session["id"] for session in sessions],
        format_func=lambda session_id: next(
            (
                f"{session['name'] or 'Untitled'} · user {session['user_id']} · {session_id}"
                for session in sessions
                if session["id"] == session_id
            ),
            str(session_id),
        ),
    )
    selected_session = next(session for session in sessions if session["id"] == selected_id)
    simulate_stream = st.toggle("Use approved-response streaming", value=False)
    st.info(
        "This uses the selected session and user ID exactly as stored in the current database. "
        "Messages and approved responses will be persisted."
    )

if "history" not in st.session_state:
    st.session_state.history = []

display_history()

prompt = st.chat_input("Enter a coding-mentor prompt or a guardrail test case")
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Running the full agent graph and persistence path..."):
            try:
                response, guardrail_state = asyncio.run(
                    run_turn(
                        get_agent(),
                        prompt,
                        str(selected_session["id"]),
                        selected_session["user_id"],
                        selected_session["username"] if isinstance(selected_session["username"], str) else None,
                        simulate_stream,
                    )
                )
            except Exception as error:
                st.exception(error)
            else:
                st.markdown(response or "_The agent returned no approved response._")
                with st.expander("Guardrail state"):
                    st.json(guardrail_state)
                st.session_state.history.append({"role": "assistant", "content": response})

"""Manual Streamlit sandbox for inspecting live progressive-hint payloads.

Run locally with:
    uv run streamlit run tests/integration/scratch_visualizer.py
"""

import asyncio
import hashlib
import sys
from pathlib import Path
from typing import Any, TypedDict

import streamlit as st

# Streamlit runs this file with ``tests/integration`` as the import root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.langgraph.nodes.hints import generate_hint_node  # noqa: E402
from app.core.langgraph.nodes.inbound_first_stage import inbound_dlp_node  # noqa: E402
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node  # noqa: E402
from app.schemas.review import Category, Finding, HintLevel, HintState, InboundTriggerReason, Severity  # noqa: E402


DEFAULT_QUERY = "Help me make this parser safe without giving me the full solution."
DEFAULT_EXERCISE = "Unvalidated Type Conversion"


class ExerciseProfile(TypedDict):
    """A student exercise and the review context associated with it."""

    problem_id: str
    code: str
    findings: list[Finding]


EXERCISE_PROFILES: dict[str, ExerciseProfile] = {
    "Unvalidated Type Conversion": {
        "problem_id": "simulated-task-age",
        "code": """def parse_age(raw_age: str) -> int:
    return int(raw_age)
""",
        "findings": [
            Finding(
                line=2,
                severity=Severity.HIGH,
                category=Category.CORRECTNESS,
                message="The conversion raises on non-numeric input.",
                rationale="Calling int() without validation can raise ValueError.",
            )
        ],
    },
    "Syntax Error Boundary": {
        "problem_id": "simulated-task-syntax",
        "code": 'print"heelllo"',
        "findings": [
            Finding(
                line=1,
                severity=Severity.HIGH,
                category=Category.CORRECTNESS,
                message="Missing parentheses and broken string delimiters.",
                rationale="Python 3 requires parentheses for print function calls.",
            )
        ],
    },
    "Clean Code (No Flaws)": {
        "problem_id": "simulated-task-clean",
        "code": """def greet(name: str) -> str:
    return f"Hello, {name}"
""",
        "findings": [],
    },
}


def initialize_session_state() -> None:
    """Create the persisted state used by the manual sandbox."""
    if "exercise_target" not in st.session_state:
        st.session_state.exercise_target = DEFAULT_EXERCISE
    if "code_editor" not in st.session_state:
        st.session_state.code_editor = EXERCISE_PROFILES[DEFAULT_EXERCISE]["code"]
    if "hint_state" not in st.session_state:
        st.session_state.hint_state = HintState().model_dump()
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "last_delivered_level" not in st.session_state:
        st.session_state.last_delivered_level = None


def load_selected_exercise() -> None:
    """Synchronize the editor with the selected exercise profile."""
    selected_profile = EXERCISE_PROFILES[st.session_state.exercise_target]
    st.session_state.code_editor = selected_profile["code"]


def build_payload(
    exercise_target: str,
    code: str,
    user_query: str,
) -> dict[str, Any]:
    """Build the student request with its internal review context."""
    selected_profile = EXERCISE_PROFILES[exercise_target]
    if code == selected_profile["code"]:
        problem_id = selected_profile["problem_id"]
        findings = [finding.model_dump() for finding in selected_profile["findings"]]
    else:
        problem_fingerprint = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
        problem_id = f"simulated-task-custom-{problem_fingerprint}"
        findings = []

    return {
        "problem_id": problem_id,
        "code": code,
        "user_query": user_query,
        "findings": findings,
        "hint_state": st.session_state.hint_state,
    }


async def run_pipeline_with_guardrails(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Run inbound guardrails before generating a live mentoring hint."""
    dlp_output = await inbound_dlp_node(payload)
    merged_state = {**payload, **dlp_output}
    if not merged_state.get("is_safe_sensitive", True):
        st.error("⚠️ **Security Alert: Sensitive Information Detected**")
        st.warning(
            "Your submission appears to contain hardcoded API keys, passwords, or secrets. "
            "Please remove all sensitive credentials from your code and query before submitting."
        )
        return None

    intent_output = await inbound_intent_node(merged_state)
    final_state = {**merged_state, **intent_output}
    if not final_state.get("is_safe_intent", True):
        trigger = final_state.get("trigger_reason")
        redirect_msg = final_state.get("constructive_redirect")
        if trigger == InboundTriggerReason.SOLUTION_EXTRACTION:
            st.warning("💡 **Mentor Advice: Share Your Progress**")
            st.info(
                redirect_msg
                or "Please show what you have tried or ask about a specific error rather than requesting a full solution."
            )
        elif trigger == InboundTriggerReason.OFF_TOPIC:
            st.warning("📌 **Off-Topic Request**")
            st.info(
                redirect_msg
                or "Please keep your questions focused on computer science, software engineering, and your current exercise."
            )
        else:
            st.error("⚠️ **Request Blocked**")
            st.info(redirect_msg or "Your request could not be processed at this time. Please rephrase your question.")
        return None

    sanitized_query = final_state.get("sanitized_query") or payload.get("user_query", "")
    sanitized_code = (
        final_state.get("sanitized_code") if final_state.get("sanitized_code") is not None else payload.get("code", "")
    )
    hint_payload = {
        **final_state,
        "user_query": sanitized_query,
        "code": sanitized_code,
    }
    return await generate_hint_node(hint_payload)


def render_mentor_response(result: dict[str, Any]) -> None:
    """Render the live mentor response as readable student-facing guidance."""
    latest_hint: dict[str, str | None] = result["latest_hint"]
    delivered_level = st.session_state.last_delivered_level or HintLevel.NUDGE.value

    st.divider()
    st.markdown("### Your mentor's guidance")
    st.markdown(f"**Mentor understanding**\n\n{latest_hint['understanding']}")
    if latest_hint.get("review"):
        st.markdown(f"**Code review feedback**\n\n{latest_hint['review']}")
    st.markdown(f"**Why this matters**\n\n{latest_hint['explanation']}")
    if latest_hint.get("hint"):
        st.markdown(f"**Your {delivered_level.replace('_', ' ')} hint**\n\n{latest_hint['hint']}")
    if latest_hint.get("next_step"):
        st.markdown(f"**Try this next**\n\n{latest_hint['next_step']}")
    if latest_hint.get("additional_context"):
        st.info(latest_hint["additional_context"])


def main() -> None:
    """Render the independent live-node inspection UI."""
    st.set_page_config(page_title="Progressive Hint Visualizer", layout="wide")
    initialize_session_state()

    st.title("AI Coding Mentor — Workspace Portal")
    st.caption(
        "Share your work-in-progress and ask for guidance. Your mentor will help you "
        "discover the next step without handing you a full solution."
    )

    exercise_target = st.selectbox(
        "Select Exercise Target",
        options=list(EXERCISE_PROFILES),
        key="exercise_target",
        on_change=load_selected_exercise,
    )
    code = st.text_area("Your Python code", key="code_editor", height=260)
    user_query = st.text_input("What would you like help with?", value=DEFAULT_QUERY)

    if st.button("Ask Mentor for a Hint", type="primary", use_container_width=True):
        payload = build_payload(exercise_target=exercise_target, code=code, user_query=user_query)
        try:
            with st.spinner("Your mentor is reviewing your work..."):
                live_result = asyncio.run(run_pipeline_with_guardrails(payload))
        except Exception:
            st.error("Your mentor is unavailable right now. Please try again in a moment.")
        else:
            if live_result is None:
                st.session_state.last_result = None
                return
            updated_state = HintState.model_validate(live_result["hint_state"])
            st.session_state.hint_state = updated_state.model_dump()
            st.session_state.last_result = live_result
            st.session_state.last_delivered_level = updated_state.history[-1].value

    result: dict[str, Any] | None = st.session_state.last_result
    if result is None:
        st.info("Paste your code, ask a question, and your mentor will offer the first hint.")
        return

    render_mentor_response(result)


if __name__ == "__main__":
    main()

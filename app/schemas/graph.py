"""This file contains the graph schema for the application."""

<<<<<<< HEAD
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.schemas.review import InboundTriggerReason, OutboundTriggerReason


class GraphState(TypedDict, total=False):
    """State definition for the LangGraph Agent/Workflow.

    A TypedDict (not a Pydantic BaseModel) so every node -- guardrails,
    review lanes, hints, chat, outbound -- can read/write it via plain dict
    access (`state.get(...)`, `state["..."]`), which is how all of the
    existing node implementations were already written. `total=False` means
    every key is optional: a fresh turn only has the keys `_build_graph_input`
    populates, and each node fills in more as the turn progresses. See
    docs/graph_state_contract.md for the full per-node contract.
    """

    # Conversation
    messages: Annotated[list[AnyMessage], add_messages]
    long_term_memory: str
    skill_profile: str

    # This turn's submission
    code: str | None
    language: str | None
    user_query: str
    problem_id: str | None

    # Inbound guardrail (Stage 1: DLP, Stage 2: Intent)
    is_safe_sensitive: bool
    detected_secret_types: list[str]
    sanitized_query: str
    sanitized_code: str | None
    is_safe_intent: bool
    inbound_trigger_reason: InboundTriggerReason | None
    constructive_redirect: str | None

    # Parallel review lanes -- appended to, never overwritten, so
    # correctness/security/performance can run concurrently.
    findings: Annotated[list[dict[str, Any]], operator.add]

    # Hint escalation
    hint_state: dict[str, Any] | None
    latest_hint: dict[str, Any] | None

    # Chat + outbound guardrail
    draft_response: str
    is_safe_output: bool
    outbound_trigger_reason: OutboundTriggerReason | None
    final_response: str
=======
from typing import Annotated

from langgraph.graph.message import add_messages
from pydantic import (
    BaseModel,
    Field,
)


class GraphState(BaseModel):
    """State definition for the LangGraph Agent/Workflow."""

    messages: Annotated[list, add_messages] = Field(
        default_factory=list, description="The messages in the conversation"
    )
    long_term_memory: str = Field(default="", description="The long term memory of the conversation")
>>>>>>> d372769 (Coding Helper — AI Mentor & Senior Code Reviewer (FastAPI + LangGraph))

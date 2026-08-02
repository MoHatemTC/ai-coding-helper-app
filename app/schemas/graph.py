"""This file contains the graph schema for the application."""

from typing import Annotated

from langgraph.graph.message import add_messages
from pydantic import (
    BaseModel,
    Field,
)

from app.schemas.document import FileAttachment


class GraphState(BaseModel):
    """State definition for the LangGraph Agent/Workflow."""

    messages: Annotated[list, add_messages] = Field(
        default_factory=list, description="The messages in the conversation"
    )
    long_term_memory: str = Field(default="", description="The long term memory of the conversation")

    summary: str = Field(default="", description="The summary of the conversation so far")
    last_message_index: int = Field(default=0, description="Index of the last message processed by summarization node")
    skill_profile: str = Field(default="", description="The user's skill profile markdown")

    pending_files: list[FileAttachment] = Field(
        default_factory=list,
        description="Files uploaded but not yet processed by the document pipeline node",
    )

    uploaded_files: list[FileAttachment] = Field(
        default_factory=list,
        description="File metadata for the current turn, consumed by store_messages",
    )

    # Guardrail fields
    is_safe_intent: bool = Field(default=True)
    is_safe_output: bool = Field(default=True)
    draft_response: str = Field(default="", description="Raw agent output — never stored")
    final_response: str = Field(default="", description="Approved response that gets stored and returned")
    inbound_trigger_reason: str | None = Field(default=None)
    outbound_trigger_reason: str | None = Field(default=None)
    constructive_redirect: str | None = Field(default=None)

    # Inbound redaction flag
    user_query_redacted: bool = Field(default=False, description="True when inbound query was flagged and redacted")

"""This file contains the graph schema for the application."""

from typing import Annotated

import operator
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import (
    BaseModel,
    Field,
)

from app.schemas.review import Finding


class GraphState(BaseModel):
    """State definition for the LangGraph Agent/Workflow."""

    messages: Annotated[list[AnyMessage], add_messages] = Field(
        default_factory=list, description="The messages in the conversation"
    )
    long_term_memory: str = Field(default="", description="The long term memory of the conversation")

    findings: Annotated[list[Finding], operator.add] = Field(
        default_factory=list, description="The compiled code review findings across all evaluation stages"
    )

    code: str | None = Field(default=None, description="The code snippet submitted for review")
    language: str | None = Field(default=None, description="The programming language of the submitted code")

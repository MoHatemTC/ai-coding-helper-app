"""This file contains the chat schema for the application."""

import re
from typing import (
    List,
    Literal,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from app.schemas.base import BaseResponse


class Message(BaseModel):
    """Message model for chat endpoint.

    Attributes:
        role: The role of the message sender (user or assistant).
        content: The content of the message.
        files: Metadata of files uploaded with this message (JSON-serialized FileAttachment list).
    """

    model_config = {"extra": "ignore"}

    role: Literal["user", "assistant", "system"] = Field(..., description="The role of the message sender")
    content: str = Field(..., description="The content of the message", min_length=1)
    files: list | None = Field(default=None, description="Metadata of files uploaded with this message")

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate the message content.

        Args:
            v: The content to validate

        Returns:
            str: The validated content

        Raises:
            ValueError: If the content contains disallowed patterns
        """
        # Check for potentially harmful content
        if re.search(r"<script.*?>.*?</script>", v, re.IGNORECASE | re.DOTALL):
            raise ValueError("Content contains potentially harmful script tags")

        # Check for null bytes
        if "\0" in v:
            raise ValueError("Content contains null bytes")

        return v


class ChatRequest(BaseModel):
    """Request model for chat endpoint.

    When sending with file attachments, use multipart/form-data with
    the message as a form field and files as UploadFile list.

    Attributes:
        message: The user message for this turn.
    """

    message: str = Field(
        ...,
        description="The user message for this turn",
    )


class ChatResponse(BaseResponse):
    """Response model for chat endpoint.

    Attributes:
        messages: List of messages returned by this request (user + assistant).
    """

    messages: List[Message] = Field(..., description="List of messages returned by this request")


class PaginatedChatResponse(BaseResponse):
    """Response model for paginated messages endpoint.

    Attributes:
        messages: List of messages in the page.
        has_more: Whether there are more messages available.
        next_cursor: Cursor for fetching the next page.
    """

    messages: List[Message] = Field(..., description="List of messages in the page")
    has_more: bool = Field(default=False, description="Whether there are more messages available")
    next_cursor: str | None = Field(default=None, description="Cursor for fetching the next page")


class StreamResponse(BaseResponse):
    """Response model for streaming chat endpoint.

    Attributes:
        content: The content of the current chunk.
        done: Whether the stream is complete.
    """

    content: str = Field(default="", description="The content of the current chunk")
    done: bool = Field(default=False, description="Whether the stream is complete")


class SessionTitle(BaseModel):
    """Structured output schema for session title generation."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=60,
    )

    @field_validator("title")
    @classmethod
    def _normalize(cls, v: str) -> str:
        v = " ".join(v.split()).strip(" \"'`.,:;!?-")
        if not v:
            raise ValueError("empty title after normalization")
        return v

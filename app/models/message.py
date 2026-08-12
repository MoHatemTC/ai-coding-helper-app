"""This file contains the message model for the application."""

from typing import (
    TYPE_CHECKING,
)
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import (
    Field,
    Relationship,
)

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.session import Session
    from app.models.user import User


class Message(BaseModel, table=True):
    """Message model for storing chat history.

    Attributes:
        id: The primary key (UUID)
        user_id: Foreign key to the user
        session_id: Foreign key to the session
        role: The message role (Human or AI)
        message: The message content
        files: JSON-serialized list of FileAttachment dicts
        created_at: When the message was created
        user: Relationship to the message owner
        session: Relationship to the session
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: int = Field(foreign_key="user.id", nullable=False)
    session_id: str = Field(foreign_key="session.id", nullable=False)
    role: str = Field(nullable=False, max_length=10)
    message: str = Field(sa_column=Column(Text, nullable=False))
    files: list | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    user: "User" = Relationship(back_populates="messages")
    session: "Session" = Relationship(back_populates="messages")

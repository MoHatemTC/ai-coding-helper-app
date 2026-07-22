"""Message service for storing and retrieving chat history from the messages table."""

from uuid import UUID

from sqlmodel import Session, col, select

from app.core.logging import logger
from app.models.message import Message as MessageModel
from app.services.database import database_service


class MessageService:
    """Service for managing chat messages in the messages table."""

    def __init__(self) -> None:
        """Initialize message service with database engine."""
        self._engine = database_service.engine

    async def store_messages(
        self,
        user_id: int,
        session_id: str,
        messages: list[dict],
    ) -> None:
        """Batch-store messages to the messages table.

        Args:
            user_id: The user who owns the messages.
            session_id: The session the messages belong to.
            messages: List of message dicts with 'role' and 'content' keys.
                      Role is stored as-is ('user', 'assistant', etc.).
        """
        if not messages:
            return

        db_messages = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content or not role:
                continue
            db_messages.append(
                MessageModel(
                    user_id=user_id,
                    session_id=session_id,
                    role=role,
                    message=content,
                )
            )

        if not db_messages:
            return

        try:
            with Session(self._engine) as session:
                session.add_all(db_messages)
                session.commit()
                logger.info(
                    "messages_stored",
                    session_id=session_id,
                    count=len(db_messages),
                )
        except Exception:
            logger.exception("failed_to_store_messages", session_id=session_id)

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        after: str | None = None,
        before: str | None = None,
    ) -> tuple[list[MessageModel], bool]:
        """Retrieve paginated messages for a session using cursor-based pagination.

        Args:
            session_id: The session to retrieve messages for.
            limit: Maximum number of messages to return (default 50, max 100).
            after: Cursor — return messages with id > after (for next page).
            before: Cursor — return messages with id < before (for previous page).

        Returns:
            Tuple of (list of messages, has_more flag).
        """
        effective_limit = min(limit, 100)

        try:
            with Session(self._engine) as session:
                statement = (
                    select(MessageModel)
                    .where(col(MessageModel.session_id) == session_id)
                    .order_by(col(MessageModel.created_at).desc())
                )

                if after:
                    after_msg = session.get(MessageModel, UUID(after))
                    if after_msg:
                        statement = statement.where(col(MessageModel.created_at) < after_msg.created_at)

                if before:
                    before_msg = session.get(MessageModel, UUID(before))
                    if before_msg:
                        statement = statement.where(col(MessageModel.created_at) > before_msg.created_at)

                statement = statement.limit(effective_limit + 1)
                results = list(session.exec(statement).all())

                has_more = len(results) > effective_limit
                messages = results[:effective_limit]
                messages.reverse()

                return messages, has_more
        except Exception:
            logger.exception("failed_to_get_messages", session_id=session_id)
            return [], False

    async def delete_messages(self, session_id: str) -> None:
        """Delete all messages for a session.

        Args:
            session_id: The session whose messages to delete.
        """
        try:
            with Session(self._engine) as session:
                statement = select(MessageModel).where(col(MessageModel.session_id) == session_id)
                results = session.exec(statement).all()
                for msg in results:
                    session.delete(msg)
                session.commit()
                logger.info("messages_deleted", session_id=session_id, count=len(results))
        except Exception:
            logger.exception("failed_to_delete_messages", session_id=session_id)


message_service = MessageService()

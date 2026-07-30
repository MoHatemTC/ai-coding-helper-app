"""Chatbot API endpoints for handling chat interactions.

This module provides endpoints for chat interactions, including regular chat,
streaming chat, message history management, and chat history clearing.
"""

import json
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.langgraph.graph import LangGraphAgent
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.metrics import llm_stream_duration_seconds
from app.models.session import Session
from app.schemas.chat import (
    ChatResponse,
    Message as MessageSchema,
    PaginatedChatResponse,
    StreamResponse,
)
from app.services.document_service import document_service
from app.services.message import message_service
from app.services.session_naming import maybe_name_session


router = APIRouter()
agent = LangGraphAgent()


async def _process_files(
    files: list[UploadFile] | None,
    session: Session,
) -> list:
    """Validate and save uploaded files, returning FileAttachment lists."""
    if not files:
        return []

    errors = []
    for file in files:
        error = await document_service.validate_file(file)
        if error:
            errors.append(error)
    if any(errors):
        raise HTTPException(status_code=400, detail=errors)
    attachments: list = []
    for file in files:
        attachment = await document_service.save_file(
            file=file,
            user_id=session.user_id,
            session_id=session.id,
        )
        attachments.append(attachment)

    if attachments:
        logger.info("files_uploaded", session_id=session.id, file_count=len(attachments))

    return attachments


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    message: str = Form(...),
    session: Session = Depends(get_current_session),
    files: list[UploadFile] | None = File(None),
):
    """Process a chat request using LangGraph.

    Accepts multipart/form-data with the message text and optional code files.
    Uploaded files are split, embedded, and stored in the vector database
    before the LLM generates a response.

    Returns only the user and assistant messages from this request.
    Message storage happens as a graph node after the LLM response.
    """
    try:
        logger.info(
            "chat_request_received",
            session_id=session.id,
        )

        pending_files = await _process_files(files, session)
        if pending_files:
            file_details = "\n".join(
                f"  - {f.original_name} (language: {f.language}, file_id: {f.file_id})" for f in pending_files
            )
            message = f"{message}\n\nUploaded files:\n{file_details}"
        user_message = MessageSchema(role="user", content=message)

        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, [user_message])

        result = await agent.get_response(
            user_message,
            session.id,
            user_id=str(session.user_id),
            username=session.username,
            pending_files=pending_files,
        )

        logger.info("chat_request_processed", session_id=session.id)

        return ChatResponse(messages=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("chat_request_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    message: str = Form(...),
    session: Session = Depends(get_current_session),
    files: list[UploadFile] | None = File(None),
):
    """Process a chat request using LangGraph with streaming response.

    Accepts multipart/form-data with the message text and optional code files.
    Uploaded files are split, embedded, and stored in the vector database
    before the LLM generates a response.
    """
    try:
        logger.info(
            "stream_chat_request_received",
            session_id=session.id,
        )

        pending_files = await _process_files(files, session)
        if pending_files:
            file_details = "\n".join(
                f"  - {f.original_name} (language: {f.language}, file_id: {f.file_id})" for f in pending_files
            )
            message = f"{message}\n\nUploaded files:\n{file_details}"

        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, [message])

        async def event_generator():
            """Generate streaming events."""
            try:
                user_message = MessageSchema(role="user", content=message)
                with llm_stream_duration_seconds.labels(model=agent.llm_service.get_llm().get_name()).time():
                    async for chunk in agent.get_stream_response(
                        user_message,
                        session.id,
                        user_id=str(session.user_id),
                        username=session.username,
                        pending_files=pending_files,
                    ):
                        response = StreamResponse(content=chunk, done=False)
                        yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                final_response = StreamResponse(content="", done=True)
                yield f"data: {json.dumps(final_response.model_dump(mode='json'))}\n\n"

            except Exception as e:
                logger.exception(
                    "stream_chat_request_failed",
                    session_id=session.id,
                    error=str(e),
                )
                error_response = StreamResponse(content=str(e), done=True)
                yield f"data: {json.dumps(error_response.model_dump(mode='json'))}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "stream_chat_request_failed",
            session_id=session.id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages", response_model=PaginatedChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_session_messages(
    request: Request,
    session: Session = Depends(get_current_session),
    limit: int = Query(default=50, ge=1, le=100, description="Messages per page"),
    after: str | None = Query(default=None, description="Cursor for next page"),
    before: str | None = Query(default=None, description="Cursor for previous page"),
):
    """Get paginated messages for a session from the messages table.

    Uses cursor-based pagination with the message id as cursor.
    """
    try:
        db_messages, has_more = await message_service.get_messages(
            session_id=session.id,
            limit=limit,
            after=after,
            before=before,
        )

        messages = [
            MessageSchema(
                role=msg.role,  # type: ignore[arg-type]
                content=msg.message,
                files=msg.files,
            )
            for msg in db_messages
        ]

        next_cursor = str(db_messages[-1].id) if has_more and db_messages else None

        return PaginatedChatResponse(
            messages=messages,
            has_more=has_more,
            next_cursor=next_cursor,
        )
    except Exception as e:
        logger.exception("get_messages_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/messages")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_chat_history(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """Clear all messages for a session from both the messages table and LangGraph checkpoints."""
    try:
        await message_service.delete_messages(session.id)
        await agent.clear_chat_history(session.id)
        return {"message": "Chat history cleared successfully"}
    except Exception as e:
        logger.exception("clear_chat_history_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/file/")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["uploads"][0])
async def serve_uploaded_file(
    request: Request,
    session: Session = Depends(get_current_session),
    url: str = Query(..., description="stored_path of the uploaded file"),
):
    """Serve an uploaded file with session-based authentication.

    The caller must provide a valid session Bearer token and the ``url``
    query parameter matching the ``stored_path`` from a FileAttachment.
    Only files belonging to that session are accessible.
    """
    try:
        requested = Path(url)
        session_dir = Path(settings.UPLOAD_DIR) / str(session.user_id) / session.id
        resolved = requested.resolve()

        if not str(resolved).startswith(str(session_dir.resolve())):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: file does not belong to this session",
            )

        if not resolved.exists():
            logger.warning("uploaded_file_not_found", path=str(resolved))
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )

        logger.info(
            "uploaded_file_served",
            user_id=session.user_id,
            session_id=session.id,
            file_name=resolved.name,
        )
        return FileResponse(str(resolved))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("file_serve_failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

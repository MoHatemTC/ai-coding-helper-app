"""Chatbot API endpoints for handling chat interactions.

This module provides endpoints for chat interactions, including regular chat,
streaming chat, message history management, and chat history clearing.
"""

import asyncio
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
from app.core.langgraph.ReAct_agent_graph import ReActAgent
from app.core.langgraph.agent_status import AgentStatusCallback
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
from app.utils.streaming import replay_response_chunks


router = APIRouter()

# Chat agent modes selectable per message via the `mode` form field.
VALID_AGENT_MODES = ("reasoning", "fast")

_reasoning_agent = ReActAgent()
_fast_agent: LangGraphAgent | None = None

# Kept for the app lifespan pre-warm (start_mcp/create_graph/stop_mcp).
agent = _reasoning_agent


def get_agent(mode: str) -> ReActAgent | LangGraphAgent:
    """Return the chat agent for a mode ('reasoning' or 'fast').

    Agents are cached singletons so each mode keeps its own connection pool,
    graph, and MCP lifecycle. The ReAct (reasoning) agent is the default and
    is pre-warmed by the app lifespan.
    """
    global _fast_agent
    if mode == "fast":
        if _fast_agent is None:
            _fast_agent = LangGraphAgent()
        return _fast_agent
    return _reasoning_agent


def _resolve_mode(mode: str) -> str:
    """Validate a mode value, raising 400 for anything unknown."""
    if mode not in VALID_AGENT_MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid_mode: {mode}")
    return mode


async def close_agents() -> None:
    """Close both agents' connection pools on application shutdown."""
    for candidate in (_reasoning_agent, _fast_agent):
        if candidate is None:
            continue
        pool = getattr(candidate, "_connection_pool", None)
        if pool:
            await pool.close()


async def _process_files(
    files: list[UploadFile] | None,
    session: Session,
) -> list:
    """Validate and save uploaded files, returning FileAttachment lists."""
    if not files:
        return []

    # Tolerate clients that always send a files part without a selection.
    files = [file for file in files if file.filename]
    if not files:
        return []

    if len(files) > settings.MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"too_many_files: {len(files)} exceeds {settings.MAX_FILES_PER_REQUEST} limit",
        )

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
    mode: str = Form("reasoning"),
):
    """Process a chat request using LangGraph.

    Accepts multipart/form-data with the message text, an optional agent
    ``mode`` ('reasoning' = ReAct agent, 'fast' = workflow agent), and optional
    code files. Uploaded files are split, embedded, and stored in the vector
    database before the LLM generates a response.

    Returns only the user and assistant messages from this request.
    Message storage happens as a graph node after the LLM response.
    """
    agent = get_agent(_resolve_mode(mode))
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
    except RuntimeError as e:
        logger.warning("chat_db_unavailable", session_id=session.id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service is unavailable. Please ensure PostgreSQL is running and try again.",
        ) from e
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
    mode: str = Form("reasoning"),
):
    """Process a chat request using LangGraph with streaming response.

    Accepts multipart/form-data with the message text, an optional agent
    ``mode`` ('reasoning' = ReAct agent, 'fast' = workflow agent), and optional
    code files. Uploaded files are split, embedded, and stored in the vector
    database before the LLM generates a response.

    The response is streamed only after the graph's outbound guardrail approves
    the full draft; the approved text is replayed in paced chunks so the client
    still sees a live streaming UX.
    """
    agent = get_agent(_resolve_mode(mode))
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

        user_message = MessageSchema(role="user", content=message)

        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, [user_message])

        async def event_generator():
            """Generate streaming events."""
            try:
                status_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
                status_callback = AgentStatusCallback(status_queue)

                with llm_stream_duration_seconds.labels(model=settings.HINT_LLM_MODEL).time():
                    # Run the full turn non-streaming. The outbound guardrail and
                    # its regenerate loop run inside the graph, so the returned
                    # text is already the final, safety-approved response. Live
                    # activity statuses are emitted as the turn runs.
                    task = asyncio.create_task(
                        agent.get_response(
                            user_message,
                            session.id,
                            user_id=str(session.user_id),
                            username=session.username,
                            pending_files=pending_files,
                            callbacks=[status_callback],
                        )
                    )

                    final_text = ""
                    while not task.done():
                        try:
                            status, tool_name = await asyncio.wait_for(status_queue.get(), timeout=0.25)
                        except asyncio.TimeoutError:
                            continue
                        response = StreamResponse(type="status", status=status, tool_name=tool_name)
                        yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                    while not status_queue.empty():
                        status, tool_name = status_queue.get_nowait()
                        response = StreamResponse(type="status", status=status, tool_name=tool_name)
                        yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                    messages = await task
                    final_text = messages[-1].content if messages else ""

                if not final_text:
                    final_response = StreamResponse(type="done", content="", done=True)
                    yield f"data: {json.dumps(final_response.model_dump(mode='json'))}\n\n"
                    return

                # Replay the approved response as a simulated stream so the UX
                # still feels live even though every token passed the guardrail.
                async for chunk in replay_response_chunks(final_text):
                    response = StreamResponse(type="content", content=chunk, done=False)
                    yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                final_response = StreamResponse(type="done", content="", done=True)
                yield f"data: {json.dumps(final_response.model_dump(mode='json'))}\n\n"

            except RuntimeError as e:
                logger.warning("stream_chat_db_unavailable", session_id=session.id, error=str(e))
                error_response = StreamResponse(
                    type="error",
                    content="Database service is unavailable. Please ensure PostgreSQL is running and try again.",
                    done=True,
                )
                yield f"data: {json.dumps(error_response.model_dump(mode='json'))}\n\n"

            except Exception as e:
                logger.exception(
                    "stream_chat_request_failed",
                    session_id=session.id,
                    error=str(e),
                )
                error_response = StreamResponse(type="error", content=str(e), done=True)
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

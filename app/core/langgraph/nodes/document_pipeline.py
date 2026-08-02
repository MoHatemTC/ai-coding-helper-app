"""LangGraph node for processing uploaded files through the document pipeline."""

from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import Command
from llama_index.core.node_parser import CodeSplitter

from app.core.logging import logger
from app.models.code_chunk import CodeChunk
from app.schemas import GraphState
from app.services.document_service import document_service
from app.services.vector_store import vector_store_service


async def document_pipeline_node(state: GraphState, config: RunnableConfig) -> Command:
    """Process pending files: split, embed, and store chunks in pgvector.

    Runs as the graph entry point. If no pending files exist, skips
    straight to the chat node.

    Args:
        state: The current graph state containing pending_files.
        config: Runnable config with metadata (user_id, session_id).

    Returns:
        Command routing to the next node.
    """
    pending = state.pending_files
    if not pending:
        return Command(update={}, goto="agent")

    metadata = config.get("metadata", {})
    user_id = metadata.get("user_id")
    session_id = metadata.get("session_id")

    if not user_id or not session_id:
        logger.warning("document_pipeline_skipped", reason="missing_user_id_or_session_id")
        return Command(update={}, goto="agent")

    user_id_int = int(user_id)

    for attachment in pending:
        try:
            content = await document_service.read_file(attachment.stored_path)
            language = attachment.language

            splitter = CodeSplitter(
                language=language,
                chunk_lines=50,
                chunk_lines_overlap=10,
                max_chars=1500,
            )
            chunk_texts = splitter.split_text(content)

            chunks = [
                CodeChunk(
                    user_id=user_id_int,
                    session_id=session_id,
                    file_id=attachment.file_id,
                    file_name=attachment.original_name,
                    language=language,
                    content=chunk_text,
                    embedding=None,
                )
                for chunk_text in chunk_texts
            ]

            if chunks:
                vector_store_service.store_chunks(chunks)

            logger.info(
                "document_pipeline_file_processed",
                file_id=attachment.file_id,
                file_name=attachment.original_name,
                chunk_count=len(chunks),
            )

        except Exception:
            logger.exception(
                "document_pipeline_file_failed",
                file_id=attachment.file_id,
                file_name=attachment.original_name,
            )

    return Command(update={"pending_files": [], "uploaded_files": pending}, goto="agent")

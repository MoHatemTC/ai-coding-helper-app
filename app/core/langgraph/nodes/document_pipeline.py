"""LangGraph node for processing uploaded files through the document pipeline.

Also ingests GitHub public repo URLs found in the latest user message: the
repo is cloned transiently, its code files are chunked through the same
pipeline as uploads, and the clone is removed right after chunking.
"""

import asyncio
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import Command
from llama_index.core.node_parser import CodeSplitter

from app.core.config import settings
from app.core.logging import logger
from app.models.code_chunk import CodeChunk
from app.schemas import GraphState
from app.schemas.document import FileAttachment
from app.services.document_service import (
    EXTENSION_LANGUAGE_MAP,
    document_service,
)
from app.services.vector_store import vector_store_service

GITHUB_REPO_PATTERN = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "build",
    "dist",
}


def _extract_github_repo_url(text: str | None) -> str | None:
    """Extract a canonical GitHub public repo URL from arbitrary text.

    Args:
        text: The text to scan (e.g. the latest user message).

    Returns:
        The canonical ``https://github.com/{owner}/{repo}`` URL, or None.
    """
    if not text:
        return None
    match = GITHUB_REPO_PATTERN.search(text)
    if not match:
        return None
    owner = match.group(1)
    repo = match.group(2).rstrip("/.,;:!?)]}")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        return None
    return f"https://github.com/{owner}/{repo}"


def _is_binary_file(path: Path) -> bool:
    """Return True if the file appears to be binary (null byte in first 1 KiB)."""
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(1024)
    except OSError:
        return True


def _iterate_repo_files(repo_path: Path, user_id: int, session_id: str) -> list[FileAttachment]:
    """Walk a cloned repo and build FileAttachments for its code files.

    Skips excluded/heavy directories, dotfiles, binaries, and files outside
    the allowed extensions. ``original_name`` is the repo-relative path so
    ``search_code(file_name=...)`` can scope to individual files.

    Args:
        repo_path: Root of the cloned repo on disk.
        user_id: The user who owns the ingested chunks.
        session_id: The session the repo belongs to.

    Returns:
        A list of FileAttachment records pointing at on-disk code files.
    """
    attachments: list[FileAttachment] = []
    total_bytes = 0

    for root, dirnames, filenames in os.walk(repo_path, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]

        for filename in filenames:
            if filename.startswith("."):
                continue

            file_path = Path(root) / filename
            ext = file_path.suffix.lower()
            if ext not in settings.ALLOWED_EXTENSIONS:
                continue

            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size > settings.MAX_FILE_SIZE:
                continue
            total_bytes += size
            if total_bytes > settings.REPO_MAX_TOTAL_BYTES:
                logger.warning(
                    "repo_max_total_bytes_exceeded",
                    user_id=user_id,
                    session_id=session_id,
                    limit=settings.REPO_MAX_TOTAL_BYTES,
                )
                break
            if _is_binary_file(file_path):
                continue

            relative_name = str(file_path.relative_to(repo_path)).replace("\\", "/")
            attachments.append(
                FileAttachment(
                    file_id=str(uuid.uuid4()),
                    original_name=relative_name,
                    stored_path=str(file_path),
                    language=EXTENSION_LANGUAGE_MAP.get(ext, "text"),
                )
            )
            if len(attachments) >= settings.REPO_MAX_FILES:
                logger.warning(
                    "repo_max_files_exceeded",
                    user_id=user_id,
                    session_id=session_id,
                    limit=settings.REPO_MAX_FILES,
                )
                break

        if len(attachments) >= settings.REPO_MAX_FILES or total_bytes > settings.REPO_MAX_TOTAL_BYTES:
            break

    return attachments


async def _clone_repo(url: str, user_id: int, session_id: str) -> Path | None:
    """Clone a public GitHub repo into a per-session scratch directory.

    A failure is non-fatal and is logged only: the caller keeps going with
    whatever uploads exist.

    Args:
        url: Canonical ``https://github.com/{owner}/{repo}`` URL.
        user_id: The user who triggered the ingestion.
        session_id: The session the repo belongs to.

    Returns:
        The path to the cloned repo root, or None if the clone failed.
    """
    if shutil.which("git") is None:
        logger.warning("git_executable_not_found")
        return None

    dest_root = Path(settings.UPLOAD_DIR) / "repos" / str(user_id) / session_id
    dest_root.mkdir(parents=True, exist_ok=True)

    repo_name = url.rstrip("/").rsplit("/", 2)[-2:]
    dest = dest_root / f"{repo_name[0]}-{repo_name[1]}"
    if dest.exists():
        shutil.rmtree(dest)

    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        url,
        str(dest),
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.REPO_CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "repo_clone_timed_out",
            url=url,
            timeout=settings.REPO_CLONE_TIMEOUT_SECONDS,
        )
        return None

    if result.returncode != 0:
        logger.warning("repo_clone_failed", url=url, stderr=result.stderr.strip())
        return None

    logger.info("repo_cloned", url=url, path=str(dest))
    return dest


def _make_writable(path: Path) -> None:
    """Recursively clear the read-only attribute git sets on pack files."""
    for root, dirs, files in os.walk(path):
        for name in [*dirs, *files]:
            try:
                os.chmod(Path(root) / name, stat.S_IWRITE)
            except OSError:
                continue


def _cleanup_repo(repo_path: Path) -> None:
    """Remove a cloned repo directory after chunking (best-effort).

    Git marks ``.git/objects/pack`` files read-only, which makes
    ``shutil.rmtree`` fail on Windows; clear the attribute first and retry a
    few times to survive transient file locks (antivirus, open handles).
    """
    if not repo_path.exists():
        return
    for attempt in range(3):
        try:
            _make_writable(repo_path)
            shutil.rmtree(repo_path)
            logger.info("repo_clone_removed", path=str(repo_path))
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
            logger.exception("repo_clone_cleanup_failed", path=str(repo_path))


def _last_human_message(state: GraphState) -> str:
    """Return the text of the latest HumanMessage in state, if any."""
    for message in reversed(state.messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def _last_human_message_object(state: GraphState) -> HumanMessage | None:
    """Return the latest HumanMessage in state, if any."""
    for message in reversed(state.messages):
        if isinstance(message, HumanMessage):
            return message
    return None


def _repo_file_listing(repo_url: str, files: list[FileAttachment]) -> str:
    """Build the 'Repo {owner}/{repo} files:' listing for the user message.

    Args:
        repo_url: Canonical ``https://github.com/{owner}/{repo}`` URL.
        files: Repo file attachments that were ingested.

    Returns:
        A multi-line listing string (without a trailing newline).
    """
    owner_repo = repo_url.rstrip("/").rsplit("/", 2)[-2:]
    lines = [f"Repo {'/'.join(owner_repo)} files:"]
    lines.extend(f"  - {f.original_name} (language: {f.language})" for f in files)
    return "\n".join(lines)


async def document_pipeline_node(state: GraphState, config: RunnableConfig) -> Command:
    """Process pending files and/or a GitHub repo URL, then route to the agent.

    Pending uploaded files are split, embedded, and stored in pgvector. If the
    latest user message contains a GitHub public repo URL, that repo is cloned
    transiently, its code files are chunked through the same pipeline, and the
    clone is removed immediately after chunking. Repo files are never exposed as
    message uploads: only a ``Repo {owner}/{repo} files:`` listing is appended
    to the user message text. A clone/ingest failure is non-fatal: the turn
    proceeds with whatever uploads exist.

    Args:
        state: The current graph state containing pending_files and messages.
        config: Runnable config with metadata (user_id, session_id).

    Returns:
        Command routing to the next node.
    """
    pending = list(state.pending_files)
    uploaded = list(state.pending_files)
    repo_files: list[FileAttachment] = []
    clone_path: Path | None = None

    repo_url = _extract_github_repo_url(_last_human_message(state))
    if repo_url:
        metadata = config.get("metadata", {})
        user_id = metadata.get("user_id")
        session_id = metadata.get("session_id")

        if user_id and session_id:
            try:
                clone_path = await _clone_repo(repo_url, int(user_id), str(session_id))
                if clone_path is not None:
                    repo_files = await asyncio.to_thread(
                        _iterate_repo_files,
                        clone_path,
                        int(user_id),
                        str(session_id),
                    )
                    pending.extend(repo_files)
                    logger.info(
                        "repo_ingested",
                        url=repo_url,
                        session_id=session_id,
                        file_count=len(repo_files),
                    )
            except Exception:
                logger.exception(
                    "repo_ingest_failed",
                    url=repo_url,
                    session_id=session_id,
                )
        else:
            logger.warning(
                "repo_ingest_skipped",
                reason="missing_user_id_or_session_id",
                url=repo_url,
            )

    if not pending:
        return Command(update={}, goto="agent")

    metadata = config.get("metadata", {})
    user_id = metadata.get("user_id")
    session_id = metadata.get("session_id")

    if not user_id or not session_id:
        logger.warning("document_pipeline_skipped", reason="missing_user_id_or_session_id")
        return Command(update={}, goto="agent")

    user_id_int = int(user_id)

    try:
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
    finally:
        if clone_path is not None:
            _cleanup_repo(clone_path)

    update: dict[str, Any] = {"pending_files": [], "uploaded_files": uploaded}
    if repo_files and repo_url:
        latest = _last_human_message_object(state)
        if latest is not None and isinstance(latest.content, str) and latest.id:
            listing = _repo_file_listing(repo_url, repo_files)
            update["messages"] = [HumanMessage(content=f"{latest.content}\n\n{listing}", id=latest.id)]
    return Command(update=update, goto="agent")

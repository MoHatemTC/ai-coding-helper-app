"""Tests for GitHub repo ingestion in the document pipeline node."""

import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import Command

from app.core.langgraph.nodes.document_pipeline import (
    RepoCloneError,
    _cleanup_repo,
    _extract_github_repo_url,
    _iterate_repo_files,
    _repo_clone_failure_reason,
    document_pipeline_node,
)
from app.schemas import GraphState
from app.schemas.document import FileAttachment


def _config(user_id: str = "7", session_id: str = "s1") -> RunnableConfig:
    """Build a RunnableConfig carrying user/session metadata."""
    return RunnableConfig(
        configurable={"thread_id": session_id},
        metadata={"user_id": user_id, "session_id": session_id},
    )


UPLOAD_URLS = [
    "https://github.com/openai/openai-python",
    "https://github.com/openai/openai-python/",
    "https://github.com/openai/openai-python.git",
    "https://www.github.com/openai/openai-python",
    "http://github.com/openai/openai-python",
    "Read https://github.com/openai/openai-python and tell me what it does.",
    "https://github.com/openai/openai-python#readme",
]


@pytest.mark.parametrize("text", UPLOAD_URLS)
def test_extract_github_repo_url_valid(text: str) -> None:
    """Extract a canonical URL from valid GitHub links in arbitrary text."""
    assert _extract_github_repo_url(text) == "https://github.com/openai/openai-python"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no link here",
        "https://gitlab.com/openai/openai-python",
        "https://github.com/only-owner",
        "git@github.com:openai/openai-python.git",
        "https://example.com/path",
    ],
)
def test_extract_github_repo_url_invalid(text: str) -> None:
    """Return None for non-GitHub URLs, git protocol URLs, and plain text."""
    assert _extract_github_repo_url(text) is None


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("remote: Repository not found.\nfatal: repository '...' not found", "repository_not_found"),
        ("fatal: could not read Username for 'https://github.com'", "authentication_required"),
        ("remote: Permission to org/repo denied to user", "access_denied"),
        ("fatal: 'org/repo' does not appear to be a git repository", "not_a_git_repository"),
        ("something unexpected went wrong", "clone_failed"),
    ],
)
def test_repo_clone_failure_reason(stderr: str, expected: str) -> None:
    """Git clone stderr maps to clean, short reason codes."""
    assert _repo_clone_failure_reason(stderr) == expected


@pytest.fixture
def repo_tree(tmp_path: Path) -> Path:
    """Build a synthetic cloned repo with code, dotfiles, and binaries."""
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (repo / "README.md").write_text("# hello", encoding="utf-8")
    (repo / "data.txt").write_text("not code", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "pkg.js").write_text("const x = 1;", encoding="utf-8")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]", encoding="utf-8")
    return repo


def test_iterate_repo_files_filters(repo_tree: Path) -> None:
    """Only allowed non-binary, non-hidden, non-excluded files are kept."""
    attachments = _iterate_repo_files(repo_tree, user_id=1, session_id="s1")

    names = {att.original_name for att in attachments}
    assert names == {"src/auth.py", "src/__init__.py", "README.md"}
    auth = next(att for att in attachments if att.original_name == "src/auth.py")
    assert auth.language == "python"
    assert Path(auth.stored_path) == repo_tree / "src" / "auth.py"


def test_iterate_repo_files_relative_path_uses_forward_slashes(repo_tree: Path) -> None:
    """Repo-relative file names always use forward slashes for tool scoping."""
    attachments = _iterate_repo_files(repo_tree, user_id=1, session_id="s1")
    assert all("\\" not in att.original_name for att in attachments)


def test_iterate_repo_files_max_files(repo_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce the per-repo file cap."""
    monkeypatch.setattr("app.core.langgraph.nodes.document_pipeline.settings.REPO_MAX_FILES", 1)
    attachments = _iterate_repo_files(repo_tree, user_id=1, session_id="s1")
    assert len(attachments) == 1


def test_cleanup_repo_removes_directory(repo_tree: Path) -> None:
    """Cleanup removes the cloned repo directory."""
    assert repo_tree.exists()
    _cleanup_repo(repo_tree)
    assert not repo_tree.exists()


def test_cleanup_repo_removes_readonly_files(repo_tree: Path) -> None:
    """Cleanup clears git's read-only pack flag before removing (Windows)."""
    pack_dir = repo_tree / ".git" / "objects" / "pack"
    pack_dir.mkdir(parents=True)
    pack = pack_dir / "pack-abc.idx"
    pack.write_text("x", encoding="utf-8")
    os.chmod(pack, stat.S_IREAD)
    _cleanup_repo(repo_tree)
    assert not repo_tree.exists()


def test_cleanup_repo_missing_directory(tmp_path: Path) -> None:
    """Cleanup is a no-op for a missing directory."""
    _cleanup_repo(tmp_path / "nope")  # should not raise


@pytest.mark.asyncio
async def test_document_pipeline_node_ingests_repo(
    repo_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GitHub URL in the message triggers clone, chunking, and cleanup."""
    stored: list = []

    async def fake_clone_repo(url: str, user_id: int, session_id: str) -> Path:
        return repo_tree

    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline._clone_repo",
        fake_clone_repo,
    )
    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline.vector_store_service.store_chunks",
        stored.append,
    )

    state = GraphState(messages=[HumanMessage(content="review https://github.com/foo/bar please", id="user-msg")])
    config = _config()

    result: Command = await document_pipeline_node(state, config)

    assert result.goto == "agent"
    stored_files = {c.file_name for chunk_list in stored for c in chunk_list}
    assert "src/auth.py" in stored_files
    update = result.update or {}
    # Repo files are NOT exposed as message uploads.
    assert update["uploaded_files"] == []
    # Instead, a listing is appended to the last user message.
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert isinstance(message.content, str)
    assert message.content.startswith("review https://github.com/foo/bar please")
    assert "Repo foo/bar files:" in message.content
    assert "  - src/auth.py (language: python)" in message.content
    assert message.id is not None


@pytest.mark.asyncio
async def test_document_pipeline_node_repo_listing_keeps_uploads(
    repo_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploaded files stay in uploaded_files while repo files only get listed."""
    uploaded = tmp_path / "main.py"
    uploaded.write_text("print('hi')\n", encoding="utf-8")
    stored: list = []

    async def fake_clone_repo(url: str, user_id: int, session_id: str) -> Path:
        return repo_tree

    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline._clone_repo",
        fake_clone_repo,
    )
    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline.vector_store_service.store_chunks",
        stored.append,
    )
    attachment = FileAttachment(
        file_id="f1",
        original_name="main.py",
        stored_path=str(uploaded),
        language="python",
    )

    state = GraphState(
        messages=[HumanMessage(content="review https://github.com/foo/bar please", id="user-msg")],
        pending_files=[attachment],
    )
    config = _config()

    result: Command = await document_pipeline_node(state, config)

    assert result.goto == "agent"
    update = result.update or {}
    uploaded_names = {att.original_name for att in update["uploaded_files"]}
    assert uploaded_names == {"main.py"}
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert isinstance(message.content, str)
    assert "Repo foo/bar files:" in message.content
    assert "  - src/auth.py (language: python)" in message.content


@pytest.mark.asyncio
async def test_document_pipeline_node_clone_removed_after_chunking(
    repo_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cloned repo directory is deleted right after the node runs."""
    assert repo_tree.exists()

    async def fake_clone_repo(url: str, user_id: int, session_id: str) -> Path:
        return repo_tree

    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline._clone_repo",
        fake_clone_repo,
    )
    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline.vector_store_service.store_chunks",
        lambda chunks: None,
    )

    state = GraphState(messages=[HumanMessage(content="review https://github.com/foo/bar please")])
    config = _config()

    await document_pipeline_node(state, config)

    assert not repo_tree.exists()


@pytest.mark.asyncio
async def test_document_pipeline_node_clone_failure_non_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed clone is non-fatal and appends a clean note to the message."""

    async def fake_clone_repo(url: str, user_id: int, session_id: str) -> Path:
        raise RepoCloneError("repository_not_found", "remote: Repository not found.")

    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline._clone_repo",
        fake_clone_repo,
    )

    state = GraphState(messages=[HumanMessage(content="review https://github.com/foo/bar please", id="user-msg")])
    config = _config()

    result: Command = await document_pipeline_node(state, config)

    assert result.goto == "agent"
    update = result.update or {}
    assert update["uploaded_files"] == []
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert isinstance(message.content, str)
    assert message.content.endswith("Repo foo/bar: could not be ingested (repository_not_found)")
    assert message.id == "user-msg"


@pytest.mark.asyncio
async def test_document_pipeline_node_clone_failure_keeps_uploads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploads still flow through when a repo clone fails."""
    uploaded = tmp_path / "main.py"
    uploaded.write_text("print('hi')\n", encoding="utf-8")
    stored: list = []

    async def fake_clone_repo(url: str, user_id: int, session_id: str) -> Path:
        raise RepoCloneError("repository_not_found")

    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline._clone_repo",
        fake_clone_repo,
    )
    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline.vector_store_service.store_chunks",
        stored.append,
    )
    attachment = FileAttachment(
        file_id="f1",
        original_name="main.py",
        stored_path=str(uploaded),
        language="python",
    )

    state = GraphState(
        messages=[HumanMessage(content="review https://github.com/foo/bar please", id="user-msg")],
        pending_files=[attachment],
    )
    config = _config()

    result: Command = await document_pipeline_node(state, config)

    assert result.goto == "agent"
    update = result.update or {}
    assert {att.original_name for att in update["uploaded_files"]} == {"main.py"}
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert isinstance(message.content, str)
    assert "Repo foo/bar: could not be ingested (repository_not_found)" in message.content


@pytest.mark.asyncio
async def test_document_pipeline_node_no_repo_url_skips_clone() -> None:
    """No clone when the message has no GitHub URL."""
    with patch(
        "app.core.langgraph.nodes.document_pipeline._clone_repo",
        new_callable=AsyncMock,
    ) as mock_clone:
        state = GraphState(messages=[HumanMessage(content="just a normal question")])
        config = _config()

        result: Command = await document_pipeline_node(state, config)

    mock_clone.assert_not_awaited()
    assert result.goto == "agent"
    assert result.update == {}


@pytest.mark.asyncio
async def test_document_pipeline_node_keeps_uploads_without_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploaded files still flow through when no repo URL is present."""
    uploaded = tmp_path / "main.py"
    uploaded.write_text("print('hi')\n", encoding="utf-8")
    stored: list = []

    monkeypatch.setattr(
        "app.core.langgraph.nodes.document_pipeline.vector_store_service.store_chunks",
        stored.append,
    )
    attachment = FileAttachment(
        file_id="f1",
        original_name="main.py",
        stored_path=str(uploaded),
        language="python",
    )

    state = GraphState(messages=[HumanMessage(content="review my file")], pending_files=[attachment])
    config = _config()

    result: Command = await document_pipeline_node(state, config)

    assert result.goto == "agent"
    stored_files = {c.file_name for chunk_list in stored for c in chunk_list}
    assert "main.py" in stored_files

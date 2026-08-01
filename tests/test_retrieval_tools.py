"""Unit tests for independent retrieval tools (grep_search, read_lines, BM25SearchService)."""

from pathlib import Path

import pytest
from app.tools.retrieval_tools import (
    BM25SearchService,
    grep_search,
    read_lines,
)


def test_load_index_then_search_returns_results(tmp_path: Path) -> None:
    """Test that calling load_index() alone populates documents and search() returns non-empty results."""
    index_dir = str(tmp_path / "test_bm25_index")

    # Step 1: Initialize first service instance and index documents
    docs = [
        {
            "doc_id": "doc1",
            "title": "FastAPI Guide",
            "text": "FastAPI is a modern web framework for building APIs with Python.",
        },
        {
            "doc_id": "doc2",
            "title": "LangGraph Overview",
            "text": "LangGraph allows building stateful multi-agent workflows with graphs.",
        },
    ]

    service1 = BM25SearchService()
    service1.index_documents(docs)
    service1.save_index(index_dir)

    # Step 2: Instantiate a completely new service instance calling ONLY load_index()
    service2 = BM25SearchService()
    service2.load_index(index_dir)

    # Step 3: Verify documents are populated and search returns non-empty results
    res = service2.search(query="FastAPI web framework", reason="testing load_index path")
    assert "session_id" in res
    assert len(res["results"]) > 0
    assert res["results"][0].doc_id == "doc1"


def test_search_requires_reason() -> None:
    """Test that search raises ValueError if reason is empty or whitespace."""
    service = BM25SearchService()
    service.index_documents([{"doc_id": "1", "title": "T", "text": "Sample text"}])

    with pytest.raises(ValueError, match="reason is required"):
        service.search(query="Sample", reason="")

    with pytest.raises(ValueError, match="reason is required"):
        service.search(query="Sample", reason="   ")


def test_grep_search_and_read_lines(tmp_path: Path) -> None:
    """Test standalone grep_search and read_lines against temporary code files."""
    test_file = tmp_path / "example.py"
    test_file.write_text(
        "def hello_world():\n    print('Hello World')\n    return True\n",
        encoding="utf-8",
    )

    matches = grep_search(pattern="hello_world", path=str(tmp_path))
    assert len(matches) == 1
    assert matches[0].line_number == 1
    assert "def hello_world():" in matches[0].line_text

    snippet = read_lines(file=str(test_file), start=1, end=2, max_span=50)
    assert "def hello_world():" in snippet
    assert "print('Hello World')" in snippet


def test_read_search_results_and_read_document(tmp_path: Path) -> None:
    """Test pagination of search results and document slice reading."""
    docs = [
        {"doc_id": f"d{i}", "title": f"Title {i}", "text": f"Document content line {i}\nSecond line"}
        for i in range(15)
    ]
    service = BM25SearchService()
    service.index_documents(docs)

    res = service.search(query="content line", reason="pagination test")
    session_id = res["session_id"]
    assert len(res["results"]) == 10

    page2 = service.read_search_results(session_id=session_id, page=2, page_size=10)
    assert len(page2) == 5

    doc_text = service.read_document(doc_id="d0", offset=1, limit=1)
    assert "Document content line 0" in doc_text

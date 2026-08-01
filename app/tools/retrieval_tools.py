"""Independent, self-contained retrieval tools for code search and reference content search.

Tool 1: `grep_search` & `read_lines`
- Ripgrep-backed code search with Python `re` fallback.
- Explicit exclusions for hidden/binary/heavy directories.
- Line range reading with span enforcement.

Tool 2: `search`, `read_search_results`, `read_document`
- BM25 retrieval (bm25s with pure-Python Okapi fallback).
- Configurable BM25 parameters (k1=1.2, b=0.75 defaults).
- Session-based caching with TTL and paginated result retrieval.
- Bounded document text slice reading.
"""

import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog
from pydantic import BaseModel, Field

try:
    import cachetools  # pyright: ignore[reportMissingImports]

    HAS_CACHETOOLS = True
except ImportError:
    cachetools = None
    HAS_CACHETOOLS = False

try:
    import bm25s  # pyright: ignore[reportMissingImports]

    HAS_BM25S = True
except ImportError:
    bm25s = None
    HAS_BM25S = False

logger = structlog.get_logger(__name__)

# Excluded directories for code search
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

# --- Data Models ---


class Match(BaseModel):
    """Represents a code search match item."""

    file: str
    line_number: int
    line_text: str


class ResultSnippet(BaseModel):
    """Represents a search result snippet for BM25 retrieval."""

    doc_id: str
    title: str
    snippet: str
    score: float


class SearchSession(BaseModel):
    """Stored session containing full ranked results for pagination."""

    session_id: str
    query: str
    reason: str
    results: List[ResultSnippet]
    created_at: float = Field(default_factory=time.time)


# --- Fallback TTL Cache ---


class SimpleTTLCache:
    """Basic TTL cache fallback if cachetools is unavailable."""

    def __init__(self, maxsize: int = 100, ttl: float = 3600.0) -> None:
        """Initialize TTL cache."""
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: Dict[str, Tuple[float, Any]] = {}

    def __setitem__(self, key: str, value: Any) -> None:
        """Set a value in cache with TTL."""
        # TODO: add locking around self._documents / session cache mutation once
        # integration architecture (sync vs. async, concurrency model) is decided.
        now = time.time()
        expired = [k for k, (t, _) in self._data.items() if now - t > self.ttl]
        for k in expired:
            del self._data[k]

        if len(self._data) >= self.maxsize and key not in self._data:
            oldest_key = min(self._data.keys(), key=lambda k: self._data[k][0])
            del self._data[oldest_key]

        self._data[key] = (now, value)

    def __getitem__(self, key: str) -> Any:
        """Get a value from cache."""
        now = time.time()
        if key not in self._data:
            raise KeyError(key)
        t, val = self._data[key]
        if now - t > self.ttl:
            del self._data[key]
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        """Check if a key exists in the cache."""
        try:
            _ = self[key]
            return True
        except KeyError:
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from cache or return default if not found."""
        try:
            return self[key]
        except KeyError:
            return default


# --- Tool 1: Grep Code Search & Line Reader ---


def _is_binary_file(file_path: Path) -> bool:
    """Check if a file appears to be binary by inspecting initial bytes."""
    try:
        with file_path.open("rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except Exception:
        return True


def _grep_ripgrep(
    rg_executable: str,
    pattern: str,
    target_path: Path,
    regex: bool,
    max_results: int,
) -> Optional[List[Match]]:
    """Execute ripgrep subprocess for fast pattern searching."""
    cmd: List[str] = [
        rg_executable,
        "--line-number",
        "--no-heading",
        "--color=never",
        "-m",
        str(max_results),
    ]

    if not regex:
        cmd.append("-F")

    for exc_dir in EXCLUDED_DIRS:
        cmd.extend(["-g", f"!{exc_dir}/*", "-g", f"!{exc_dir}"])

    cmd.extend(["--", pattern, str(target_path)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode not in (0, 1):
            logger.warning(
                "ripgrep_execution_failed",
                returncode=result.returncode,
                stderr=result.stderr,
            )
            return None

        matches: List[Match] = []
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.splitlines():
                parts = line.split(":", 2)
                if len(parts) == 3:
                    file_str, line_num_str, line_text = parts
                    try:
                        line_num = int(line_num_str)
                        matches.append(
                            Match(
                                file=file_str,
                                line_number=line_num,
                                line_text=line_text,
                            )
                        )
                    except ValueError:
                        continue
                if len(matches) >= max_results:
                    break
        return matches
    except Exception as exc:
        logger.exception("ripgrep_subprocess_error", error=str(exc))
        return None


def _grep_python_fallback(
    pattern: str,
    search_regex: Optional[re.Pattern[str]],
    target_path: Path,
    regex: bool,
    max_results: int,
) -> List[Match]:
    """Fallback grep implementation using Python filesystem traversal and regex/substring search."""
    matches: List[Match] = []

    def scan_file(file_path: Path) -> None:
        if len(matches) >= max_results:
            return
        if file_path.name.startswith(".") or _is_binary_file(file_path):
            return

        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, start=1):
                    line_clean = line.rstrip("\r\n")
                    matched = False
                    if regex and search_regex is not None:
                        if search_regex.search(line_clean):
                            matched = True
                    else:
                        if pattern in line_clean:
                            matched = True

                    if matched:
                        matches.append(
                            Match(
                                file=str(file_path),
                                line_number=line_idx,
                                line_text=line_clean,
                            )
                        )
                        if len(matches) >= max_results:
                            break
        except Exception as exc:
            logger.warning("grep_fallback_file_read_error", file=str(file_path), error=str(exc))

    if target_path.is_file():
        scan_file(target_path)
        return matches

    for root, dirnames, filenames in os.walk(target_path, topdown=True):
        if len(matches) >= max_results:
            break

        # Filter excluded/hidden directories in-place
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".")]

        for filename in filenames:
            if len(matches) >= max_results:
                break
            if filename.startswith("."):
                continue
            scan_file(Path(root) / filename)

    return matches


def grep_search(
    pattern: str,
    path: str,
    regex: bool = False,
    max_results: int = 50,
) -> List[Match]:
    """Standalone grep-based tool for searching code.

    Args:
        pattern: Pattern to search for.
        path: File or directory path.
        regex: Whether pattern is a regex.
        max_results: Maximum number of matches to return.

    Returns:
        List[Match]: Matching occurrences with file, line_number, line_text.
    """
    logger.info("grep_search_started", pattern=pattern, path=path, regex=regex, max_results=max_results)

    if not pattern:
        logger.info("grep_search_empty_pattern")
        return []

    target_path = Path(path)
    if not target_path.exists():
        logger.warning("grep_search_path_not_found", path=path)
        return []

    search_regex: Optional[re.Pattern[str]] = None
    if regex:
        try:
            search_regex = re.compile(pattern)
        except re.error as exc:
            logger.exception("grep_search_invalid_regex", pattern=pattern, error=str(exc))
            return []

    rg_executable = shutil.which("rg")
    if rg_executable:
        rg_matches = _grep_ripgrep(
            rg_executable=rg_executable,
            pattern=pattern,
            target_path=target_path,
            regex=regex,
            max_results=max_results,
        )
        if rg_matches is not None:
            return rg_matches

    return _grep_python_fallback(
        pattern=pattern,
        search_regex=search_regex,
        target_path=target_path,
        regex=regex,
        max_results=max_results,
    )


def read_lines(file: str, start: int, end: int, max_span: int = 50) -> str:
    """Read literal text between line start and end, enforcing max_span bound.

    Args:
        file: Target file path.
        start: 1-indexed starting line.
        end: 1-indexed ending line.
        max_span: Maximum number of lines allowed per call.

    Returns:
        str: Lines read joined by newlines.
    """
    logger.info("read_lines_started", file=file, start=start, end=end, max_span=max_span)

    file_path = Path(file)
    if not file_path.exists() or not file_path.is_file():
        logger.warning("read_lines_file_not_found", file=file)
        return ""

    if start < 1:
        start = 1
    if end < start:
        return ""

    span = min(end - start + 1, max_span)
    effective_end = start + span - 1

    try:
        selected_lines: List[str] = []
        with file_path.open("r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f, start=1):
                if start <= idx <= effective_end:
                    selected_lines.append(line)
                elif idx > effective_end:
                    break
        return "".join(selected_lines)
    except Exception as exc:
        logger.exception("read_lines_error", file=file, error=str(exc))
        return ""


# --- Tool 2: BM25 Search Service ---


class BM25SearchService:
    """BM25 search service with configurable parameters, session caching, and document retrieval."""

    def __init__(
        self,
        index_dir: Optional[str] = None,
        k1: float = 1.2,
        b: float = 0.75,
        cache_maxsize: int = 100,
        cache_ttl: int = 3600,
    ) -> None:
        """Initialize BM25 search service."""
        self.k1 = k1
        self.b = b
        self.index_dir = index_dir
        # TODO: add locking around self._documents / session cache mutation once
        # integration architecture (sync vs. async, concurrency model) is decided.
        self._documents: Dict[str, Dict[str, str]] = {}
        self._bm25_retriever: Any = None

        if HAS_CACHETOOLS and cachetools is not None:
            self._session_cache: Any = cachetools.TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)
        else:
            self._session_cache = SimpleTTLCache(maxsize=cache_maxsize, ttl=float(cache_ttl))

        if index_dir and Path(index_dir).exists():
            self.load_index(index_dir)

    def load_index(self, index_dir: str) -> None:
        """Load BM25 index and associated documents mapping from directory."""
        logger.info("bm25_load_index_started", index_dir=index_dir)
        path = Path(index_dir)
        if not path.exists():
            logger.warning("bm25_index_dir_not_found", index_dir=index_dir)
            return

        # Load sidecar documents mapping if present
        sidecar_file = path / "documents.json"
        if sidecar_file.exists():
            try:
                with sidecar_file.open("r", encoding="utf-8") as f:
                    loaded_docs = json.load(f)
                    if isinstance(loaded_docs, dict):
                        self._documents = loaded_docs
                        logger.info("bm25_documents_sidecar_loaded", count=len(self._documents))
            except Exception as exc:
                logger.exception("bm25_sidecar_documents_load_failed", error=str(exc))

        if HAS_BM25S and bm25s is not None:
            try:
                retriever = bm25s.BM25.load(index_dir, load_corpus=True)
                self._bm25_retriever = retriever
                logger.info("bm25s_index_loaded_successfully", index_dir=index_dir)

                # Reconstruct self._documents from retriever corpus if still empty
                if not self._documents and hasattr(retriever, "corpus") and retriever.corpus is not None:
                    corpus = retriever.corpus
                    for idx, item in enumerate(corpus):
                        if isinstance(item, dict):
                            doc_id = str(item.get("doc_id", item.get("id", str(idx))))
                            title = str(item.get("title", f"Doc-{doc_id}"))
                            text = str(item.get("text", item.get("content", "")))
                        elif isinstance(item, str):
                            doc_id = str(idx)
                            title = f"Doc-{doc_id}"
                            text = item
                        else:
                            doc_id = str(idx)
                            title = f"Doc-{doc_id}"
                            text = str(item)
                        self._documents[doc_id] = {
                            "doc_id": doc_id,
                            "title": title,
                            "text": text,
                        }
                    logger.info("bm25_documents_reconstructed_from_corpus", count=len(self._documents))
                return
            except Exception as exc:
                logger.exception("bm25s_load_failed_falling_back", index_dir=index_dir, error=str(exc))

        logger.info("bm25_fallback_index_loaded", index_dir=index_dir)

    def save_index(self, index_dir: str) -> None:
        """Save BM25 index and sidecar documents mapping to directory."""
        logger.info("bm25_save_index_started", index_dir=index_dir)
        path = Path(index_dir)
        path.mkdir(parents=True, exist_ok=True)

        sidecar_file = path / "documents.json"
        try:
            with sidecar_file.open("w", encoding="utf-8") as f:
                json.dump(self._documents, f, indent=2)
            logger.info("bm25_documents_sidecar_saved", count=len(self._documents))
        except Exception as exc:
            logger.exception("bm25_sidecar_save_failed", error=str(exc))

        if HAS_BM25S and bm25s is not None and self._bm25_retriever is not None:
            try:
                corpus_list = list(self._documents.values())
                self._bm25_retriever.save(index_dir, corpus=corpus_list)
                logger.info("bm25s_index_saved_successfully", index_dir=index_dir)
            except Exception as exc:
                logger.exception("bm25s_index_save_failed", error=str(exc))

    def index_documents(self, documents: List[Dict[str, str]]) -> None:
        """Index a list of document dicts with keys: doc_id, title, text."""
        logger.info("bm25_indexing_documents", count=len(documents))
        self._documents.clear()
        corpus_texts: List[str] = []

        for doc in documents:
            doc_id = doc.get("doc_id", str(uuid.uuid4()))
            title = doc.get("title", f"Doc-{doc_id}")
            text = doc.get("text", "")
            self._documents[doc_id] = {
                "doc_id": doc_id,
                "title": title,
                "text": text,
            }
            corpus_texts.append(text)

        if HAS_BM25S and bm25s is not None and corpus_texts:
            try:
                corpus_tokens = bm25s.tokenize(corpus_texts)
                retriever = bm25s.BM25(k1=self.k1, b=self.b)
                retriever.index(corpus_tokens)
                self._bm25_retriever = retriever
                logger.info("bm25s_indexing_completed")
                return
            except Exception as exc:
                logger.exception("bm25s_indexing_error_fallback", error=str(exc))

        self._bm25_retriever = None

    def _tokenize(self, text: str) -> List[str]:
        """Simple whitespace/word tokenizer for BM25 fallback."""
        return re.findall(r"\w+", text.lower())

    def _python_bm25_score(self, query: str, top_k: int) -> List[Tuple[str, float]]:
        """Pure Python Okapi BM25 implementation fallback."""
        query_tokens = self._tokenize(query)
        if not query_tokens or not self._documents:
            return []

        doc_count = len(self._documents)
        doc_ids = list(self._documents.keys())
        doc_tokens_list = [self._tokenize(self._documents[did]["text"]) for did in doc_ids]
        doc_lens = [len(dt) for dt in doc_tokens_list]
        avgdl = sum(doc_lens) / doc_count if doc_count > 0 else 1.0

        # Calculate document frequencies
        df: Dict[str, int] = {}
        for dt in doc_tokens_list:
            unique_terms = set(dt)
            for t in unique_terms:
                df[t] = df.get(t, 0) + 1

        scores: List[Tuple[str, float]] = []
        for did, dt, dlen in zip(doc_ids, doc_tokens_list, doc_lens, strict=False):
            score = 0.0
            term_counts: Dict[str, int] = {}
            for t in dt:
                term_counts[t] = term_counts.get(t, 0) + 1

            for qt in query_tokens:
                if qt not in df:
                    continue
                n_qt = df[qt]
                idf = math.log(1.0 + (doc_count - n_qt + 0.5) / (n_qt + 0.5))
                freq = term_counts.get(qt, 0)
                numerator = freq * (self.k1 + 1.0)
                denominator = freq + self.k1 * (1.0 - self.b + self.b * (dlen / avgdl))
                score += idf * (numerator / denominator)

            if score > 0:
                scores.append((did, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def search(self, query: str, reason: str, top_k: int = 100) -> Dict[str, Any]:
        """Execute BM25 query, cache full ranked list, return session_id & first page snippets.

        Args:
            query: Search query string.
            reason: Required explanation of why the query is being run.
            top_k: Maximum ranked items to cache.

        Raises:
            ValueError: If reason is empty or whitespace.
        """
        logger.info("bm25_search_started", query=query, reason=reason, top_k=top_k)

        if not reason or not reason.strip():
            logger.warning("bm25_search_missing_reason", query=query)
            raise ValueError("reason is required for search and cannot be empty")

        results: List[ResultSnippet] = []

        if HAS_BM25S and bm25s is not None and self._bm25_retriever is not None:
            try:
                query_tokens = bm25s.tokenize([query])
                doc_indices, scores = self._bm25_retriever.retrieve(query_tokens, k=min(top_k, len(self._documents)))
                doc_ids = list(self._documents.keys())
                for idx, score in zip(doc_indices[0], scores[0], strict=False):
                    if idx < len(doc_ids):
                        did = doc_ids[idx]
                        doc_info = self._documents[did]
                        text = doc_info["text"]
                        snippet = text[:200] + "..." if len(text) > 200 else text
                        results.append(
                            ResultSnippet(
                                doc_id=did,
                                title=doc_info["title"],
                                snippet=snippet,
                                score=float(score),
                            )
                        )
            except Exception as exc:
                logger.exception("bm25s_search_failed_falling_back", error=str(exc))
                results = []

        if not results and self._documents:
            py_scores = self._python_bm25_score(query, top_k)
            for did, score in py_scores:
                doc_info = self._documents[did]
                text = doc_info["text"]
                snippet = text[:200] + "..." if len(text) > 200 else text
                results.append(
                    ResultSnippet(
                        doc_id=did,
                        title=doc_info["title"],
                        snippet=snippet,
                        score=float(score),
                    )
                )

        session_id = str(uuid.uuid4())
        session = SearchSession(
            session_id=session_id,
            query=query,
            reason=reason,
            results=results,
        )
        self._session_cache[session_id] = session

        page_1 = results[:10]
        return {
            "session_id": session_id,
            "results": page_1,
        }

    def read_search_results(
        self,
        session_id: str,
        page: int = 1,
        page_size: int = 10,
    ) -> List[ResultSnippet]:
        """Page through cached search session ranking."""
        logger.info("bm25_read_search_results", session_id=session_id, page=page, page_size=page_size)

        session: Optional[SearchSession] = self._session_cache.get(session_id)
        if not session:
            logger.warning("bm25_session_not_found_or_expired", session_id=session_id)
            return []

        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        return session.results[start_idx:end_idx]

    def read_document(
        self,
        doc_id: str,
        offset: int = 1,
        limit: int = 100,
    ) -> str:
        """Return a bounded slice of document text."""
        logger.info("bm25_read_document", doc_id=doc_id, offset=offset, limit=limit)

        doc_info = self._documents.get(doc_id)
        if not doc_info:
            logger.warning("bm25_document_not_found", doc_id=doc_id)
            return ""

        text = doc_info["text"]
        lines = text.splitlines(keepends=True)

        if offset < 1:
            offset = 1
        if limit < 1:
            limit = 100

        start_idx = offset - 1
        end_idx = start_idx + limit

        if lines:
            selected = lines[start_idx:end_idx]
            return "".join(selected)

        # Character fallback if single line / empty lines
        char_start = offset - 1
        char_end = char_start + limit
        return text[char_start:char_end]


# Default module-level service instance
_default_bm25_service = BM25SearchService()


def get_default_bm25_service() -> BM25SearchService:
    """Get the global default BM25SearchService instance."""
    return _default_bm25_service


def search(
    query: str,
    reason: str,
    top_k: int = 100,
) -> Dict[str, Any]:
    """Run BM25 search against reference index and cache results in session store.

    Args:
        query: Search query string.
        reason: Purpose of query (required).
        top_k: Maximum ranked items to cache.

    Returns:
        Dict[str, Any]: Dict containing 'session_id' and first page 'results'.

    Raises:
        ValueError: If reason is empty or whitespace.
    """
    return _default_bm25_service.search(query=query, reason=reason, top_k=top_k)


def read_search_results(
    session_id: str,
    page: int = 1,
    page_size: int = 10,
) -> List[ResultSnippet]:
    """Retrieve paginated results from session cache without re-querying.

    Args:
        session_id: Session identifier.
        page: 1-indexed page number.
        page_size: Number of items per page.

    Returns:
        List[ResultSnippet]: List of result snippets for specified page.
    """
    return _default_bm25_service.read_search_results(session_id=session_id, page=page, page_size=page_size)


def read_document(
    doc_id: str,
    offset: int = 1,
    limit: int = 100,
) -> str:
    """Retrieve a bounded slice of document text.

    Args:
        doc_id: Document identifier.
        offset: 1-indexed starting line/char position.
        limit: Bounded length/count to read.

    Returns:
        str: Document text slice.
    """
    return _default_bm25_service.read_document(doc_id=doc_id, offset=offset, limit=limit)

"""Service for handling file uploads: validation, storage, and cleanup."""

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.logging import logger
from app.schemas.document import FileAttachment


EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "c",
    ".c": "c",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".hs": "haskell",
    ".erl": "erlang",
    ".ex": "elixir",
    ".exs": "elixir",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".edn": "clojure",
    ".zig": "zig",
    ".nim": "nim",
    ".dart": "dart",
}


class DocumentService:
    """Handles file upload validation, storage on disk, and cleanup."""

    @staticmethod
    def detect_language(filename: str) -> str:
        """Detect language from a file's extension.

        Args:
            filename: The original filename.

        Returns:
            Detected language string, or 'text' if unknown.
        """
        ext = Path(filename).suffix.lower()
        return EXTENSION_LANGUAGE_MAP.get(ext, "text")

    @staticmethod
    async def validate_file(file: UploadFile) -> str | None:
        """Validate a file against size and extension constraints.

        Args:
            file: The uploaded file.

        Returns:
            An error message string if invalid, or None if valid.
        """
        if not file.filename:
            return "filename_is_empty"

        ext = Path(file.filename).suffix.lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            return f"extension_not_allowed: {ext}"

        # Reject clearly-binary files: source files are text, and a null byte
        # in the first 1 KiB is a reliable binary signal regardless of the
        # client-supplied Content-Type.
        head = await file.read(1024)
        file.file.seek(0)
        if b"\x00" in head:
            return "binary_content_not_allowed"

        # Check file size by seeking to end
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if size > settings.MAX_FILE_SIZE:
            return f"file_too_large: {size} bytes exceeds {settings.MAX_FILE_SIZE} limit"

        return None

    @staticmethod
    async def save_file(
        file: UploadFile,
        user_id: int,
        session_id: str,
    ) -> FileAttachment:
        """Save an uploaded file to disk and return its metadata.

        Files are stored at: ``{UPLOAD_DIR}/{user_id}/{session_id}/{file_id}{ext}``

        Args:
            file: The uploaded file (must be validated first).
            user_id: The user who uploaded the file.
            session_id: The session the file belongs to.

        Returns:
            A FileAttachment with the file metadata.
        """
        file_id = str(uuid.uuid4())
        language = DocumentService.detect_language(file.filename or "unnamed")
        ext = Path(file.filename or "").suffix.lower()

        upload_path = Path(settings.UPLOAD_DIR) / str(user_id) / session_id / f"{file_id}{ext}"
        upload_path.parent.mkdir(parents=True, exist_ok=True)

        content = await file.read()
        if len(content) > settings.MAX_FILE_SIZE:
            raise ValueError(f"file_too_large: {len(content)} bytes exceeds {settings.MAX_FILE_SIZE} limit")

        upload_path.write_bytes(content)

        logger.info(
            "file_saved",
            file_id=file_id,
            original_name=file.filename,
            size=len(content),
            language=language,
            path=str(upload_path),
        )

        return FileAttachment(
            file_id=file_id,
            original_name=file.filename or "unnamed",
            stored_path=str(upload_path),
            language=language,
        )

    @staticmethod
    async def read_file(stored_path: str) -> str:
        """Read a file's content from disk as a string.

        Args:
            stored_path: The on-disk path of the file.

        Returns:
            The file contents as a string.
        """
        path = Path(stored_path)
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    async def delete_file(stored_path: str) -> None:
        """Delete a file from disk.

        Args:
            stored_path: The on-disk path of the file.
        """
        path = Path(stored_path)
        if path.exists():
            path.unlink()
            logger.info("file_deleted", path=str(path))

    @staticmethod
    async def cleanup_session_files(user_id: int, session_id: str) -> None:
        """Recursively delete all files for a session.

        Args:
            user_id: The user who owns the files.
            session_id: The session whose files to delete.
        """
        session_dir = Path(settings.UPLOAD_DIR) / str(user_id) / session_id
        if session_dir.exists():
            import shutil

            shutil.rmtree(session_dir)
            logger.info("session_files_cleaned", user_id=user_id, session_id=session_id)


document_service = DocumentService()

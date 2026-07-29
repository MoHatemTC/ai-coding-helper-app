"""Document and file attachment schemas for the document pipeline."""

from pydantic import BaseModel, Field


class FileAttachment(BaseModel):
    """Metadata for a file uploaded as part of a chat conversation."""

    file_id: str = Field(..., description="Unique identifier for the uploaded file")
    original_name: str = Field(..., description="Original filename as uploaded by the user")
    stored_path: str = Field(..., description="Path on disk where the file is stored")
    language: str = Field(..., description="Detected programming language of the file")

"""Document data model — used by provider, offering, listing, and promotion files."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import DocumentCategoryEnum, MimeTypeEnum


class DocumentData(BaseModel):
    """Document data for SDK/API payloads.

    Note: The document title is NOT stored here - it's the key in the documents dict.
    When stored in the database, the backend extracts the key as the title field.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=500, description="Document description")
    mime_type: MimeTypeEnum = Field(description="Document MIME type")
    version: str | None = Field(default=None, max_length=50, description="Document version")
    category: DocumentCategoryEnum = Field(description="Document category for organization and filtering")
    meta: dict[str, Any] | None = Field(
        default=None,
        description="JSON containing operation stats",
    )
    file_path: str | None = Field(
        default=None,
        max_length=1000,
        description="Path to file to upload (mutually exclusive with external_url)",
    )
    external_url: str | None = Field(
        default=None,
        max_length=1000,
        description="External URL for the document (mutually exclusive with object_key)",
    )
    sort_order: int = Field(default=0, description="Sort order within category")
    is_active: bool = Field(default=True, description="Whether document is active")
    is_public: bool = Field(
        default=False,
        description="Whether document is publicly accessible without authentication",
    )



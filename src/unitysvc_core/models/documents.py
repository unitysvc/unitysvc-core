"""Document data model — used by provider, offering, listing, and promotion files."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @field_validator("meta")
    @classmethod
    def _no_output_contains(cls, meta: dict[str, Any] | None) -> dict[str, Any] | None:
        """Refuse the retired ``meta.output_contains`` (unitysvc#2490): a test
        passes or fails on its exit code, and the backend refuses the key at
        ingest (unitysvc#2862), so catch it at ``specs validate`` instead."""
        if meta and "output_contains" in meta:
            raise ValueError(
                "'output_contains' is no longer supported. A test passes or fails on its "
                "exit code: assert inside the script and exit non-zero on failure (unitysvc#2490)."
            )
        return meta

    @model_validator(mode="after")
    def _instance_spec_format(self) -> DocumentData:
        """An ``instance_spec`` states its format in ``meta.format`` (default ``compose``); only that is accepted."""
        if self.category == DocumentCategoryEnum.instance_spec:
            from ..instance_spec import (
                SUPPORTED_FORMATS,
                environment_overlay_error,
                instance_spec_format,
                sha256_error,
                snapshot_url_error,
                unsupported_format_message,
            )

            fmt = instance_spec_format(self.meta)
            if fmt not in SUPPORTED_FORMATS:
                raise ValueError(unsupported_format_message((self.meta or {}).get("format")))
            if (self.file_path is None) == (self.external_url is None):
                raise ValueError("an instance_spec Document has exactly one of file_path or external_url")
            if self.external_url is not None and (problem := snapshot_url_error(self.external_url)):
                raise ValueError(f"instance_spec external_url {problem}")
            meta = self.meta or {}
            if "source" in meta and (problem := snapshot_url_error(meta["source"])):
                raise ValueError(f"instance_spec meta.source {problem}")
            if "sha256" in meta and (problem := sha256_error(meta["sha256"])):
                raise ValueError(f"instance_spec meta.sha256 {problem}")
            if "environment" in meta and (problem := environment_overlay_error(meta["environment"])):
                raise ValueError(f"instance_spec meta.environment {problem}")
        return self

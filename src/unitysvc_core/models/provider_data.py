"""Base data model for providers.

This module defines `ProviderData`, a base model containing the core fields
for provider data that is shared between:
- unitysvc-core (CLI): Used for file-based provider definitions
- unitysvc (backend): Used for API payloads and database operations

The `ProviderV1` model extends this with file-specific fields like `schema_version`
and `time_created` for data file validation.
"""

from typing import Any

from pydantic import BaseModel, EmailStr, Field, HttpUrl

from .base import ProviderStatusEnum


class ProviderData(BaseModel):
    """
    Base data structure for provider information.

    This model contains the core fields needed to describe a provider,
    without file-specific validation fields. It serves as:

    1. The base class for `ProviderV1` in unitysvc-core (with additional
       schema_version, time_created, and services_populator fields for file validation)

    2. The data structure imported by unitysvc backend for:
       - API payload validation
       - Database comparison logic in find_and_compare_provider()
       - Publish operations from CLI

    Key characteristics:
    - Uses string identifiers that match database requirements
    - Contains all user-provided data without system-generated IDs
    - Does not include permission/audit fields (handled by backend CRUD layer)
    """

    # Provider identification
    name: str = Field(
        description="Unique provider identifier (URL-friendly, e.g., 'fireworks', 'anthropic')",
        min_length=2,
        max_length=100,
    )

    display_name: str | None = Field(
        default=None,
        max_length=200,
        description="Human-readable provider name (e.g., 'Fireworks AI', 'Anthropic')",
    )

    # Contact information
    contact_email: EmailStr = Field(description="Primary contact email for the provider")

    secondary_contact_email: EmailStr | None = Field(
        default=None,
        description="Secondary contact email",
    )

    homepage: HttpUrl = Field(description="Provider's homepage URL")

    # Provider information
    description: str | None = Field(
        default=None,
        description="Brief description of the provider",
    )

    # Status
    status: ProviderStatusEnum = Field(
        default=ProviderStatusEnum.draft,
        description="Provider status: draft (skip publish), ready (for review), or deprecated (retired)",
    )

    # Documents (keyed by title, as dicts for flexibility)
    documents: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Documents associated with the provider, keyed by title",
    )

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from .documents import DocumentData
from .validators import validate_name
from .provider_data import ProviderData


class ServicesPopulator(BaseModel):
    """Configuration for automatically populating service data."""

    model_config = ConfigDict(extra="forbid")

    command: str | list[str] | None = Field(
        default=None,
        description="Command to execute for populating services (string or list of arguments). Comment out to disable.",
    )
    requirements: list[str] | None = Field(
        default=None,
        description="Python packages to install before executing the command (e.g., ['any-llm-sdk[anthropic]'])",
    )
    envs: dict[str, Any] | None = Field(
        default=None,
        description="Environment variables to set when executing the command (values converted to strings)",
    )


class ProviderV1(ProviderData):
    """
    Provider information for service providers (provider_v1 schema).

    Extends ProviderData with:
    - schema_version: Schema identifier for file validation
    - time_created: Timestamp for file creation
    - services_populator: How to automatically populate service data (with envs)
    - logo, terms_of_service: Convenience fields (converted to documents during import)
    - Typed Document model instead of dict for file validation
    - Field validators for name format
    """

    model_config = ConfigDict(extra="forbid")

    # File-specific fields for validation
    schema_version: str = Field(default="provider_v1", description="Schema identifier", alias="schema")
    time_created: datetime

    # How to automatically populate service data, if available
    services_populator: ServicesPopulator | None = None

    # Convenience fields for logo and terms of service (converted to documents during import)
    logo: str | HttpUrl | None = None

    terms_of_service: None | str | HttpUrl = Field(
        default=None,
        description="Either a path to a .md file or a URL to terms of service",
    )

    # Override with typed DocumentData model for file validation
    documents: dict[str, DocumentData] | None = Field(  # type: ignore[assignment]
        default=None,
        description="Documents associated with the provider, keyed by title (e.g. logo)",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Validate that provider name uses URL-safe identifiers."""
        return validate_name(v, "provider", allow_slash=False)

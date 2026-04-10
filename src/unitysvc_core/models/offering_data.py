"""Base data model for service offerings.

This module defines `ServiceOfferingData`, a base model containing the core fields
for service offering data that is shared between:
- unitysvc-core (CLI): Used for file-based service definitions
- unitysvc (backend): Used for API payloads and database operations

The `OfferingV1` model extends this with file-specific fields like `schema_version`
and `time_created` for data file validation.
"""

from typing import Any

from pydantic import BaseModel, Field

from .base import CurrencyEnum, OfferingStatusEnum, ServiceTypeEnum


class ServiceOfferingData(BaseModel):
    """
    Base data structure for service offering information.

    This model contains the core fields needed to describe a service offering,
    without file-specific validation fields. It serves as:

    1. The base class for `OfferingV1` in unitysvc-core (with additional
       schema_version and time_created fields for file validation)

    2. The data structure imported by unitysvc backend for:
       - API payload validation
       - Database comparison logic in find_and_compare_service_offering()
       - Publish operations from CLI

    Key characteristics:
    - status maps to database 'status' field
    - Contains all user-provided data without system-generated IDs
    - Does not include permission/audit fields (handled by backend CRUD layer)
    - Provider relationship is determined by file location (SDK mode) or
      by being published together in a single API call (API mode)
    """

    model_config = {"extra": "ignore"}

    # Service identification
    name: str = Field(
        description="Technical service name (e.g., 'gpt-4')",
        max_length=100,
    )

    display_name: str | None = Field(
        default=None,
        max_length=200,
        description="Human-readable service name for display (e.g., 'GPT-4 Turbo', 'Claude 3 Opus')",
    )

    service_type: ServiceTypeEnum = Field(
        default=ServiceTypeEnum.llm,
        description="Category for grouping/comparison",
    )

    capabilities: list[str] = Field(
        default_factory=list,
        description="Specific features this service provides (e.g., 'text_to_speech', 'embedding')",
    )

    description: str | None = Field(
        default=None,
        description="Service description",
    )

    tagline: str | None = Field(
        default=None,
        description="Short elevator pitch or description for the service",
    )

    # Status
    status: OfferingStatusEnum = Field(
        default=OfferingStatusEnum.draft,
        description="Offering status: draft (skip publish), ready (for review), or deprecated (retired)",
    )

    # Technical details
    details: dict[str, Any] | None = Field(
        default=None,
        description="Static technical specifications and features",
    )

    # Pricing
    payout_price: dict[str, Any] | None = Field(
        default=None,
        description="Payout pricing: How to calculate seller payout",
    )

    # Access interfaces (keyed by name)
    upstream_access_config: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Upstream access interfaces, keyed by name",
    )

    # Documents (keyed by title)
    documents: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Documents associated with the service, keyed by title",
    )

    # Currency for payout_price
    currency: CurrencyEnum = Field(
        default=CurrencyEnum.USD,
        description="Currency for payout_price",
    )

    # Tags for the service (arbitrary strings, e.g., 'byok', 'free', 'open-data')
    tags: list[str] | None = Field(
        default=None,
        description="List of tags for the service (arbitrary strings)",
    )

"""Base data models for service listings.

This module defines `ServiceListingData`, a base model containing the core fields
for service listing data that is shared between:
- unitysvc-core (CLI): Used for file-based listing definitions
- unitysvc (backend): Used for API payloads and database operations

The `ListingV1` model extends this with file-specific fields like `schema_version`
and `time_created` for data file validation.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .base import CurrencyEnum, ListingStatusEnum


class ServiceListingData(BaseModel):
    """
    Base data structure for service listing information.

    This model contains the core fields needed to describe a service listing,
    without file-specific validation fields. It serves as:

    1. The base class for `ListingV1` in unitysvc-core (with additional
       schema_version and time_created fields for file validation)

    2. The data structure imported by unitysvc backend for:
       - API payload validation
       - Database comparison logic in find_and_compare_service_listing()
       - Publish operations from CLI

    Key characteristics:
    - Contains all user-provided data without system-generated IDs
    - Does not include permission/audit fields (handled by backend CRUD layer)
    - Uses dict types for nested structures to maintain flexibility between
      file definitions and database operations
    - Service/provider relationships are determined by file location (SDK mode) or
      by being published together in a single API call (API mode)
    """

    model_config = {"extra": "ignore"}

    # Service ID for updates (set by SDK after first publish)
    # When provided, updates the existing service. When absent, creates a new service.
    service_id: UUID | None = Field(
        default=None,
        description="Service ID from previous publish. If provided, updates existing service. "
        "Stored in override file (e.g., listing.override.json) by SDK after first publish.",
    )

    # Listing identification
    name: str | None = Field(
        default=None,
        max_length=255,
        description="Name identifier for the service listing (defaults to offering name if not provided)",
    )

    # Display name for UI (optional - falls back to Service.name derived from offering/listing name)
    display_name: str | None = Field(
        default=None,
        max_length=200,
        description="Human-readable listing name (e.g., 'Premium GPT-4 Access', 'Enterprise AI Services')",
    )

    # Status - seller-accessible statuses
    status: ListingStatusEnum = Field(
        default=ListingStatusEnum.draft,
        description="Listing status: draft (skip publish), ready (ready for admin review), or deprecated (retired)",
    )

    # List pricing
    list_price: dict[str, Any] | None = Field(
        default=None,
        description="List price: Listed price for customers per unit of service usage",
    )

    # Currency for list_price
    currency: CurrencyEnum = Field(
        default=CurrencyEnum.USD,
        description="Currency for list_price (indexed for filtering)",
    )

    # Access interfaces (keyed by name)
    user_access_interfaces: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="User access interfaces for the listing, keyed by name",
    )

    # Documents (keyed by title)
    documents: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Documents associated with the listing, keyed by title (e.g., service level agreements)",
    )

    # User parameters
    user_parameters_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for user parameters",
    )

    user_parameters_ui_schema: dict[str, Any] | None = Field(
        default=None,
        description="UI schema for user parameters form rendering",
    )

    # Service-specific options
    service_options: dict[str, Any] | None = Field(
        default=None,
        description="Service-specific options that modify backend behavior. "
        "Keys are option names, values are option configurations. "
        "The backend decides which options it supports.",
    )

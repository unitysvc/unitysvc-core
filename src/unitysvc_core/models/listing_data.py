"""Base data models for service listings.

This module defines `ServiceListingData`, a base model containing the core fields
for service listing data that is shared between:
- unitysvc-core (CLI): Used for file-based listing definitions
- unitysvc (backend): Used for API payloads and database operations

The `ListingV1` model extends this with file-specific fields
and `time_created` for data file validation.
"""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .base import CurrencyEnum, ListingStatusEnum

AUDIENCE_MAX_PHRASES = 8
AUDIENCE_MAX_LENGTH = 120
# Plain text only: details belong in the description or documents.
_AUDIENCE_FORBIDDEN = re.compile(r"https?://|www\.|[<>\[\]`]", re.IGNORECASE)


class ServiceListingData(BaseModel):
    """
    Base data structure for service listing information.

    This model contains the core fields needed to describe a service listing,
    without file-specific validation fields. It serves as:

    1. The base class for `ListingV1` in unitysvc-core (with additional
       time_created field for file validation)

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

    # NOTE: a listing carries no service_id. Service identity is backend-
    # assigned provenance, not authored listing content — it lives in
    # ``service.json`` (the ``ServiceStatus`` sidecar) and travels separately
    # on upload/revision, never embedded in the listing.

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

    # Who the service is for (unitysvc/unitysvc#2717)
    audience: list[str] | None = Field(
        default=None,
        description=(
            "Who the service is for and what a buyer must know before enrolling, as short "
            "plain-text phrases (e.g. 'US customers only / export restrictions may apply'). "
            "Shown before enrollment; informational only, never enforced. "
            f"At most {AUDIENCE_MAX_PHRASES} phrases of 1-{AUDIENCE_MAX_LENGTH} characters, no URLs or markup."
        ),
    )

    # Service-specific options
    service_options: dict[str, Any] | None = Field(
        default=None,
        description="Service-specific options that modify backend behavior. "
        "Keys are option names, values are option configurations. "
        "The backend decides which options it supports.",
    )

    @field_validator("audience")
    @classmethod
    def validate_audience(cls, v: list[str] | None) -> list[str] | None:
        """At most 8 short plain-text phrases, each trimmed to 1-120 characters,
        with no URLs or markup (unitysvc/unitysvc#2717)."""
        if v is None:
            return None
        if len(v) > AUDIENCE_MAX_PHRASES:
            raise ValueError(f"audience has at most {AUDIENCE_MAX_PHRASES} phrases, got {len(v)}")
        phrases = [phrase.strip() for phrase in v]
        for phrase in phrases:
            if not 1 <= len(phrase) <= AUDIENCE_MAX_LENGTH:
                raise ValueError(f"each audience phrase is 1-{AUDIENCE_MAX_LENGTH} characters, got {phrase!r}")
            if "\n" in phrase or _AUDIENCE_FORBIDDEN.search(phrase):
                raise ValueError(
                    f"audience phrases are plain text, without URLs or markup: {phrase!r}; "
                    "put details in the description or documents"
                )
        if len(set(phrases)) != len(phrases):
            raise ValueError("audience lists a phrase twice")
        return phrases

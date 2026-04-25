"""Subscription Plan data model.

Single source of truth for subscription plan payloads across:

1. Admin CLI uploads (``usvc_admin plan upload`` / ``plan validate``).
2. unitysvc backend payload validation.

The ``slug`` field is enforced as a URL-friendly slug (matching
the pattern used elsewhere for stable public identifiers — group
names, access interface names) so plans hardcoded in scripts
survive admin recreations and remain safe in URLs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Mirrors the ``slug`` rule used for service group names. Lowercase
# ASCII alphanumeric plus '-' and '_', must start with letter/digit.
_SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class SubscriptionTierEnum(StrEnum):
    """High-level subscription tier categories."""

    free = "free"
    individual = "individual"  # Paid individual tiers
    team = "team"  # Team/group tiers
    enterprise = "enterprise"  # Enterprise tiers


class SubscriptionPlanStatusEnum(StrEnum):
    """Subscription plan lifecycle and visibility status.

    - incomplete: Plan is being drafted/configured, can be updated
    - active: Plan is live and shown on public pricing page, immutable
    - private: Plan is live but hidden (custom/enterprise plans), immutable
    - expired: Plan is archived, no longer available for new subscriptions
    """

    incomplete = "incomplete"
    active = "active"
    private = "private"
    expired = "expired"


SUBSCRIPTION_PLAN_SCHEMA_VERSION = "subscription_plan_v1"


class SubscriptionPlanV1(BaseModel):
    """Subscription Plan model for CLI publishing and backend ingest."""

    model_config = ConfigDict(extra="forbid")

    # ============================================================================
    # CLI-specific metadata fields
    # ============================================================================

    schema_version: str = Field(
        default=SUBSCRIPTION_PLAN_SCHEMA_VERSION,
        description="Schema identifier for validation",
        alias="schema",
    )
    time_created: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when this plan file was created",
    )

    # ============================================================================
    # Plan identification
    # ============================================================================

    slug: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=_SLUG_PATTERN,
        description=(
            "Unique plan identifier (e.g., 'pro-2025-v1', 'enterprise-acme'). "
            "URL-friendly slug: lowercase ASCII alphanumeric plus '-' / '_', "
            "must start with a letter or digit."
        ),
    )
    name: str = Field(
        ...,
        description="Plan name (e.g., 'Pro', 'Team', 'Enterprise')",
    )
    tier: SubscriptionTierEnum = Field(
        ...,
        description="Tier category: free, individual, team, enterprise",
    )
    display_name: str = Field(
        ...,
        description="Human-readable display name for marketing/UI",
    )
    description: str | None = Field(
        default=None,
        description="Plan description for marketing/UI",
    )

    # ============================================================================
    # Plan availability
    # ============================================================================

    status: SubscriptionPlanStatusEnum = Field(
        default=SubscriptionPlanStatusEnum.incomplete,
        description="Plan lifecycle status: incomplete, active, private, expired",
    )
    valid_from: datetime | None = Field(
        default=None,
        description="Plan availability start date (defaults to now if not specified)",
    )
    valid_until: datetime | None = Field(
        default=None,
        description="Plan availability end date (None = no end)",
    )

    # ============================================================================
    # Pricing
    # ============================================================================

    base_amount: Decimal = Field(
        ...,
        description="Base price per billing interval (Decimal for currency precision)",
    )
    annual_discount_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Discount percentage for annual billing (0-100)",
    )
    currency: str = Field(
        default="USD",
        description="Currency code (ISO 4217)",
    )
    included_seats: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Number of seats included in base_amount. "
            "None = flat pricing (quantity ignored). "
            "0 = simple per-seat (base_amount ignored, additional_seat_price * quantity). "
            "N = tiered (base_amount covers N seats, additional_seat_price per extra)."
        ),
    )
    additional_seat_price: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "Price per seat beyond included_seats. "
            "Required when included_seats is set."
        ),
    )

    # ============================================================================
    # Plan terms and metadata
    # ============================================================================

    terms: dict[str, Any] = Field(
        ...,
        description="Plan terms including features, limits, SLA, etc.",
    )
    plan_pricing: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Plan-level pricing that replaces per-request charges at statement time. "
            "Supports graduated/tiered pricing based on total_customer_charge or request_count."
        ),
    )
    extra_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional metadata (templates, taglines, marketing copy, etc.)",
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate that currency is a 3-letter uppercase ISO 4217 code."""
        if len(v) != 3 or not v.isupper():
            raise ValueError(
                f"Currency '{v}' must be a 3-letter uppercase ISO 4217 code "
                "(e.g., 'USD', 'EUR')"
            )
        return v

    @field_validator("base_amount")
    @classmethod
    def validate_base_amount(cls, v: Decimal) -> Decimal:
        """Validate that base amount is non-negative."""
        if v < 0:
            raise ValueError(f"Base amount must be non-negative, got {v}")
        return v


def is_subscription_plan_file(data: dict[str, Any]) -> bool:
    """Check if a data dict is a subscription plan file (by schema version)."""
    return data.get("schema") == SUBSCRIPTION_PLAN_SCHEMA_VERSION

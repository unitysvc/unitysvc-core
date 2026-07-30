"""Service-level constraint, rate-limit, and access-interface models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .base import (
    AccessMethodEnum,
    AuthMethodEnum,
    ContentFilterEnum,
    OveragePolicyEnum,
    QuotaResetCycleEnum,
    RateLimitUnitEnum,
    TimeWindowEnum,
)


class RateLimit(BaseModel):
    """Store rate limiting rules for services."""

    model_config = ConfigDict(extra="forbid")

    # Core rate limit definition
    limit: int = Field(description="Maximum allowed in the time window")
    unit: RateLimitUnitEnum = Field(description="What is being limited")
    window: TimeWindowEnum = Field(description="Time window for the limit")

    # Optional additional info
    description: str | None = Field(default=None, max_length=255, description="Human-readable description")
    burst_limit: int | None = Field(default=None, description="Short-term burst allowance")

    # Status
    is_active: bool = Field(default=True, description="Whether rate limit is active")


class ServiceConstraints(BaseModel):
    """DEPRECATED — planned SLA/quota surface that was never enforced.

    No routing resolver or gateway path ever read these fields; they were
    authored nowhere in seller data. The ``constraints`` field has been removed
    from ``AccessInterfaceData``. The class is retained (still exported) only so
    existing imports do not break; it is unused and slated for removal. Do not
    add new references. See unitysvc/unitysvc#1717.
    """

    model_config = ConfigDict(extra="forbid")

    # Usage Quotas & Billing
    monthly_quota: int | None = Field(default=None, description="Monthly usage quota (requests, tokens, etc.)")
    daily_quota: int | None = Field(default=None, description="Daily usage quota (requests, tokens, etc.)")
    quota_unit: RateLimitUnitEnum | None = Field(default=None, description="Unit for quota limits")
    quota_reset_cycle: QuotaResetCycleEnum | None = Field(default=None, description="How often quotas reset")
    overage_policy: OveragePolicyEnum | None = Field(default=None, description="What happens when quota is exceeded")

    # Authentication & Security
    auth_methods: list[AuthMethodEnum] | None = Field(default=None, description="Supported authentication methods")
    ip_whitelist_required: bool | None = Field(default=None, description="Whether IP whitelisting is required")
    tls_version_min: str | None = Field(default=None, description="Minimum TLS version required")

    # Request/Response Constraints
    max_request_size_bytes: int | None = Field(default=None, description="Maximum request payload size in bytes")
    max_response_size_bytes: int | None = Field(default=None, description="Maximum response payload size in bytes")
    timeout_seconds: int | None = Field(default=None, description="Request timeout in seconds")
    max_batch_size: int | None = Field(default=None, description="Maximum number of items in batch requests")

    # Content & Model Restrictions
    content_filters: list[ContentFilterEnum] | None = Field(
        default=None, description="Active content filtering policies"
    )
    input_languages: list[str] | None = Field(default=None, description="Supported input languages (ISO 639-1 codes)")
    output_languages: list[str] | None = Field(default=None, description="Supported output languages (ISO 639-1 codes)")
    max_context_length: int | None = Field(default=None, description="Maximum context length in tokens")
    region_restrictions: list[str] | None = Field(
        default=None, description="Geographic restrictions (ISO country codes)"
    )

    # Availability & SLA
    uptime_sla_percent: float | None = Field(default=None, description="Uptime SLA percentage (e.g., 99.9)")
    response_time_sla_ms: int | None = Field(default=None, description="Response time SLA in milliseconds")
    maintenance_windows: list[str] | None = Field(default=None, description="Scheduled maintenance windows")

    # Concurrency & Connection Limits
    max_concurrent_requests: int | None = Field(default=None, description="Maximum concurrent requests allowed")
    connection_timeout_seconds: int | None = Field(default=None, description="Connection timeout in seconds")
    max_connections_per_ip: int | None = Field(default=None, description="Maximum connections per IP address")


class AccessInterfaceData(BaseModel):
    """User-facing access interface data — a **pure routing-resolution object**.

    It answers "which candidate does this request address?" — nothing more. It
    carries only the customer-facing address (``base_url``), the request
    ``routing_key``, and selection/visibility metadata (``is_active`` /
    ``is_primary`` / ``sort_order``). All upstream-access and enforcement
    concerns (endpoint credentials, transformers, response-eval rules, rate
    limits) live on the per-channel ``upstream_access_config`` entry — an
    opaque JSON object the gateway reads as raw JSONB — which is where the
    gateway resolves them.

    Historically this also carried ``rate_limits`` / ``constraints`` /
    ``response_rules``; those were authored nowhere in customer-facing
    interfaces and have moved to the channel (rate limits) or been dropped
    (``constraints`` — never enforced). See unitysvc/unitysvc#1717.

    Note: The interface name is NOT stored here - it's the key in the interfaces dict.
    When stored in the database, the backend extracts the key as the name field.
    """

    model_config = ConfigDict(extra="forbid")

    access_method: AccessMethodEnum = Field(default=AccessMethodEnum.http, description="Type of access method")

    base_url: str = Field(max_length=500, description="Base URL for api access")

    description: str | None = Field(default=None, max_length=500, description="Interface description")

    routing_key: dict[str, Any] | None = Field(
        default=None,
        description="Request routing key for matching (e.g., {'model': 'gpt-4'})",
    )

    is_active: bool = Field(default=True, description="Whether interface is active")
    is_primary: bool = Field(default=False, description="Whether this is the primary interface")
    sort_order: int = Field(default=0, description="Display order")


class ServiceStatus(BaseModel):
    """Backend-assigned service **status / identity sidecar** (the ``service.json``
    file) — *not* the authored service data.

    A service's ``provider_data`` / ``offering_data`` / ``listing_data`` are the
    authored service data; this is the *other*, backend-owned half — the status
    record the backend materializes once a service exists. It is the
    **round-trip** sidecar: the ingest task returns it, the seller stores it in
    ``service.json`` beside the spec files, and replays it on the next
    upload/revision so the backend can match the upload to the existing service.

    Of these fields, ``service_id`` and ``template_instance_id`` are consumed on
    the way *in* (they declare which service / which template the publish
    targets); the rest are populated on the way *out* as informational status /
    provenance for the seller. ``extra="ignore"`` keeps unknown keys from
    breaking either direction.
    """

    model_config = ConfigDict(extra="ignore")

    service_id: UUID | None = Field(
        default=None,
        description=(
            "Backend-assigned service id from a previous publish. When present "
            "on upload, the request targets the existing service (revise/"
            "replace) instead of creating a new one."
        ),
    )

    revision_of: UUID | None = Field(
        default=None,
        description=(
            "Set when this record describes a revision: the canonical service "
            "id the revision derives from. ``service_id`` is then the revision's "
            "own id."
        ),
    )

    status: str | None = Field(
        default=None,
        description="Service identity status as resolved by the last ingest.",
    )

    name: str | None = Field(
        default=None,
        description="Backend-derived service name (from listing/offering).",
    )

    display_name: str | None = Field(
        default=None,
        description="Backend-derived human-readable service name.",
    )

    time_created: datetime | None = Field(
        default=None,
        description="When the service was first created (informational provenance).",
    )

    template_instance_id: UUID | None = Field(
        default=None,
        description=(
            "Set when the service was published from a seller TemplateInstance "
            "(the seller-instances flow). Like ``service_id`` it is consumed on "
            "the way *in* to declare the operation — with no ``service_id`` it "
            "creates a service from the template (the backend pins the form's "
            "service_id); with one it updates an existing template-generated "
            "service — and echoed on the way *out* so ``service.json`` records "
            "the template association. Absent for plain (non-template) services."
        ),
    )

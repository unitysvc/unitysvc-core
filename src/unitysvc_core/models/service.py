"""Service-level constraint, rate-limit, and access-interface models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import (
    AccessMethodEnum,
    AuthMethodEnum,
    ContentFilterEnum,
    OveragePolicyEnum,
    QuotaResetCycleEnum,
    RateLimitUnitEnum,
    RequestTransformEnum,
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
    """User-facing access interface data (customer side).

    Note: The interface name is NOT stored here - it's the key in the interfaces dict.
    When stored in the database, the backend extracts the key as the name field.
    """

    model_config = ConfigDict(extra="forbid")

    access_method: AccessMethodEnum = Field(default=AccessMethodEnum.http, description="Type of access method")

    base_url: str = Field(max_length=500, description="Base URL for api access")

    api_key: str | None = Field(default=None, max_length=2000, description="API key if required")

    description: str | None = Field(default=None, max_length=500, description="Interface description")

    request_transformer: dict[RequestTransformEnum, dict[str, Any] | str] | None = Field(
        default=None, description="Request transformation configuration"
    )

    routing_key: dict[str, Any] | None = Field(
        default=None,
        description="Request routing key for matching (e.g., {'model': 'gpt-4'})",
    )

    rate_limits: list[RateLimit] | None = Field(
        default=None,
        description="Rate limit",
    )
    constraints: ServiceConstraints | None = Field(default=None, description="Service constraints and conditions")
    response_rules: dict[str, dict[str, Any] | str] | None = Field(
        default=None,
        description="Response evaluation rules keyed by rule name. "
        "Values are either a rule dict or a Jinja2 template string. Validated by the backend.",
    )
    is_active: bool = Field(default=True, description="Whether interface is active")
    is_primary: bool = Field(default=False, description="Whether this is the primary interface")
    sort_order: int = Field(default=0, description="Display order")


class UpstreamAccessConfigData(AccessInterfaceData):
    """Upstream (seller-facing) access config data.

    Extends AccessInterfaceData with extra="allow" to support protocol-specific
    configuration fields (e.g., S3 bucket/region, SMTP host/port) that the
    gateway needs to reach the upstream service.
    """

    model_config = ConfigDict(extra="allow")

    # base_url is optional for upstream configs (e.g., S3 uses bucket + region instead)
    base_url: str | None = Field(default=None, max_length=500, description="Base URL for api access")  # type: ignore[assignment]


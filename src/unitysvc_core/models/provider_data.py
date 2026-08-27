"""Base data model for providers.

This module defines `ProviderData`, a base model containing the core fields
for provider data that is shared between:
- unitysvc-core (CLI): Used for file-based provider definitions
- unitysvc (backend): Used for API payloads and database operations

The `ProviderV1` model extends this with file-specific fields
and `time_created` for data file validation.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, model_validator

from .base import ProviderStatusEnum, RateLimitUnitEnum, TimeWindowEnum


class ProviderAccountRateLimit(BaseModel):
    """One ceiling the provider grants the SELLER'S ACCOUNT.

    This is the only rate limit a seller can state truthfully. Providers scope
    their limits to the account that owns the upstream key — OpenAI, Groq and
    Anthropic at the org level, Mistral per workspace, Parasail per account —
    so the number belongs to the provider record, once, not to each of the
    seller's services.

    Declaring it per service is not merely coarse, it inverts: a 60 RPM account
    ceiling written onto 18 services authorises 1080 RPM against an account
    that grants 60. See unitysvc/unitysvc#1937.

    What a seller CANNOT state is any individual customer's allowance — that
    depends on how many customers are active at request time. The gateway
    derives that from this ceiling; nobody authors it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=100,
        description=(
            "Seller-scoped rate-limit bucket name. Channels reference this "
            "name via rate_limit_refs / ops_rate_limit_refs; all matching refs "
            "for the same seller consume the same live gateway bucket."
        ),
    )

    limit: int = Field(gt=0, description="Maximum allowed — in flight for `concurrent`, per window otherwise")

    unit: RateLimitUnitEnum = Field(description="What is being limited (requests, tokens, concurrent, …)")

    window: TimeWindowEnum | None = Field(
        default=None,
        description="Time window. Omitted for `concurrent`, which is a gauge rather than a counter.",
    )

    description: str | None = Field(
        default=None,
        max_length=255,
        description="Where the number came from, e.g. the provider's published limit for this tier",
    )

    @model_validator(mode="after")
    def _window_matches_unit(self) -> "ProviderAccountRateLimit":
        """`concurrent` is a gauge; everything else is a windowed counter.

        The distinction is load-bearing downstream, not pedantry: a concurrency
        slot returns when the request finishes, so capacity lent to one caller
        comes back within seconds. A windowed counter does not refill until the
        window rolls, so the same lending starves later callers for the rest of
        it. Requiring the field to match the unit stops a limit being authored
        that the enforcement layer cannot honour as written.
        """
        if self.unit is RateLimitUnitEnum.concurrent:
            if self.window is not None:
                raise ValueError("`concurrent` is an in-flight gauge and takes no window; omit it")
        elif self.window is None:
            raise ValueError(f"`{self.unit.value}` is counted over a window; set `window`")
        return self


class ProviderData(BaseModel):
    """
    Base data structure for provider information.

    This model contains the core fields needed to describe a provider,
    without file-specific validation fields. It serves as:

    1. The base class for `ProviderV1` in unitysvc-core (with additional
       time_created and services_populator fields for file validation)

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

    # What the upstream grants this seller's account (unitysvc/unitysvc#1937).
    # Omitted means "not declared", which the gateway reads as no enforcement —
    # the same behaviour as today, but chosen rather than accidental.
    rate_limits: list[ProviderAccountRateLimit] | None = Field(
        default=None,
        description=(
            "Ceilings the provider grants the seller's account, shared by every service "
            "and customer routed through that credential. Not per service, and not per "
            "customer — the gateway derives a customer's share from these."
        ),
    )

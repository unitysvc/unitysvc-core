"""Enum types and basic definitions shared across the data models.

This module contains only enums and simple constants. Data classes with
behavior (pricing, documents, service constraints, etc.) and validation
functions live in their own modules:

- ``pricing.py`` — pricing types and cost calculation
- ``documents.py`` — DocumentData
- ``service.py`` — RateLimit, ServiceConstraints, AccessInterfaceData, UpstreamAccessConfigData
- ``validators.py`` — validate_name, validate_service_options, suggest_valid_name
"""

from __future__ import annotations

from enum import StrEnum


class AccessMethodEnum(StrEnum):
    http = "http"
    websocket = "websocket"
    grpc = "grpc"
    smtp = "smtp"


class CurrencyEnum(StrEnum):
    """Supported currency codes for pricing."""

    # Traditional currencies
    USD = "USD"  # US Dollar
    EUR = "EUR"  # Euro
    GBP = "GBP"  # British Pound
    JPY = "JPY"  # Japanese Yen
    CNY = "CNY"  # Chinese Yuan
    CAD = "CAD"  # Canadian Dollar
    AUD = "AUD"  # Australian Dollar
    CHF = "CHF"  # Swiss Franc
    INR = "INR"  # Indian Rupee
    KRW = "KRW"  # Korean Won

    # Cryptocurrencies
    BTC = "BTC"  # Bitcoin
    ETH = "ETH"  # Ethereum
    USDT = "USDT"  # Tether
    USDC = "USDC"  # USD Coin
    TAO = "TAO"  # Bittensor TAO

    # Credits/Points (for platforms that use credits)
    CREDITS = "CREDITS"  # Generic credits system


class AuthMethodEnum(StrEnum):
    api_key = "api_key"
    oauth = "oauth"
    jwt = "jwt"
    bearer_token = "bearer_token"
    basic_auth = "basic_auth"


class ContentFilterEnum(StrEnum):
    adult = "adult"
    violence = "violence"
    hate_speech = "hate_speech"
    profanity = "profanity"
    pii = "pii"  # Personally Identifiable Information


class DocumentContextEnum(StrEnum):
    service_definition = "service_definition"  # Documents belong to ServiceDefinition
    service_offering = "service_offering"  # Documents belong to ServiceOffering
    service_listing = "service_listing"  # Documents belong to ServiceListing
    user = "user"  # can be for seller, subscriber, consumer
    # Backend-specific contexts
    seller = "seller"  # Documents belong to Seller
    provider = "provider"  # Documents belong to Provider
    blog_post = "blog_post"  # Documents belong to BlogPost
    #
    customer_statement = "customer_statement"
    seller_invoice = "seller_invoice"


class DocumentCategoryEnum(StrEnum):
    getting_started = "getting_started"
    api_reference = "api_reference"
    tutorial = "tutorial"
    code_example = "code_example"
    code_example_output = "code_example_output"
    connectivity_test = "connectivity_test"  # Test connectivity & performance (not visible to users)
    request_template = "request_template"  # Default request body for playground pre-fill
    use_case = "use_case"
    troubleshooting = "troubleshooting"
    changelog = "changelog"
    best_practice = "best_practice"
    specification = "specification"
    service_level_agreement = "service_level_agreement"
    terms_of_service = "terms_of_service"
    statement = "statement"
    invoice = "invoice"
    logo = "logo"
    avatar = "avatar"
    blog_content = "blog_content"  # Main content for blog posts
    blog_banner = "blog_banner"  # Banner/cover image for blog posts
    attachment = "attachment"  # Attachments for markdown documents
    other = "other"


class MimeTypeEnum(StrEnum):
    markdown = "markdown"
    python = "python"
    javascript = "javascript"
    bash = "bash"
    html = "html"
    json = "json"
    text = "text"
    pdf = "pdf"
    jpeg = "jpeg"
    png = "png"
    svg = "svg"
    url = "url"


class ServiceGroupStatusEnum(StrEnum):
    draft = "draft"
    active = "active"
    private = "private"
    archived = "archived"


class GroupOwnerTypeEnum(StrEnum):
    """Type of entity that owns a service group."""

    platform = "platform"
    seller = "seller"
    customer = "customer"


class GroupTypeEnum(StrEnum):
    """Type of service group.

    - ``regular``: Enrollable, services-bearing group (the common case).
    - ``category``: Non-enrollable parent that organizes child groups.
    - ``misc``: System-generated catch-all for uncategorized services.
    """

    regular = "regular"
    category = "category"
    misc = "misc"


class SellerTypeEnum(StrEnum):
    individual = "individual"
    organization = "organization"
    partnership = "partnership"
    corporation = "corporation"


class ListingStatusEnum(StrEnum):
    """
    Status values that sellers can set for listings.

    Seller-accessible statuses:
    - draft: Work in progress, skipped during publish (won't be sent to backend)
    - ready: Complete and ready for admin review/testing
    - deprecated: Retired/end of life, no longer offered

    Note: Admin-managed workflow statuses (upstream_ready, downstream_ready, in_service)
    are set by the backend admin after testing and validation. These are not included in this
    enum since sellers cannot set them through the CLI tool.
    """

    draft = "draft"
    ready = "ready"
    deprecated = "deprecated"


class OveragePolicyEnum(StrEnum):
    block = "block"  # Block requests when quota exceeded
    throttle = "throttle"  # Reduce rate when quota exceeded
    charge = "charge"  # Allow with additional charges
    queue = "queue"  # Queue requests until quota resets


class PricingTypeEnum(StrEnum):
    """
    Pricing type determines the structure and calculation method.
    The type is stored as the 'type' field in the pricing object.
    """

    # Basic pricing types
    one_million_tokens = "one_million_tokens"
    one_thousand_tokens = "one_thousand_tokens"
    one_token = "one_token"
    one_second = "one_second"
    one_minute = "one_minute"
    one_hour = "one_hour"
    one_day = "one_day"
    one_month = "one_month"
    one_byte = "one_byte"
    one_kilobyte = "one_kilobyte"
    one_megabyte = "one_megabyte"
    one_gigabyte = "one_gigabyte"
    one_thousand = "one_thousand"
    one_million = "one_million"
    image = "image"
    step = "step"
    # Seller-only: seller receives a percentage of what customer pays
    revenue_share = "revenue_share"
    # Composite pricing types
    constant = "constant"  # Fixed amount (fee or discount)
    add = "add"  # Sum of multiple prices
    multiply = "multiply"  # Base price multiplied by factor
    max = "max"  # Highest of multiple prices (lenient)
    min = "min"  # Lowest of multiple prices (lenient)
    first = "first"  # First applicable price (lenient)
    # Tiered pricing types
    tiered = "tiered"  # Volume-based tiers (all units at one tier's price)
    graduated = "graduated"  # Graduated tiers (each tier's units at that rate)
    # Expression-based pricing (payout_price only)
    expr = "expr"  # Arbitrary expression using usage metrics



class QuotaResetCycleEnum(StrEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    yearly = "yearly"


class RateLimitUnitEnum(StrEnum):
    requests = "requests"
    tokens = "tokens"
    input_tokens = "input_tokens"
    output_tokens = "output_tokens"
    bytes = "bytes"
    concurrent = "concurrent"


class RequestTransformEnum(StrEnum):
    # https://docs.api7.ai/hub/proxy-rewrite
    proxy_rewrite = "proxy_rewrite"
    # https://docs.api7.ai/hub/body-transformer
    body_transformer = "body_transformer"
    # Simple body replacement from rendered enrollment data
    set_body = "set_body"


class ServiceTypeEnum(StrEnum):
    """Broad service category — defines the access pattern and protocol.

    AI modalities (vision, tools, rerank, etc.) are tracked via the
    `capabilities` list on ServiceOffering, not service_type.
    """

    # === AI / ML services (HTTP API) ===
    llm = "llm"  # Language models, chat completions
    embedding = "embedding"  # Text/vector embedding
    image_generation = "image_generation"  # Image creation/editing

    # === Communication services ===
    notification = "notification"  # Push notifications (HTTP)
    email = "email"  # Email delivery (SMTP gateway)

    # === Content services ===
    content = "content"  # Downloadable files, datasets, images, software
    streaming = "streaming"  # Video, audio, live feeds (persistent connection)

    # === Compute services ===
    compute = "compute"  # GPU instances, dev environments (SSH/WireGuard)

    # === Proxy services ===
    # Protocol-level passthrough to a customer-owned upstream. Platform relays
    # or proxies requests without interpreting their content (e.g. http-relay,
    # smtp-relay, s3-relay, s3-proxy-multi).
    proxy = "proxy"

    # === Infrastructure services ===
    database = "database"  # Managed DB/cache access (SSH tunnel)
    monitoring = "monitoring"  # Uptime checks, health monitoring
    analytics = "analytics"  # Recommendation, anomaly detection, forecasting


class ServiceVisibilityEnum(StrEnum):
    """Visibility of a service in the catalog.

    - unlisted: Live and routable, not in catalog, accessible via direct link
    - public: In catalog, fully discoverable
    - private: Live and routable, ops/internal use only
    """

    unlisted = "unlisted"
    public = "public"
    private = "private"


class TimeWindowEnum(StrEnum):
    second = "second"
    minute = "minute"
    hour = "hour"
    day = "day"
    month = "month"


class OfferingStatusEnum(StrEnum):
    """
    Status values that sellers can set for service offerings.

    Seller-accessible statuses:
    - draft: Work in progress, skipped during publish
    - ready: Complete and ready for admin review
    - deprecated: Service is retired/end of life
    """

    draft = "draft"
    ready = "ready"
    deprecated = "deprecated"


# Backwards compatibility alias
UpstreamStatusEnum = OfferingStatusEnum


class ProviderStatusEnum(StrEnum):
    """
    Status values that sellers can set for providers.

    Seller-accessible statuses:
    - draft: Work in progress, skipped during publish
    - ready: Complete and ready for admin review
    - deprecated: Provider is retired/end of life
    """

    draft = "draft"
    ready = "ready"
    deprecated = "deprecated"


class PriceRuleApplyAtEnum(StrEnum):
    """When the price rule is applied."""

    request = "request"  # Applied per API call
    statement = "statement"  # Applied during billing/statement generation



class PriceRuleStatusEnum(StrEnum):
    """Seller-facing status values for promotions.

    The backend may define additional statuses (scheduled, expired,
    cancelled) for internal lifecycle management, but sellers only
    interact with these three.
    """

    draft = "draft"  # Not yet active, can be edited
    active = "active"  # Currently active and applied
    paused = "paused"  # Temporarily disabled



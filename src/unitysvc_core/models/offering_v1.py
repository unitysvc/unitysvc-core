from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, HttpUrl, field_validator

from .documents import DocumentData
from .offering_data import ServiceOfferingData
from .pricing import Pricing
from .validators import validate_channel_name, validate_description, validate_service_identifier


class OfferingV1(ServiceOfferingData):
    """
    Service offering model for file-based definitions (offering_v1 schema).

    Extends ServiceOfferingData with:
    - time_created: Timestamp for file creation
    - logo: Convenience field (converted to documents during import)
    - tags: Tags for the service (e.g., bring your own API key)
    - Typed models (AccessInterface, Document, Pricing) instead of dicts
    - Field validators for name format

    This model is used for validating offering.json/offering.toml files
    created by the CLI tool.
    """

    model_config = ConfigDict(extra="forbid")

    # File-specific fields for validation
    time_created: datetime

    # Override to make required in file validation (base has Optional for API flexibility)
    description: str = Field(  # type: ignore[assignment]
        description="Service description",
    )

    # Static information (optional — not all service types have meaningful details)
    details: dict[str, Any] = Field(  # type: ignore[assignment]
        default_factory=dict,
        description="Dictionary of static features and information",
    )

    # Convenience field for logo (converted to documents during import)
    logo: str | HttpUrl | None = None

    # Required in file validation (base allows None for API flexibility). Each
    # value is an OPAQUE per-channel JSON object: the gateway reads
    # upstream_access_config as raw JSONB, and channels are genuinely
    # heterogeneous (http has base_url; smtp has host/port; s3 has bucket/region;
    # a raw channel wraps arbitrary fields). A typed model here enforced nothing
    # (extra="allow") while misfitting non-HTTP channels — so it is intentionally
    # left open and validated by the gateway/backend at use. See unitysvc/unitysvc#1717.
    upstream_access_config: dict[str, dict[str, Any]] = Field(  # type: ignore[assignment]
        description="Upstream access channels, keyed by channel name (opaque per-channel objects)",
    )

    documents: dict[str, DocumentData] | None = Field(  # type: ignore[assignment]
        default=None,
        description="Documents associated with the service, keyed by title (e.g. tech spec.)",
    )

    payout_price: Pricing | None = Field(  # type: ignore[assignment]
        default=None,
        description="Payout pricing: How to calculate seller payout",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Validate the service-name slot of the platform identifier.

        Service identifier grammar: ``<name>[@<variant>]``. No ``/`` —
        provider namespace comes from the directory structure. See
        ``validate_service_identifier`` for full rules.
        """
        return validate_service_identifier(v, "service")

    @field_validator("description")
    @classmethod
    def validate_description_format(cls, v: str) -> str:
        """Enforce the two-mode marketplace description convention.

        The frontend renders the description in a collapsed list view (first
        paragraph only) and an expanded view (all paragraphs), so it must have a
        short teaser paragraph followed by longer body copy. See
        ``validate_description``.
        """
        return validate_description(v, "service")

    @field_validator("upstream_access_config")
    @classmethod
    def validate_channel_names(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Validate every channel name (key of ``upstream_access_config``).

        Channel names are selected via the ``<name>@<channel>`` identifier
        suffix, so each must follow the channel-name grammar and must not
        contain ``@``. See ``validate_channel_name``.
        """
        for channel_name in v:
            validate_channel_name(channel_name)
        return v

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, HttpUrl, field_validator, model_validator

from .channel import UpstreamAccessConfig
from .documents import DocumentData
from .offering_data import ServiceOfferingData
from .pricing import Pricing
from .validators import validate_details_links, validate_service_identifier


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

    # Static information (optional — not all service types have meaningful details)
    details: dict[str, Any] = Field(  # type: ignore[assignment]
        default_factory=dict,
        description=(
            "Dictionary of static features and information. Its 'links' key holds the product "
            "links (label -> https:// URL, including 'homepage'), required when the offering "
            "declares no channel"
        ),
    )

    # Convenience field for logo (converted to documents during import)
    logo: str | HttpUrl | None = None

    # The base allows None and keeps channels as plain dicts, for stored data
    # the platform has annotated. Omitted and empty are the same: an offering
    # with no channel, an external app reached at its own site (#2873).
    upstream_access_config: UpstreamAccessConfig = Field(  # type: ignore[assignment]
        default_factory=lambda: UpstreamAccessConfig({}),
        description="Upstream access channels, keyed by channel name; omit for an external app",
    )

    documents: dict[str, DocumentData] | None = Field(  # type: ignore[assignment]
        default=None,
        description="Documents associated with the service, keyed by title (e.g. tech spec.)",
    )

    payout_price: Pricing | None = Field(  # type: ignore[assignment]
        default=None,
        description="Payout pricing: How to calculate seller payout",
    )

    @model_validator(mode="after")
    def _validate_links(self) -> "OfferingV1":
        """Product links are well formed, and an offering with no channel has
        its homepage (unitysvc/unitysvc#2873)."""
        channel_less = not self.upstream_access_config.root
        if errors := validate_details_links(self.details, channel_less=channel_less):
            raise ValueError("; ".join(errors))
        return self

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Validate the service-name slot of the platform identifier.

        Service identifier grammar: ``<name>[@<variant>]``. No ``/`` —
        provider namespace comes from the directory structure. See
        ``validate_service_identifier`` for full rules.
        """
        return validate_service_identifier(v, "service")

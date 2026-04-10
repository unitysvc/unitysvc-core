from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, HttpUrl, field_validator

from .documents import DocumentData
from .pricing import Pricing
from .service import UpstreamAccessConfigData
from .validators import validate_name
from .offering_data import ServiceOfferingData


class OfferingV1(ServiceOfferingData):
    """
    Service offering model for file-based definitions (offering_v1 schema).

    Extends ServiceOfferingData with:
    - schema_version: Schema identifier for file validation
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
    schema_version: str = Field(default="offering_v1", description="Schema identifier", alias="schema")
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

    # Override with typed models for file validation
    upstream_access_config: dict[str, UpstreamAccessConfigData] = Field(  # type: ignore[assignment]
        description="Upstream access interfaces, keyed by name",
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
        """Validate that service name uses valid identifiers (allows slashes for hierarchical names)."""
        return validate_name(v, "service", allow_slash=True)

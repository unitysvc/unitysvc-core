from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from .documents import DocumentData
from .listing_data import ServiceListingData
from .pricing import Pricing
from .service import AccessInterfaceData
from .validators import validate_name


class ListingV1(ServiceListingData):
    """
    Service listing model for file-based definitions (listing_v1 schema).

    Extends ServiceListingData with:
    - schema_version: Schema identifier for file validation
    - time_created: Timestamp for file creation
    - Typed models (AccessInterface, Document, Pricing) instead of dicts
    - Field validators for name format

    This model is used for validating listing.json/listing.toml files
    created by the CLI tool.
    """

    model_config = ConfigDict(extra="forbid")

    # File-specific fields for validation
    schema_version: str = Field(default="listing_v1", description="Schema identifier", alias="schema")
    time_created: datetime

    # Override with typed models instead of dicts for file validation
    # (status, user_parameters_schema, user_parameters_ui_schema are inherited from ServiceListingData)
    user_access_interfaces: dict[str, AccessInterfaceData] | None = Field(  # type: ignore[assignment]
        default=None,
        description="User access interfaces for the listing, keyed by name",
    )

    list_price: Pricing | None = Field(  # type: ignore[assignment]
        default=None,
        description="List price: Listed price for customers",
    )

    documents: dict[str, DocumentData] | None = Field(  # type: ignore[assignment]
        default=None,
        description="Documents associated with the listing, keyed by title (e.g. service level agreements)",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str | None) -> str | None:
        """Validate that listing name uses valid identifiers (allows slashes for hierarchical names)."""
        if v is None:
            return v
        return validate_name(v, "listing", allow_slash=True)

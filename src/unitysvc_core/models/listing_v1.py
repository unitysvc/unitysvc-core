from datetime import datetime

from pydantic import ConfigDict, Field, field_validator

from .documents import DocumentData
from .listing_data import ServiceListingData
from .pricing import Pricing
from .service import AccessInterfaceData
from .validators import validate_service_identifier


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

    # listing.name IS the service identifier (service_name) and is REQUIRED.
    # It is the single source of truth — no composition, no fallback to
    # offering.name. The seller writes the full identifier verbatim:
    # `<provider>/<service-name>[@variant]` (namespaced, self-service) or a bare
    # `<name>` (top-level, admin-gated at registration). See issue #1138.
    name: str = Field(  # type: ignore[assignment]
        ...,
        max_length=255,
        description="Service identifier (service_name). REQUIRED. Either a namespaced "
        "'<provider>/<service-name>[@variant]' or a bare top-level name.",
    )

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
    def validate_name_format(cls, v: str) -> str:
        """Validate the service identifier against the #1138 grammar.

        ``listing.name`` is the full identifier ``<name>[@<variant>]`` where
        ``<name>`` may be hierarchical (``<provider>/<service-name>...``). This
        checks grammar only; the "namespaced first segment must equal the
        provider slug" and top-level (no ``/``) rules need provider context and
        are enforced by the catalog-layer ``DataValidator``.
        """
        return validate_service_identifier(v, "listing")

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

    # Convenience field for the instance spec of a container service: a relative
    # path or an https URL (converted to documents["Instance spec"] during import,
    # as logo is; core.instance_spec.expand_instance_spec does it). Its format is
    # compose. unitysvc/unitysvc-core#107.
    instance_spec: str | HttpUrl | None = Field(
        default=None,
        description=(
            "The Compose file a container service runs: a path relative to this file, or an https URL "
            "(a snapshot source, fetched once at upload). Shortcut for one instance_spec Document"
        ),
    )

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

    @field_validator("instance_spec")
    @classmethod
    def _instance_spec_source(cls, value: str | HttpUrl | None) -> str | HttpUrl | None:
        from ..instance_spec import snapshot_url_error

        text = None if value is None else str(value)
        if text is not None and "://" in text and (problem := snapshot_url_error(text)):
            raise ValueError(problem)
        return value

    @model_validator(mode="after")
    def _one_instance_spec(self) -> "OfferingV1":
        """A container offering has exactly one instance spec (shortcut or Document); any other has none."""
        from .channel import ContainerChannel

        count = sum(d.category == "instance_spec" for d in (self.documents or {}).values())
        count += self.instance_spec is not None
        has_container = any(isinstance(c, ContainerChannel) for c in self.upstream_access_config.root.values())
        if has_container and count == 0:
            raise ValueError(
                "an offering with a container channel needs its instance spec: "
                "set 'instance_spec' (a path or https URL) or add one document of category 'instance_spec'"
            )
        if count > 1:
            raise ValueError(
                f"an offering has one instance spec, found {count}: use either 'instance_spec' or a "
                "document of category 'instance_spec', and only one"
            )
        if count == 1 and not has_container:
            raise ValueError("only an offering with a container channel has an instance spec")
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

from pydantic import ConfigDict, Field

from .promotion_data import PromotionData


class PromotionV1(PromotionData):
    """
    Promotion model for file-based definitions (promotion_v1 schema).

    Extends PromotionData with:
    - schema_version: Schema identifier for file validation
    - extra="forbid" to catch typos in promotion files

    This model is used for validating promotion TOML/JSON files
    created by the CLI tool.

    Note: pricing stays as dict[str, Any] because promotion pricing
    (e.g., {"type": "multiply", "factor": "0.80"}) uses a simplified
    format without a base field — the base is implicit (existing price).
    """

    model_config = ConfigDict(extra="forbid")

    # File-specific field
    schema_version: str = Field(
        default="promotion_v1",
        description="Schema identifier",
        alias="schema",
    )

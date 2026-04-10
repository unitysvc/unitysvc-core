"""Base data model for service groups.

This module defines ``ServiceGroupData``, a base model containing the core fields
for service group data that is shared between:
- unitysvc-core (CLI): Used for file-based group definitions and seller CLI
- unitysvc-admin (CLI): Used for admin group management
- unitysvc (backend): Used for API payloads

The ``validate_service_group()`` function provides dict-level validation for
raw data (e.g., from TOML/JSON files) before constructing the model.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .base import ServiceGroupStatusEnum


class ServiceGroupData(BaseModel):
    """Base data structure for service group information.

    This model contains the core fields needed to describe a service group,
    without file-specific validation fields. It serves as:

    1. The base class for file-level validation (with schema_version field)
    2. The data structure for API payload validation
    3. The shared model for both seller and admin CLIs
    """

    model_config = {"extra": "ignore"}

    name: str = Field(
        max_length=100,
        description="URL-friendly slug (unique per owner, e.g., 'my-llm-services')",
    )

    display_name: str = Field(
        max_length=200,
        description="Human-readable name for UI display",
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Detailed description of the group",
    )

    status: ServiceGroupStatusEnum = Field(
        default=ServiceGroupStatusEnum.draft,
        description="Group status (draft, active, private, archived)",
    )

    parent_group_name: str | None = Field(
        default=None,
        description="Parent group name for hierarchy (resolved to ancestor_path)",
    )

    membership_rules: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Rules for automatic service membership. "
            'Format: {"expression": "<python_expression>"}\n'
            "Available variables: service_id, seller_id, provider_id, seller_name, "
            "provider_name, name, display_name, service_type, status, listing_type, "
            "tags, is_featured"
        ),
    )

    sort_order: int = Field(
        default=0,
        description="Display order within parent level",
    )


SERVICE_GROUP_SCHEMA_VERSION = "service_group_v1"


def is_service_group_file(data: dict[str, Any]) -> bool:
    """Check if a data dict is a service group file (by schema version)."""
    return data.get("schema") == SERVICE_GROUP_SCHEMA_VERSION


def strip_schema_field(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the data dict without the ``schema`` field."""
    return {k: v for k, v in data.items() if k != "schema"}


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


_DANGEROUS_PATTERNS = [
    r"__",
    r"\bimport\b",
    r"\bexec\b",
    r"\beval\b",
    r"\bcompile\b",
    r"\bopen\s*\(",
    r"\bfile\b",
    r"\binput\b",
    r"\bglobals\b",
    r"\blocals\b",
    r"\bgetattr\b",
    r"\bsetattr\b",
    r"\bdelattr\b",
    r"\bvars\b",
    r"\bdir\b",
    r"\bbreakpoint\b",
]


def validate_service_group(data: dict[str, Any]) -> list[str]:
    """Validate a service group data dict.

    Args:
        data: Service group data dict (from JSON/TOML file)

    Returns:
        List of validation error strings (empty = valid)
    """
    errors: list[str] = []

    # Required fields
    if "name" not in data:
        errors.append("Missing required field: name")
    elif not isinstance(data["name"], str):
        errors.append("name must be a string")
    elif len(data["name"]) > 100:
        errors.append("name must be at most 100 characters")
    elif not re.match(r"^[a-z0-9]+(?:[-:][a-z0-9]+)*$", data["name"]):
        errors.append(
            "name must be lowercase alphanumeric with hyphens "
            "(e.g., 'my-llm-services')"
        )

    if "display_name" not in data:
        errors.append("Missing required field: display_name")
    elif not isinstance(data["display_name"], str):
        errors.append("display_name must be a string")
    elif len(data["display_name"]) > 200:
        errors.append("display_name must be at most 200 characters")

    # Optional fields
    if "description" in data and data["description"] is not None:
        if not isinstance(data["description"], str):
            errors.append("description must be a string")
        elif len(data["description"]) > 2000:
            errors.append("description must be at most 2000 characters")

    # Membership rules
    if "membership_rules" in data and data["membership_rules"] is not None:
        rules = data["membership_rules"]
        if not isinstance(rules, dict):
            errors.append("membership_rules must be a dictionary")
        elif "expression" not in rules:
            errors.append("membership_rules must contain an 'expression' key")
        elif not isinstance(rules["expression"], str):
            errors.append("membership_rules.expression must be a string")
        elif not rules["expression"].strip():
            errors.append("membership_rules.expression cannot be empty")
        else:
            # Security check
            for pattern in _DANGEROUS_PATTERNS:
                if re.search(pattern, rules["expression"], re.IGNORECASE):
                    errors.append(
                        f"Disallowed pattern in rule expression: {pattern}"
                    )

    if "sort_order" in data:
        if not isinstance(data["sort_order"], int):
            errors.append("sort_order must be an integer")

    return errors

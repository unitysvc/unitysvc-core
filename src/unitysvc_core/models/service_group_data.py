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

from .base import GroupOwnerTypeEnum, GroupTypeEnum, ServiceGroupStatusEnum

# Slug pattern enforced on group ``name`` and ``parent_group_name``.
# Matches the pattern enforced at the unitysvc backend on
# ``ServiceGroupBase.name`` (CHECK constraint + Pydantic). Group names
# are the public, stable identifier — they appear in URLs
# (``/customer/groups/{name}``) and in SDK scripts that may run months
# later — so they must be predictable across consumers.
_GROUP_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class ServiceGroupData(BaseModel):
    """Single data model for service group payloads.

    Used identically by seller publishing (``usvc seller …``), admin
    uploads (``usvc_admin groups upload``), and backend ingest. The
    only audience-specific behaviour is that seller workflows supply
    their own ``owner_type`` / ``owner_id`` defaults at the CLI/backend
    layer (the seller's own role) rather than the platform defaults
    declared here, which is the right thing for admin tooling.
    """

    model_config = {"extra": "ignore"}

    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=_GROUP_NAME_PATTERN,
        description=(
            "URL-friendly slug, unique per owner (e.g., 'my-llm-services'). "
            "Lowercase ASCII alphanumeric plus '-' / '_', must start with a "
            "letter or digit. Used as the public identifier in customer SDK "
            "paths, so changing it is a breaking change for callers."
        ),
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

    owner_type: GroupOwnerTypeEnum = Field(
        default=GroupOwnerTypeEnum.platform,
        description="Type of owner (platform, seller, customer)",
    )

    owner_id: str | None = Field(
        default=None,
        description=(
            "Owner ID (UUID string). For platform groups, the backend "
            "fills in the platform-superuser ID when omitted; for "
            "seller/customer groups it must be supplied."
        ),
    )

    group_type: GroupTypeEnum = Field(
        default=GroupTypeEnum.regular,
        description=(
            "Type of group: regular (enrollable), category (non-enrollable "
            "parent), or misc (system-generated catch-all)."
        ),
    )

    parent_group_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=_GROUP_NAME_PATTERN,
        description=(
            "Parent group name for hierarchy (resolved to ancestor_path "
            "by the backend). Must itself be a valid group slug."
        ),
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

    access_interface_data_template: str | None = Field(
        default=None,
        description=(
            "Jinja2 template that renders to AccessInterfaceData JSON when "
            "populated with service data. Used by admin to auto-generate "
            "group-scoped AccessInterfaces."
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


_GROUP_NAME_RE = re.compile(_GROUP_NAME_PATTERN)
_VALID_OWNER_TYPES = {e.value for e in GroupOwnerTypeEnum}
_VALID_GROUP_TYPES = {e.value for e in GroupTypeEnum}


def _check_slug(value: Any, field: str, errors: list[str]) -> None:
    """Append a slug-format error for ``field`` if ``value`` doesn't conform."""
    if not isinstance(value, str):
        errors.append(f"{field} must be a string")
    elif len(value) < 1 or len(value) > 100:
        errors.append(f"{field} must be between 1 and 100 characters")
    elif not _GROUP_NAME_RE.match(value):
        errors.append(
            f"{field} must be a URL-friendly slug — lowercase ASCII "
            "alphanumeric plus '-' / '_', must start with a letter "
            "or digit (e.g. 'my-llm-services')"
        )


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
    else:
        _check_slug(data["name"], "name", errors)

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

    if "parent_group_name" in data and data["parent_group_name"] is not None:
        _check_slug(data["parent_group_name"], "parent_group_name", errors)

    if "owner_type" in data and data["owner_type"] is not None:
        if data["owner_type"] not in _VALID_OWNER_TYPES:
            errors.append(
                f"owner_type must be one of {sorted(_VALID_OWNER_TYPES)}, "
                f"got {data['owner_type']!r}"
            )

    if "group_type" in data and data["group_type"] is not None:
        if data["group_type"] not in _VALID_GROUP_TYPES:
            errors.append(
                f"group_type must be one of {sorted(_VALID_GROUP_TYPES)}, "
                f"got {data['group_type']!r}"
            )

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

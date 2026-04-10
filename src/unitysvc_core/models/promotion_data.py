"""Base data model for promotions.

This module defines ``PromotionData``, a base model containing the core fields
for promotion data that is shared between:
- unitysvc-core (CLI): Used for file-based promotion definitions
- unitysvc (backend): Used for API payloads (``SellerPromotionCreate``)

The ``validate_promotion()`` function provides dict-level validation for
raw data (e.g., from TOML files) before constructing the model.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .base import PriceRuleApplyAtEnum, PriceRuleStatusEnum
from .pricing import validate_pricing

# Derive allowed values from enums
APPLY_AT_VALUES = {e.value for e in PriceRuleApplyAtEnum}
STATUS_VALUES = {e.value for e in PriceRuleStatusEnum}

PROMOTION_SCHEMA_VERSION = "promotion_v1"

# Pattern for {{ promotion_code(N) }} template
_PROMOTION_CODE_PATTERN = re.compile(
    r"^\{\{\s*promotion_code\(\s*(\d+)\s*\)\s*\}\}$"
)


class PromotionData(BaseModel):
    """
    Base data structure for promotion information.

    This model contains the core fields needed to describe a promotion,
    without file-specific validation fields. It serves as:

    1. The base class for file-level validation in unitysvc-core
       (with additional schema_version field)

    2. The data structure imported by unitysvc backend for:
       - API payload validation (SellerPromotionCreate)
       - Promotion upsert and CRUD operations
       - Publish operations from CLI

    Key characteristics:
    - scope is the source of truth for customer/service targeting
    - pricing uses the shared Pricing union type
    - Does not include system fields (seller_id, code, requires_redemption)
      which are materialized by the backend during ingestion
    """

    model_config = {"extra": "ignore"}

    name: str = Field(
        max_length=100,
        description="Display name of the promotion (unique per seller)",
    )

    description: str | None = Field(
        default=None,
        max_length=500,
        description="Human-readable description",
    )

    scope: dict[str, Any] | None = Field(
        default=None,
        description="Customer and service targeting. "
        "null = all customers, all services (blanket promotion).",
    )

    pricing: dict[str, Any] = Field(
        description="Pricing specification (e.g., multiply, constant, add)",
    )

    apply_at: PriceRuleApplyAtEnum = Field(
        default=PriceRuleApplyAtEnum.request,
        description="When to apply: 'request' or 'statement'",
    )

    priority: int = Field(
        default=0,
        description="Higher priority rules are applied first",
    )

    status: PriceRuleStatusEnum = Field(
        default=PriceRuleStatusEnum.draft,
        description="Lifecycle status: draft, active, paused",
    )

    expires_at: datetime | None = Field(
        default=None,
        description="When the promotion expires (code-based only)",
    )

    max_uses: int | None = Field(
        default=None,
        description="Maximum total redemptions (code-based only)",
    )


def is_promotion_file(data: dict[str, Any]) -> bool:
    """Check if a data dict is a promotion file (by schema version)."""
    return data.get("schema") == PROMOTION_SCHEMA_VERSION


def strip_schema_field(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the data dict without the ``schema`` field.

    The backend accepts the promotion file format directly, so we only
    need to strip the file-level schema field before POSTing.
    """
    return {k: v for k, v in data.items() if k != "schema"}


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

def _validate_scope_customers(customers: Any, errors: list[str]) -> None:
    """Validate ``scope.customers``."""
    if customers == "*":
        return
    if isinstance(customers, list):
        if not all(isinstance(c, str) for c in customers):
            errors.append("scope.customers list must contain strings")
        return
    if isinstance(customers, dict):
        allowed_keys = {"code", "subscription"}
        unknown = set(customers.keys()) - allowed_keys
        if unknown:
            errors.append(
                f"scope.customers has unknown keys: {unknown}"
            )
        if "code" in customers:
            code = customers["code"]
            if not isinstance(code, str):
                errors.append("scope.customers.code must be a string")
            elif len(code) > 50 and not _PROMOTION_CODE_PATTERN.match(code):
                errors.append(
                    "scope.customers.code must be <= 50 chars "
                    "or a {{ promotion_code(N) }} template"
                )
        if "subscription" in customers:
            if not isinstance(customers["subscription"], str):
                errors.append(
                    "scope.customers.subscription must be a string"
                )
        return
    errors.append(
        'scope.customers must be "*", a list of IDs, '
        'or a dict with "code" / "subscription"'
    )


def _validate_scope_services(services: Any, errors: list[str]) -> None:
    """Validate ``scope.services``."""
    if services == "*":
        return
    if isinstance(services, list):
        if not all(isinstance(s, str) for s in services):
            errors.append("scope.services list must contain strings")
        return
    errors.append('scope.services must be "*" or a list of service names')


def _validate_scope(scope: Any, errors: list[str]) -> None:
    """Validate the top-level ``scope`` field."""
    if scope is None:
        return
    if not isinstance(scope, dict):
        errors.append("scope must be a dict or null")
        return
    allowed_keys = {"customers", "services"}
    unknown = set(scope.keys()) - allowed_keys
    if unknown:
        errors.append(f"scope has unknown keys: {unknown}")
    if "customers" in scope:
        _validate_scope_customers(scope["customers"], errors)
    if "services" in scope:
        _validate_scope_services(scope["services"], errors)


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def validate_promotion(data: dict[str, Any]) -> list[str]:
    """Validate a promotion data dict.

    Args:
        data: Promotion data dict (from JSON/TOML file)

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

    if "pricing" not in data:
        errors.append("Missing required field: pricing")
    elif isinstance(data["pricing"], dict):
        try:
            validate_pricing(data["pricing"])
        except Exception as e:
            errors.append(f"Invalid pricing: {e}")

    # Scope
    if "scope" in data:
        _validate_scope(data.get("scope"), errors)

    # Optional scalar fields
    if "description" in data and data["description"] is not None:
        if not isinstance(data["description"], str):
            errors.append("description must be a string")
        elif len(data["description"]) > 500:
            errors.append("description must be at most 500 characters")

    if "apply_at" in data:
        if data["apply_at"] not in APPLY_AT_VALUES:
            errors.append(
                f"apply_at must be one of {sorted(APPLY_AT_VALUES)}, "
                f"got '{data['apply_at']}'"
            )

    if "status" in data:
        if data["status"] not in STATUS_VALUES:
            errors.append(
                f"status must be one of {sorted(STATUS_VALUES)}, "
                f"got '{data['status']}'"
            )

    if "priority" in data:
        if not isinstance(data["priority"], int):
            errors.append("priority must be an integer")

    if "max_uses" in data and data["max_uses"] is not None:
        if not isinstance(data["max_uses"], int) or data["max_uses"] < 1:
            errors.append("max_uses must be a positive integer")

    return errors


# ---------------------------------------------------------------------------
# Scope display helpers (for CLI)
# ---------------------------------------------------------------------------

def describe_scope(scope: dict[str, Any] | None) -> str:
    """Return a human-readable one-line description of the scope."""
    if scope is None:
        return "all customers, all services"

    parts: list[str] = []

    customers = scope.get("customers")
    if customers is None or customers == "*":
        parts.append("all customers")
    elif isinstance(customers, dict):
        if "code" in customers:
            code = customers["code"]
            if _PROMOTION_CODE_PATTERN.match(code):
                parts.append("code: (auto-generated)")
            else:
                parts.append(f"code: {code}")
        if "subscription" in customers:
            parts.append(f"subscription: {customers['subscription']}")
    elif isinstance(customers, list):
        parts.append(f"{len(customers)} customer(s)")

    services = scope.get("services")
    if services is None or services == "*":
        parts.append("all services")
    elif isinstance(services, list):
        if len(services) <= 3:
            parts.append(f"services: {', '.join(services)}")
        else:
            parts.append(f"{len(services)} service(s)")

    return "; ".join(parts)

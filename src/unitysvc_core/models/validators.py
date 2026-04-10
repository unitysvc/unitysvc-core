"""Name and service-option validation helpers."""

from __future__ import annotations

import re
from typing import Any


def validate_name(name: str, entity_type: str, display_name: str | None = None, *, allow_slash: bool = False) -> str:
    """
    Validate that a name field uses valid identifiers.

    Name format rules:
    - Only letters (upper/lowercase), numbers, dots, dashes, and underscores allowed
    - If allow_slash=True, slashes are also allowed for hierarchical names
    - Must start and end with alphanumeric characters (not special characters)
    - Cannot have consecutive slashes (when allow_slash=True)
    - Cannot be empty

    Args:
        name: The name value to validate
        entity_type: Type of entity (provider, seller, service, listing) for error messages
        display_name: Optional display name to suggest a valid name from
        allow_slash: Whether to allow slashes for hierarchical names (default: False)

    Returns:
        The validated name (unchanged if valid)

    Raises:
        ValueError: If the name doesn't match the required pattern

    Examples:
        Without slashes (providers, sellers):
            - name='amazon-bedrock' or name='Amazon-Bedrock'
            - name='fireworks.ai' or name='Fireworks.ai'
            - name='llama-3.1' or name='Llama-3.1'

        With slashes (services, listings):
            - name='gpt-4' or name='GPT-4'
            - name='models/gpt-4' or name='models/GPT-4'
            - name='black-forest-labs/FLUX.1-dev'
            - name='api/v1/completion'
    """
    # Build pattern based on allow_slash parameter
    if allow_slash:
        # Pattern: starts with alphanumeric, can contain alphanumeric/dot/dash/underscore/slash, ends with alphanumeric
        name_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9._/-]*[a-zA-Z0-9])?$"
        allowed_chars = "letters, numbers, dots, dashes, underscores, and slashes"
    else:
        # Pattern: starts with alphanumeric, can contain alphanumeric/dot/dash/underscore, ends with alphanumeric
        name_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$"
        allowed_chars = "letters, numbers, dots, dashes, and underscores"

    # Check for consecutive slashes if slashes are allowed
    if allow_slash and "//" in name:
        raise ValueError(f"Invalid {entity_type} name '{name}'. Name cannot contain consecutive slashes.")

    if not re.match(name_pattern, name):
        # Build helpful error message
        error_msg = (
            f"Invalid {entity_type} name '{name}'. "
            f"Name must contain only {allowed_chars}. "
            f"It must start and end with an alphanumeric character.\n"
        )

        # Suggest a valid name based on display_name if available
        if display_name:
            suggested_name = suggest_valid_name(display_name, allow_slash=allow_slash)
            if suggested_name and suggested_name != name:
                error_msg += f"  Suggestion: Set name='{suggested_name}' and display_name='{display_name}'\n"

        # Add appropriate examples based on allow_slash
        if allow_slash:
            error_msg += (
                "  Examples:\n"
                "    - name='gpt-4' or name='GPT-4'\n"
                "    - name='models/gpt-4' or name='models/GPT-4'\n"
                "    - name='black-forest-labs/FLUX.1-dev'\n"
                "    - name='api/v1/completion'"
            )
        else:
            error_msg += (
                "  Note: Use 'display_name' field for brand names with spaces and special characters.\n"
                "  Examples:\n"
                "    - name='amazon-bedrock' or name='Amazon-Bedrock'\n"
                "    - name='fireworks.ai' or name='Fireworks.ai'\n"
                "    - name='llama-3.1' or name='Llama-3.1'"
            )

        raise ValueError(error_msg)

    return name


SUPPORTED_SERVICE_OPTIONS: dict[str, type | tuple[type, ...]] = {
    "enrollment_vars": dict,  # Named Jinja2 template values rendered per-enrollment
    "routing_vars": dict,  # Seller-managed operational variables for template resolution at request time
    "enrollment_limit": int,
    "enrollment_limit_per_customer": int,
    "enrollment_limit_per_user": int,
    "ops_testing_parameters": dict,
    "prompt_recurrence": bool,  # Prompt recurrence options during enrollment
    "recurrence_min_interval_seconds": int,
    "recurrence_max_interval_seconds": int,
    "recurrence_allow_cron": bool,
}


def validate_service_options(service_options: dict[str, Any] | None) -> list[str]:
    """Validate service_options keys and value types.

    Returns list of error messages for unrecognized keys, wrong types, or invalid values.
    """
    if not service_options:
        return []

    errors: list[str] = []
    supported_keys = sorted(SUPPORTED_SERVICE_OPTIONS.keys())

    for key, value in service_options.items():
        if key not in SUPPORTED_SERVICE_OPTIONS:
            errors.append(f"Unrecognized service_option '{key}'. Supported options: {', '.join(supported_keys)}")
            continue

        expected_type = SUPPORTED_SERVICE_OPTIONS[key]

        # Reject booleans for int keys (isinstance(True, int) is True in Python)
        if expected_type is int and isinstance(value, bool):
            errors.append(f"service_options.{key} must be int, got bool")
            continue

        if not isinstance(value, expected_type):
            if isinstance(expected_type, tuple):
                type_name = " or ".join(t.__name__ for t in expected_type)
            else:
                type_name = expected_type.__name__
            errors.append(f"service_options.{key} must be {type_name}, got {type(value).__name__}")
            continue

        # Validate enrollment_vars values are all strings (Jinja2 templates)
        if key == "enrollment_vars" and isinstance(value, dict):
            for env_key, env_val in value.items():
                if not isinstance(env_key, str):
                    errors.append(f"service_options.enrollment_vars key must be str, got {type(env_key).__name__}")
                elif not isinstance(env_val, str):
                    errors.append(
                        f"service_options.enrollment_vars.{env_key} must be str, got {type(env_val).__name__}"
                    )

        # Non-positive integers for enrollment_limit* keys
        if expected_type is int and key.startswith("enrollment_limit") and isinstance(value, int):
            if value <= 0:
                errors.append(f"service_options.{key} must be a positive integer, got {value}")

        # Recurrence interval bounds
        if key in ("recurrence_min_interval_seconds", "recurrence_max_interval_seconds") and isinstance(value, int):
            if value < 1:
                errors.append(f"service_options.{key} must be >= 1, got {value}")

    # Cross-field: min <= max for recurrence intervals
    if "recurrence_min_interval_seconds" in (service_options or {}) and "recurrence_max_interval_seconds" in (
        service_options or {}
    ):
        min_val = service_options["recurrence_min_interval_seconds"]
        max_val = service_options["recurrence_max_interval_seconds"]
        if isinstance(min_val, int) and isinstance(max_val, int) and min_val > max_val:
            errors.append(
                f"service_options.recurrence_min_interval_seconds ({min_val}) "
                f"must be <= recurrence_max_interval_seconds ({max_val})"
            )

    return errors


def suggest_valid_name(display_name: str, *, allow_slash: bool = False) -> str:
    """
    Suggest a valid name based on a display name.

    Replaces invalid characters with hyphens and ensures it follows the naming rules.
    Preserves the original case.

    Args:
        display_name: The display name to convert
        allow_slash: Whether to allow slashes for hierarchical names (default: False)

    Returns:
        A suggested valid name
    """
    if allow_slash:
        # Replace characters that aren't alphanumeric, dot, dash, underscore, or slash with hyphens
        suggested = re.sub(r"[^a-zA-Z0-9._/-]+", "-", display_name)
        # Remove leading/trailing special characters
        suggested = suggested.strip("._/-")
        # Collapse multiple consecutive dashes
        suggested = re.sub(r"-+", "-", suggested)
        # Remove consecutive slashes
        suggested = re.sub(r"/+", "/", suggested)
    else:
        # Replace characters that aren't alphanumeric, dot, dash, or underscore with hyphens
        suggested = re.sub(r"[^a-zA-Z0-9._-]+", "-", display_name)
        # Remove leading/trailing dots, dashes, or underscores
        suggested = suggested.strip("._-")
        # Collapse multiple consecutive dashes
        suggested = re.sub(r"-+", "-", suggested)

    return suggested

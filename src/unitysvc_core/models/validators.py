"""Name and service-option validation helpers."""

from __future__ import annotations

import re
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError


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


# Single path segment in a service identifier (provider slot, service-name
# slot, or any sub-segment of a hierarchical name like Qwen/Qwen2.5-...).
# Alphanumeric + ``.`` ``-`` ``_``, must start with alphanumeric.
_SERVICE_NAME_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def validate_service_identifier(name: str, entity_type: str) -> str:
    """Validate a service identifier (offering or listing name).

    Enforces the platform service-naming convention:

    - The identifier has the form ``<name>[@<variant>]`` with at most one
      ``@`` separating the bare name from an optional seller-defined
      variant tag (e.g. ``claude-opus-4-7@byok``, ``gpt-4@premium-eu``).
    - ``<name>`` may be a single segment (``claude-opus-4-7``) or a
      hierarchical ``segment/segment[/segment...]`` form for providers
      whose model identifiers use ``creator/model`` (e.g. HuggingFace's
      ``Qwen/Qwen2.5-Coder-7B-Instruct``).
    - Each segment must be at least 2 characters so it cannot collide
      with single-letter gateway primitive prefixes (``a/``, ``g/``,
      ``b/``, ``c/``, ``l/``, ``m/``, ``r/``, ``d/``, ``t/``, ``f/``).
    - Each segment uses only letters, digits, ``.``, ``-``, ``_`` and
      must start with an alphanumeric character.

    Args:
        name: The identifier to validate.
        entity_type: Type of entity (``service``, ``listing``) for error
            messages.

    Returns:
        The validated identifier (unchanged if valid).

    Raises:
        ValueError: If the identifier doesn't match the required pattern.

    Examples:
        Valid: ``claude-opus-4-7``, ``gpt-4``,
        ``Qwen/Qwen2.5-Coder-7B-Instruct`` (HuggingFace hierarchical),
        ``claude-opus-4-7@byok``, ``Qwen/Qwen2.5-Coder-7B-Instruct@byok``.

        Invalid: ``a`` (single-char — collides with /a/ primitive),
        ``a/b`` (single-char first segment),
        ``Qwen/x`` (single-char second segment),
        ``gpt-4@byok@premium`` (multiple ``@``),
        ``-gpt-4`` (must start with alphanumeric),
        ``Qwen//model`` (empty segment from ``//``).
    """
    if not name:
        raise ValueError(f"Invalid {entity_type} name: name cannot be empty")

    at_parts = name.split("@")
    if len(at_parts) > 2:
        raise ValueError(
            f"Invalid {entity_type} name '{name}': at most one '@' is allowed (separates name from variant tag)."
        )

    bare_name = at_parts[0]
    if not bare_name:
        raise ValueError(f"Invalid {entity_type} name '{name}': empty name before '@'.")

    for segment in bare_name.split("/"):
        if not segment:
            raise ValueError(
                f"Invalid {entity_type} name '{name}': empty segment (consecutive or leading/trailing '/')."
            )
        if len(segment) < 2:
            raise ValueError(
                f"Invalid {entity_type} name '{name}': segment '{segment}' "
                f"must be at least 2 characters. Single-character segments "
                f"are reserved to avoid collision with single-letter gateway "
                f"primitive prefixes (a/, g/, b/, c/, l/, m/, r/, d/, t/, f/)."
            )
        if not _SERVICE_NAME_SEGMENT_RE.match(segment):
            raise ValueError(
                f"Invalid {entity_type} name '{name}': segment '{segment}' "
                f"has invalid characters (allowed: letters, digits, '.', "
                f"'-', '_'; must start with an alphanumeric character)."
            )

    if len(at_parts) == 2:
        variant = at_parts[1]
        if not variant:
            raise ValueError(f"Invalid {entity_type} name '{name}': empty variant after '@'.")
        for segment in variant.split("/"):
            if not segment:
                raise ValueError(f"Invalid {entity_type} name '{name}': empty variant segment.")
            if not _SERVICE_NAME_SEGMENT_RE.match(segment):
                raise ValueError(
                    f"Invalid {entity_type} name '{name}': variant segment "
                    f"'{segment}' has invalid characters (allowed: letters, "
                    f"digits, '.', '-', '_'; must start with an alphanumeric "
                    f"character)."
                )

    return name


def validate_channel_name(name: str, entity_type: str = "channel") -> str:
    """Validate an upstream access-channel name.

    A channel name is the key of an entry in ``upstream_access_config`` (and
    the keys of channel-based pricing). It is also the value selected by the
    ``@<channel>`` suffix of a service identifier (``<name>@<channel>``), so it
    must satisfy the same per-segment grammar as a variant tag and, critically,
    must **not** contain ``@`` — that character is the channel-selector
    delimiter and would make the identifier ambiguous.

    Rules:

    - Non-empty.
    - No ``@`` (reserved as the ``<name>@<channel>`` selector delimiter).
    - Each ``/``-separated segment uses only letters, digits, ``.``, ``-``,
      ``_`` and must start with an alphanumeric character. (Single-character
      channel names are allowed, matching variant-tag rules — only bare
      *service* names carry the ≥2-char primitive-prefix restriction.)

    Args:
        name: The channel name to validate.
        entity_type: Label used in error messages (default ``"channel"``).

    Returns:
        The validated channel name (unchanged if valid).

    Raises:
        ValueError: If the channel name is empty, contains ``@``, or has an
            invalid segment.

    Examples:
        Valid: ``managed``, ``byok``, ``byoe``, ``gateway``, ``apprise``,
        ``eu-west``, ``p``.

        Invalid: ``""`` (empty), ``byok@eu`` (contains ``@``),
        ``-byok`` (must start alphanumeric), ``by//ok`` (empty segment).
    """
    if not name:
        raise ValueError(f"Invalid {entity_type} name: name cannot be empty")

    if "@" in name:
        raise ValueError(
            f"Invalid {entity_type} name '{name}': '@' is not allowed — it is the "
            f"channel-selector delimiter in '<name>@<channel>' identifiers."
        )

    for segment in name.split("/"):
        if not segment:
            raise ValueError(
                f"Invalid {entity_type} name '{name}': empty segment (consecutive or leading/trailing '/')."
            )
        if not _SERVICE_NAME_SEGMENT_RE.match(segment):
            raise ValueError(
                f"Invalid {entity_type} name '{name}': segment '{segment}' "
                f"has invalid characters (allowed: letters, digits, '.', '-', "
                f"'_'; must start with an alphanumeric character)."
            )

    return name


# An MCP service's namespace is the ``routing_key.server`` value on its user
# access interface. It plays the same role ``routing_key.username`` plays for
# SMTP: the sole selector against a shared gateway ``base_url``. It also
# prefixes every tool the MCP gateway exposes, as ``<namespace>__<tool>``.
#
# The 24-character cap is load-bearing, not cosmetic. MCP clients constrain
# tool names to ``^[a-zA-Z0-9_-]{1,64}$``, so capping the namespace at 24
# leaves at least 38 characters for the upstream tool name before the gateway
# has to truncate-and-hash. (This is also why dots are excluded — the
# ``seller.service.tool`` shape originally floated in unitysvc/unitysvc#1799
# is not expressible in a client-legal tool name.)
MCP_NAMESPACE_MAX_LEN = 24
_MCP_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def validate_mcp_namespace(namespace: str, field: str) -> list[str]:
    """Validate an MCP service namespace.

    Returns a list of error strings; empty when valid. Follows the
    error-accumulating convention of the other listing-level validators
    rather than raising, so a single validation pass can report every
    problem in a spec at once.
    """
    if not namespace:
        return [f"{field}: MCP namespace must not be empty"]
    if len(namespace) > MCP_NAMESPACE_MAX_LEN:
        return [
            f"{field}: MCP namespace {namespace!r} is {len(namespace)} characters; "
            f"the maximum is {MCP_NAMESPACE_MAX_LEN} so the gateway's exposed tool "
            "name '<namespace>__<tool>' fits the 64-character MCP tool-name limit"
        ]
    if not _MCP_NAMESPACE_PATTERN.match(namespace):
        return [
            f"{field}: MCP namespace {namespace!r} must be lowercase ASCII "
            "alphanumeric plus '_', starting with a letter or digit "
            "(e.g. 'github', 'acme_tools')"
        ]
    return []


# Marketplace description convention. The frontend renders the offering
# ``description`` in two modes: a collapsed "list" view that shows only the
# first paragraph, and an expanded view that shows every paragraph. So the
# description must be split into ``\n\n``-separated paragraphs, with a short
# first paragraph that stands alone as the list-view teaser and longer body
# copy after it. Paragraphs are separated by a blank line (``\n\n``).
DESCRIPTION_FIRST_PARAGRAPH_MAX_LEN = 200


def validate_description(description: str, entity_type: str = "service") -> str:
    """Validate the two-mode paragraph convention for a marketplace description.

    The frontend shows only the first paragraph in its collapsed list view and
    all paragraphs when expanded, so a conforming description must be:

    - **At least two paragraphs**, separated by a blank line (``\\n\\n``).
    - A **first paragraph under ``DESCRIPTION_FIRST_PARAGRAPH_MAX_LEN``
      characters** — it is the standalone teaser shown in the list view.
    - **Later paragraphs longer, in total, than the first** — the expanded
      view must add substantive detail beyond the teaser.

    Args:
        description: The description text to validate.
        entity_type: Label used in error messages (default ``"service"``).

    Returns:
        The description unchanged if valid.

    Raises:
        ValueError: If the description doesn't follow the convention.
    """
    paragraphs = [p.strip() for p in description.split("\n\n")]
    paragraphs = [p for p in paragraphs if p]

    if len(paragraphs) < 2:
        raise ValueError(
            f"The {entity_type} description must have at least two paragraphs "
            f"separated by a blank line ('\\n\\n'): a short first paragraph shown "
            f"in the collapsed list view, then one or more longer paragraphs shown "
            f"when expanded. Found {len(paragraphs)} paragraph(s)."
        )

    first = paragraphs[0]
    rest_len = sum(len(p) for p in paragraphs[1:])

    if len(first) >= DESCRIPTION_FIRST_PARAGRAPH_MAX_LEN:
        raise ValueError(
            f"The first paragraph of the {entity_type} description is the "
            f"collapsed list-view teaser and must be under "
            f"{DESCRIPTION_FIRST_PARAGRAPH_MAX_LEN} characters; it is "
            f"{len(first)}. Move detail into later paragraphs (separated by "
            f"'\\n\\n')."
        )

    if rest_len <= len(first):
        raise ValueError(
            f"The {entity_type} description's later paragraphs ({rest_len} "
            f"characters total) must be longer than the first paragraph "
            f"({len(first)} characters): the first paragraph is a brief teaser, "
            f"and the expanded view should add substantive detail."
        )

    return description


SUPPORTED_SERVICE_OPTIONS: dict[str, type | tuple[type, ...]] = {
    "enrollment": dict,  # Per-enrollment config: {limit, limit_per_customer, limit_per_user}
    "routing_vars": dict,  # Seller-managed operational variables for template resolution at request time
    "ops_testing_parameters": dict,
    "prompt_recurrence": bool,  # Prompt recurrence options during enrollment
    "recurrence_min_interval_seconds": int,
    "recurrence_max_interval_seconds": int,
    "recurrence_allow_cron": bool,
}

# Inner keys of the ``enrollment`` service option and their expected types.
SUPPORTED_ENROLLMENT_OPTIONS: dict[str, type | tuple[type, ...]] = {
    "limit": int,  # global active-enrollment cap per service
    "limit_per_customer": int,
    "limit_per_user": int,
}


def _validate_enrollment_option(value: dict[str, Any]) -> list[str]:
    """Light validation of the nested ``service_options.enrollment`` dict.

    The top-level allowlist only requires ``enrollment`` to be a dict; this
    checks the known inner keys (types + value constraints) and rejects
    unknown inner keys so typos surface.
    """
    errors: list[str] = []
    for key, val in value.items():
        if key not in SUPPORTED_ENROLLMENT_OPTIONS:
            errors.append(
                f"Unrecognized service_options.enrollment key '{key}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_ENROLLMENT_OPTIONS))}"
            )
            continue
        expected_type = SUPPORTED_ENROLLMENT_OPTIONS[key]
        if expected_type is int and isinstance(val, bool):
            errors.append(f"service_options.enrollment.{key} must be int, got bool")
            continue
        if not isinstance(val, expected_type):
            type_name = expected_type.__name__ if isinstance(expected_type, type) else str(expected_type)
            errors.append(f"service_options.enrollment.{key} must be {type_name}, got {type(val).__name__}")
            continue
        if key.startswith("limit") and isinstance(val, int) and val <= 0:
            errors.append(f"service_options.enrollment.{key} must be a positive integer, got {val}")
    return errors


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

        # Validate the nested enrollment config (limit*).
        if key == "enrollment" and isinstance(value, dict):
            errors.extend(_validate_enrollment_option(value))

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


# S3 bucket name rules: https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
_S3_GATEWAY_PREFIX = "${S3_GATEWAY_BASE_URL}/"


def validate_s3_gateway_alias(alias: str, field: str) -> list[str]:
    """Validate the alias portion of an S3 gateway base_url.

    ``alias`` is the part after ``${S3_GATEWAY_BASE_URL}/``.  Jinja2 template
    aliases (containing ``{{`` or ``{%``) must be skipped by the caller.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    if not alias:
        errors.append(f"{field}: S3 gateway alias is empty (must be a valid S3 bucket name)")
        return errors

    if not _S3_BUCKET_RE.match(alias):
        errors.append(
            f"{field}: S3 gateway alias '{alias}' is not a valid S3 bucket name — "
            f"must be 3-63 characters, lowercase letters/digits/hyphens only, "
            f"and must start and end with a letter or digit "
            f"(see https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html)"
        )
        return errors

    if alias.startswith("xn--"):
        errors.append(f"{field}: S3 gateway alias '{alias}' cannot start with 'xn--' (reserved prefix)")
    elif alias.endswith("-s3alias") or alias.endswith("--ol-s3"):
        errors.append(f"{field}: S3 gateway alias '{alias}' uses a reserved suffix")

    return errors


def validate_listing_s3_base_urls(user_access_interfaces: dict[str, Any] | None) -> list[str]:
    """Validate S3 gateway aliases across all user_access_interfaces.

    For each interface whose base_url starts with ``${S3_GATEWAY_BASE_URL}/``,
    the alias must satisfy AWS S3 bucket naming rules.  Jinja2 template aliases
    (containing ``{{`` or ``{%``) are skipped.

    Returns a list of error messages (empty if all valid).
    """
    if not user_access_interfaces or not isinstance(user_access_interfaces, dict):
        return []

    errors: list[str] = []
    for iface_name, iface in user_access_interfaces.items():
        if not isinstance(iface, dict):
            continue
        base_url = iface.get("base_url", "")
        if not isinstance(base_url, str) or not base_url.startswith(_S3_GATEWAY_PREFIX):
            continue
        alias = base_url[len(_S3_GATEWAY_PREFIX) :]
        if "{{" in alias or "{%" in alias:
            continue
        field = f"user_access_interfaces.{iface_name}.base_url"
        errors.extend(validate_s3_gateway_alias(alias, field))

    return errors


_API_GATEWAY_PREFIX = "${API_GATEWAY_BASE_URL}"

# Single path segment in a gateway service identifier (provider slot,
# service-name slot, or variant tag). Alphanumeric + . - _, must start
# with alphanumeric.
_GATEWAY_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def validate_listing_gateway_base_urls(user_access_interfaces: dict[str, Any] | None) -> list[str]:
    """Validate ``${API_GATEWAY_BASE_URL}/...`` base_urls.

    The gateway base_url is intentionally left almost unconstrained: a provider
    may route many services through a single base_url and differentiate them by
    other parts of the request (e.g. ``routing_key`` / ``model``), so we cannot
    require the path to track the service name. For example
    ``${API_GATEWAY_BASE_URL}/parasail`` with ``model: <model_name>`` is a valid
    topology that does not need ``…/parasail/<model_name>``. Specs are also
    frequently rendered from Jinja2 ``.j2`` templates, so forcing a literal
    ``{{ service_name }}`` segment would require awkward ``{% raw %}`` wrapping.

    The **only** rule enforced: the first path segment after
    ``${API_GATEWAY_BASE_URL}/``, when it is a single character, must be ``a`` —
    single-letter prefixes are reserved for platform wrapper-stack primitives
    (``b/``, ``m/``, ``l/``, ``t/``, ``f/`` …) and only the ``/a/<alias>``
    movable pointer (#1139) is available to listings. For an ``a/`` path the
    alias remainder must satisfy the per-segment grammar. The gateway root, a
    fully dynamic first segment (``{{ … }}`` / ``${ … }``), and any
    multi-character literal first segment (a provider path, a rendered
    ``{{ service_name }}``, etc.) are all accepted.

    Returns a list of error messages (empty if all valid).
    """
    if not user_access_interfaces or not isinstance(user_access_interfaces, dict):
        return []

    errors: list[str] = []
    for iface_name, iface in user_access_interfaces.items():
        if not isinstance(iface, dict):
            continue
        base_url = iface.get("base_url", "")
        if not isinstance(base_url, str):
            continue
        if not base_url.startswith(_API_GATEWAY_PREFIX):
            continue

        field = f"user_access_interfaces.{iface_name}.base_url"
        suffix = base_url[len(_API_GATEWAY_PREFIX) :]
        if suffix.startswith("/"):
            suffix = suffix[1:]

        # Gateway root, or a path whose first segment is dynamic — nothing
        # static to constrain. Allowed.
        if not suffix or _earliest_dynamic_marker(suffix) == 0:
            continue

        first_segment = suffix.split("/", 1)[0]

        # Single-character first segments are reserved for platform primitives;
        # only the ``a/`` movable pointer (#1139) is available to listings.
        if len(first_segment) == 1:
            if first_segment != "a":
                errors.append(
                    f"{field}: base_url '{base_url}' uses reserved single-letter prefix "
                    f"'/{first_segment}/'. Single-character path prefixes are reserved for "
                    f"platform primitives; only '${{API_GATEWAY_BASE_URL}}/a/<alias>' is "
                    f"available to listings."
                )
                continue
            # ``/a/<alias>`` movable pointer — validate the static alias grammar
            # (truncate any dynamic per-enrollment suffix first).
            static = suffix
            dyn = _earliest_dynamic_marker(static)
            if dyn is not None:
                static = static[:dyn].rstrip("/")
            errors.extend(_validate_gateway_path_prefix(static, field))
            continue

        # Multi-character first segment (provider path, rendered service name,
        # etc.) — unconstrained.

    return errors


def _earliest_dynamic_marker(s: str) -> int | None:
    """Return the index of the earliest dynamic-substitution marker in ``s``.

    Recognized markers:

    - ``{{`` — Jinja variable
    - ``{%`` — Jinja block tag
    - ``${`` — shell-style env-var reference (e.g. ``${ customer_secrets.X }``)

    Returns the smallest index where one of these markers begins, or
    ``None`` if no markers are present.
    """
    candidates = [i for i in (s.find("{{"), s.find("{%"), s.find("${")) if i >= 0]
    return min(candidates) if candidates else None


def _validate_gateway_path_prefix(path: str, field: str) -> list[str]:
    """Validate ``<provider>[/<segment>...][@<variant>]`` grammar.

    ``path`` is the substring after ``${API_GATEWAY_BASE_URL}/`` (with
    the leading slash already stripped). The first segment is the
    provider slot; subsequent segments are part of the service-name and
    may be hierarchical (e.g. HuggingFace's ``Qwen/Qwen2.5-Coder-7B-Instruct``).
    Returns error messages — empty if valid.

    Special case — the ``a/`` movable-pointer naming convention (#1139):
    a leading ``a/`` segment is permitted as a customer-facing hint that
    the published URL is a "movable pointer" — the seller / platform
    publisher reserves the right to re-point the underlying target to a
    new listing later. The single-character first-segment rule (normally
    reserved to avoid collision with gateway primitive prefixes like
    ``m/``, ``l/``, ``f/``, etc.) is therefore relaxed *only* for the
    literal first segment ``a``; the rest of the path after the strip
    must satisfy the normal ``<provider>[/<segment>...][@<variant>]``
    grammar. All other single-letter prefixes remain reserved.
    """
    errors: list[str] = []

    # ``a/`` movable-pointer naming convention (#1139). Strip the
    # leading ``a/`` and validate the remainder under the normal rules.
    # Stripped exactly once — nested ``a/a/...`` is unusual and not part
    # of the convention.
    if path.startswith("a/"):
        path = path[2:]
        if not path:
            errors.append(
                f"{field}: gateway path 'a/' is incomplete — the 'a/' "
                f"movable-pointer prefix must be followed by a real path "
                f"(e.g. 'a/cohere-latest')"
            )
            return errors

    # Split on '@' first to separate the name part from the optional variant.
    at_parts = path.split("@")
    if len(at_parts) > 2:
        errors.append(
            f"{field}: gateway path '{path}' has multiple '@' — "
            f"at most one variant tag is allowed (e.g. '<provider>/<service>@byok')"
        )
        return errors

    name_part = at_parts[0]
    variant = at_parts[1] if len(at_parts) == 2 else None

    # Name part: 1 or more segments separated by '/'.
    segments = name_part.split("/")
    for i, segment in enumerate(segments):
        slot_name = "provider" if i == 0 else f"segment {i + 1}"
        if not segment:
            errors.append(
                f"{field}: gateway path '{path}' has an empty {slot_name} (consecutive or leading/trailing '/')"
            )
            continue
        if len(segment) < 2:
            errors.append(
                f"{field}: {slot_name} '{segment}' in gateway path '{path}' must be "
                f"at least 2 characters (single-letter segments are reserved to avoid "
                f"collision with gateway primitive prefixes a/, g/, b/, c/, l/, m/, r/, d/, t/, f/)"
            )
            continue
        if not _GATEWAY_SEGMENT_RE.match(segment):
            errors.append(
                f"{field}: {slot_name} '{segment}' in gateway path '{path}' has "
                f"invalid characters (allowed: letters, digits, '.', '-', '_'; must "
                f"start with an alphanumeric character)"
            )

    # Variant tag: per-segment rules apply (variants are rarely hierarchical,
    # but we don't forbid it).
    if variant is not None:
        if not variant:
            errors.append(
                f"{field}: gateway path '{path}' has an empty variant after '@' "
                f"(use '<name>@<variant>' or omit the '@')"
            )
        else:
            for segment in variant.split("/"):
                if not segment:
                    errors.append(f"{field}: variant in gateway path '{path}' has an empty segment")
                    continue
                if not _GATEWAY_SEGMENT_RE.match(segment):
                    errors.append(
                        f"{field}: variant segment '{segment}' in gateway path '{path}' has "
                        f"invalid characters (allowed: letters, digits, '.', '-', '_'; must "
                        f"start with an alphanumeric character)"
                    )

    return errors


_SMTP_GATEWAY_BASE_URL = "${SMTP_GATEWAY_BASE_URL}"


def validate_listing_smtp_base_urls(user_access_interfaces: dict[str, Any] | None) -> list[str]:
    """Validate SMTP gateway interfaces in listing_v1 user_access_interfaces.

    For each interface whose base_url is or starts with ``${SMTP_GATEWAY_BASE_URL}``:

    - ``base_url`` must be exactly ``${SMTP_GATEWAY_BASE_URL}`` — no path suffix.
    - ``routing_key`` must be a dict with a non-empty ``username`` key.

    Returns a list of error messages (empty if all valid).
    """
    if not user_access_interfaces or not isinstance(user_access_interfaces, dict):
        return []

    errors: list[str] = []
    for iface_name, iface in user_access_interfaces.items():
        if not isinstance(iface, dict):
            continue
        base_url = iface.get("base_url", "")
        if not isinstance(base_url, str):
            continue
        if not (base_url == _SMTP_GATEWAY_BASE_URL or base_url.startswith(_SMTP_GATEWAY_BASE_URL + "/")):
            continue

        field = f"user_access_interfaces.{iface_name}"

        if base_url != _SMTP_GATEWAY_BASE_URL:
            errors.append(
                f"{field}.base_url: SMTP gateway base_url must be exactly "
                f"'${{SMTP_GATEWAY_BASE_URL}}' with no path suffix — "
                f"SMTP routing uses routing_key.username, not URL path"
            )

        routing_key = iface.get("routing_key")
        if not isinstance(routing_key, dict):
            errors.append(
                f"{field}.routing_key: SMTP gateway interface requires a 'routing_key' dict with a 'username' entry"
            )
        else:
            username = routing_key.get("username")
            if not username or not isinstance(username, str):
                errors.append(
                    f"{field}.routing_key.username: SMTP gateway interface requires "
                    f"a non-empty 'username' in routing_key"
                )

    return errors


# Slug pattern enforced on access interface names (the dictionary keys
# of ``user_access_interfaces``). Names become the SDK handle for
# routing — e.g. ``service.dispatch(interface="default")`` — so they
# must be URL-safe and predictable across consumers (URLs, dict keys,
# attribute access). The unitysvc backend enforces the same pattern on
# AccessInterfaceBase.name; surfacing it at CLI validation time means
# sellers see the violation before publishing.
_INTERFACE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _suggest_interface_slug(name: str) -> str:
    """Suggest a slug-conformant rename for a non-conforming interface key."""
    suggested = re.sub(r"[^a-z0-9_-]+", "_", name.lower())
    suggested = suggested.strip("_-")
    # Slugs must start with a letter or digit; if stripping leaves
    # something starting with a non-alphanumeric char (impossible after
    # the strip above, but defensive) or an empty string, fall back.
    if not suggested or not suggested[0].isalnum():
        return "interface"
    return suggested


def validate_access_interface_names(user_access_interfaces: dict[str, Any] | None) -> list[str]:
    """Validate that ``user_access_interfaces`` keys are URL-friendly slugs.

    Each key in the ``user_access_interfaces`` dict becomes the access
    interface's name on the backend and is used by SDK callers as the
    routing handle. Allowing arbitrary strings (e.g. ``"Provider SDK"``)
    breaks URL construction, attribute-style access, and pushes
    inconsistent identifiers to scripts.

    Returns a list of error messages (empty if all keys conform).
    """
    if not user_access_interfaces or not isinstance(user_access_interfaces, dict):
        return []

    errors: list[str] = []
    for iface_name in user_access_interfaces:
        if not isinstance(iface_name, str) or not _INTERFACE_NAME_PATTERN.match(iface_name):
            suggested = _suggest_interface_slug(str(iface_name))
            errors.append(
                f"user_access_interfaces.'{iface_name}': interface name must be a "
                "URL-friendly slug (lowercase ASCII alphanumeric plus '-' / '_', "
                "must start with a letter or digit). "
                f"Suggestion: rename to '{suggested}'."
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


def build_jinja_var_context(
    service_options: dict[str, Any] | None,
    user_parameters_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the ``{ service_name, params, routing_vars, enrollment }``
    context used to render Jinja templates in listing/offering data.

    A name is "defined" for validation purposes if it appears in:

    - ``service_name``: always defined — the platform injects it (equal to
      ``listing.name``) at render time, so ``{{ service_name }}`` is the
      canonical way to route a gateway base_url (issue #1138).
    - ``params``: ``user_parameters_schema.properties`` (declared parameters)
      *or* ``service_options.ops_testing_parameters`` (test defaults). Either
      means the runtime will have a value, so referencing it is safe.
    - ``routing_vars``: keys of ``service_options.routing_vars``.
    - ``enrollment``: intrinsic per-enrollment fields ``code`` / ``id`` — always
      available at render time (#1202), so ``{{ enrollment.code }}`` is always
      defined.

    Values are placeholder strings — only key presence matters for
    StrictUndefined checks.
    """
    service_options = service_options or {}
    user_parameters_schema = user_parameters_schema or {}

    params_keys: set[str] = set()
    props = user_parameters_schema.get("properties") if isinstance(user_parameters_schema, dict) else None
    if isinstance(props, dict):
        params_keys.update(k for k in props if isinstance(k, str))
    ops_testing = service_options.get("ops_testing_parameters") if isinstance(service_options, dict) else None
    if isinstance(ops_testing, dict):
        params_keys.update(k for k in ops_testing if isinstance(k, str))

    routing_vars = service_options.get("routing_vars") if isinstance(service_options, dict) else None

    return {
        # Platform-injected at render time (= listing.name). Always defined.
        "service_name": "",
        "params": {k: "" for k in params_keys},
        "routing_vars": {k: "" for k in routing_vars} if isinstance(routing_vars, dict) else {},
        # Intrinsic per-enrollment fields, always available at render time (#1202).
        "enrollment": {"code": "", "id": ""},
    }


def _iter_strings(value: Any, path: str):
    """Yield ``(field_path, str_value)`` for every string nested under ``value``."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, sub in value.items():
            yield from _iter_strings(sub, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, sub in enumerate(value):
            yield from _iter_strings(sub, f"{path}[{idx}]")


def validate_listing_jinja_var_references(data: dict[str, Any] | None) -> list[str]:
    """Validate that every ``{{ params.X }}`` / ``{{ routing_vars.X }}`` /
    ``{{ enrollment.X }}`` reference inside ``user_access_interfaces``
    resolves to a name declared in the listing's own
    ``service_options`` / ``user_parameters_schema`` (or the intrinsic
    ``enrollment`` namespace).

    A reference is undefined when:

    - ``params.X``: ``X`` is not in ``user_parameters_schema.properties`` and
      not in ``service_options.ops_testing_parameters``.
    - ``routing_vars.X``: ``X`` is not in ``service_options.routing_vars``.
    - ``enrollment.X``: ``X`` is not an intrinsic field (``code`` / ``id``).

    Returns a list of error messages (empty if all references resolve).
    """
    if not isinstance(data, dict):
        return []
    uai = data.get("user_access_interfaces")
    if not isinstance(uai, dict) or not uai:
        return []

    context = build_jinja_var_context(data.get("service_options"), data.get("user_parameters_schema"))
    jinja_env = Environment(undefined=StrictUndefined, autoescape=False)
    errors: list[str] = []

    for iface_name, iface in uai.items():
        if not isinstance(iface, dict):
            continue
        for field_path, value in _iter_strings(iface, f"user_access_interfaces.{iface_name}"):
            if "{{" not in value and "{%" not in value:
                continue
            try:
                jinja_env.from_string(value).render(**context)
            except UndefinedError as exc:
                errors.append(
                    f"{field_path}: Jinja reference is undefined — {exc.message}. "
                    f"Define the variable in the listing's service_options "
                    f"(routing_vars), user_parameters_schema.properties (for params), "
                    f"or use the intrinsic enrollment.code / enrollment.id."
                )
            except TemplateSyntaxError as exc:
                errors.append(f"{field_path}: Jinja syntax error in '{value}' — {exc.message}.")

    return errors

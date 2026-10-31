"""Service access: what a service exports, and what a seller asks a customer to approve.

Three stages (unitysvc/unitysvc#2720):

* **Exports** — what a target service exposes. A service declares it in its own
  definition, as the offering's ``exports``::

      "exports": {
        "permissions": {"read": ["GET /records/*"], "write": ["POST /records"]},
        "options": {"type": "object", "properties": {
          "log_level": {"type": "string", "enum": ["info", "warn", "error"], "default": "info"},
          "max_size": {"type": "integer", "minimum": 1}}}
      }

  A service that declares no ``permissions`` has one implicit permission,
  ``invoke``, covering every route (``"* /*"``); that is what
  ``service:<name>`` and the ``service:*`` wildcard grant. A service that
  declares ``permissions`` has exactly those, each covering the routes it
  lists, and ``invoke`` only if it declares it too. A data service (``me``,
  ``log``, ``needs``) therefore declares ``read`` / ``write`` and no
  ``invoke``: holding the customer's own data, it is reached only by a grant
  that names its permissions, never by ``invoke`` or a wildcard. Permissions
  name the routes they cover; they are never inferred from HTTP methods
  (``POST /chat/completions`` modifies nothing). ``options`` is a JSON Schema,
  like a channel's ``params.schema`` (with an optional
  ``options_ui_schema``), of the customizations a grant may set on the
  service, including limits only it can apply (a message size); a property's
  ``default`` is what the service does when the policy does not set the
  option. Each property is a string, integer, number, boolean or array of
  strings, so a grant can write its options in query-string syntax
  (``"log_level=warn"``, arrays comma-joined). That is only how a grant stores
  them, not how they reach the service (below).

* **Customer access requests** — what a seller asks the enrolling customer to
  approve: to use other services through that customer's account and
  authority (write to their log, notify them, call AI with their preference
  and wallet). A channel declares them as
  ``upstream_access_config.<channel>.customer_access_requests``::

      {"object": "service:log", "permissions": ["invoke"],
       "required": true, "justification": "Records each check you run",
       "options": "log_level=warn",
       "limits": {"count_per_day": 1000}}

  A request grants nothing by itself. ``required`` says whether the customer
  may decline it. Its ``object`` and ``permissions`` are fixed, and so are
  its ``options``: the values the seller declares are the values the policy
  holds (unitysvc/unitysvc#2831). An option it does not declare is the
  customer's to set. Its ``limits`` only pre-fill the enrollment form.
  Several requests may name one object (two kinds of room) if each fixes an
  option to a different value, so an approval says which one it is.

* **The key's policy** — what the customer actually approves when enrolling:
  they keep every required request, keep or decline each optional one, add
  options the request leaves open, and set limits, their own included. Each
  approved request becomes an entry (a grant) in the policy of the key issued
  for that enrollment, in the same shape, with the request's fixed options.

**Invariant.** Calls made under an approved request run under the enrolling
customer's delegated identity, within the approved permissions, options and
limits. Looking up a target's ``exports`` validates a request's *capabilities*;
customer consent and key issuance establish its *authority*.

* **The service applies its options; the upstream URL is never the channel.**
  The gateway does not change a target's upstream URL (its ``base_url`` or
  query) to carry policy. A service implemented in the gateway (``me``,
  ``log``, ``ai``, ``notify``, ``email``, ``bill``) reads the calling key's
  policy from the routing context and applies the option itself. A proxied
  service can only be influenced through its own upstream's interface, so it
  applies an option only where its configuration maps it into the upstream
  request in a form that upstream understands (for example a request
  transformer setting a body field); until such a mapping exists, options are
  enforced by gateway-implemented services only. Either way the policy itself
  never reaches a seller. An option the policy does not set takes the
  service's ``default``.

* **Limits are universal**: the platform meters every call, so any service can
  be limited by ``count`` or ``amount`` per hour, day or month, or in total,
  without exporting anything (``{"count_per_day": 2, "amount_per_month": "2.00"}``).

A request's ``object`` is ``service:<name>``; or ``resource:room``, a room the
platform provisions for the enrollment (unitysvc/unitysvc#2830), whose
``kind`` (``chat``, ``support``, ``announcement``, ``discussion``) the
request fixes and whose permissions the kind bounds (:data:`ROOM_KINDS`); or
``apikey``: how long a key leased for the enrollment lives
(``limits.api_key_lifespan``, up to a day, with a justification), which grants
nothing. :func:`build_policy` turns a customer's approvals into the policy an
enrollment keeps.

A policy has three kinds of entry (unitysvc/unitysvc#2770):

* ``{"object": "enrollment", "limits": {...}}``: this enrollment's own limits
  (what it may cost in total, say). The customer's own; no seller requests it,
  and it grants nothing.
* ``{"object": "service:<name>", ...}``: a service the enrolled app calls for
  the customer, usually a common one (``service:ai``, ``service:notify``). A
  seller may name a specific service (``service:labs/llm-premium`` rather than
  ``service:ai`` with ``options="service=labs/llm-premium"``), at the cost of
  the customer's choice of provider, which a customer may decline.
* ``{"object": "service:<name>", "enrollment": "<CODE>", ...}``: one of the
  customer's enrollments of a service that requires enrolling (a chain
  enrollment), which the app calls through ``/e/<CODE>``. Its params are fixed
  by that enrollment, so it takes no ``options``. Which enrollment is the
  customer's choice when approving, so a request never names one; core checks
  the code's form, the caller that the enrollment is the customer's.

There is no central list of services. Core does no I/O: every request's
permissions and options are checked against its target's ``exports`` when the
caller passes an :data:`ExportsResolver` that looks the target up in the
published catalog bundle, where every service row carries its ``exports``. An
``invoke`` claim is looked up too, since a service that declares permissions
may not export ``invoke``. Limits need no lookup. Without a resolver, only the
shape of each entry is checked.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

from .models.validators import validate_service_identifier

SERVICE_PREFIX = "service:"
INVOKE = "invoke"
# The key itself: a request for a longer-lived leased key, and the approved
# lifespan in a policy. It grants nothing.
APIKEY_OBJECT = "apikey"
# Every service at once. It belongs to keys a customer creates by hand: a
# policy derived from an enrollment names each service it calls
# (unitysvc/unitysvc#2783), so no request or approval may hold it.
SERVICE_WILDCARD = "service:*"
API_KEY_LIFESPAN = "api_key_lifespan"
# Seconds a leased key lives unless the policy says otherwise, and at most.
DEFAULT_API_KEY_LIFESPAN = 3600
MAX_API_KEY_LIFESPAN = 86400
# The channel key a seller's requests are declared under.
REQUESTS_KEY = "customer_access_requests"

ExportsResolver = Callable[[str], dict[str, Any] | None]
"""Look up a service by name: its ``exports`` (``{}`` when it declares none),
or ``None`` when the catalog has no such service."""

_ROUTE = re.compile(r"^(\*|GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\S*$")
# A grant writes its options in query-string syntax, so an option's name stays
# plain; so does a permission's.
_OPTION_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_PERMISSION_NAME = _OPTION_NAME
# ``count_per_day``, ``amount_per_month``, ``count_total`` ...
_LIMIT = re.compile(r"^(count|amount)_(per_hour|per_day|per_month|total)$")


# The JSON Schema types an option may have: each serializes to one query value
# (an array as comma-joined strings, OpenAPI ``style: form, explode: false``).
_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})


@dataclass(frozen=True)
class Exports:
    permissions: dict[str, tuple[str, ...]]
    # The ``options`` JSON Schema (``{"type": "object", "properties": ...}``),
    # empty when the service accepts none.
    options: dict[str, Any]

    @property
    def permission_names(self) -> tuple[str, ...]:
        return tuple(self.permissions)

    @property
    def option_names(self) -> tuple[str, ...]:
        return tuple(self.options.get("properties", {}))


# Every route, for the implicit ``invoke`` of a service that declares no permissions.
ALL_ROUTES = "* /*"
DEFAULT_PERMISSIONS: dict[str, tuple[str, ...]] = {INVOKE: (ALL_ROUTES,)}
DEFAULT_EXPORTS = Exports(DEFAULT_PERMISSIONS, {})

# What a request may ask the platform to provision for the enrolling customer,
# rather than a service to call (unitysvc/unitysvc#2830): ``resource:<name>``.
# Core knows each resource, so its claims need no catalog lookup.
RESOURCE_PREFIX = "resource:"
# A room's kind is its identity: a request fixes it, and each kind allows the
# permissions its provisioning grants. The customer creates and owns a chat
# room; the others are the seller's rooms the customer is admitted to (a
# private support room, a read-only announcement room, a discussion room).
ROOM_KINDS: dict[str, frozenset[str]] = {
    "chat": frozenset({"create"}),
    "support": frozenset({"join", "read", "post"}),
    "announcement": frozenset({"join", "read"}),
    "discussion": frozenset({"join", "read", "post"}),
}
ROOM_KIND = "kind"
ROOM_MAX_MEMBERS = "max_members"
RESOURCES: dict[str, Exports] = {
    "room": Exports(
        {name: () for name in ("create", "join", "read", "post")},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                ROOM_KIND: {"type": "string", "enum": list(ROOM_KINDS)},
                ROOM_MAX_MEMBERS: {"type": "integer", "minimum": 1},
            },
        },
    ),
}

_REQUEST_KEYS = frozenset({"object", "permissions", "required", "justification", "options", "limits"})
_APIKEY_REQUEST_KEYS = frozenset({"object", "required", "justification", "limits"})
# What a policy entry (an approved request) holds.
_POLICY_KEYS = frozenset({"object", "permissions", "options", "limits"})
# This enrollment itself: an entry capping what it may cost. It grants nothing.
ENROLLMENT_OBJECT = "enrollment"
# On a service entry, one enrollment of that service (a chain enrollment).
ENROLLMENT_KEY = "enrollment"
# An enrollment code: canonical Crockford base32 (no I, L, O, U).
_ENROLLMENT_CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]+$")


def _strings(value: Any) -> bool:
    """A non-empty list of distinct non-empty strings."""
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(v, str) and v.strip() for v in value)
        and len(set(value)) == len(value)
    )


def parse_exports(where: str, declared: Any) -> Exports:
    """Read an offering's ``exports``, refusing a malformed one.

    ``None`` or ``{}`` is the default: ``invoke`` on every route, no options.
    Without ``permissions`` (options only), ``invoke`` is still the one
    permission. With ``permissions``, the service exports exactly those; it
    may declare ``invoke`` among them.
    """
    if declared is None:
        return DEFAULT_EXPORTS
    if not isinstance(declared, dict):
        raise ValueError(f"{where} must be an object")
    if extra := set(declared) - {"permissions", "options", "options_ui_schema"}:
        raise ValueError(f"{where}: unknown keys {sorted(extra)}")
    permissions = declared.get("permissions", {})
    if not isinstance(permissions, dict) or ("permissions" in declared and not permissions):
        raise ValueError(
            f'{where}.permissions must be {{"<name>": ["<METHOD> /<path>", ...]}} with at least one '
            f"permission; omit it to export only '{INVOKE}' on every route"
        )
    for name, routes in permissions.items():
        at = f"{where}.permissions.{name}"
        if not _PERMISSION_NAME.match(name):
            raise ValueError(f"{at}: a permission name is lowercase letters, digits and '_'")
        if not _strings(routes) or not all(_ROUTE.match(r) for r in routes):
            raise ValueError(f"{at} must be a non-empty list of routes like 'GET /records/*'")
    options = declared.get("options", {})
    if options != {}:
        _check_options_schema(f"{where}.options", options)
    ui_schema = declared.get("options_ui_schema")
    if ui_schema is not None and not isinstance(ui_schema, dict):
        raise ValueError(f"{where}.options_ui_schema must be an object")
    if not permissions:
        return Exports(DEFAULT_PERMISSIONS, options)
    return Exports({name: tuple(routes) for name, routes in permissions.items()}, options)


def _check_options_schema(where: str, schema: Any) -> None:
    """Refuse an ``options`` schema that is not a JSON Schema object whose
    properties are query-serializable: ``string``, ``integer``, ``number``,
    ``boolean``, or an ``array`` of strings. Options are optional, so
    ``required`` is refused, and only the declared properties are accepted."""
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        raise ValueError(f'{where} must be a JSON Schema {{"type": "object", "properties": {{...}}}}')
    if extra := set(schema) - {"type", "properties", "additionalProperties", "title", "description"}:
        raise ValueError(f"{where}: unknown keys {sorted(extra)} (options are optional; no 'required')")
    if schema.get("additionalProperties", False) is not False:
        raise ValueError(f"{where}.additionalProperties must be false")
    if ref := _find_reference(schema):
        raise ValueError(f"{where}: {ref!r} is not allowed; an options schema is self-contained")
    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"{where} is not a valid JSON Schema: {exc.message}") from None
    for name, prop in schema["properties"].items():
        at = f"{where}.properties.{name}"
        if not _OPTION_NAME.match(name):
            raise ValueError(f"{at}: an option name is lowercase letters, digits and '_'")
        kind = prop.get("type") if isinstance(prop, dict) else None
        if not isinstance(kind, str):
            raise ValueError(f"{at}.type must be one type, one of {sorted(_SCALAR_TYPES | {'array'})}")
        if kind == "array":
            items = prop.get("items")
            if not isinstance(items, dict) or items.get("type") != "string":
                raise ValueError(f"{at}: an array option is a list of strings")
        elif kind not in _SCALAR_TYPES:
            raise ValueError(f"{at}.type must be one of {sorted(_SCALAR_TYPES | {'array'})}")


_REFERENCE_KEYS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})


def _find_reference(node: Any) -> str | None:
    """The first schema reference keyword anywhere in ``node``, or ``None``.

    Options schemas are flat, so a reference is never needed, and resolving one
    could fetch a URL: refusing them keeps validation free of I/O."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _REFERENCE_KEYS:
                return str(key)
            if found := _find_reference(value):
                return found
    elif isinstance(node, list):
        for item in node:
            if found := _find_reference(item):
                return found
    return None


def _typed(prop: dict[str, Any], text: str) -> Any:
    """``text`` (a query value) as the type ``prop`` declares, or ``text``
    unchanged when it does not convert, so the schema reports the mismatch."""
    kind = prop.get("type")
    try:
        if kind == "integer":
            return int(text)
        if kind == "number":
            number = Decimal(text)
            return float(number) if number.is_finite() else text
    except (ValueError, InvalidOperation):
        return text
    if kind == "boolean":
        return {"true": True, "false": False}.get(text, text)
    if kind == "array":
        return text.split(",")
    return text


def _options_error(schema: dict[str, Any], values: dict[str, str]) -> str | None:
    """Why query-string option ``values`` do not satisfy the ``options``
    schema, or ``None``."""
    properties = schema.get("properties", {})
    typed = {name: _typed(properties.get(name, {}), text) for name, text in values.items()}
    # An empty registry retrieves nothing, so validation never performs I/O even
    # for a schema that bypassed ``_check_options_schema``.
    validator = Draft7Validator(schema, registry=Registry())
    error = next(iter(sorted(validator.iter_errors(typed), key=lambda e: list(e.path))), None)
    if error is None:
        return None
    field = ".".join(str(p) for p in error.path)
    return f"{field} {error.message}" if field else error.message


def _positive_decimal(text: str) -> bool:
    try:
        return Decimal(text).is_finite() and Decimal(text) > 0
    except InvalidOperation:
        return False


def parse_options(where: str, options: Any) -> dict[str, str]:
    """Read a grant's ``options`` query string (``"log_level=warn&max_size=2000"``)
    into names and values, refusing a malformed one. Each name appears once."""
    if not isinstance(options, str):
        raise ValueError(f'{where} must be a query string like "log_level=warn"')
    if not options:
        return {}
    try:
        pairs = parse_qsl(options, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ValueError(f'{where} must be a query string like "log_level=warn"') from None
    parsed: dict[str, str] = {}
    for name, value in pairs:
        if not _OPTION_NAME.match(name):
            raise ValueError(f"{where}: {name!r} is not an option name")
        if name in parsed:
            raise ValueError(f"{where}: {name} is set twice")
        if not value:
            raise ValueError(f'{where} must be a query string like "log_level=warn"; {name} has no value')
        parsed[name] = value
    return parsed


def validate_limits(where: str, limits: Any, *, apikey: bool = False) -> None:
    """Refuse ``limits`` unless it maps ``<metric>_per_<window>`` or
    ``<metric>_total`` to a maximum: ``count`` a positive integer, ``amount`` a
    finite positive decimal, as a number (``2.5``) or a string (``"2.50"``).
    Windows: hour, day, month. The same shape serves a request's suggested
    limits, the limits a customer sets in a key's policy, and an enrollment's own
    limits.

    With ``apikey`` (the ``apikey`` entry), the only limit is
    ``api_key_lifespan``: how many seconds a leased key lives, an integer from 1
    to :data:`MAX_API_KEY_LIFESPAN`.
    """
    if not isinstance(limits, dict):
        raise ValueError(f'{where} must be an object like {{"count_per_day": 2}}')
    if apikey:
        if set(limits) != {API_KEY_LIFESPAN}:
            raise ValueError(f'{where} must be {{"{API_KEY_LIFESPAN}": <seconds>}}')
        value = limits[API_KEY_LIFESPAN]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_API_KEY_LIFESPAN:
            raise ValueError(
                f"{where}.{API_KEY_LIFESPAN} must be a whole number of seconds from 1 to {MAX_API_KEY_LIFESPAN}"
            )
        return
    for key, value in limits.items():
        at = f"{where}.{key}"
        match = _LIMIT.match(key) if isinstance(key, str) else None
        if match is None:
            raise ValueError(f"{at}: a limit is count_/amount_ followed by per_hour, per_day, per_month or total")
        if match.group(1) == "count":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{at} must be a positive integer")
        elif isinstance(value, bool) or not isinstance(value, (int, float, str)) or not _positive_decimal(str(value)):
            raise ValueError(f"{at} must be a positive amount")


def validate_access_requests(
    where: str, requests: Any, resolve: ExportsResolver | None = None, *, explicit_required: bool = True
) -> None:
    """Refuse a channel's ``customer_access_requests`` unless every entry is a
    valid request.

    ``where`` prefixes error messages
    (``upstream_access_config.<channel>.customer_access_requests``).
    With ``resolve``, every entry's permissions and options are checked
    against its target's ``exports`` (``invoke`` included: a service that
    declares permissions may not export it), and an unknown target is
    refused. Limits need no lookup. Without ``resolve``, only the shape is
    checked.

    Every entry says whether it is ``required``. :func:`build_policy` passes
    ``explicit_required=False``: a declaration stored before that rule reads
    as optional, as it always did.
    """
    if not isinstance(requests, list):
        raise ValueError(f"{where} must be a list of access request objects")
    seen: list[tuple[str, dict[str, str], Exports | None]] = []
    for index, entry in enumerate(requests):
        at = f"{where}[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("object"), str):
            raise ValueError(f'{at} must be {{"object": "service:<name>", ...}}')
        obj = entry["object"]
        if obj == APIKEY_OBJECT:
            if any(other == obj for other, _, _ in seen):
                raise ValueError(f"{at}: {obj} is listed twice")
            seen.append((obj, {}, None))
            _check_apikey_request(at, entry)
            _check_required(at, entry, explicit_required)
            continue
        if obj == SERVICE_WILDCARD:
            raise ValueError(_service_wildcard_refusal(at, "requested"))
        resource = obj.startswith(RESOURCE_PREFIX)
        name = obj.removeprefix(RESOURCE_PREFIX if resource else SERVICE_PREFIX)
        if resource:
            if name not in RESOURCES:
                raise ValueError(
                    f"{at}: {obj!r} cannot be requested; the resources are "
                    + ", ".join(f"'{RESOURCE_PREFIX}{r}'" for r in RESOURCES)
                )
        elif not obj.startswith(SERVICE_PREFIX):
            raise ValueError(
                f"{at}: {obj!r} cannot be requested; name one service as 'service:<name>' "
                f"or a resource as '{RESOURCE_PREFIX}<name>'"
            )
        else:
            try:
                validate_service_identifier(name, "requested service")
            except ValueError as exc:
                raise ValueError(f"{at}: {obj!r} cannot be requested: {exc}") from None
        if extra := set(entry) - _REQUEST_KEYS:
            raise ValueError(f"{at}: unknown keys {sorted(extra)}")
        justification = entry.get("justification")
        if not isinstance(justification, str) or not justification.strip():
            raise ValueError(
                f"{at}.justification must say why the service needs {obj}; the customer reads it before approving"
            )
        _check_required(at, entry, explicit_required)
        permissions: list[str] = entry.get("permissions") or []
        if not _strings(permissions) or not all(_PERMISSION_NAME.match(p) for p in permissions):
            raise ValueError(
                f"{at}.permissions must be a non-empty list of distinct permission names "
                "(lowercase letters, digits and '_'; no wildcards)"
            )
        options = parse_options(f"{at}.options", entry.get("options", ""))
        validate_limits(f"{at}.limits", entry.get("limits", {}))
        exports = None
        if resource:
            exports = RESOURCES[name]
            check_resource(at, obj, permissions, options, entry.get("limits"))
            if kind := next(
                (o[ROOM_KIND] for other, o, _ in seen if other == obj and o[ROOM_KIND] == options[ROOM_KIND]), None
            ):
                raise ValueError(f"{at}: {obj} with {ROOM_KIND}={kind} is listed twice; a kind is requested once")
        elif resolve is not None:
            declared = resolve(name)
            if declared is None:
                raise ValueError(f"{at}: {obj} is not a published service")
            exports = parse_exports(f"{obj} exports", declared)
            check_claim(at, obj, permissions, options, exports)
        for other_index, (other, other_options, _) in enumerate(seen):
            if other == obj and _conflict(exports, options, other_options) is None:
                raise ValueError(
                    f"{at}: {obj} is listed twice; requests of one object must each fix an option "
                    f"to a different value (kind=chat, kind=feed), or the customer can't say which "
                    f"one they approve (see [{other_index}])"
                )
        seen.append((obj, options, exports))


def _check_required(at: str, entry: dict[str, Any], explicit: bool) -> None:
    if "required" in entry:
        if not isinstance(entry["required"], bool):
            raise ValueError(f"{at}.required must be true or false")
    elif explicit:
        raise ValueError(f"{at}.required must be true or false: say whether the customer may decline it")


def _conflict(exports: Exports | None, a: dict[str, str], b: dict[str, str]) -> str | None:
    """The first option ``a`` and ``b`` both set, to different values, or
    ``None``. Values are compared as the target's ``exports`` type them
    (``100`` and ``0100`` are one integer); without exports, as written."""
    properties = exports.options.get("properties", {}) if exports else {}
    for name in sorted(set(a) & set(b)):
        prop = properties.get(name, {})
        if _typed(prop, a[name]) != _typed(prop, b[name]):
            return name
    return None


def _label(request: dict[str, Any]) -> str:
    """A request as the customer tells it apart: its object, and its fixed options."""
    return f"{request['object']} ({request['options']})" if request.get("options") else str(request["object"])


def match_request(
    where: str, requests: list[dict[str, Any]], entry: dict[str, Any], exports: Exports | None = None
) -> int:
    """The index of the request in ``requests`` (already valid) that ``entry``,
    an approval or a stored policy entry, approves.

    It is the request of the same object whose fixed options the entry does
    not contradict: one the entry repeats, or leaves out. Requests of one
    object each fix an option to a different value, so only an entry that
    leaves all of those out can fit more than one; it is refused, naming the
    option that tells them apart. Raises ``ValueError``.
    """
    obj = entry.get("object")
    options = parse_options(f"{where}.options", entry.get("options", ""))
    same = [i for i, request in enumerate(requests) if request["object"] == obj]
    if not same:
        raise ValueError(f"{where}: {obj} is not requested")
    fixed = {i: parse_options("options", requests[i].get("options", "")) for i in same}
    fitting = [i for i in same if _conflict(exports, fixed[i], options) is None]
    if not fitting:
        if len(same) == 1:
            name = _conflict(exports, fixed[same[0]], options)
            assert name is not None
            raise ValueError(f"{where}.options.{name}: the service fixes it to {fixed[same[0]][name]!r}")
        raise ValueError(
            f"{where}: no request of {obj} has these options; the service requests "
            + ", ".join(_label(requests[i]) for i in same)
        )
    if len(fitting) > 1:
        name = _conflict(exports, fixed[fitting[0]], fixed[fitting[1]])
        raise ValueError(f"{where}.options: {obj} is requested {len(fitting)} times; set {name} to say which")
    return fitting[0]


def _service_wildcard_refusal(at: str, verb: str) -> str:
    return (
        f"{at}: {SERVICE_WILDCARD!r} cannot be {verb}: every service at once is only for keys "
        "a customer creates by hand; name each service as 'service:<name>'"
    )


def _check_apikey_request(at: str, entry: dict[str, Any]) -> None:
    """The ``apikey`` request: how long a leased key should live, with why."""
    if extra := set(entry) - _APIKEY_REQUEST_KEYS:
        raise ValueError(f"{at}: unknown keys {sorted(extra)} (the {APIKEY_OBJECT} request takes only limits)")
    justification = entry.get("justification")
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError(f"{at}.justification must say why a leased key needs to live this long; the customer reads it")
    validate_limits(f"{at}.limits", entry.get("limits"), apikey=True)


def check_claim(where: str, obj: str, permissions: list[str], options: dict[str, str], exports: Exports) -> None:
    """Refuse a claim on ``obj`` (``service:<name>``) that its ``exports`` don't cover.

    The check shared by a seller's access request and a key's policy entry:
    every permission must be one the target exports (``invoke`` only if it
    does), and every option, already split by :func:`parse_options`, must be
    one it declares, with a value valid for that option's schema.
    """
    if not set(permissions) <= set(exports.permission_names):
        raise ValueError(f"{where}.permissions: {obj} exports only {list(exports.permission_names)}")
    if unknown := sorted(set(options) - set(exports.option_names)):
        raise ValueError(
            f"{where}.options: {obj} accepts no option {unknown[0]!r} "
            f"(options: {sorted(exports.option_names) or 'none'})"
        )
    if error := _options_error(exports.options, options):
        raise ValueError(f"{where}.options: {error}")


def check_resource(where: str, obj: str, permissions: list[str], options: dict[str, str], limits: Any = None) -> None:
    """Refuse a claim on ``obj`` (``resource:<name>``) that the resource does
    not allow, for a request and for a policy entry alike.

    A room claim names its ``kind`` in its options; its permissions are some
    of those the kind allows, and ``max_members`` (a positive integer) only
    caps a room the customer creates. A resource is provisioned, not called,
    so it takes no limits.
    """
    check_claim(where, obj, permissions, options, RESOURCES[obj.removeprefix(RESOURCE_PREFIX)])
    if limits:
        raise ValueError(f"{where}.limits: {obj} is provisioned, not called; it takes no limits")
    kind = options.get(ROOM_KIND)
    if kind is None:
        raise ValueError(
            f"{where}.options must set {ROOM_KIND} (one of {list(ROOM_KINDS)}): a room's kind is its identity"
        )
    if not set(permissions) <= ROOM_KINDS[kind]:
        raise ValueError(f"{where}.permissions: a {kind} room allows only {sorted(ROOM_KINDS[kind])}")
    if ROOM_MAX_MEMBERS in options and "create" not in ROOM_KINDS[kind]:
        raise ValueError(f"{where}.options.{ROOM_MAX_MEMBERS}: only a room the customer creates takes a member cap")


def validate_channel_access_requests(config: Any, resolve: ExportsResolver | None = None) -> None:
    """Refuse the ``customer_access_requests`` of any channel of an ``upstream_access_config``
    unless valid. Each channel declares its own (unitysvc/unitysvc#2726)."""
    if not isinstance(config, dict):
        return
    for name, channel in config.items():
        if isinstance(channel, dict) and REQUESTS_KEY in channel:
            validate_access_requests(f"upstream_access_config.{name}.{REQUESTS_KEY}", channel[REQUESTS_KEY], resolve)


def build_policy(requests: Any, approvals: Any, resolve: ExportsResolver | None = None) -> list[dict[str, Any]]:
    """The key policy a customer's ``approvals`` of a channel's ``requests``
    make (unitysvc/unitysvc#2766, #2831).

    ``requests`` is the channel's ``customer_access_requests``; ``approvals``
    lists the requests the customer approves, each ``{"object", "permissions",
    "options", "limits"}`` (the ``apikey`` entry only ``{"object", "limits"}``).
    Each approval matches one request (:func:`match_request`), at most once,
    and every ``required`` request is approved. An approved request keeps
    exactly its permissions and its fixed options: the approval may repeat
    them, or leave the options out, but not change them. Beside them the
    customer sets the options the request leaves open and the limits, checked
    against the target's ``exports`` (with ``resolve``) and
    :func:`validate_limits`; a request's limits only pre-fill the form. A key
    lifespan may only be shortened. Returns the approvals as policy entries,
    their options in one canonical query string (names sorted), without empty
    ``options`` / ``limits``. Raises ``ValueError``.

    The customer may also add, at most once and without a request, this
    enrollment's own limits: ``{"object": "enrollment", "limits": {...}}``,
    with at least one limit and nothing else (unitysvc/unitysvc#2770). And an
    approval of a request that fixes no options may select one of the
    customer's enrollments of its service (``"enrollment": "<CODE>"``, a chain
    enrollment): a canonical code, and no ``options``. The caller checks the
    enrollment is the customer's.
    """
    validate_access_requests("requests", requests, explicit_required=False)
    if not isinstance(approvals, list):
        raise ValueError("approvals must be a list of approved requests")
    policy: list[dict[str, Any]] = []
    approved: set[int] = set()
    for index, approval in enumerate(approvals):
        at = f"approvals[{index}]"
        if not isinstance(approval, dict) or not isinstance(approval.get("object"), str):
            raise ValueError(f'{at} must be {{"object": ..., "permissions": [...]}}')
        obj = approval["object"]
        if obj == SERVICE_WILDCARD:
            raise ValueError(_service_wildcard_refusal(at, "approved"))
        if obj == ENROLLMENT_OBJECT:
            if any(entry["object"] == obj for entry in policy):
                raise ValueError(f"{at}: {obj} is listed twice")
            policy.append(_enrollment_entry(at, approval))
            continue
        if obj == APIKEY_OBJECT:
            if extra := set(approval) - {"object", "limits"}:
                raise ValueError(f"{at}: unknown keys {sorted(extra)} (the {APIKEY_OBJECT} entry takes only limits)")
        elif extra := set(approval) - _POLICY_KEYS - {ENROLLMENT_KEY}:
            raise ValueError(f"{at}: unknown keys {sorted(extra)}")
        exports = None
        resource = obj.startswith(RESOURCE_PREFIX) and obj.removeprefix(RESOURCE_PREFIX) in RESOURCES
        if resource:
            exports = RESOURCES[obj.removeprefix(RESOURCE_PREFIX)]
        elif resolve is not None and obj.startswith(SERVICE_PREFIX) and any(r["object"] == obj for r in requests):
            declared = resolve(obj.removeprefix(SERVICE_PREFIX))
            if declared is None:
                raise ValueError(f"{at}: {obj} is not a published service")
            exports = parse_exports(f"{obj} exports", declared)
        matched = match_request(at, requests, approval, exports)
        if matched in approved:
            raise ValueError(f"{at}: {_label(requests[matched])} is approved twice")
        approved.add(matched)
        request = requests[matched]
        if obj == APIKEY_OBJECT:
            validate_limits(f"{at}.limits", approval.get("limits"), apikey=True)
            asked = request["limits"][API_KEY_LIFESPAN]
            if approval["limits"][API_KEY_LIFESPAN] > asked:
                raise ValueError(f"{at}.limits.{API_KEY_LIFESPAN}: the service asks for at most {asked} seconds")
            policy.append({"object": obj, "limits": dict(approval["limits"])})
            continue
        fixed = parse_options("options", request.get("options", ""))
        code = approval.get(ENROLLMENT_KEY)
        if ENROLLMENT_KEY in approval:
            if not isinstance(code, str) or not _ENROLLMENT_CODE.match(code):
                raise ValueError(f"{at}.{ENROLLMENT_KEY} must be an enrollment code, like JYY87Y")
            if approval.get("options"):
                raise ValueError(f"{at}.options: an enrollment's params are fixed; it takes no options")
            if fixed:
                raise ValueError(
                    f"{at}.{ENROLLMENT_KEY}: the service fixes options of {obj} ({request['options']}), "
                    "which an enrollment cannot carry; approve it without one"
                )
        permissions: list[str] = approval.get("permissions") or []
        if not _strings(permissions) or set(permissions) != set(request["permissions"]):
            raise ValueError(f"{at}.permissions: the service requests exactly {request['permissions']}")
        # The request's values win over an equal value written another way.
        options = {**parse_options(f"{at}.options", approval.get("options", "")), **fixed}
        limits = approval.get("limits", {})
        validate_limits(f"{at}.limits", limits)
        if resource:
            check_resource(at, obj, permissions, options, limits)
        elif exports is not None:
            check_claim(at, obj, permissions, options, exports)
        entry: dict[str, Any] = {"object": obj, "permissions": list(permissions)}
        if code is not None:
            entry[ENROLLMENT_KEY] = code
        if options:
            entry["options"] = options_text(options)
        if limits:
            entry["limits"] = dict(limits)
        policy.append(entry)
    if missing := [_label(r) for i, r in enumerate(requests) if r.get("required") and i not in approved]:
        raise ValueError(f"approvals: the service requires {', '.join(missing)}")
    return policy


def options_text(options: dict[str, str]) -> str:
    """``options`` as the one query string a policy stores: names sorted, and
    only what must be escaped escaped (``service=labs/llm-fast``,
    ``profile=a,b``), so equal options are equal text."""
    return urlencode(sorted(options.items()), safe=",/")


def _enrollment_entry(at: str, approval: dict[str, Any]) -> dict[str, Any]:
    """This enrollment's own entry: limits only, and at least one."""
    if extra := set(approval) - {"object", "limits"}:
        raise ValueError(f"{at}: unknown keys {sorted(extra)} (the {ENROLLMENT_OBJECT} entry takes only limits)")
    limits = approval.get("limits")
    if not limits:
        raise ValueError(f"{at}.limits: the {ENROLLMENT_OBJECT} entry must set at least one limit")
    validate_limits(f"{at}.limits", limits)
    return {"object": ENROLLMENT_OBJECT, "limits": dict(limits)}


def api_key_lifespan(policy: Any) -> int:
    """Seconds a key leased with ``policy`` lives: its ``apikey`` entry's
    ``api_key_lifespan``, else :data:`DEFAULT_API_KEY_LIFESPAN`. A malformed
    entry gets the default, never more."""
    for entry in policy if isinstance(policy, list) else []:
        if isinstance(entry, dict) and entry.get("object") == APIKEY_OBJECT:
            try:
                validate_limits("apikey.limits", entry.get("limits"), apikey=True)
            except ValueError:
                return DEFAULT_API_KEY_LIFESPAN
            return int(entry["limits"][API_KEY_LIFESPAN])
    return DEFAULT_API_KEY_LIFESPAN

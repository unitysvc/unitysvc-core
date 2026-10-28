"""Enrollment parameters: each channel declares the parameters it reads.

A channel's ``params`` declares them (unitysvc/unitysvc#2726); a listing
declares none. Checks shared by seller validation and backend ingest:

- a channel reads only parameters it declares, and declares ``params`` if it
  reads any;
- every required parameter has a value in ``params.testing_defaults``;
- every required parameter is read by its channel or by document content.

Only the fields the platform renders with an enrollment's ``params`` are
consumers: the channel itself and document content. Metadata such as
``display_name`` or ``description`` is never rendered with ``params``, so a
reference there does not count (unitysvc/unitysvc#2649).
"""

from typing import Any

from jinja2 import Environment, TemplateSyntaxError, nodes

_ENV = Environment()


def referenced_param_names(value: Any) -> set[str] | None:
    """Names read from ``params`` in Jinja, or None for dynamic/whole-map use.

    Literal strings/comments are not references. Dictionary iteration and
    dynamic lookups conservatively count as consuming all parameters.
    """
    names: set[str] = set()
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return names
        tree = _ENV.parse(value)
        parents = {id(child): parent for parent in tree.find_all(nodes.Node) for child in parent.iter_child_nodes()}
        for node in tree.find_all(nodes.Name):
            if node.name != "params" or node.ctx != "load":
                continue
            parent = parents.get(id(node))
            if isinstance(parent, nodes.Getitem) and isinstance(parent.arg, nodes.Const):
                if isinstance(parent.arg.value, str):
                    names.add(parent.arg.value)
                    continue
            if isinstance(parent, nodes.Getattr):
                call = parents.get(id(parent))
                if isinstance(call, nodes.Call) and call.node is parent:
                    if parent.attr == "get" and call.args and isinstance(call.args[0], nodes.Const):
                        if isinstance(call.args[0].value, str):
                            names.add(call.args[0].value)
                            continue
                    return None
                names.add(parent.attr)
                continue
            return None
        return names
    items = value.values() if isinstance(value, dict) else value if isinstance(value, (list, tuple)) else ()
    for item in items:
        found = referenced_param_names(item)
        if found is None:
            return None
        names |= found
    return names


def unused_required_params(parameters_schema: dict[str, Any] | None, *sources: Any) -> list[str]:
    """Required names that no source consumes (schema declarations are not sources)."""
    required = (parameters_schema or {}).get("required") or []
    if not required:
        return []
    used: set[str] = set()
    for source in sources:
        names = referenced_param_names(source)
        if names is None:
            return []
        used |= names
    return [name for name in required if name not in used]


# Keys of a document entry that hold its rendered content. Backend ingest sees
# a ``.j2`` document's template source inlined as ``file_content``; the seller
# validator inlines the same file before calling this module.
_DOCUMENT_CONTENT_KEYS = ("file_content", "content")


def _document_contents(documents: Any) -> list[str]:
    entries = documents.values() if isinstance(documents, dict) else documents if isinstance(documents, list) else ()
    out: list[str] = []
    for doc in entries:
        if isinstance(doc, dict):
            out.extend(doc[key] for key in _DOCUMENT_CONTENT_KEYS if isinstance(doc.get(key), str))
    return out


# A channel declares its own enrollment parameters in one object (unitysvc/unitysvc#2726):
#   "params": {"schema": {...}, "ui_schema": {...}, "testing_defaults": {...}}
CHANNEL_PARAMS_KEY = "params"
CHANNEL_PARAMS_FIELDS = frozenset({"schema", "ui_schema", "testing_defaults"})


def _channels(offering: dict[str, Any]) -> dict[str, dict[str, Any]]:
    config = offering.get("upstream_access_config")
    if not isinstance(config, dict):
        return {}
    return {name: channel for name, channel in config.items() if isinstance(channel, dict)}


def _channel_body(channel: dict[str, Any]) -> dict[str, Any]:
    """The channel config the gateway renders: its ``params`` declaration is not a consumer."""
    return {key: value for key, value in channel.items() if key != CHANNEL_PARAMS_KEY}


def channel_parameter_schemas(offering: dict[str, Any]) -> dict[str, Any]:
    """``channel name -> params.schema`` for the channels that declare parameters."""
    schemas: dict[str, Any] = {}
    for name, channel in _channels(offering).items():
        params = channel.get(CHANNEL_PARAMS_KEY)
        if params is not None:
            schemas[name] = params.get("schema") if isinstance(params, dict) else params
    return schemas


def validate_channel_parameters(offering: dict[str, Any]) -> list[str]:
    """Errors in the parameters an offering's channels declare (unitysvc/unitysvc#2726).

    A channel declares ``params``: ``schema`` (a JSON Schema of the enrollment
    parameters), an optional ``ui_schema`` (form hints) and ``testing_defaults``
    (the values the platform tests the channel with). A channel that reads
    ``{{ params.* }}`` must declare them. Offering-only rules;
    :func:`validate_required_parameter_usage` adds the consumer check, which
    needs the listing's documents too.
    """
    channels = _channels(offering)
    errors: list[str] = []
    for name, channel in channels.items():
        where = f"upstream_access_config.{name}"
        try:
            used = referenced_param_names(_channel_body(channel))
        except TemplateSyntaxError as exc:
            errors.append(f"{where}: invalid Jinja template: {exc}")
            continue
        params = channel.get(CHANNEL_PARAMS_KEY)
        if params is None:
            if used is None or used:
                errors.append(
                    f"{where} reads enrollment parameters but declares no params; "
                    "each channel declares the parameters it uses"
                )
            continue
        at = f"{where}.{CHANNEL_PARAMS_KEY}"
        if not isinstance(params, dict):
            errors.append(f'{at} must be {{"schema": ..., "ui_schema": ..., "testing_defaults": ...}}')
            continue
        if extra := sorted(set(params) - CHANNEL_PARAMS_FIELDS):
            errors.append(f"{at}: unknown keys {extra} (allowed: schema, ui_schema, testing_defaults)")
        schema = params.get("schema")
        if not isinstance(schema, dict):
            errors.append(f"{at}.schema must be a JSON Schema object")
            continue
        ui_schema = params.get("ui_schema")
        if ui_schema is not None and not isinstance(ui_schema, dict):
            errors.append(f"{at}.ui_schema must be an object")
        raw_properties = schema.get("properties")
        properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
        required = [r for r in schema.get("required") or [] if isinstance(r, str)]
        if used is not None and (undeclared := sorted(used - set(properties) - set(required))):
            errors.append(f"{where} reads parameter(s) {', '.join(undeclared)} that {at}.schema does not declare")
        defaults = params.get("testing_defaults")
        if defaults is not None and not isinstance(defaults, dict):
            errors.append(f"{at}.testing_defaults must be an object")
            continue
        if missing := [r for r in required if r not in (defaults or {})]:
            errors.append(
                f"{where}: required parameter(s) {', '.join(missing)} need a value in "
                f"{at}.testing_defaults, used to test the channel"
            )
    return errors


def validate_required_parameter_usage(listing: dict[str, Any], offering: dict[str, Any]) -> list[str]:
    """Return hard validation errors, shared by seller validation and backend ingest.

    Parameters are declared on channels only. Each channel's required
    parameters must be read by that channel or by document content; another
    channel's use does not count. Deprecated content can still be retired.
    """
    if listing.get("status") == "deprecated" or offering.get("status") == "deprecated":
        return []
    if listing.get("user_parameters_schema") or listing.get("parameters_schema"):
        return [
            "Enrollment parameters are declared on the listing (user_parameters_schema); declare them in "
            "the params of the channel that reads them instead (unitysvc/unitysvc#2726)."
        ]
    return _unused_channel_parameters(listing, offering, channel_parameter_schemas(offering))


def _unused_channel_parameters(
    listing: dict[str, Any], offering: dict[str, Any], channel_schemas: dict[str, Any]
) -> list[str]:
    """Each channel's required parameters must be read by that channel or by
    document content; another channel's use does not count."""
    channels = _channels(offering)
    documents = [_document_contents(listing.get("documents")), _document_contents(offering.get("documents"))]
    errors: list[str] = []
    for name, schema in sorted(channel_schemas.items()):
        if not isinstance(schema, dict):
            continue  # reported by validate_channel_parameters
        try:
            unused = unused_required_params(schema, _channel_body(channels[name]), *documents)
        except TemplateSyntaxError as exc:
            errors.append(f"Cannot validate required parameter usage: invalid Jinja template: {exc}")
            continue
        if unused:
            errors.append(
                f"upstream_access_config.{name}: required parameter(s) {', '.join(unused)} are used by "
                "neither the channel nor document content. Reference each one as {{ params.NAME }}, "
                "or drop it from the required list."
            )
    return errors

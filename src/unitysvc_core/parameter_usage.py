"""Shared cross-file validation of required enrollment parameter usage.

This checks that required parameters have consumers. It does not prove that
an intended enrollment channel consumes them; interface/document use counts.
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


def validate_required_parameter_usage(listing: dict[str, Any], offering: dict[str, Any]) -> list[str]:
    """Return hard validation errors, shared by seller validation and backend ingest.

    Deprecated content can still be retired. Referencing a parameter in a
    document satisfies usage without guaranteeing correct channel attribution.
    """
    if listing.get("status") == "deprecated" or offering.get("status") == "deprecated":
        return []
    schema = listing.get("user_parameters_schema") or listing.get("parameters_schema")
    content = {k: v for k, v in listing.items() if k not in ("user_parameters_schema", "parameters_schema")}
    try:
        unused = unused_required_params(schema, content, offering)
    except TemplateSyntaxError as exc:
        return [f"Cannot validate required parameter usage: invalid Jinja template: {exc}"]
    if not unused:
        return []
    return [
        f"Required user parameter(s) {', '.join(unused)} are used by no channel, interface or document. "
        "Reference each one as {{ params.NAME }} (for example ${ customer_secrets.{{ params.NAME }} }), "
        "or drop it from the required list."
    ]

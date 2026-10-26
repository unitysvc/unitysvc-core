"""A service's ``exports``, and channel ``customer_access_requests`` checked against them (unitysvc/unitysvc#2720)."""

from __future__ import annotations

from typing import Any

import pytest

from unitysvc_core.grants import (
    DEFAULT_EXPORTS,
    check_claim,
    parse_exports,
    parse_options,
    validate_access_requests,
    validate_channel_access_requests,
    validate_limits,
)

_WHERE = "upstream_access_config.plus.customer_access_requests"


# A stand-in catalog: each service's ``exports`` as its own definition declares it.
def _options(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


_CATALOG: dict[str, dict[str, Any]] = {
    "me": {
        "permissions": {"read": ["GET /profile/*"], "write": ["PUT /profile/*"]},
        "options": _options(profile={"type": "array", "items": {"type": "string", "not": {"const": "*"}}}),
    },
    "needs": {"permissions": {"read": ["GET /needs/*"], "write": ["POST /needs", "PUT /needs/*"]}},
    "notify": {"options": _options(max_size={"type": "integer", "minimum": 1, "maximum": 10000})},
    "log": {
        "options": _options(
            log_level={"type": "string", "enum": ["debug", "info", "warn", "error"], "default": "info"},
            include_attributes={"type": "boolean"},
        ),
        "options_ui_schema": {"log_level": {"ui:widget": "radio"}},
    },
    "ai": {"options": _options(model={"type": "string", "minLength": 1})},
    "bill": {"options": _options(max_charge={"type": "number", "exclusiveMinimum": 0})},
    "resp200": {},  # declares nothing: invoke only
}


def _resolve(name: str) -> dict[str, Any] | None:
    return _CATALOG.get(name)


def _dep(obj: str = "service:log", **fields: Any) -> dict[str, Any]:
    return {
        "object": obj,
        "permissions": ["invoke"],
        "justification": "Records each check you run",
        **fields,
    }


def _check(*entries: dict[str, Any]) -> None:
    validate_access_requests(_WHERE, list(entries), _resolve)


def _never_resolve(name: str) -> dict[str, Any] | None:
    raise AssertionError(f"looked up {name}")


# A service's own exports -------------------------------------------------------


@pytest.mark.parametrize("declared", [None, {}])
def test_declaring_nothing_exports_invoke_only(declared: Any) -> None:
    exports = parse_exports("exports", declared)
    assert exports == DEFAULT_EXPORTS
    assert exports.permission_names == ("invoke",)
    assert exports.options == {}


def test_declared_permissions_are_exactly_what_is_exported() -> None:
    """A data service declares read/write and gets no implicit invoke."""
    exports = parse_exports("exports", _CATALOG["me"])
    assert exports.permission_names == ("read", "write")
    assert exports.permissions["read"] == ("GET /profile/*",)


def test_options_alone_keep_the_implicit_invoke() -> None:
    exports = parse_exports("exports", _CATALOG["log"])
    assert exports.permission_names == ("invoke",)
    assert exports.permissions["invoke"] == ("* /*",)
    assert exports.option_names == ("log_level", "include_attributes")


def test_invoke_may_be_declared_beside_other_permissions() -> None:
    exports = parse_exports("exports", {"permissions": {"invoke": ["POST /checks"], "admin": ["DELETE /checks/*"]}})
    assert exports.permission_names == ("invoke", "admin")
    assert exports.permissions["invoke"] == ("POST /checks",)


@pytest.mark.parametrize("name", sorted(_CATALOG))
def test_each_catalog_export_parses(name: str) -> None:
    parse_exports("exports", _CATALOG[name])


@pytest.mark.parametrize(
    ("declared", "error"),
    [
        ([], "must be an object"),
        ({"scope": "x"}, r"unknown keys \['scope'\]"),
        ({"permissions": ["read"]}, "permissions must be"),
        ({"permissions": {}}, "at least one permission"),
        ({"permissions": {"Read": ["GET /x"]}}, "permission name"),
        ({"permissions": {"read-all": ["GET /x"]}}, "permission name"),
        ({"permissions": {"read": "GET /x"}}, "list of routes"),
        ({"permissions": {"read": []}}, "list of routes"),
        ({"permissions": {"read": ["/records"]}}, "list of routes"),
        ({"permissions": {"read": ["FETCH /records"]}}, "list of routes"),
        ({"permissions": {"read": ["GET records"]}}, "list of routes"),
        ({"permissions": {"read": ["GET /a", "GET /a"]}}, "list of routes"),
        ({"options": []}, "JSON Schema"),
        ({"options": {"max_size": {"type": "integer"}}}, "JSON Schema"),
        ({"options": {"type": "object", "properties": {}, "required": ["x"]}}, "no 'required'"),
        ({"options": {"type": "object", "properties": {}, "additionalProperties": True}}, "additionalProperties"),
        ({"options": _options(**{"Log-Level": {"type": "string"}})}, "query argument"),
        ({"options": _options(nested={"type": "object"})}, "type must be one of"),
        ({"options": _options(untyped={"enum": ["a"]})}, "must be one type"),
        ({"options": _options(ids={"type": "array", "items": {"type": "integer"}})}, "list of strings"),
        ({"options": _options(level={"type": "string", "enum": "info"})}, "not a valid JSON Schema"),
        ({"options_ui_schema": []}, "options_ui_schema must be an object"),
        ({"options": _options(level={"type": ["string", "null"]})}, "must be one type"),
        ({"options": _options(level={"$ref": "https://example.com/s.json"})}, r"'\$ref' is not allowed"),
        ({"options": _options(level={"type": "string", "allOf": [{"$ref": "#/x"}]})}, r"'\$ref' is not allowed"),
    ],
)
def test_malformed_exports_are_refused(declared: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        parse_exports("exports", declared)


# Options as a query string -----------------------------------------------------------


def test_options_parse_from_a_query_string() -> None:
    assert parse_options("o", "log_level=warn&max_size=2000") == {"log_level": "warn", "max_size": "2000"}
    assert parse_options("o", "") == {}
    assert parse_options("o", "fields=contact.email%2Clocation") == {"fields": "contact.email,location"}


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"log_level": "warn"}, "must be a query string"),
        ("log_level", "must be a query string"),
        ("log_level=", "must be a query string"),
        ("Log-Level=warn", "not an option name"),
        ("log_level=warn&log_level=info", "set twice"),
    ],
)
def test_malformed_options_are_refused(options: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        parse_options("o", options)


# Limits ---------------------------------------------------------------------------


def test_valid_limits() -> None:
    validate_limits("l", {"amount_per_day": 2.5, "amount_per_month": "2.50", "amount_total": 10})
    validate_limits(
        "l",
        {
            "count_per_day": 2,
            "count_per_month": 30,
            "amount_per_month": "2.00",
            "amount_total": 10,
            "count_per_hour": 1,
        },
    )
    validate_limits("l", {})


@pytest.mark.parametrize(
    ("limits", "error"),
    [
        ([{"count_per_day": 2}], "must be an object"),
        ({"calls_per_day": 2}, "count_/amount_"),
        ({"count_per_week": 2}, "count_/amount_"),
        ({"count_per_day": 0}, "positive integer"),
        ({"count_per_day": "2"}, "positive integer"),
        ({"count_per_day": True}, "positive integer"),
        ({"amount_per_day": "-1"}, "positive amount"),
        ({"amount_per_day": "abc"}, "positive amount"),
        ({"amount_per_day": float("inf")}, "positive amount"),
        ({"amount_per_day": float("nan")}, "positive amount"),
        ({"amount_per_day": -2.5}, "positive amount"),
    ],
)
def test_malformed_limits_are_refused(limits: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_limits("l", limits)


# Requests checked against the target's exports -----------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        _dep(),
        _dep(required=True),
        _dep(options="log_level=warn"),
        _dep(options="log_level=error&include_attributes=false"),
        _dep(limits={"count_per_day": 1000}),
        _dep("service:notify", options="max_size=2000", limits={"count_per_day": 100}),
        _dep("service:ai", options="model=labs%2Fllm-fast"),
        _dep("service:bill", options="max_charge=1.50", limits={"amount_per_month": "2.00"}),
        _dep("service:me", permissions=["read"], options="profile=contact.email,location"),
        _dep("service:needs", permissions=["read", "write"]),
        _dep("service:resp200"),
    ],
    ids=lambda d: f"{d['object']}-{d.get('options', '')}",
)
def test_a_valid_request_is_accepted(entry: dict[str, Any]) -> None:
    _check(entry)


def test_an_empty_list_requests_nothing() -> None:
    _check()


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        ("service:log", "must be a list"),
        (["service:log"], r'must be \{"object"'),
        ([_dep("service:*")], r"'service:\*' cannot be requested"),
        ([_dep("api:*")], r"'api:\*' cannot be requested"),
        ([_dep("enrollment:ABCD1234")], r"'enrollment:ABCD1234' cannot be requested"),
        ([_dep("*")], r"'\*' cannot be requested"),
        ([_dep("service:")], "'service:' cannot be requested"),
        ([_dep("service:unknown-svc", permissions=["read"])], "service:unknown-svc is not a published service"),
        ([_dep(), _dep()], "listed twice"),
        ([_dep(scope="x")], r"unknown keys \['scope'\]"),
        ([{**_dep(), "permissions": None}], "permissions must be"),
        ([_dep(permissions=[])], "permissions must be"),
        ([_dep(permissions=["invoke", "invoke"])], "permissions must be"),
        ([_dep(permissions=["read"])], r"exports only \['invoke'\]"),
        ([_dep("service:resp200", permissions=["write"])], r"exports only \['invoke'\]"),
        ([_dep("service:me")], r"exports only \['read', 'write'\]"),
        ([_dep("service:needs", permissions=["invoke", "read"])], r"exports only \['read', 'write'\]"),
        ([_dep("service:unknown-svc")], "service:unknown-svc is not a published service"),
        ([{k: v for k, v in _dep().items() if k != "justification"}], "justification"),
        ([_dep(justification="  ")], "justification"),
        ([_dep(required="yes")], "required must be"),
        ([_dep(options={"log_level": "warn"})], "must be a query string"),
        ([_dep(options="cap=1")], "accepts no option 'cap'"),
        ([_dep("service:resp200", options="x=1")], "none"),
        ([_dep(options="log_level=trace")], "log_level 'trace' is not one of"),
        ([_dep(options="include_attributes=yes")], "include_attributes 'yes' is not of type 'boolean'"),
        ([_dep("service:notify", options="max_size=0")], "max_size 0 is less than the minimum of 1"),
        ([_dep("service:notify", options="max_size=big")], "max_size 'big' is not of type 'integer'"),
        ([_dep("service:notify", options="max_size=99999")], "greater than the maximum"),
        ([_dep("service:bill", options="max_charge=-1")], "max_charge -1.0 is less than or equal"),
        ([_dep("service:bill", options="max_charge=nan")], "max_charge 'nan' is not of type 'number'"),
        ([_dep("service:me", permissions=["read"], options="profile=a,*")], "profile.1"),
        ([_dep(limits={"calls_per_day": 5})], "count_/amount_"),
    ],
)
def test_an_invalid_request_is_refused(entries: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_access_requests(_WHERE, entries, _resolve)


def test_an_invoke_claim_is_looked_up() -> None:
    """A service that declares permissions may not export invoke, so even the
    default claim is checked against the target's exports."""
    with pytest.raises(AssertionError, match="looked up anything"):
        validate_access_requests(_WHERE, [_dep("service:anything")], _never_resolve)


def test_limits_alone_need_no_option_schema() -> None:
    """Limits are universal: a target that exports no options still takes them."""
    _check(_dep("service:resp200", limits={"count_per_month": 2}))


# Without a catalog only the shape is checked --------------------------------------


def test_without_a_resolver_an_unpublished_target_passes() -> None:
    validate_access_requests(_WHERE, [_dep("service:not-yet-published", options="anything=1")])


def test_without_a_resolver_the_shape_is_still_checked() -> None:
    with pytest.raises(ValueError, match="justification"):
        validate_access_requests(_WHERE, [_dep(justification="")])


# Every channel of an upstream_access_config -----------------------------------------


def test_several_channels_may_each_declare_requests() -> None:
    """Each channel is its own plan (unitysvc/unitysvc#2726)."""
    validate_channel_access_requests(
        {
            "byo-ai": {"customer_access_requests": [_dep("service:ai"), _dep()]},
            "managed": {"customer_access_requests": [_dep()]},
        },
        _resolve,
    )


def test_each_channel_s_requests_are_checked() -> None:
    with pytest.raises(
        ValueError, match=r"upstream_access_config\.me\.customer_access_requests\[0\]: 'service:\*' cannot"
    ):
        validate_channel_access_requests(
            {"api": {"base_url": "https://u"}, "me": {"customer_access_requests": [_dep("service:*")]}}
        )


def test_a_config_without_requests_passes() -> None:
    validate_channel_access_requests(
        {"api": {"base_url": "https://u"}, "plus": {"customer_access_requests": []}}, _resolve
    )


def test_validation_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote reference in a target's exports is refused, never fetched."""
    import socket

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("validation opened a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    remote = {"options": _options(level={"type": "string", "$ref": "http://127.0.0.1:9/schema.json"})}
    with pytest.raises(ValueError, match=r"'\$ref' is not allowed"):
        validate_access_requests(_WHERE, [_dep("service:remote", options="level=x")], lambda name: remote)


# A key's policy entry, checked with the same rule as a request -------------------


def test_check_claim_accepts_a_covered_claim() -> None:
    me = parse_exports("exports", _CATALOG["me"])
    check_claim("policy[0]", "service:me", ["read", "write"], parse_options("o", "profile=research,address.state"), me)


@pytest.mark.parametrize(
    ("permissions", "options", "error"),
    [
        (["invoke"], "", r"exports only \['read', 'write'\]"),
        (["read"], "cap=1", "accepts no option 'cap'"),
        (["read"], "profile=a,*", "profile.1"),
    ],
)
def test_check_claim_refuses_what_exports_do_not_cover(permissions: list[str], options: str, error: str) -> None:
    me = parse_exports("exports", _CATALOG["me"])
    with pytest.raises(ValueError, match=error):
        check_claim("policy[0]", "service:me", permissions, parse_options("o", options), me)

"""A service's ``exports``, and channel ``customer_access_requests`` checked against them (unitysvc/unitysvc#2720)."""

from __future__ import annotations

from typing import Any

import pytest

from unitysvc_core.grants import (
    DEFAULT_API_KEY_LIFESPAN,
    DEFAULT_EXPORTS,
    api_key_lifespan,
    build_policy,
    check_claim,
    match_request,
    options_text,
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
    # Stands in for a resource with kinds (unitysvc/unitysvc#2830).
    "rooms": {
        "permissions": {"create": ["POST /rooms"]},
        "options": _options(
            kind={"type": "string", "enum": ["chat", "feed"]},
            max_members={"type": "integer", "minimum": 1},
            topic={"type": "string"},
        ),
    },
}


def _resolve(name: str) -> dict[str, Any] | None:
    return _CATALOG.get(name)


def _dep(obj: str = "service:log", **fields: Any) -> dict[str, Any]:
    return {
        "object": obj,
        "permissions": ["invoke"],
        "justification": "Records each check you run",
        "required": False,
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
        ({"options": _options(**{"Log-Level": {"type": "string"}})}, "option name"),
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


# The apikey request: how long a leased key lives (unitysvc/unitysvc#2766) ----------


def _lifespan(seconds: Any = 14400, **fields: Any) -> dict[str, Any]:
    return {
        "object": "apikey",
        "limits": {"api_key_lifespan": seconds},
        "justification": "Batch runs take up to four hours",
        "required": False,
        **fields,
    }


def test_a_key_lifespan_may_be_requested_without_a_lookup() -> None:
    """The apikey entry names no service, so it needs no catalog."""
    validate_access_requests(_WHERE, [_lifespan(86400)], _never_resolve)
    _check(_lifespan(1), _dep())


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        ({k: v for k, v in _lifespan().items() if k != "justification"}, "justification"),
        (_lifespan(86401), "from 1 to 86400"),
        (_lifespan(0), "from 1 to 86400"),
        (_lifespan("3600"), "from 1 to 86400"),
        (_lifespan(True), "from 1 to 86400"),
        (_lifespan(permissions=["invoke"]), "takes only limits"),
        (_lifespan(required="yes"), "required must be true or false"),
        ({**_lifespan(), "limits": {"api_key_lifespan": 7200, "count_per_day": 2}}, "must be"),
        ({**_lifespan(), "limits": {}}, "must be"),
    ],
)
def test_a_malformed_lifespan_request_is_refused(entry: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        _check(entry)


def test_the_lifespan_is_requested_once_and_only_on_the_apikey_entry() -> None:
    with pytest.raises(ValueError, match="listed twice"):
        _check(_lifespan(), _lifespan(7200))
    with pytest.raises(ValueError, match="count_/amount_"):
        _check(_dep(limits={"api_key_lifespan": 7200}))


# A customer's approvals become a policy ------------------------------------------

_REQUESTS: list[dict[str, Any]] = [
    _dep("service:log", required=True, options="log_level=warn", limits={"count_per_day": 1000}),
    _dep("service:me", permissions=["read", "write"]),
    _lifespan(),
]


_LOG_APPROVAL: dict[str, Any] = {"object": "service:log", "permissions": ["invoke"]}


def _approve(*approvals: dict[str, Any]) -> list[dict[str, Any]]:
    return build_policy(_REQUESTS, list(approvals), _resolve)


def test_approvals_become_a_policy_with_the_fixed_options_and_the_customers_choices() -> None:
    policy = _approve(
        {"object": "service:log", "permissions": ["invoke"], "options": "include_attributes=true"},
        {
            "object": "service:me",
            "permissions": ["write", "read"],
            "options": "profile=address",
            "limits": {"count_per_hour": 10, "amount_total": "5.00"},
        },
        {"object": "apikey", "limits": {"api_key_lifespan": 7200}},
    )
    assert policy == [
        # The request's log_level=warn is fixed: it is in the policy though the approval leaves it out.
        {"object": "service:log", "permissions": ["invoke"], "options": "include_attributes=true&log_level=warn"},
        {
            "object": "service:me",
            "permissions": ["write", "read"],
            "options": "profile=address",
            "limits": {"count_per_hour": 10, "amount_total": "5.00"},
        },
        {"object": "apikey", "limits": {"api_key_lifespan": 7200}},
    ]
    assert api_key_lifespan(policy) == 7200


def test_optional_requests_may_be_declined_and_empty_limits_are_dropped() -> None:
    policy = _approve({"object": "service:log", "permissions": ["invoke"], "options": "", "limits": {}})
    assert policy == [{"object": "service:log", "permissions": ["invoke"], "options": "log_level=warn"}]
    assert api_key_lifespan(policy) == DEFAULT_API_KEY_LIFESPAN


@pytest.mark.parametrize(
    ("approvals", "error"),
    [
        ([], "requires service:log"),
        ([{"object": "service:me", "permissions": ["read", "write"]}], "requires service:log"),
        ([{"object": "service:ai", "permissions": ["invoke"]}], "service:ai is not requested"),
        ([_LOG_APPROVAL, _LOG_APPROVAL], "approved twice"),
        ([{"object": "service:log", "permissions": []}], "requests exactly"),
        ([{"object": "service:log", "permissions": ["invoke", "admin"]}], "requests exactly"),
        ([{"object": "service:log", "permissions": ["invoke"], "options": "log_level=error"}], "fixes it to 'warn'"),
        (
            [{"object": "service:log", "permissions": ["invoke"], "options": "include_attributes=yes"}],
            "include_attributes 'yes'",
        ),
        ([{"object": "service:log", "permissions": ["invoke"], "options": "cap=1"}], "accepts no option 'cap'"),
        ([{"object": "service:log", "permissions": ["invoke"], "limits": {"count_per_week": 1}}], "count_/amount_"),
        ([{"object": "service:log", "permissions": ["invoke"], "required": True}], "unknown keys"),
        (
            [
                {"object": "service:log", "permissions": ["invoke"]},
                {"object": "apikey", "limits": {"api_key_lifespan": 20000}},
            ],
            "asks for at most 14400",
        ),
        (
            [
                {"object": "service:log", "permissions": ["invoke"]},
                {"object": "apikey", "permissions": ["invoke"], "limits": {"api_key_lifespan": 60}},
            ],
            "takes only limits",
        ),
        ("all", "must be a list"),
    ],
)
def test_an_approval_outside_the_requests_or_exports_is_refused(approvals: Any, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        build_policy(_REQUESTS, approvals, _resolve)


# Every included request keeps exactly its permissions (unitysvc/unitysvc#2831) -----


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize(
    "permissions",
    [["read"], ["write"], ["read", "write", "admin"], ["read", "admin"]],
    ids=lambda p: "+".join(p),
)
def test_permissions_cannot_be_removed_added_or_replaced(required: bool, permissions: list[str]) -> None:
    requests = [_dep("service:me", permissions=["read", "write"], required=required)]
    with pytest.raises(ValueError, match=r"requests exactly \['read', 'write'\]"):
        build_policy(requests, [{"object": "service:me", "permissions": permissions}], _resolve)


@pytest.mark.parametrize("required", [True, False])
def test_permissions_may_be_listed_in_any_order(required: bool) -> None:
    requests = [_dep("service:me", permissions=["read", "write"], required=required)]
    policy = build_policy(requests, [{"object": "service:me", "permissions": ["write", "read"]}], _resolve)
    assert policy == [{"object": "service:me", "permissions": ["write", "read"]}]


# A request's options are fixed; options it leaves open are the customer's ---------


def test_an_open_option_is_the_customers_choice() -> None:
    """service:ai with no model fixed: the customer picks one, or none."""
    requests = [_dep("service:ai", required=True)]
    assert build_policy(requests, [{"object": "service:ai", "permissions": ["invoke"]}], _resolve) == [
        {"object": "service:ai", "permissions": ["invoke"]}
    ]
    assert build_policy(
        requests, [{"object": "service:ai", "permissions": ["invoke"], "options": "model=labs/llm-premium"}], _resolve
    ) == [{"object": "service:ai", "permissions": ["invoke"], "options": "model=labs/llm-premium"}]


@pytest.mark.parametrize("required", [True, False])
def test_a_declared_option_is_fixed(required: bool) -> None:
    requests = [_dep("service:ai", required=required, options="model=labs%2Fllm-balanced")]
    expected = [{"object": "service:ai", "permissions": ["invoke"], "options": "model=labs/llm-balanced"}]
    for options in ["", "model=labs/llm-balanced", "model=labs%2Fllm-balanced"]:
        approval = {"object": "service:ai", "permissions": ["invoke"], "options": options}
        assert build_policy(requests, [approval], _resolve) == expected
    with pytest.raises(
        ValueError, match=r"approvals\[0\]\.options\.model: the service fixes it to 'labs/llm-balanced'"
    ):
        build_policy(
            requests,
            [{"object": "service:ai", "permissions": ["invoke"], "options": "model=labs/llm-premium"}],
            _resolve,
        )


def test_an_optional_request_with_fixed_options_may_still_be_declined() -> None:
    assert build_policy([_dep("service:ai", options="model=labs/llm-balanced")], [], _resolve) == []


def test_fixed_values_compare_as_their_type() -> None:
    """02000 is the integer 2000, so repeating it does not change the fixed value."""
    requests = [_dep("service:notify", options="max_size=2000")]
    approval = {"object": "service:notify", "permissions": ["invoke"], "options": "max_size=02000"}
    assert build_policy(requests, [approval], _resolve) == [
        {"object": "service:notify", "permissions": ["invoke"], "options": "max_size=2000"}
    ]
    with pytest.raises(ValueError, match="fixes it to '2000'"):
        build_policy(requests, [{**approval, "options": "max_size=2001"}], _resolve)


def test_without_a_resolver_fixed_values_compare_as_written() -> None:
    requests = [_dep("service:notify", options="max_size=2000")]
    with pytest.raises(ValueError, match="fixes it to '2000'"):
        build_policy(requests, [{"object": "service:notify", "permissions": ["invoke"], "options": "max_size=02000"}])


def test_policy_options_are_one_canonical_query_string() -> None:
    assert options_text({"topic": "a&b", "kind": "chat", "profile": "x,y", "service": "labs/llm"}) == (
        "kind=chat&profile=x,y&service=labs/llm&topic=a%26b"
    )


# Several requests of one object, told apart by a fixed option ---------------------

_ROOMS: list[dict[str, Any]] = [
    _dep("service:rooms", permissions=["create"], required=True, options="kind=chat&max_members=100"),
    _dep("service:rooms", permissions=["create"], options="kind=feed"),
]


def _room(options: str = "", **fields: Any) -> dict[str, Any]:
    return {"object": "service:rooms", "permissions": ["create"], "options": options, **fields}


def test_requests_of_one_object_are_approved_separately() -> None:
    policy = build_policy(_ROOMS, [_room("kind=feed&topic=news"), _room("kind=chat")], _resolve)
    assert policy == [
        {"object": "service:rooms", "permissions": ["create"], "options": "kind=feed&topic=news"},
        {"object": "service:rooms", "permissions": ["create"], "options": "kind=chat&max_members=100"},
    ]
    # A stored entry finds its request again, by its fixed options.
    assert [match_request("policy", _ROOMS, entry) for entry in policy] == [1, 0]


def test_declining_one_kind_leaves_the_other() -> None:
    assert build_policy(_ROOMS, [_room("kind=chat")], _resolve) == [
        {"object": "service:rooms", "permissions": ["create"], "options": "kind=chat&max_members=100"}
    ]


@pytest.mark.parametrize(
    ("approvals", "error"),
    [
        ([_room()], r"service:rooms is requested 2 times; set kind to say which"),
        ([_room("topic=news")], "set kind to say which"),
        ([_room("kind=feed")], r"requires service:rooms \(kind=chat&max_members=100\)"),
        ([_room("kind=chat"), _room("kind=chat&max_members=100")], "approved twice"),
        ([_room("kind=chat&max_members=50")], "no request of service:rooms has these options"),
    ],
)
def test_an_approval_must_say_which_request_it_approves(approvals: list[dict[str, Any]], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        build_policy(_ROOMS, approvals, _resolve)


@pytest.mark.parametrize(
    "second",
    [
        _dep("service:rooms", permissions=["create"], options="kind=chat"),
        _dep("service:rooms", permissions=["create"], options="topic=news"),
        _dep("service:rooms", permissions=["create"]),
        _dep("service:rooms", permissions=["create"], options="kind=chat&max_members=0100"),
    ],
    ids=["same-kind", "other-key", "no-options", "same-typed-value"],
)
def test_requests_of_one_object_must_fix_an_option_differently(second: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="listed twice; requests of one object must each fix an option"):
        _check(_ROOMS[0], second)


# Whether the customer may decline a request ----------------------------------------


def test_every_request_says_whether_it_is_required() -> None:
    unstated = {k: v for k, v in _dep().items() if k != "required"}
    with pytest.raises(ValueError, match=r"\[0\]\.required must be true or false: say whether"):
        _check(unstated)
    lifespan = {k: v for k, v in _lifespan().items() if k != "required"}
    with pytest.raises(ValueError, match=r"\[0\]\.required must be true or false: say whether"):
        _check(lifespan)


def test_a_stored_request_without_required_reads_as_optional() -> None:
    """Declarations stored before the rule still build policies."""
    unstated = {k: v for k, v in _dep().items() if k != "required"}
    assert build_policy([unstated], [], _resolve) == []


def test_a_key_lifespan_may_be_required() -> None:
    requests = [_lifespan(required=True)]
    with pytest.raises(ValueError, match="requires apikey"):
        build_policy(requests, [])
    assert build_policy(requests, [{"object": "apikey", "limits": {"api_key_lifespan": 600}}]) == [
        {"object": "apikey", "limits": {"api_key_lifespan": 600}}
    ]


@pytest.mark.parametrize("permission", ["*", "read*", "Read", "read-all", ""])
def test_a_permission_is_a_plain_name_even_without_a_resolver(permission: str) -> None:
    with pytest.raises(ValueError, match="permission names"):
        validate_access_requests(_WHERE, [_dep(permissions=[permission])])


def test_an_approval_of_an_unpublished_target_is_refused() -> None:
    with pytest.raises(ValueError, match="service:gone is not a published service"):
        build_policy([_dep("service:gone")], [{"object": "service:gone", "permissions": ["invoke"]}], _resolve)


def test_without_a_resolver_only_the_shape_is_checked() -> None:
    policy = build_policy(
        [_dep("service:not-yet-published")],
        [{"object": "service:not-yet-published", "permissions": ["invoke"], "options": "anything=1"}],
    )
    assert policy[0]["options"] == "anything=1"


def test_malformed_requests_build_no_policy() -> None:
    with pytest.raises(ValueError, match="justification"):
        build_policy([_dep(justification="")], [])


@pytest.mark.parametrize(
    "policy",
    [None, [], [{"object": "service:*", "permissions": ["invoke"]}], [{"object": "apikey", "limits": {}}], "x"],
)
def test_a_policy_without_a_valid_lifespan_gets_the_default(policy: Any) -> None:
    assert api_key_lifespan(policy) == DEFAULT_API_KEY_LIFESPAN


# This enrollment's own limits (unitysvc/unitysvc#2770) ---------------------------


def test_the_enrollment_entry_needs_no_request() -> None:
    policy = _approve(_LOG_APPROVAL, {"object": "enrollment", "limits": {"amount_per_day": "1.00"}})
    assert policy[-1] == {"object": "enrollment", "limits": {"amount_per_day": "1.00"}}
    # Any enrollment may carry one, even of a channel that requests nothing.
    assert build_policy([], [{"object": "enrollment", "limits": {"count_total": 5}}]) == [
        {"object": "enrollment", "limits": {"count_total": 5}}
    ]
    assert api_key_lifespan(policy) == DEFAULT_API_KEY_LIFESPAN


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        ({"object": "enrollment"}, "at least one limit"),
        ({"object": "enrollment", "limits": {}}, "at least one limit"),
        ({"object": "enrollment", "limits": {"calls": 1}}, "count_/amount_"),
        ({"object": "enrollment", "permissions": ["invoke"], "limits": {"count_total": 1}}, "takes only limits"),
        ({"object": "enrollment", "options": "a=b", "limits": {"count_total": 1}}, "takes only limits"),
        ({"object": "enrollment", "enrollment": "ABC123", "limits": {"count_total": 1}}, "takes only limits"),
    ],
)
def test_the_enrollment_entry_takes_only_limits(entry: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        _approve(_LOG_APPROVAL, entry)


def test_the_enrollment_entry_appears_once() -> None:
    entry = {"object": "enrollment", "limits": {"count_total": 1}}
    with pytest.raises(ValueError, match="listed twice"):
        _approve(_LOG_APPROVAL, entry, entry)


def test_an_approval_may_select_one_of_the_customers_enrollments() -> None:
    policy = _approve(
        _LOG_APPROVAL,
        {
            "object": "service:me",
            "permissions": ["read", "write"],
            "enrollment": "JYY87Y",
            "limits": {"count_per_day": 3},
        },
    )
    assert policy[-1] == {
        "object": "service:me",
        "permissions": ["read", "write"],
        "enrollment": "JYY87Y",
        "limits": {"count_per_day": 3},
    }


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ({"enrollment": "jyy87y"}, "must be an enrollment code"),
        ({"enrollment": "AB-12"}, "must be an enrollment code"),
        ({"enrollment": "ABCDIO"}, "must be an enrollment code"),
        ({"enrollment": ""}, "must be an enrollment code"),
        ({"enrollment": 123}, "must be an enrollment code"),
        ({"enrollment": "JYY87Y", "options": "profile=address"}, "takes no options"),
    ],
)
def test_a_chain_enrollment_names_a_code_and_takes_no_options(extra: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        _approve(_LOG_APPROVAL, {"object": "service:me", "permissions": ["read", "write"], **extra})


def test_a_chain_enrollment_is_still_an_approval_of_a_request() -> None:
    with pytest.raises(ValueError, match="is not requested"):
        _approve(_LOG_APPROVAL, {"object": "service:notify", "permissions": ["invoke"], "enrollment": "JYY87Y"})
    with pytest.raises(ValueError, match="requests exactly"):
        _approve(_LOG_APPROVAL, {"object": "service:me", "permissions": ["delete"], "enrollment": "JYY87Y"})


def test_a_chain_enrollment_cannot_bypass_fixed_options() -> None:
    """An enrollment carries no options, so it can't carry the fixed ones either."""
    with pytest.raises(ValueError, match=r"fixes options of service:log \(log_level=warn\)"):
        _approve({**_LOG_APPROVAL, "enrollment": "JYY87Y"})


def test_a_request_never_names_an_enrollment() -> None:
    with pytest.raises(ValueError, match="unknown keys"):
        validate_access_requests(_WHERE, [_dep("service:me", enrollment="JYY87Y")])


@pytest.mark.parametrize(
    "entry",
    [
        _dep("service:*"),
        {"object": "service:*", "permissions": ["deny"], "justification": "x"},
        {"object": "service:*", "permissions": ["invoke"], "justification": "x", "limits": {"count_per_day": 1}},
    ],
)
def test_service_wildcard_is_refused_by_rule(entry: dict[str, Any]) -> None:
    """unitysvc/unitysvc#2783: service:* in any form is for manually created keys."""
    with pytest.raises(ValueError, match=r"'service:\*' cannot be requested: every service at once is only for keys"):
        validate_access_requests(_WHERE, [entry])


def test_service_wildcard_is_never_approved() -> None:
    with pytest.raises(ValueError, match=r"'service:\*' cannot be approved: every service at once"):
        build_policy(_REQUESTS, [{"object": "service:*", "permissions": ["invoke"]}], _resolve)


def test_a_seller_cannot_request_the_enrollment_entry() -> None:
    with pytest.raises(ValueError, match="cannot be requested"):
        validate_access_requests(_WHERE, [{"object": "enrollment", "justification": "x", "limits": {"count_total": 1}}])

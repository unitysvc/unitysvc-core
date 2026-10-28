"""User access interfaces are canonical gateway addresses (unitysvc/unitysvc#2726)."""

import pytest

from unitysvc_core.models.validators import validate_listing_interfaces_are_canonical

API = "${API_GATEWAY_BASE_URL}"
SMTP = "${SMTP_GATEWAY_BASE_URL}"
S3 = "${S3_GATEWAY_BASE_URL}"
MCP = "${MCP_GATEWAY_BASE_URL}"


def _check(**interfaces):
    return validate_listing_interfaces_are_canonical(interfaces)


@pytest.mark.parametrize(
    "iface",
    [
        {"base_url": f"{API}/openai", "routing_key": {"model": "gpt-4"}},
        {"base_url": f"{API}/{{{{ service_name }}}}"},
        {"base_url": f"{API}/a/demo"},
        {"base_url": f"{API}/p/llm"},
        {"base_url": API},
        {"base_url": MCP},
        {"base_url": SMTP, "routing_key": {"username": "smtp-relay"}},
        {"base_url": SMTP, "routing_key": {"username": "{{ enrollment.code }}"}},
        {"base_url": SMTP, "routing_key": {"username": "{{enrollment.code}}"}},
        {"base_url": f"{S3}/{{{{ enrollment.code }}}}"},
        {"base_url": f"{S3}/{{{{ enrollment.code }}}}/"},
        {"base_url": f"{S3}/public-bucket"},
    ],
)
def test_a_canonical_interface_is_accepted(iface):
    assert _check(main=iface) == []


@pytest.mark.parametrize(
    ("iface", "field", "hint"),
    [
        ({"base_url": f"{API}/{{{{ enrollment.code }}}}"}, "base_url", "/e/<CODE>"),
        ({"base_url": f"{API}/e/{{{{ enrollment.code }}}}"}, "base_url", "/e/<CODE>"),
        ({"base_url": f"{API}/ntfy/{{{{ enrollment.code }}}}"}, "base_url", "/e/<CODE>"),
        ({"base_url": f"{API}/demo", "routing_key": {"model": "{{ params.model }}"}}, "routing_key.model", "/e/<CODE>"),
        ({"base_url": f"{API}/x/{{{{ enrollment.id }}}}"}, "base_url", "/e/<CODE>"),
        ({"base_url": SMTP, "routing_key": {"username": "{{ params.user }}"}}, "routing_key.username", "exactly"),
        ({"base_url": SMTP, "routing_key": {"username": "x-{{ enrollment.code }}"}}, "routing_key.username", "exactly"),
        ({"base_url": f"{S3}/{{{{ enrollment.code }}}}/{{{{ params.prefix }}}}"}, "base_url", "exactly"),
        ({"base_url": f"{S3}/{{{{ params.bucket }}}}"}, "base_url", "exactly"),
    ],
)
def test_a_per_enrollment_interface_is_refused(iface, field, hint):
    errors = _check(main=iface)
    assert len(errors) == 1
    assert errors[0].startswith(f"user_access_interfaces.main.{field}:")
    assert hint in errors[0]


def test_every_offending_interface_is_reported():
    errors = _check(
        canonical={"base_url": f"{API}/svc"},
        enrollment={"base_url": f"{API}/{{{{ enrollment.code }}}}"},
        param={"base_url": f"{API}/svc", "routing_key": {"model": "{{ params.model }}"}},
    )
    assert [e.split(":")[0] for e in errors] == [
        "user_access_interfaces.enrollment.base_url",
        "user_access_interfaces.param.routing_key.model",
    ]


def test_nothing_to_check():
    assert validate_listing_interfaces_are_canonical(None) == []
    assert validate_listing_interfaces_are_canonical({}) == []


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example.com/v1",
        "${FOO_GATEWAY_BASE_URL}/x",
        "${ secrets.URL }",
        "${ customer_secrets.URL }",
        f"{API}/${{ secrets.PATH }}",
        f"{API}x/svc",
    ],
)
def test_only_the_four_gateways_are_addresses(base_url):
    errors = _check(main={"base_url": base_url})
    assert len(errors) == 1
    assert errors[0].startswith("user_access_interfaces.main.base_url:")
    assert "is not a gateway address" in errors[0]


def test_routing_vars_never_render_into_an_interface():
    """Only service_name is filled in when an interface is stored."""
    errors = _check(main={"base_url": f"{API}/{{{{ routing_vars.region }}}}/chat"})
    assert len(errors) == 1
    assert "the only template a user access interface may use is {{ service_name }}" in errors[0]

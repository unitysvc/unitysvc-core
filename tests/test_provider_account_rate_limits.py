"""Provider-account rate limits (unitysvc/unitysvc#1937).

The only rate limit a seller can state truthfully is the one the provider
grants their account. Per-service limits invert the arithmetic — a 60 RPM
account ceiling authored onto 18 services authorises 1080 RPM — and no seller
can state an individual customer's share, which depends on who is active at
request time.
"""

from typing import get_args

import pytest
from pydantic import ValidationError

from unitysvc_core.models import ProviderAccountRateLimit, ProviderV1
from unitysvc_core.models.base import (
    RateLimitUnitEnum,
    RateLimitUnitLiteral,
    TimeWindowEnum,
    TimeWindowLiteral,
)

BASE = {
    "name": "fireworks",
    "contact_email": "hello@example.com",
    "homepage": "https://example.com",
    "time_created": "2026-01-01T00:00:00Z",
}


def test_concurrency_needs_no_window() -> None:
    limit = ProviderAccountRateLimit(name="fireworks_concurrency", limit=10, unit="concurrent")
    assert limit.name == "fireworks_concurrency"
    assert limit.window is None


def test_name_is_required() -> None:
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(limit=10, unit="concurrent")


def test_concurrency_rejects_a_window() -> None:
    # A gauge has no window. Accepting one would let a seller author a limit
    # the enforcement layer cannot honour as written.
    with pytest.raises(ValidationError, match="takes no window"):
        ProviderAccountRateLimit(name="fireworks_concurrency", limit=10, unit="concurrent", window="minute")


@pytest.mark.parametrize("unit", ["requests", "tokens", "input_tokens", "output_tokens", "bytes"])
def test_counted_units_require_a_window(unit: str) -> None:
    with pytest.raises(ValidationError, match="counted over a window"):
        ProviderAccountRateLimit(name=f"fireworks_{unit}", limit=100, unit=unit)
    assert ProviderAccountRateLimit(name=f"fireworks_{unit}", limit=100, unit=unit, window="minute").window is not None


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(name="fireworks_concurrency", limit=0, unit="concurrent")


def test_unknown_field_is_rejected() -> None:
    # extra="forbid": a typo'd key must not silently become a limit nobody enforces,
    # which is the failure mode of the per-channel `rate_limits` this replaces.
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(name="fireworks_concurrency", limit=10, unit="concurrent", scope="account")


def test_provider_accepts_the_block() -> None:
    provider = ProviderV1(**BASE, rate_limits=[{"name": "fireworks_concurrency", "limit": 10, "unit": "concurrent"}])
    assert provider.rate_limits is not None
    assert provider.rate_limits[0].name == "fireworks_concurrency"
    assert provider.rate_limits[0].limit == 10


def test_provider_without_the_block_is_unchanged() -> None:
    # Omission means "not declared" — the gateway applies nothing, exactly as
    # today. Chosen rather than accidental.
    assert ProviderV1(**BASE).rate_limits is None


def test_several_dimensions_coexist() -> None:
    provider = ProviderV1(
        **BASE,
        rate_limits=[
            {
                "name": "fireworks_concurrency",
                "limit": 10,
                "unit": "concurrent",
                "description": "engine capacity",
            },
            {"name": "fireworks_perminute", "limit": 600, "unit": "requests", "window": "minute"},
            {"name": "fireworks_input_tokens", "limit": 60000, "unit": "input_tokens", "window": "minute"},
        ],
    )
    assert [rl.unit for rl in provider.rate_limits] == [
        "concurrent",
        "requests",
        "input_tokens",
    ]


def test_literal_mirrors_match_their_enums() -> None:
    """The mirrors in base.py must not drift from the enums they stand in for.

    ``unit``/``window`` are typed as Literals rather than the shared enums (see
    RateLimitUnitLiteral in base.py for why). The enums stay the semantic source
    of truth and are still used elsewhere, so a value added to one and not the
    other would silently narrow or widen what a provider may author.
    """
    assert set(get_args(RateLimitUnitLiteral)) == {member.value for member in RateLimitUnitEnum}
    assert set(get_args(TimeWindowLiteral)) == {member.value for member in TimeWindowEnum}


def test_the_nested_model_pulls_in_no_named_subschema() -> None:
    """The condition that broke Python SDK codegen — kept from coming back.

    An enum (or any named sub-schema) one level down makes pydantic treat the
    *outer* model's validation and serialization schemas as distinct. FastAPI
    then splits every model that carries this one into an ``X-Input``/
    ``X-Output`` pair whose halves share a title, and Python SDK codegen drops
    one of them plus everything referencing it — silently. ``ProviderData`` is
    such a carrier: it is a request body on ``POST /seller/services`` and a
    response field on ``GET /seller/services/{id}``.
    """
    assert ProviderAccountRateLimit.model_json_schema().get("$defs", {}) == {}


def test_the_value_set_is_still_closed() -> None:
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(name="fireworks_typo", limit=10, unit="concurent")
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(name="fireworks_rpm", limit=10, unit="requests", window="fortnight")


def test_enum_members_are_still_accepted_as_values() -> None:
    """Callers that pass ``RateLimitUnitEnum.concurrent`` keep working.

    Both enums are StrEnums, so a member validates against the Literal and
    lands as the plain string. Reading the field back gives a ``str``, not a
    member — ``.unit.value`` no longer works, ``.unit`` does.
    """
    limit = ProviderAccountRateLimit(
        name="fireworks_rpm",
        limit=600,
        unit=RateLimitUnitEnum.requests,
        window=TimeWindowEnum.minute,
    )

    assert limit.unit == "requests"
    assert limit.window == "minute"
    assert limit.model_dump(mode="json")["unit"] == "requests"

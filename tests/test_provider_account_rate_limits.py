"""Provider-account rate limits (unitysvc/unitysvc#1937).

The only rate limit a seller can state truthfully is the one the provider
grants their account. Per-service limits invert the arithmetic — a 60 RPM
account ceiling authored onto 18 services authorises 1080 RPM — and no seller
can state an individual customer's share, which depends on who is active at
request time.
"""

import pytest
from pydantic import ValidationError

from unitysvc_core.models import ProviderAccountRateLimit, ProviderV1

BASE = {
    "name": "fireworks",
    "contact_email": "hello@example.com",
    "homepage": "https://example.com",
    "time_created": "2026-01-01T00:00:00Z",
}


def test_concurrency_needs_no_window() -> None:
    limit = ProviderAccountRateLimit(limit=10, unit="concurrent")
    assert limit.window is None


def test_concurrency_rejects_a_window() -> None:
    # A gauge has no window. Accepting one would let a seller author a limit
    # the enforcement layer cannot honour as written.
    with pytest.raises(ValidationError, match="takes no window"):
        ProviderAccountRateLimit(limit=10, unit="concurrent", window="minute")


@pytest.mark.parametrize("unit", ["requests", "tokens", "input_tokens", "output_tokens", "bytes"])
def test_counted_units_require_a_window(unit: str) -> None:
    with pytest.raises(ValidationError, match="counted over a window"):
        ProviderAccountRateLimit(limit=100, unit=unit)
    assert ProviderAccountRateLimit(limit=100, unit=unit, window="minute").window is not None


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(limit=0, unit="concurrent")


def test_unknown_field_is_rejected() -> None:
    # extra="forbid": a typo'd key must not silently become a limit nobody enforces,
    # which is the failure mode of the per-channel `rate_limits` this replaces.
    with pytest.raises(ValidationError):
        ProviderAccountRateLimit(limit=10, unit="concurrent", scope="account")


def test_provider_accepts_the_block() -> None:
    provider = ProviderV1(**BASE, rate_limits=[{"limit": 10, "unit": "concurrent"}])
    assert provider.rate_limits is not None
    assert provider.rate_limits[0].limit == 10


def test_provider_without_the_block_is_unchanged() -> None:
    # Omission means "not declared" — the gateway applies nothing, exactly as
    # today. Chosen rather than accidental.
    assert ProviderV1(**BASE).rate_limits is None


def test_several_dimensions_coexist() -> None:
    provider = ProviderV1(
        **BASE,
        rate_limits=[
            {"limit": 10, "unit": "concurrent", "description": "engine capacity"},
            {"limit": 600, "unit": "requests", "window": "minute"},
            {"limit": 60000, "unit": "input_tokens", "window": "minute"},
        ],
    )
    assert [rl.unit.value for rl in provider.rate_limits] == [
        "concurrent",
        "requests",
        "input_tokens",
    ]

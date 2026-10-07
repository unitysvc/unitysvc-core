"""Tests for ``effective_price()``: the per-unit comparison figure every pricing type defines."""

import typing
from decimal import Decimal
from typing import Any

import pytest

from unitysvc_core.models.pricing import (
    BasePriceData,
    EffectivePrice,
    Pricing,
    upstream_price_from_description,
    validate_pricing,
)


def ep(data: dict[str, Any]) -> EffectivePrice | None:
    return validate_pricing(data).effective_price()


def rate(amount: str, unit: str, source: str = "rate") -> EffectivePrice:
    return EffectivePrice(amount=Decimal(amount), unit=unit, source=source)  # type: ignore[arg-type]


TOKENS = {"type": "one_million_tokens", "input": "1", "output": "2"}  # blend 1.8
CHEAP_TOKENS = {"type": "one_million_tokens", "price": "0.5"}
PER_REQUEST = {"type": "constant", "price": "0.01"}
HF_UPSTREAM = "billed by Hugging Face directly at $1.4 / $4.4 per 1M input/output tokens"
BYOK = {"type": "constant", "price": "0", "description": HF_UPSTREAM}
NO_AMOUNT = {"type": "constant", "price": "0", "description": "Bring your own key"}


def test_every_pricing_type_defines_its_own_effective_price() -> None:
    members = typing.get_args(typing.get_args(Pricing)[0])
    assert len(members) == 18
    for cls in members:
        assert "effective_price" in vars(cls), f"{cls.__name__} does not define effective_price()"
    assert BasePriceData().effective_price() is None


class TestSimpleTypes:
    def test_token_split_is_blended(self) -> None:
        assert ep({"type": "one_million_tokens", "input": "1.4", "output": "4.4"}) == rate("3.8", "one_million_tokens")

    def test_token_split_ignores_cached_input(self) -> None:
        figure = ep({"type": "one_million_tokens", "input": "1", "cached_input": "0.1", "output": "2"})
        assert figure == rate("1.8", "one_million_tokens")

    def test_token_unified(self) -> None:
        assert ep(CHEAP_TOKENS) == rate("0.5", "one_million_tokens")

    @pytest.mark.parametrize(
        ("kind", "price", "per_million"),
        [("one_thousand_tokens", "0.002", "2"), ("one_token", "0.000003", "3")],
    )
    def test_token_scales_normalize_to_per_million(self, kind: str, price: str, per_million: str) -> None:
        assert ep({"type": kind, "price": price}) == rate(per_million, "one_million_tokens")

    def test_count_is_per_request(self) -> None:
        assert ep({"type": "one_thousand", "price": "10"}) == rate("0.01", "request")
        assert ep({"type": "one_million", "price": "2"}) == rate("0.000002", "request")

    @pytest.mark.parametrize(
        ("kind", "price", "per_hour"),
        [
            ("one_second", "0.001", "3.6"),
            ("one_minute", "0.006", "0.36"),
            ("one_hour", "2", "2"),
            ("one_day", "24", "1"),
        ],
    )
    def test_time_normalizes_to_per_hour(self, kind: str, price: str, per_hour: str) -> None:
        assert ep({"type": kind, "price": price}) == rate(per_hour, "one_hour")

    def test_data_normalizes_to_per_gigabyte(self) -> None:
        assert ep({"type": "one_megabyte", "price": "0.001"}) == rate("1.024", "one_gigabyte")
        assert ep({"type": "one_gigabyte", "price": "0.09"}) == rate("0.09", "one_gigabyte")

    def test_characters_normalize_to_per_million(self) -> None:
        assert ep({"type": "one_thousand_characters", "price": "0.015"}) == rate("15", "one_million_characters")

    def test_image_and_step(self) -> None:
        assert ep({"type": "image", "price": "0.04"}) == rate("0.04", "image")
        assert ep({"type": "step", "price": "0.001"}) == rate("0.001", "step")

    def test_constant_is_per_request(self) -> None:
        assert ep(PER_REQUEST) == rate("0.01", "request")

    def test_revenue_share_and_expr_are_unknown(self) -> None:
        assert ep({"type": "revenue_share", "percentage": "70"}) is None
        assert ep({"type": "expr", "expr": "input_tokens / 1000000 * 2.5"}) is None


class TestByokConstant:
    def test_zero_with_upstream_description(self) -> None:
        assert ep(BYOK) == rate("3.8", "one_million_tokens", "upstream")

    def test_zero_without_description_is_free(self) -> None:
        assert ep({"type": "constant", "price": "0"}) == rate("0", "request")

    def test_zero_with_unparseable_description_is_unknown(self) -> None:
        assert ep(NO_AMOUNT) is None

    def test_zero_zero_placeholder_is_unknown(self) -> None:
        text = "billed directly at $0 / $0 per 1M input/output tokens"
        placeholder = {"type": "constant", "price": "0", "description": text}
        assert ep(placeholder) is None

    def test_nonzero_ignores_description(self) -> None:
        priced = {"type": "constant", "price": "0.02", "description": "$1 / $2 per 1M input/output tokens"}
        assert ep(priced) == rate("0.02", "request")


class TestByokZeroRateTokens:
    """Generators also write BYOK as a zero token rate, e.g. Mistral models."""

    MISTRAL = (
        "Free ~ BYOK | usage is billed by Mistral AI directly at $1.5 / $7.5 / $0.15 per 1M input/output/cached tokens"
    )

    def test_zero_split_rate_reads_description(self) -> None:
        byok = {"type": "one_million_tokens", "input": "0", "output": "0", "cached_input": "0"}
        figure = ep({**byok, "description": self.MISTRAL})
        assert figure == rate("6.3", "one_million_tokens", "upstream")

    def test_zero_unified_rate_reads_description(self) -> None:
        figure = ep({"type": "one_thousand_tokens", "price": "0", "description": HF_UPSTREAM})
        assert figure == rate("3.8", "one_million_tokens", "upstream")

    def test_zero_rate_without_description_is_free(self) -> None:
        assert ep({"type": "one_million_tokens", "input": "0", "output": "0"}) == rate("0", "one_million_tokens")

    def test_zero_rate_with_unparseable_description_is_unknown(self) -> None:
        assert ep({"type": "one_million_tokens", "price": "0", "description": "Bring your own key"}) is None

    def test_nonzero_rate_ignores_description(self) -> None:
        priced = {**TOKENS, "description": self.MISTRAL}
        assert ep(priced) == rate("1.8", "one_million_tokens")


class TestUpstreamPriceFromDescription:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("billed by Hugging Face directly at $1.4 / $4.4 per 1M input/output tokens", "3.8"),
            ("$1.518/$4.554 / 1M input/output tokens", "3.9468"),
            ("$4 / $20 / $0.2 per 1M input/output/cached tokens", "16.8"),
            ("$3.45/$0.345/$17.25 / 1M in/cached/out tokens", "14.49"),
            ("$0.50 / 1M tokens", "0.50"),
            ("$0.001 / $0.002 per 1K input/output tokens", "1.8"),
        ],
    )
    def test_known_shapes(self, text: str, expected: str) -> None:
        assert upstream_price_from_description(text) == Decimal(expected)

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "Bring your own OpenAI key",
            "$1 / $2 per 1M input/output/cached tokens",  # three labels, two amounts
            "$1 / $2 per 1M input/cached tokens",  # no output
            "$0.01 per message",  # not tokens
            "$1 / $2 per 1M tokens",  # two amounts, no labels
        ],
    )
    def test_unknown_shapes_fail_closed(self, text: str | None) -> None:
        assert upstream_price_from_description(text) is None


class TestComposites:
    def test_add_sums_one_unit(self) -> None:
        figure = ep({"type": "add", "prices": [PER_REQUEST, {"type": "constant", "price": "0.005"}]})
        assert figure == rate("0.015", "request")

    def test_add_with_mixed_units_is_unknown(self) -> None:
        assert ep({"type": "add", "prices": [TOKENS, PER_REQUEST]}) is None

    def test_add_with_unknown_child_is_unknown(self) -> None:
        assert ep({"type": "add", "prices": [PER_REQUEST, {"type": "revenue_share", "percentage": "10"}]}) is None

    def test_multiply_scales_base(self) -> None:
        assert ep({"type": "multiply", "factor": "0.5", "base": TOKENS}) == rate("0.9", "one_million_tokens")

    def test_multiply_keeps_upstream_source(self) -> None:
        assert ep({"type": "multiply", "factor": "2", "base": BYOK}) == rate("7.6", "one_million_tokens", "upstream")

    def test_max_and_min(self) -> None:
        prices = [TOKENS, CHEAP_TOKENS]
        assert ep({"type": "max", "prices": prices}) == rate("1.8", "one_million_tokens")
        assert ep({"type": "min", "prices": prices}) == rate("0.5", "one_million_tokens")

    def test_mixed_units_compare_within_dominant_unit(self) -> None:
        # Tokens outrank requests, which outrank every other unit.
        prices = [{"type": "one_second", "price": "100"}, PER_REQUEST, CHEAP_TOKENS]
        assert ep({"type": "max", "prices": prices}) == rate("0.5", "one_million_tokens")
        prices = [{"type": "one_second", "price": "100"}, PER_REQUEST]
        assert ep({"type": "max", "prices": prices}) == rate("0.01", "request")

    def test_first_takes_first_known(self) -> None:
        prices = [{"type": "revenue_share", "percentage": "10"}, PER_REQUEST, TOKENS]
        assert ep({"type": "first", "prices": prices}) == rate("0.01", "request")

    def test_invalid_child_is_skipped_not_raised(self) -> None:
        assert ep({"type": "max", "prices": [{"type": "nonsense"}, PER_REQUEST]}) == rate("0.01", "request")


class TestWorstCase:
    def test_tiered_takes_largest_tier_not_first(self) -> None:
        tiered = {
            "type": "tiered",
            "based_on": "request_count",
            "tiers": [
                {"up_to": 1000, "price": CHEAP_TOKENS},
                {"up_to": None, "price": TOKENS},
            ],
        }
        assert ep(tiered) == rate("1.8", "one_million_tokens")

    @pytest.mark.parametrize(
        ("based_on", "unit_prices", "expected"),
        [
            ("request_count", ["0.005", "0.01", "0.008"], rate("0.01", "request")),
            ("input_tokens", ["0.000001", "0.000002"], rate("2", "one_million_tokens")),
            ("one_thousand_tokens", ["0.003"], rate("3", "one_million_tokens")),
            ("one_minute", ["0.01"], rate("0.6", "one_hour")),
            ("bytes_out", ["0.000000001"], rate("1.073741824", "one_gigabyte")),
            ("images_generated", ["0.04"], rate("0.04", "image")),
            ("count", ["0.002"], rate("0.002", "request")),
        ],
    )
    def test_graduated_takes_largest_unit_price(
        self, based_on: str, unit_prices: list[str], expected: EffectivePrice
    ) -> None:
        tiers: list[dict[str, Any]] = [{"up_to": (i + 1) * 1000, "unit_price": p} for i, p in enumerate(unit_prices)]
        tiers[-1]["up_to"] = None
        assert ep({"type": "graduated", "based_on": based_on, "tiers": tiers}) == expected

    @pytest.mark.parametrize("based_on", ["customer_charge", "input_tokens + output_tokens"])
    def test_graduated_without_unit_is_unknown(self, based_on: str) -> None:
        graduated = {"type": "graduated", "based_on": based_on, "tiers": [{"up_to": None, "unit_price": "0.1"}]}
        assert ep(graduated) is None

    def test_channel_takes_largest_not_default(self) -> None:
        channel = {
            "type": "channel",
            "default": "byok",
            "channels": {"byok": {"type": "constant", "price": "0"}, "plus": PER_REQUEST},
        }
        assert ep(channel) == rate("0.01", "request")

    def test_channel_byok_upstream_beats_cheaper_managed(self) -> None:
        channel = {"type": "channel", "default": "managed", "channels": {"managed": TOKENS, "byok": BYOK}}
        assert ep(channel) == rate("3.8", "one_million_tokens", "upstream")

    def test_channel_ignores_unknown_channels(self) -> None:
        channel = {
            "type": "channel",
            "default": "managed",
            "channels": {"managed": TOKENS, "byok": NO_AMOUNT},
        }
        assert ep(channel) == rate("1.8", "one_million_tokens")

    def test_all_unknown_is_unknown(self) -> None:
        channel = {
            "type": "channel",
            "default": "byok",
            "channels": {"byok": NO_AMOUNT},
        }
        assert ep(channel) is None

    def test_nested_composites_resolve_recursively(self) -> None:
        nested = {
            "type": "channel",
            "default": "managed",
            "channels": {
                "managed": {
                    "type": "tiered",
                    "based_on": "request_count",
                    "tiers": [
                        {"up_to": 100, "price": {"type": "multiply", "factor": "2", "base": TOKENS}},
                        {"up_to": None, "price": CHEAP_TOKENS},
                    ],
                },
                "byok": {"type": "constant", "price": "0"},
            },
        }
        assert ep(nested) == rate("3.6", "one_million_tokens")


def test_does_not_read_the_stored_nominal() -> None:
    # The auto-filled composite ``price`` (first tier) is not the effective price.
    tiered = validate_pricing(
        {
            "type": "tiered",
            "based_on": "request_count",
            "tiers": [{"up_to": 10, "price": CHEAP_TOKENS}, {"up_to": None, "price": TOKENS}],
        }
    )
    assert tiered.price == "0.5"
    assert tiered.effective_price() == rate("1.8", "one_million_tokens")


def test_effective_price_serializes_compactly() -> None:
    assert rate("3.8", "one_million_tokens", "upstream").model_dump(mode="json") == {
        "amount": "3.8",
        "unit": "one_million_tokens",
        "source": "upstream",
    }

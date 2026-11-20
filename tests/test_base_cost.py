"""``base_cost``: the platform's cost of a container channel, as a price."""

from __future__ import annotations

import copy
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from unitysvc_core.base_cost import (
    base_cost_positions,
    priced_channel,
    validate_base_cost_list_price,
    validate_base_cost_payout_price,
)
from unitysvc_core.models.pricing import BaseCostPriceData, UsageData, validate_pricing
from unitysvc_core.validator import DataValidator

ROOT = Path(__file__).parent
SCHEMA_DIR = ROOT.parent / "src" / "unitysvc_core" / "schema"
WEIXIN = ROOT / "example_data" / "kadxy" / "services" / "weixin-ilink-gateway"
AT_COST = {"type": "base_cost"}
TIMES_1_2 = {"type": "multiply", "factor": "1.2", "base": {"type": "base_cost"}}
PLUS_A_DOLLAR = {"type": "add", "prices": [{"type": "base_cost"}, {"type": "one_month", "price": "1"}]}


class TestPrice:
    def test_a_leaf_with_no_fields_of_its_own(self) -> None:
        price = validate_pricing(AT_COST)
        assert isinstance(price, BaseCostPriceData)

    @pytest.mark.parametrize("extra", [{"markup": "1.00"}, {"percentage": "10"}, {"factor": "1.2"}])
    def test_unknown_keys_are_refused(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            validate_pricing({**AT_COST, **extra})

    def test_a_written_summary_price_is_accepted_and_is_not_an_input(self) -> None:
        # The platform replaces it, so a stored listing validates again.
        assert validate_pricing({"type": "base_cost", "price": "5"}).type == "base_cost"

    def test_core_cannot_say_what_it_costs(self) -> None:
        price = validate_pricing(AT_COST)
        assert price.effective_price() is None
        with pytest.raises(ValueError, match="resolved by the platform"):
            price.calculate_cost(UsageData(one_minute=60))

    def test_the_platforms_summary_price_is_the_effective_price(self) -> None:
        # Per month, as the platform writes it; compared per hour over core's 30-day month.
        figure = validate_pricing({"type": "base_cost", "price": "7.20"}).effective_price()
        assert figure is not None
        assert (figure.amount, figure.unit) == (Decimal("0.01"), "one_hour")
        # Still not a figure core computes: the platform resolves the cost.
        with pytest.raises(ValueError, match="resolved by the platform"):
            validate_pricing({"type": "base_cost", "price": "7.20"}).calculate_cost(UsageData(one_minute=60))

    def test_a_margin_is_composed_like_any_other_price(self) -> None:
        times = validate_pricing(TIMES_1_2)
        assert times.type == "multiply"
        plus = validate_pricing(PLUS_A_DOLLAR)
        assert plus.type == "add"
        # With the platform's summary on the leaf, the composite's figure follows.
        summarized = validate_pricing({**TIMES_1_2, "base": {"type": "base_cost", "price": "7.20"}})
        figure = summarized.effective_price()
        assert figure is not None and figure.amount == Decimal("0.012")

    def test_a_channel_price_may_be_base_cost(self) -> None:
        price = validate_pricing(
            {
                "type": "channel",
                "default": "base",
                "channels": {"base": AT_COST, "plus": {"type": "one_hour", "price": "0.02"}},
            }
        )
        assert price.type == "channel"  # type: ignore[union-attr]
        assert price.price == "0.02"  # type: ignore[union-attr]


def _service() -> tuple[dict[str, Any], dict[str, Any]]:
    listing = json.loads((WEIXIN / "listing.json").read_text())
    offering = json.loads((WEIXIN / "offering.json").read_text())
    return listing, offering


def _errors(listing: dict[str, Any], offering: dict[str, Any], tmp_path: Path) -> dict[str, list[str]]:
    shutil.copy(WEIXIN / "compose.yaml", tmp_path / "compose.yaml")
    shutil.copy(WEIXIN / "README.md", tmp_path / "README.md")
    return DataValidator(ROOT / "example_data", SCHEMA_DIR).validate_service(
        listing=listing, offering=offering, service_dir=tmp_path
    )


def _base_cost_text(errors: dict[str, list[str]]) -> str:
    return "\n".join(e for kind in ("listing", "offering") for e in errors.get(kind, []) if "base_cost" in e)


class TestValidator:
    def test_the_weixin_example_still_validates_without_it(self, tmp_path: Path) -> None:
        listing, offering = _service()
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    @pytest.mark.parametrize("price", [AT_COST, TIMES_1_2, PLUS_A_DOLLAR])
    def test_a_container_service_may_be_priced_from_its_cost(self, price: dict[str, Any], tmp_path: Path) -> None:
        listing, offering = _service()
        listing["list_price"] = copy.deepcopy(price)
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    @pytest.mark.parametrize(
        "payout",
        [None, {"type": "revenue_share", "percentage": "80"}, {"type": "constant", "price": "0"}],
    )
    def test_the_payout_is_the_sellers_business(self, payout: Any, tmp_path: Path) -> None:
        """At cost the seller earns (P - C) * r = 0 whatever the share; above cost the margin is
        theirs. The platform's developer-seller rule, not this type, says who may not be paid."""
        listing, offering = _service()
        listing["list_price"] = copy.deepcopy(TIMES_1_2)
        if payout is None:
            offering.pop("payout_price", None)
        else:
            offering["payout_price"] = payout
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    def test_only_a_container_offering_has_a_base_cost(self, tmp_path: Path) -> None:
        listing, offering = _service()
        listing["list_price"] = copy.deepcopy(AT_COST)
        offering["upstream_access_config"]["http"] = {
            "access_method": "http",
            "base_url": "https://example.com",
            "api_key": None,
        }
        assert "prices a container channel" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_a_channel_price_may_be_base_cost_beside_a_paid_one(self, tmp_path: Path) -> None:
        listing, offering = _service()
        channel = next(iter(offering["upstream_access_config"]))
        listing["list_price"] = {
            "type": "channel",
            "default": channel,
            "channels": {channel: copy.deepcopy(TIMES_1_2)},
        }
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    def test_a_channel_price_must_name_a_container_channel(self, tmp_path: Path) -> None:
        listing, offering = _service()
        channel = next(iter(offering["upstream_access_config"]))
        listing["list_price"] = {
            "type": "channel",
            "default": "ghost",
            "channels": {"ghost": copy.deepcopy(AT_COST), channel: {"type": "one_hour", "price": "0.05"}},
        }
        assert "only to a container channel" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_never_a_payout_price(self, tmp_path: Path) -> None:
        listing, offering = _service()
        offering["payout_price"] = copy.deepcopy(TIMES_1_2)
        assert "list_price type" in _base_cost_text(_errors(listing, offering, tmp_path))


class TestRules:
    """The rule functions backend ingest shares with the validator (``unitysvc_core.base_cost``)."""

    def test_where_the_type_sits_and_which_channel_it_prices(self) -> None:
        assert base_cost_positions(AT_COST) == [()]
        assert base_cost_positions(TIMES_1_2) == [("base",)]
        assert base_cost_positions(PLUS_A_DOLLAR) == [("prices", "0")]
        channel = {
            "type": "channel",
            "default": "a",
            "channels": {"a": TIMES_1_2, "b": {"type": "constant", "price": "1"}},
        }
        [position] = base_cost_positions(channel)
        assert position == ("channels", "a", "base")
        assert priced_channel(position) == "a"
        assert priced_channel(("base",)) is None and priced_channel(()) is None
        assert base_cost_positions({"type": "one_hour", "price": "1"}) == []
        assert base_cost_positions(None) == []

    def test_a_listing_alone_has_nothing_to_check(self) -> None:
        assert validate_base_cost_list_price({"list_price": PLUS_A_DOLLAR}, None) == []

    def test_with_the_offering_each_leaf_must_price_a_container_channel(self) -> None:
        container = {"upstream_access_config": {"base": {"access_method": "container", "resources": "nano"}}}
        proxy = {"upstream_access_config": {"http": {"access_method": "http", "base_url": "https://x"}}}
        for price in (AT_COST, TIMES_1_2, PLUS_A_DOLLAR):
            assert validate_base_cost_list_price({"list_price": price}, container) == []
            [error] = validate_base_cost_list_price({"list_price": price}, proxy)
            assert "container channel" in error
        keyed = {"type": "channel", "default": "http", "channels": {"http": TIMES_1_2}}
        [error] = validate_base_cost_list_price({"list_price": keyed}, proxy)
        assert error.startswith("list_price.channels.http.base:")

    def test_a_payout_price_is_never_base_cost(self) -> None:
        assert "list_price type" in validate_base_cost_payout_price({"payout_price": TIMES_1_2})[0]
        assert validate_base_cost_payout_price({"payout_price": {"type": "constant", "price": "0"}}) == []
        assert validate_base_cost_payout_price({}) == []


def test_the_bundled_schemas_know_the_type() -> None:
    for name in ("listing_v1", "offering_v1"):
        assert "BaseCostPriceData" in (SCHEMA_DIR / f"{name}.json").read_text()

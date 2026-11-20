"""``base_cost``: the list price of a container service offered at the platform's cost."""

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
    is_zero_payout,
    validate_base_cost_list_price,
    validate_base_cost_payout_price,
)
from unitysvc_core.models.pricing import BaseCostPriceData, UsageData, validate_pricing
from unitysvc_core.validator import DataValidator

ROOT = Path(__file__).parent
SCHEMA_DIR = ROOT.parent / "src" / "unitysvc_core" / "schema"
WEIXIN = ROOT / "example_data" / "kadxy" / "services" / "weixin-ilink-gateway"
ZERO_SHARE = {"type": "revenue_share", "percentage": "0"}


class TestPrice:
    def test_defaults_to_at_cost(self) -> None:
        price = validate_pricing({"type": "base_cost"})
        assert isinstance(price, BaseCostPriceData)
        assert price.markup == "0"

    def test_a_fixed_markup(self) -> None:
        assert validate_pricing({"type": "base_cost", "markup": "1.50"}).markup == "1.50"  # type: ignore[union-attr]

    @pytest.mark.parametrize("markup", ["-0.01", 1.5, "abc"])
    def test_the_markup_is_a_non_negative_decimal_string(self, markup: Any) -> None:
        with pytest.raises(ValidationError):
            validate_pricing({"type": "base_cost", "markup": markup})

    def test_unknown_keys_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_pricing({"type": "base_cost", "percentage": "10"})

    def test_a_written_summary_price_is_accepted_and_is_not_an_input(self) -> None:
        # The platform replaces it, so a stored listing validates again.
        assert validate_pricing({"type": "base_cost", "price": "5"}).type == "base_cost"

    def test_core_cannot_say_what_it_costs(self) -> None:
        price = validate_pricing({"type": "base_cost"})
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

    def test_a_channel_price_may_be_base_cost(self) -> None:
        price = validate_pricing(
            {
                "type": "channel",
                "default": "base",
                "channels": {"base": {"type": "base_cost"}, "plus": {"type": "one_hour", "price": "0.02"}},
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


def _at_cost(listing: dict[str, Any], offering: dict[str, Any]) -> None:
    listing["list_price"] = {"type": "base_cost"}
    offering["payout_price"] = copy.deepcopy(ZERO_SHARE)


class TestValidator:
    def test_the_weixin_example_still_validates_without_it(self, tmp_path: Path) -> None:
        listing, offering = _service()
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    def test_at_cost_with_a_zero_share(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    def test_a_zero_constant_payout_is_also_nothing(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        offering["payout_price"] = {"type": "constant", "price": "0"}
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    def test_no_payout_at_all_is_fine(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        offering.pop("payout_price", None)
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    @pytest.mark.parametrize(
        "payout",
        [
            {"type": "revenue_share", "percentage": "80"},
            {"type": "constant", "price": "0.01"},
            {"type": "one_hour", "price": "0.01"},
        ],
    )
    def test_a_payout_means_the_author_takes_payment(self, payout: dict[str, Any], tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        offering["payout_price"] = payout
        assert "takes no payment" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_a_markup_stays_with_the_platform(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        listing["list_price"] = {"type": "base_cost", "markup": "2.00"}
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""
        offering["payout_price"] = {"type": "revenue_share", "percentage": "50"}
        assert "takes no payment" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_only_a_container_offering_has_a_base_cost(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        offering["upstream_access_config"]["http"] = {
            "access_method": "http",
            "base_url": "https://example.com",
            "api_key": None,
        }
        assert "needs a container service" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_a_channel_price_may_be_base_cost_beside_a_paid_one(self, tmp_path: Path) -> None:
        listing, offering = _service()
        channel = next(iter(offering["upstream_access_config"]))
        listing["list_price"] = {
            "type": "channel",
            "default": channel,
            "channels": {channel: {"type": "base_cost"}},
        }
        offering["payout_price"] = copy.deepcopy(ZERO_SHARE)
        assert _base_cost_text(_errors(listing, offering, tmp_path)) == ""

    def test_a_channel_price_must_name_a_container_channel(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        channel = next(iter(offering["upstream_access_config"]))
        listing["list_price"] = {
            "type": "channel",
            "default": "ghost",
            "channels": {"ghost": {"type": "base_cost"}, channel: {"type": "one_hour", "price": "0.05"}},
        }
        assert "only to a container channel" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_not_a_part_of_a_composite_price(self, tmp_path: Path) -> None:
        listing, offering = _service()
        _at_cost(listing, offering)
        listing["list_price"] = {
            "type": "add",
            "prices": [{"type": "base_cost"}, {"type": "constant", "price": "1"}],
        }
        assert "whole list_price or the price of one channel" in _base_cost_text(_errors(listing, offering, tmp_path))

    def test_never_a_payout_price(self, tmp_path: Path) -> None:
        listing, offering = _service()
        offering["payout_price"] = {"type": "base_cost"}
        assert "list_price type" in _base_cost_text(_errors(listing, offering, tmp_path))


class TestRules:
    """The rule functions backend ingest shares with the validator (``unitysvc_core.base_cost``)."""

    def test_where_the_type_sits(self) -> None:
        assert base_cost_positions({"type": "base_cost"}) == [()]
        channel = {
            "type": "channel",
            "default": "a",
            "channels": {"a": {"type": "base_cost"}, "b": {"type": "constant", "price": "1"}},
        }
        assert base_cost_positions(channel) == [("channels", "a")]
        composite = {"type": "add", "prices": [{"type": "constant", "price": "1"}, {"type": "base_cost"}]}
        assert base_cost_positions(composite) == [("prices", "1")]
        assert base_cost_positions({"type": "one_hour", "price": "1"}) == []
        assert base_cost_positions(None) == []

    def test_what_pays_the_author_nothing(self) -> None:
        assert is_zero_payout(ZERO_SHARE)
        assert is_zero_payout({"type": "constant", "price": "0.00"})
        assert not is_zero_payout({"type": "revenue_share", "percentage": "80"})
        assert not is_zero_payout({"type": "one_hour", "price": "0"})
        assert not is_zero_payout(None)
        keyed = {
            "type": "channel",
            "default": "a",
            "channels": {"a": ZERO_SHARE, "b": {"type": "constant", "price": "1"}},
        }
        assert is_zero_payout(keyed, "a")
        assert is_zero_payout(keyed, "ghost")  # falls back to the default
        assert not is_zero_payout(keyed, "b")

    def test_a_listing_alone_is_checked_for_shape_only(self) -> None:
        assert validate_base_cost_list_price({"list_price": {"type": "base_cost"}}, None) == []
        composite = {
            "list_price": {"type": "multiply", "prices": [{"type": "base_cost"}, {"type": "constant", "price": "2"}]}
        }
        assert any("composite" in e for e in validate_base_cost_list_price(composite, None))
        assert validate_base_cost_list_price({"list_price": {"type": "one_hour", "price": "1"}}, None) == []

    def test_with_the_offering_the_channel_and_the_payout_are_checked(self) -> None:
        listing = {"list_price": {"type": "base_cost"}}
        container = {"upstream_access_config": {"base": {"access_method": "container", "resources": "nano"}}}
        assert validate_base_cost_list_price(listing, {**container, "payout_price": ZERO_SHARE}) == []
        assert validate_base_cost_list_price(listing, container) == []  # no payout at all
        errors = validate_base_cost_list_price(
            listing, {**container, "payout_price": {"type": "revenue_share", "percentage": "10"}}
        )
        assert any("takes no payment" in e for e in errors)
        proxy = {"upstream_access_config": {"http": {"access_method": "http", "base_url": "https://x"}}}
        assert any("container service" in e for e in validate_base_cost_list_price(listing, proxy))

    def test_a_payout_price_is_never_base_cost(self) -> None:
        assert "list_price type" in validate_base_cost_payout_price({"payout_price": {"type": "base_cost"}})[0]
        assert validate_base_cost_payout_price({"payout_price": ZERO_SHARE}) == []
        assert validate_base_cost_payout_price({}) == []


def test_the_bundled_schemas_know_the_type() -> None:
    for name in ("listing_v1", "offering_v1"):
        assert "BaseCostPriceData" in (SCHEMA_DIR / f"{name}.json").read_text()

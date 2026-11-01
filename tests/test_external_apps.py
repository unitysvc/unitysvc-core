"""External apps and product links (unitysvc/unitysvc#2873).

An offering may declare no channel: it is then an external app, reached at its
own homepage, which ``details.links.homepage`` must give. ``details.links`` is
validated for every offering that declares it, and ``external`` is no longer a
channel type.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from unitysvc_core.models.channel import UpstreamAccessConfig
from unitysvc_core.models.offering_v1 import OfferingV1
from unitysvc_core.models.validators import validate_details_links
from unitysvc_core.utils import load_data_file
from unitysvc_core.validator import DataValidator

SCHEMA_DIR = Path(__file__).parents[1] / "src" / "unitysvc_core" / "schema"
EXAMPLES = Path(__file__).parent / "example_data"
SERVICE1 = EXAMPLES / "provider1" / "services" / "service1"
HOMEPAGE = {"homepage": "https://app.example"}


def _offering(**changes: Any) -> dict[str, Any]:
    data, _ = load_data_file(next(SERVICE1.glob("offering.*")))
    assert data is not None
    data = copy.deepcopy(data)
    data.update(changes)
    return data


def _external(**changes: Any) -> dict[str, Any]:
    data = _offering(details={"links": dict(HOMEPAGE)}, **changes)
    data.pop("upstream_access_config", None)
    data.pop("service_type", None)
    return data


@pytest.fixture
def validator() -> DataValidator:
    return DataValidator(EXAMPLES, SCHEMA_DIR)


class TestLinks:
    def test_links_are_optional_with_a_channel(self) -> None:
        assert validate_details_links({}, channel_less=False) == []

    def test_labels_are_free_and_homepage_is_required_when_links_exist(self) -> None:
        links = {"homepage": "https://a.example", "Status page": "https://status.a.example"}
        assert validate_details_links({"links": links}, channel_less=False) == []

        errors = validate_details_links({"links": {"docs": "https://a.example/docs"}}, channel_less=False)
        assert any("must include 'homepage'" in e for e in errors)

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("http://a.example", "https:// URL"),
            ("mailto:help@a.example", "https:// URL"),
            ("https://", "https:// URL"),
            ("https://app.example:abc", "https:// URL"),
            ("https://app.example:99999", "https:// URL"),
            ("https://bad host.example", "https:// URL"),
            ("https://[broken", "https:// URL"),
            ("https://[::1", "https:// URL"),
            ("https://user:pw@a.example", "embed credentials"),
            ("", "nonempty"),
        ],
    )
    def test_a_malformed_link_is_rejected(self, url: str, message: str) -> None:
        errors = validate_details_links({"links": {"homepage": url}}, channel_less=False)
        assert any(message in e for e in errors), errors

    def test_labels_are_short_and_links_few(self) -> None:
        long_label = {**HOMEPAGE, "x" * 41: "https://a.example/x"}
        assert any("longer than 40" in e for e in validate_details_links({"links": long_label}, channel_less=False))

        many = {**HOMEPAGE, **{f"page {i}": f"https://a.example/{i}" for i in range(10)}}
        assert any("at most 10 links" in e for e in validate_details_links({"links": many}, channel_less=False))

    def test_an_empty_label_is_rejected(self) -> None:
        errors = validate_details_links({"links": {**HOMEPAGE, " ": "https://a.example/x"}}, channel_less=False)
        assert any("nonempty string" in e for e in errors)

    def test_other_details_stay_free_form(self) -> None:
        details = {"links": dict(HOMEPAGE), "context_length": 8192, "anything": {"goes": True}}
        assert validate_details_links(details, channel_less=True) == []


class TestOffering:
    def test_an_omitted_or_empty_config_is_an_external_app(self) -> None:
        omitted = OfferingV1.model_validate(_external())
        empty = OfferingV1.model_validate(_external(upstream_access_config={}))

        assert omitted.upstream_access_config.root == {}
        assert empty.upstream_access_config.root == {}

    def test_an_external_app_needs_its_homepage(self) -> None:
        data = _external()
        data["details"] = {}
        with pytest.raises(ValueError, match="external app"):
            OfferingV1.model_validate(data)

    def test_a_service_with_a_channel_rejects_malformed_links(self) -> None:
        with pytest.raises(ValueError, match="https:// URL"):
            OfferingV1.model_validate(_offering(details={"links": {"homepage": "http://a.example"}}))

    def test_external_is_no_longer_a_channel_type(self) -> None:
        with pytest.raises(ValueError):
            UpstreamAccessConfig.model_validate({"site": {"type": "external", "base_url": "https://a.example"}})


class TestValidator:
    def test_an_external_app_validates_without_a_connectivity_test(self, validator: DataValidator) -> None:
        listing, _ = load_data_file(next(SERVICE1.glob("listing.*")))
        assert listing is not None
        listing = copy.deepcopy(listing)
        listing.pop("user_access_interfaces", None)
        listing.pop("list_price", None)
        listing.pop("documents", None)

        result = validator.validate_service(offering=_external(), listing=listing, service_dir=SERVICE1)

        assert result["offering"] == []
        assert not any("connectivity_test" in e for e in result["listing"]), result["listing"]

    def test_an_external_app_has_no_interfaces_and_no_price(self, validator: DataValidator) -> None:
        listing, _ = load_data_file(next(SERVICE1.glob("listing.*")))
        assert listing is not None
        listing = copy.deepcopy(listing)
        listing["user_access_interfaces"] = {
            "canonical": {"access_method": "http", "base_url": "${API_GATEWAY_BASE_URL}/x"}
        }
        listing["list_price"] = {"type": "constant", "price": "1"}

        errors = validator.validate_service(offering=_external(), listing=listing, service_dir=SERVICE1)["listing"]

        assert any("remove user_access_interfaces" in e for e in errors)
        assert any("remove list_price" in e for e in errors)

    def test_a_service_with_a_channel_still_needs_a_connectivity_test(self, validator: DataValidator) -> None:
        listing, _ = load_data_file(next(SERVICE1.glob("listing.*")))
        assert listing is not None
        listing = copy.deepcopy(listing)
        listing.pop("documents", None)

        errors = validator.validate_service(offering=_offering(), listing=listing, service_dir=SERVICE1)["listing"]

        assert any("connectivity_test" in e for e in errors)

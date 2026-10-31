"""Validation tests for shared service-offering data."""

import pytest
from pydantic import ValidationError

from unitysvc_core.models import ServiceOfferingData


def test_service_offering_requires_description() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ServiceOfferingData(name="missing-description", summary="No description")

    assert exc_info.value.errors()[0]["loc"] == ("description",)
    assert exc_info.value.errors()[0]["type"] == "missing"


def test_service_type_is_optional() -> None:
    """A service that fits no category leaves it out; there is no default."""
    offering = ServiceOfferingData(name="commons", summary="A room", description="A room for a group")
    assert offering.service_type is None


@pytest.mark.parametrize("service_type", ["communication", "buy_and_sell"])
def test_application_service_types(service_type: str) -> None:
    offering = ServiceOfferingData(name="app", summary="An app", description="An app", service_type=service_type)
    assert offering.service_type == service_type

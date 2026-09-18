"""Validation tests for shared service-offering data."""

import pytest
from pydantic import ValidationError

from unitysvc_core.models import ServiceOfferingData


def test_service_offering_requires_description() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ServiceOfferingData(name="missing-description", summary="No description")

    assert exc_info.value.errors()[0]["loc"] == ("description",)
    assert exc_info.value.errors()[0]["type"] == "missing"

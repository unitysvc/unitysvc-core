"""A listing's ``audience``: short plain-text phrases shown before enrollment (unitysvc/unitysvc#2717)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from unitysvc_core.models.listing_data import ServiceListingData


def test_audience_is_optional() -> None:
    assert ServiceListingData().audience is None


def test_phrases_are_kept_and_trimmed() -> None:
    listing = ServiceListingData(
        audience=[
            "  Rice University students (enrollment at Rice required) ",
            "US customers only / export restrictions may apply",
        ]
    )
    assert listing.audience == [
        "Rice University students (enrollment at Rice required)",
        "US customers only / export restrictions may apply",
    ]


def test_absent_audience_leaves_the_dump_unchanged() -> None:
    """``None`` is dropped by ``exclude_none``, so content ids of existing listings stay the same."""
    assert "audience" not in ServiceListingData(name="x").model_dump(exclude_none=True)


@pytest.mark.parametrize(
    ("audience", "error"),
    [
        (["a"] * 9, "at most 8 phrases"),
        ([""], "1-120 characters"),
        (["   "], "1-120 characters"),
        (["x" * 121], "1-120 characters"),
        (["see https://example.com"], "without URLs or markup"),
        (["see www.example.com"], "without URLs or markup"),
        (["<b>members</b> only"], "without URLs or markup"),
        (["[members](x) only"], "without URLs or markup"),
        (["`members` only"], "without URLs or markup"),
        (["two\nlines"], "without URLs or markup"),
        (["Members only", " Members only "], "lists a phrase twice"),
        ("US only", "valid list"),
    ],
)
def test_malformed_audience_is_refused(audience: object, error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        ServiceListingData(audience=audience)

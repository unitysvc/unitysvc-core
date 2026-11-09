"""DocumentData validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from unitysvc_core.models import DocumentData, ListingV1


def _doc(meta: dict | None) -> dict:
    return {"category": "connectivity_test", "mime_type": "bash", "file_path": "connectivity.sh.j2", "meta": meta}


def test_meta_output_contains_is_refused() -> None:
    with pytest.raises(ValidationError, match="'output_contains' is no longer supported"):
        DocumentData.model_validate(_doc({"channels": ["gateway"], "output_contains": "connectivity ok"}))


@pytest.mark.parametrize("meta", [None, {}, {"channels": ["gateway"], "expected_charge": {"amount": "0.001"}}])
def test_other_meta_is_accepted(meta: dict | None) -> None:
    assert DocumentData.model_validate(_doc(meta)).meta == meta


def test_listing_validation_reports_the_retired_key() -> None:
    """``specs validate`` runs the listing model, so a listing document with the
    key fails there rather than only at the backend's upload."""
    with pytest.raises(ValidationError, match="output_contains"):
        ListingV1.model_validate(
            {
                "schema": "listing_v1",
                "name": "labs/example",
                "documents": {"Connectivity test": _doc({"output_contains": "ok"})},
            }
        )

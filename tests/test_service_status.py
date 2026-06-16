"""Tests for the ServiceStatus model (the service.json provenance structure)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from unitysvc_core.models import ServiceStatus

_SID = "2c1f9e7a-0000-0000-0000-000000000000"


def test_empty_service_data_has_no_identity() -> None:
    sd = ServiceStatus()
    assert sd.service_id is None
    assert sd.time_created is None


def test_service_id_coerced_to_uuid() -> None:
    sd = ServiceStatus.model_validate({"service_id": _SID})
    assert sd.service_id == UUID(_SID)


def test_time_created_and_extra_fields() -> None:
    # time_created is a recognized provenance field; unknown extras are
    # tolerated (extra="ignore") rather than rejected.
    sd = ServiceStatus.model_validate({"service_id": _SID, "time_created": "2026-01-01T00:00:00", "note": "x"})
    assert sd.time_created == datetime(2026, 1, 1, 0, 0)
    assert "note" not in sd.model_dump()


def test_full_identity_record_round_trips() -> None:
    # The shape the ingest task returns and the seller persists to service.json.
    rev = "11111111-0000-0000-0000-000000000000"
    sd = ServiceStatus.model_validate(
        {
            "service_id": _SID,
            "revision_of": rev,
            "status": "revision_created",
            "name": "acme/widget",
            "display_name": "ACME Widget",
            "time_created": "2026-01-01T00:00:00",
        }
    )
    assert sd.service_id == UUID(_SID)
    assert sd.revision_of == UUID(rev)
    assert sd.status == "revision_created"
    assert sd.name == "acme/widget"
    assert sd.display_name == "ACME Widget"

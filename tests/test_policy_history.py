"""A policy keeps its own history of limits (unitysvc/unitysvc#3034)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from unitysvc_core.grants import build_policy
from unitysvc_core.policy_history import (
    active_policy,
    is_retired,
    policy_at,
    save_policy,
    validate_history,
)

T1 = datetime(2026, 10, 1, tzinfo=UTC)
T2 = datetime(2026, 11, 1, tzinfo=UTC)
T3 = datetime(2026, 12, 1, tzinfo=UTC)
BILL = {"object": "service:bill", "permissions": ["invoke"]}


def _bill(**limits: Any) -> dict[str, Any]:
    return {**BILL, "limits": limits}


def _enrollment(**limits: Any) -> dict[str, Any]:
    return {"object": "enrollment", "limits": limits}


def _first_save(edited: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return save_policy([], edited, T1)


class TestFirstSave:
    def test_new_limits_start_now_and_grants_without_limits_are_untouched(self) -> None:
        stored = _first_save([_bill(amount_total="5.00"), {"object": "service:me", "permissions": ["read"]}])
        assert stored == [
            {**BILL, "limits": {"amount_total": "5.00"}, "since": T1.isoformat()},
            {"object": "service:me", "permissions": ["read"]},
        ]

    def test_limits_with_one_start_share_an_entry(self) -> None:
        [entry] = _first_save([_bill(amount_total="5.00", count_per_day=10)])
        assert entry["limits"] == {"amount_total": "5.00", "count_per_day": 10}

    def test_the_key_lifespan_has_no_history(self) -> None:
        stored = _first_save([{"object": "apikey", "limits": {"api_key_lifespan": 3600}}])
        assert stored == [{"object": "apikey", "limits": {"api_key_lifespan": 3600}}]


class TestEditAndSave:
    def test_an_unchanged_save_changes_nothing(self) -> None:
        stored = _first_save([_bill(amount_total="5.00")])
        assert save_policy(stored, active_policy(stored), T2) == stored

    def test_a_changed_total_is_retired_and_restarts_the_allowance(self) -> None:
        """Allow $5, the seller charges $5, the customer allows another payment at $3."""
        stored = _first_save([_bill(amount_total="5.00")])
        saved = save_policy(stored, [_bill(amount_total="3.00")], T2)
        assert saved == [
            {
                "object": "service:bill",
                "limits": {"amount_total": "5.00"},
                "since": T1.isoformat(),
                "expired_at": T2.isoformat(),
            },
            {**BILL, "limits": {"amount_total": "3.00"}, "since": T2.isoformat()},
        ]

    def test_a_changed_windowed_value_keeps_its_start(self) -> None:
        stored = _first_save([_bill(amount_per_day="10")])
        saved = save_policy(stored, [_bill(amount_per_day="5")], T2)
        active = active_policy(saved)
        assert active == [{**BILL, "limits": {"amount_per_day": "5"}, "since": T1.isoformat()}]
        [old] = [e for e in saved if is_retired(e)]
        assert old["limits"] == {"amount_per_day": "10"} and old["since"] == T1.isoformat()

    def test_an_explicit_regrant_restarts_a_windowed_limit_with_the_same_value(self) -> None:
        stored = _first_save([_bill(amount_per_day="10")])
        saved = save_policy(stored, active_policy(stored), T2, regrant={("service:bill", "amount_per_day")})
        assert active_policy(saved)[0]["since"] == T2.isoformat()
        assert len([e for e in saved if is_retired(e)]) == 1

    def test_editing_one_limit_never_restarts_another(self) -> None:
        stored = _first_save([_bill(amount_per_month="100", count_per_day=5)])
        saved = save_policy(stored, [_bill(amount_per_month="100", count_per_day=9)], T2)
        active = active_policy(saved)
        # The unchanged monthly limit keeps its start; the daily one keeps its start too (windowed).
        assert {e["since"] for e in active} == {T1.isoformat()}
        assert [e["limits"] for e in active] == [{"amount_per_month": "100", "count_per_day": 9}]

    def test_a_total_edit_beside_an_unchanged_limit_splits_the_entry(self) -> None:
        stored = _first_save([_bill(amount_per_month="100", amount_total="5.00")])
        saved = save_policy(stored, [_bill(amount_per_month="100", amount_total="3.00")], T2)
        active = active_policy(saved)
        assert active == [
            {**BILL, "limits": {"amount_per_month": "100"}, "since": T1.isoformat()},
            {"object": "service:bill", "limits": {"amount_total": "3.00"}, "since": T2.isoformat()},
        ]
        # The caller's edit of the active policy round-trips: limit-only entries stay with their grant.
        assert save_policy(saved, active_policy(saved), T3) == saved

    def test_a_removed_limit_is_retired(self) -> None:
        stored = _first_save([_bill(amount_total="5.00", count_total=2)])
        saved = save_policy(stored, [_bill(amount_total="5.00")], T2)
        [old] = [e for e in saved if is_retired(e)]
        assert old["limits"] == {"count_total": 2}
        assert active_policy(saved) == [{**BILL, "limits": {"amount_total": "5.00"}, "since": T1.isoformat()}]

    def test_a_removed_grant_retires_its_limits(self) -> None:
        stored = _first_save([_bill(amount_total="5.00"), _enrollment(amount_per_month="20")])
        saved = save_policy(stored, [_enrollment(amount_per_month="20")], T2)
        [old] = [e for e in saved if is_retired(e)]
        assert old["object"] == "service:bill" and "permissions" not in old
        assert [e["object"] for e in active_policy(saved)] == ["enrollment"]

    def test_history_is_kept_through_later_saves(self) -> None:
        stored = _first_save([_bill(amount_total="5.00")])
        stored = save_policy(stored, [_bill(amount_total="3.00")], T2)
        stored = save_policy(stored, [_bill(amount_total="2.00")], T3)
        assert [e["limits"]["amount_total"] for e in stored if is_retired(e)] == ["5.00", "3.00"]
        assert active_policy(stored)[0]["limits"] == {"amount_total": "2.00"}
        validate_history(stored)

    def test_decimal_spellings_of_one_value_are_equal(self) -> None:
        stored = _first_save([_bill(amount_total="5.00")])
        assert save_policy(stored, [_bill(amount_total=5)], T2) == stored

    def test_a_legacy_limit_without_a_start_stays_without_one_until_it_changes(self) -> None:
        legacy = [_bill(amount_per_day="10")]
        assert save_policy(legacy, [_bill(amount_per_day="10")], T2) == legacy
        saved = save_policy(legacy, [_bill(amount_total="5")], T2)
        [old] = [e for e in saved if is_retired(e)]
        assert "since" not in old and old["limits"] == {"amount_per_day": "10"}


class TestOnlyTheServerWritesTime:
    def test_a_caller_cannot_expire_an_entry(self) -> None:
        stored = _first_save([_bill(amount_total="5.00")])
        edited = [{**_bill(amount_total="5.00"), "expired_at": T2.isoformat()}]
        with pytest.raises(ValueError, match="only the server retires"):
            save_policy(stored, edited, T2)

    def test_a_caller_cannot_backdate_or_move_a_start(self) -> None:
        stored = _first_save([_bill(amount_total="5.00")])
        with pytest.raises(ValueError, match="set by the server"):
            save_policy(stored, [{**_bill(amount_total="5.00"), "since": "2026-01-01T00:00:00+00:00"}], T2)

    def test_the_stored_start_may_be_echoed(self) -> None:
        stored = _first_save([_bill(amount_total="5.00")])
        assert save_policy(stored, active_policy(stored), T2) == stored

    def test_a_caller_cannot_start_a_new_limit_in_the_past(self) -> None:
        with pytest.raises(ValueError, match="set by the server"):
            save_policy([], [{**_bill(amount_total="5.00"), "since": T1.isoformat()}], T2)

    def test_an_approval_cannot_carry_a_start(self) -> None:
        requests = [
            {"object": "service:log", "permissions": ["write"], "required": True, "justification": "Keeps your log"}
        ]
        with pytest.raises(ValueError, match="unknown keys"):
            build_policy(
                requests,
                [{"object": "service:log", "permissions": ["write"], "limits": {"count_per_day": 1}, "since": "x"}],
            )


class TestReading:
    def test_active_policy_drops_the_history_and_copies(self) -> None:
        stored = save_policy(_first_save([_bill(amount_total="5.00")]), [_bill(amount_total="3.00")], T2)
        active = active_policy(stored)
        assert len(stored) == 2 and len(active) == 1
        active[0]["limits"]["amount_total"] = "99"
        assert stored[1]["limits"]["amount_total"] == "3.00"

    def test_a_policy_that_is_not_a_list_has_no_active_entries(self) -> None:
        assert active_policy(None) == [] and active_policy("x") == []

    def test_the_version_in_force_at_a_time(self) -> None:
        stored = save_policy(_first_save([_bill(amount_total="5.00")]), [_bill(amount_total="3.00")], T2)
        before = datetime(2026, 10, 15, tzinfo=UTC)
        after = datetime(2026, 11, 15, tzinfo=UTC)
        assert [e["limits"] for e in policy_at(stored, before)] == [{"amount_total": "5.00"}]
        assert [e["limits"] for e in policy_at(stored, after)] == [{"amount_total": "3.00"}]
        # The boundary belongs to the new version.
        assert [e["limits"] for e in policy_at(stored, T2)] == [{"amount_total": "3.00"}]


class TestValidateHistory:
    def test_a_saved_policy_is_valid(self) -> None:
        validate_history(save_policy(_first_save([_bill(amount_total="5.00")]), [_bill(amount_total="3.00")], T2))

    @pytest.mark.parametrize(
        "entry,message",
        [
            (
                {
                    "object": "service:bill",
                    "permissions": ["invoke"],
                    "limits": {"count_total": 1},
                    "expired_at": "2026-11-01T00:00:00+00:00",
                },
                "holds only",
            ),
            ({"object": "service:bill", "limits": {"count_total": 1}, "expired_at": "yesterday"}, "ISO 8601"),
            (
                {
                    "object": "service:bill",
                    "limits": {"count_total": 1},
                    "since": "2026-11-01T00:00:00",
                    "expired_at": "2026-11-02T00:00:00+00:00",
                },
                "ISO 8601",
            ),
            (
                {
                    "object": "service:bill",
                    "limits": {"count_total": 1},
                    "since": "2026-11-02T00:00:00+00:00",
                    "expired_at": "2026-11-01T00:00:00+00:00",
                },
                "before since",
            ),
            ({"object": "service:bill", "limits": {}, "expired_at": "2026-11-01T00:00:00+00:00"}, "records the limits"),
        ],
    )
    def test_a_malformed_history_is_refused(self, entry: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            validate_history([entry])

"""A policy keeps its own history of limits (unitysvc/unitysvc#3034).

A limit that changes is never overwritten: the old version is *retired in place* and
the new one appended, so nothing about a limit is ever lost and no separate table or
audit log is needed. The history lives in the policy, in two entry keys only the
server writes:

* ``since``: when the limit's allowance started (an ISO 8601 UTC time). An active
  entry holds limits that share one ``since``; a limit's own start time is what
  separates one allowance from the next. A total limit gets a new ``since`` whenever
  the customer grants it again; a windowed limit keeps its ``since`` when only its
  value changes. A limit with no ``since`` (a policy written before this) started when
  its enrollment was created.
* ``expired_at``: when the entry was retired. A retired entry holds only ``object``,
  ``limits`` and ``since``: it grants nothing, so a reader that forgets to skip it can
  never read it as a grant.

::

    [
      {"object": "enrollment", "limits": {"amount_per_month": "20"}, "since": T1, "expired_at": T2},
      {"object": "enrollment", "limits": {"amount_per_month": "30"}, "since": T1},
      {"object": "service:bill", "permissions": ["invoke"], "limits": {"amount_total": "5.00"}, "since": T1}
    ]

*Edit shows the active policy, save keeps the history.* A caller edits
:func:`active_policy` and :func:`save_policy` merges the result into the stored policy:
it retires what was removed or changed, appends what is new, stamps ``since`` and
``expired_at`` from the time it is given, and keeps every retired entry untouched. A
caller never sees or sends retired entries, and a ``since`` or ``expired_at`` it
supplies that the server did not write is refused.

Each active entry keeps one ``since``. When the limits of a grant do not all share one,
the rest are written as *limit-only* entries: ``{"object", "limits", "since"}`` with no
``permissions``, which add limits to the same object and grant nothing.

Core does no I/O: every function takes the time it needs.
"""

from __future__ import annotations

from collections.abc import Collection
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .grants import APIKEY_OBJECT, ENROLLMENT_KEY, options_text, parse_options, validate_limits

SINCE_KEY = "since"
EXPIRED_KEY = "expired_at"
# What a retired entry may hold.
_RETIRED_KEYS = frozenset({"object", "limits", SINCE_KEY, EXPIRED_KEY})
# What identifies a grant: its object, the enrollment it selects, its permissions and options.
_GRANT = tuple[str, str, tuple[str, ...], str]


def is_retired(entry: Any) -> bool:
    """Whether ``entry`` is history (it has ``expired_at``)."""
    return isinstance(entry, dict) and EXPIRED_KEY in entry


def active_policy(policy: Any) -> list[dict[str, Any]]:
    """The entries of ``policy`` in force, copied: what an edit shows and what a leased
    key and the gateway carry. ``policy`` that is not a list has none."""
    if not isinstance(policy, list):
        return []
    return [deepcopy(entry) for entry in policy if isinstance(entry, dict) and not is_retired(entry)]


def policy_at(policy: Any, at: datetime) -> list[dict[str, Any]]:
    """The entries of ``policy`` in force at ``at``, retired ones included: those whose
    ``since`` is not after ``at`` and whose ``expired_at`` is after it. This is how a
    charge decided at ``at`` is explained after the policy has changed. A missing
    ``since`` counts as the beginning of time."""
    when = _utc(at)
    found: list[dict[str, Any]] = []
    for entry in policy if isinstance(policy, list) else []:
        if not isinstance(entry, dict):
            continue
        started = _parse(entry.get(SINCE_KEY)) if SINCE_KEY in entry else None
        ended = _parse(entry.get(EXPIRED_KEY)) if EXPIRED_KEY in entry else None
        if (started is None or started <= when) and (ended is None or when < ended):
            found.append(deepcopy(entry))
    return found


def validate_history(policy: Any) -> None:
    """Refuse a stored policy whose history is malformed: a retired entry that holds
    anything but ``object``, ``limits``, ``since`` and ``expired_at``, a timestamp that
    is not ISO 8601 with a zone, or an entry retired before it started. Raises
    ``ValueError``."""
    if not isinstance(policy, list):
        raise ValueError("policy must be a list")
    for index, entry in enumerate(policy):
        at = f"policy[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{at} must be an object")
        for key in (SINCE_KEY, EXPIRED_KEY):
            if key in entry and _parse(entry[key]) is None:
                raise ValueError(f"{at}.{key} must be an ISO 8601 time with a zone")
        if is_retired(entry):
            if extra := set(entry) - _RETIRED_KEYS:
                raise ValueError(f"{at}: a retired entry holds only object, limits and since, not {sorted(extra)}")
            if not entry.get("limits"):
                raise ValueError(f"{at}.limits: a retired entry records the limits it retired")
            started = _parse(entry.get(SINCE_KEY)) if SINCE_KEY in entry else None
            ended = _parse(entry[EXPIRED_KEY])
            if started is not None and ended is not None and ended < started:
                raise ValueError(f"{at}: expired_at is before since")


def save_policy(
    stored: Any,
    edited: Any,
    now: datetime,
    *,
    regrant: Collection[tuple[str, str]] = (),
) -> list[dict[str, Any]]:
    """The policy to store when a caller saves ``edited`` (an edit of
    :func:`active_policy`) over ``stored``, at time ``now``.

    Limits are compared by grant (object, enrollment, permissions, options) and limit
    name. A limit that is new is stamped ``since = now``. One whose value is unchanged
    keeps its ``since``. One whose value changed is retired (``expired_at = now``) and
    replaced: a ``total`` limit takes ``since = now`` (a new value is a new grant, so a
    fresh counter), a windowed limit keeps its ``since`` (the spend so far stays).
    ``regrant`` lists ``(object, limit name)`` pairs the customer explicitly grants
    again ("allow another"): each is retired and restarted at ``now`` even when its value
    is the same. A limit missing from ``edited`` is retired. Every retired entry already
    in ``stored`` is kept as it is, and a ``since`` the caller supplies must equal the
    stored one (``expired_at`` is never accepted): a backdated ``since`` would count more
    history, a later one would forgive spend. Raises ``ValueError``.
    """
    stamp = _utc(now).isoformat()
    history = [deepcopy(e) for e in stored if is_retired(e)] if isinstance(stored, list) else []
    known = _limits(stored)
    if not isinstance(edited, list):
        raise ValueError("policy must be a list of grants")

    active: list[dict[str, Any]] = []
    retired: list[tuple[str, str | None, str, Any]] = []  # object, since, limit, value
    kept: set[tuple[_GRANT, str]] = set()
    grant_regrant = set(regrant)
    holders = _holders(edited)
    for index, entry in enumerate(edited):
        at = f"policy[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("object"), str):
            raise ValueError(f'{at} must be {{"object": ..., "permissions": [...]}}')
        if EXPIRED_KEY in entry:
            raise ValueError(f"{at}.{EXPIRED_KEY}: only the server retires an entry")
        limits = entry.get("limits")
        body = {k: v for k, v in entry.items() if k not in (SINCE_KEY, "limits")}
        if entry["object"] == APIKEY_OBJECT or not limits:
            # No limit history: the key lifespan, or a grant with no limits.
            if SINCE_KEY in entry:
                raise ValueError(f"{at}.{SINCE_KEY}: an entry with no limits has no start time")
            active.append({**body, **({"limits": dict(limits)} if limits else {})})
            continue
        validate_limits(f"{at}.limits", limits)
        grant = _grant(entry, holders)
        by_since: dict[str | None, dict[str, Any]] = {}
        for name, value in limits.items():
            old = known.get((grant, name))
            since: str | None
            if old is None:
                since = stamp
            else:
                old_value, old_since = old
                restarted = (entry["object"], name) in grant_regrant
                changed = not _same(old_value, value)
                if SINCE_KEY in entry and entry[SINCE_KEY] != old_since:
                    raise ValueError(f"{at}.{SINCE_KEY}: the start time of {name} is set by the server")
                if not changed and not restarted:
                    since = old_since
                    value = old_value  # keep the stored spelling of an equal value
                else:
                    retired.append((entry["object"], old_since, name, old_value))
                    since = stamp if restarted or name.endswith("_total") else old_since
                kept.add((grant, name))
            if old is None and SINCE_KEY in entry:
                raise ValueError(f"{at}.{SINCE_KEY}: the start time of {name} is set by the server")
            by_since.setdefault(since, {})[name] = value
        first = True
        for since, group in by_since.items():
            piece: dict[str, Any] = dict(body) if first else {"object": entry["object"]}
            piece["limits"] = group
            if since is not None:
                piece[SINCE_KEY] = since
            active.append(piece)
            first = False

    for (grant, name), (value, since) in known.items():
        if (grant, name) not in kept:
            retired.append((grant[0], since, name, value))

    for obj, since, name, value in retired:
        retire = _slot(history, obj, since, name, stamp)
        retire["limits"][name] = value
    return history + active


def _slot(history: list[dict[str, Any]], obj: str, since: str | None, name: str, stamp: str) -> dict[str, Any]:
    """The retired entry to record ``name`` in (one per object, ``since`` and
    ``expired_at``, split if the name is already in it)."""
    for entry in history:
        if (
            entry["object"] == obj
            and entry.get(SINCE_KEY) == since
            and entry[EXPIRED_KEY] == stamp
            and name not in entry["limits"]
        ):
            return entry
    entry = {"object": obj, "limits": {}, EXPIRED_KEY: stamp}
    if since is not None:
        entry[SINCE_KEY] = since
    history.append(entry)
    return entry


def _holders(policy: Any) -> dict[str, dict[str, Any]]:
    """The entry that holds each object's permissions: a limit-only entry of that
    object (no ``permissions``) belongs to its grant."""
    return {
        e["object"]: e
        for e in policy
        if isinstance(e, dict) and isinstance(e.get("object"), str) and "permissions" in e and not is_retired(e)
    }


def _grant(entry: dict[str, Any], holders: dict[str, dict[str, Any]]) -> _GRANT:
    if "permissions" not in entry:
        entry = holders.get(entry["object"], entry)
    permissions = entry.get("permissions")
    options = parse_options("options", entry.get("options", "")) if entry.get("options") else {}
    return (
        entry["object"],
        str(entry.get(ENROLLMENT_KEY, "")),
        tuple(sorted(permissions)) if isinstance(permissions, list) else (),
        options_text(options),
    )


def _limits(stored: Any) -> dict[tuple[_GRANT, str], tuple[Any, str | None]]:
    """Each active limit of ``stored`` by (grant, name): its value and ``since``.

    A limit-only entry (no ``permissions``) belongs to the grant of the same object
    that holds permissions, so it is keyed by that grant; with none, by its own."""
    entries = [e for e in active_policy(stored) if e.get("limits") and e.get("object") != APIKEY_OBJECT]
    holders = _holders(entries)
    found: dict[tuple[_GRANT, str], tuple[Any, str | None]] = {}
    for entry in entries:
        grant = _grant(entry, holders)
        for name, value in entry["limits"].items():
            found[(grant, name)] = (value, entry.get(SINCE_KEY))
    return found


def _same(a: Any, b: Any) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return a == b


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None

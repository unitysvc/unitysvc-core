"""``base_cost``: the platform's cost of a container channel, as a price.

A leaf price type like ``constant``: ``{"type": "base_cost"}`` is what the platform
pays to run the channel's size for the metered runtime, and nothing more. A seller
who wants a margin composes it like any other price, ``{"type": "multiply",
"factor": "1.2", "base": {"type": "base_cost"}}`` or an ``add`` with a time price;
a seller who takes nothing lists the bare type (a developer seller is held to
exactly that by the platform).

The one rule, shared by seller validation (``DataValidator``) and backend ingest,
which runs the same check on an upload: ``base_cost`` prices a container channel,
so it is a ``list_price`` type, never a ``payout_price``, and wherever it sits in a
listing's ``list_price`` (the whole price, a leaf of a composite, a channel's entry
or a leaf inside one) the channel it prices must be a ``container`` channel of the
offering. Core holds the shape only: the platform resolves the figure from the
size the channel names (``BaseCostPriceData``).
"""

from typing import Any

Position = tuple[str, ...]


def base_cost_positions(price: Any, path: Position = ()) -> list[Position]:
    """Where ``base_cost`` prices sit in a pricing object: ``()`` for the whole price,
    ``("channels", name, ...)`` under a channel's entry, other paths inside a composite."""
    if not isinstance(price, dict):
        return []
    if price.get("type") == "base_cost":
        return [path]
    found: list[Position] = []
    for key, value in price.items():
        if isinstance(value, dict):
            if key == "channels":
                for name, child in value.items():
                    found.extend(base_cost_positions(child, (*path, "channels", str(name))))
            else:
                found.extend(base_cost_positions(value, (*path, key)))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                found.extend(base_cost_positions(item, (*path, key, str(i))))
    return found


def container_channels(offering: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The offering's ``container`` channels: ``{name: channel config}``."""
    config = offering.get("upstream_access_config") if isinstance(offering, dict) else None
    if not isinstance(config, dict):
        return {}
    return {n: c for n, c in config.items() if isinstance(c, dict) and c.get("access_method") == "container"}


def is_container_only(offering: dict[str, Any] | None) -> bool:
    """Whether every channel of an offering is a ``container`` channel (and it has one)."""
    config = offering.get("upstream_access_config") if isinstance(offering, dict) else None
    return (
        isinstance(config, dict)
        and bool(config)
        and all(isinstance(c, dict) and c.get("access_method") == "container" for c in config.values())
    )


def priced_channel(position: Position) -> str | None:
    """The channel a ``base_cost`` at ``position`` prices: the entry it sits under in a
    channel-keyed price, or ``None`` when it prices every channel of the offering."""
    return position[1] if len(position) >= 2 and position[0] == "channels" else None


def validate_base_cost_list_price(listing: dict[str, Any], offering: dict[str, Any] | None) -> list[str]:
    """The rule of ``base_cost`` in a listing's ``list_price``, as error messages.

    Each ``base_cost`` must price a container channel: one under a channel's entry needs that
    channel to be a ``container`` channel of the offering; one anywhere else prices every channel,
    so the offering must be container-only. Without the offering there is nothing to check. An
    empty list when the listing has none.
    """
    errors: list[str] = []
    if offering is None:
        return errors
    for position in base_cost_positions(listing.get("list_price")):
        channel = priced_channel(position)
        where = "list_price" + "".join(f".{part}" for part in position)
        if channel is None:
            if not is_container_only(offering):
                errors.append(
                    f"{where}: base_cost prices a container channel (every channel of the offering "
                    "must be a container channel); price other channels with a channel-keyed list_price"
                )
        elif channel not in container_channels(offering):
            errors.append(
                f"{where}: base_cost applies only to a container channel of the offering "
                f"({sorted(container_channels(offering)) or 'it has none'})"
            )
    return errors


def validate_base_cost_payout_price(offering: dict[str, Any]) -> list[str]:
    """``base_cost`` is a list price: an offering's ``payout_price`` may not contain one."""
    if base_cost_positions(offering.get("payout_price")):
        return ["payout_price: base_cost is a list_price type; a payout is a revenue_share or a price"]
    return []

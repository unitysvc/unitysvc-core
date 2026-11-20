"""``base_cost``: the list price of a container service offered at the platform's cost.

The rules of the type, shared by seller validation (``DataValidator``) and backend
ingest, which runs the same checks on an upload:

- ``base_cost`` is a ``list_price`` type, never a ``payout_price``, and only as the
  whole list price or as the price of one channel in a channel-keyed price, not a
  part of a composite price;
- it needs a container channel: the whole list price needs a container-only
  offering, and a channel's price must be a ``container`` channel of the offering;
- the author takes no payment: ``payout_price`` is a zero revenue share (or a zero
  constant), so the platform's markup, if any, stays with the platform.

Core holds the shape only. The platform resolves the figure from the size the
channel names (``BaseCostPriceData``).
"""

from decimal import Decimal, InvalidOperation
from typing import Any

Position = tuple[str, ...]


def base_cost_positions(price: Any, path: Position = ()) -> list[Position]:
    """Where ``base_cost`` prices sit in a pricing object: ``()`` for the whole price,
    ``("channels", name)`` for a channel's price, deeper paths for anything else."""
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


def is_zero_payout(payout: Any, channel: str | None = None) -> bool:
    """Whether a payout price pays the seller nothing (a zero share or a zero constant).

    A channel-keyed payout is read for ``channel``, falling back to its ``default``.
    """
    if not isinstance(payout, dict):
        return False
    kind = payout.get("type")
    try:
        if kind == "revenue_share":
            return Decimal(str(payout.get("percentage"))) == 0
        if kind == "constant":
            return Decimal(str(payout.get("price"))) == 0
    except InvalidOperation:
        return False
    if kind == "channel" and isinstance(payout.get("channels"), dict):
        channels = payout["channels"]
        child = channels.get(channel) if channel is not None else None
        child = child if child is not None else channels.get(payout.get("default"))
        return is_zero_payout(child)
    return False


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


def validate_base_cost_list_price(listing: dict[str, Any], offering: dict[str, Any] | None) -> list[str]:
    """The rules of a ``base_cost`` in a listing's ``list_price``, as error messages.

    Without the offering only the listing's own shape is checked (where the type sits);
    with it, that each ``base_cost`` prices a container channel and that the offering's
    ``payout_price`` pays the author nothing. An empty list when the listing has none.
    """
    errors: list[str] = []
    positions = base_cost_positions(listing.get("list_price"))
    if not positions:
        return errors
    channel_names: list[str | None] = []
    for position in positions:
        if position == ():
            channel_names.append(None)
            if offering is not None and not is_container_only(offering):
                errors.append(
                    "list_price: base_cost needs a container service (every channel of the offering "
                    "must be a container channel); price other channels with a channel-keyed list_price"
                )
        elif len(position) == 2 and position[0] == "channels":
            channel_names.append(position[1])
            if offering is not None and position[1] not in container_channels(offering):
                errors.append(
                    f"list_price.channels.{position[1]}: base_cost applies only to a container channel "
                    f"of the offering ({sorted(container_channels(offering)) or 'it has none'})"
                )
        else:
            errors.append(
                f"list_price.{'.'.join(position)}: base_cost must be the whole list_price or the price "
                "of one channel, not a part of a composite price"
            )
    if isinstance(offering, dict) and offering.get("payout_price") is not None:
        for name in channel_names:
            if not is_zero_payout(offering["payout_price"], name):
                where = "" if name is None else f" for channel {name!r}"
                errors.append(
                    f"A base_cost list_price is for an author who takes no payment: payout_price{where} "
                    'must be {"type": "revenue_share", "percentage": "0"} (or a zero constant)'
                )
                break
    return errors


def validate_base_cost_payout_price(offering: dict[str, Any]) -> list[str]:
    """``base_cost`` is a list price: an offering's ``payout_price`` may not be one."""
    if base_cost_positions(offering.get("payout_price")):
        return ["payout_price: base_cost is a list_price type; a payout is a revenue_share or a price"]
    return []

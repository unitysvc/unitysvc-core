"""Pricing data models and cost-calculation logic.

Contains:
- Price validators (_validate_price_string, _validate_amount_string, _validate_percentage_string)
  and their Annotated string types (PriceStr, AmountStr, PercentageStr).
- ``UsageData`` — the shape passed into ``calculate_cost``.
- All ``*PriceData`` classes (simple, composite, tiered, graduated, expression).
- The ``Pricing`` discriminated union and ``validate_pricing`` helper.
- Equivalence groups and metric resolution helpers used by tiered/graduated/expr pricing.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.functional_validators import BeforeValidator


def _validate_price_string(v: Any) -> str:
    """Validate that price values are strings representing valid decimal numbers.

    This prevents floating-point precision issues where values like 2.0
    might become 1.9999999 when saved/loaded. Prices are stored as strings
    and converted to Decimal only when calculations are needed.

    Negative values are allowed to support seller-funded incentives where
    the payout_price is negative (seller pays the platform).
    """
    if isinstance(v, float):
        raise ValueError(
            f"Price value must be a string (e.g., '0.50'), not a float ({v}). Floats can cause precision issues."
        )

    # Convert int to string first
    if isinstance(v, int):
        v = str(v)

    if not isinstance(v, str):
        raise ValueError(f"Price value must be a string, got {type(v).__name__}")

    # Validate it's a valid decimal number
    try:
        Decimal(v)
    except InvalidOperation:
        raise ValueError(f"Price value '{v}' is not a valid decimal number")

    return v


# Price string type that only accepts strings/ints, not floats
PriceStr = Annotated[str, BeforeValidator(_validate_price_string)]


def _validate_amount_string(v: Any) -> str:
    """Validate that amount values are strings representing valid decimal numbers.

    Similar to _validate_price_string but allows negative values for
    discounts, fees, and adjustments.
    """
    if isinstance(v, float):
        raise ValueError(
            f"Amount value must be a string (e.g., '-5.00'), not a float ({v}). Floats can cause precision issues."
        )

    # Convert int to string first
    if isinstance(v, int):
        v = str(v)

    if not isinstance(v, str):
        raise ValueError(f"Amount value must be a string, got {type(v).__name__}")

    # Validate it's a valid decimal number (can be negative)
    try:
        Decimal(v)
    except InvalidOperation:
        raise ValueError(f"Amount value '{v}' is not a valid decimal number")

    return v


# Amount string type that allows negative values (for fees, discounts)
AmountStr = Annotated[str, BeforeValidator(_validate_amount_string)]


# ============================================================================
# Usage Data for cost calculation
# ============================================================================


class UsageData(BaseModel):
    """
    Usage data for cost calculation.

    Different pricing types require different usage fields:
    - one_million_tokens: input_tokens, output_tokens, cached_input_tokens (or total_tokens)
    - one_second/one_minute/one_hour/one_day/one_month: any time field (auto-converted)
    - image: count
    - step: count

    Fields within the same **equivalence group** measure the same dimension
    and convert automatically.  For example, pricing that asks for
    ``one_minute`` accepts usage in ``one_hour``.

    Equivalence groups:
    - **time**: seconds, one_second, one_minute, one_hour, one_day, one_month
    - **tokens**: one_token, one_thousand_tokens, one_million_tokens
    - **data**: one_byte, one_kilobyte, one_megabyte, one_gigabyte
    - **count**: count, one_thousand, one_million
    - **characters**: characters, one_character, one_thousand_characters, one_million_characters

    Only one field per group should be set per usage instance.

    Extra fields are ignored, so you can pass **usage_info directly.
    """

    model_config = ConfigDict(extra="ignore")

    # Token-based usage (for LLMs) — used by TokenPriceData's split pricing
    input_tokens: int | None = None
    cached_input_tokens: int | None = None  # For providers with discounted cached token rates
    output_tokens: int | None = None
    total_tokens: int | None = None  # Alternative to input/output for unified pricing

    # Token-scaled usage — equivalence group for based_on in tiered/graduated.
    # (input_tokens/output_tokens remain separate for TokenPriceData.)
    one_token: int | None = None
    one_thousand_tokens: float | None = None
    one_million_tokens: float | None = None

    # Time-based usage — set whichever unit you have.
    # These form an equivalence group; cross-unit conversion is automatic.
    seconds: float | None = None  # backward compat alias for one_second
    one_second: float | None = None
    one_minute: float | None = None
    one_hour: float | None = None
    one_day: float | None = None
    one_month: float | None = None  # 30 days (2,592,000 seconds)

    # Data-based usage — equivalence group for bandwidth/storage pricing.
    one_byte: float | None = None
    one_kilobyte: float | None = None
    one_megabyte: float | None = None
    one_gigabyte: float | None = None

    # Names the GATEWAY actually ships (usage_event.lua), kept as aliases so they
    # survive ``extra="ignore"``. ``images_generated`` and ``duration_seconds``
    # join their equivalence groups below; the byte counters deliberately do
    # NOT — see DataPriceData.based_on.
    images_generated: int | None = None
    duration_seconds: float | None = None
    bytes_in: int | None = None
    bytes_out: int | None = None

    # Character-based usage (text-to-speech and similar).
    # ``characters`` is what providers actually report — DashScope's TTS answers
    # ``usage: {"characters": N}`` — and is the group's base unit, the way
    # ``seconds`` is for time.
    characters: int | None = None
    one_character: int | None = None
    one_thousand_characters: float | None = None
    one_million_characters: float | None = None

    # Count-based usage (images, steps, requests)
    # count + scaled variants form an equivalence group.
    count: int | None = None
    one_thousand: float | None = None
    one_million: float | None = None


# ============================================================================
# Effective price — a per-unit comparison figure, never billed
# ============================================================================

#: Normalized units an effective price is expressed in. Each dimension has one,
#: so figures of the same dimension always compare directly.
UNIT_TOKENS = "one_million_tokens"
UNIT_REQUEST = "request"
UNIT_TIME = "one_hour"
UNIT_DATA = "one_gigabyte"
UNIT_CHARACTERS = "one_million_characters"
UNIT_IMAGE = "image"
UNIT_STEP = "step"

#: Each measured equivalence group's normalized unit (counts compare per request).
_GROUP_UNIT = {
    "tokens": UNIT_TOKENS,
    "time": UNIT_TIME,
    "data": UNIT_DATA,
    "characters": UNIT_CHARACTERS,
}

#: When figures in different units must be reduced to one (``max``, ``min``,
#: ``tiered``, ``channel``), the first unit present in this order wins; any
#: other unit follows alphabetically.
_UNIT_PRIORITY = (UNIT_TOKENS, UNIT_REQUEST)

#: Output tokens weigh 4x input: typical chat traffic, and the platform's
#: long-standing blend convention.
_OUTPUT_WEIGHT = Decimal(4)


class EffectivePrice(BaseModel):
    """What using a service realistically costs per unit, for comparison.

    A best-effort estimate for sorting, filtering and routing — never billed.
    ``price`` (when set) is what is charged; this is computed from the pricing
    object by ``effective_price()`` and never written back into it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal = Field(description="Cost per one ``unit``")
    unit: str = Field(
        description="Normalized unit: 'one_million_tokens', 'request', 'one_hour', 'one_gigabyte', "
        "'one_million_characters', 'image' or 'step'",
    )
    source: Literal["rate", "upstream"] = Field(
        default="rate",
        description="'rate': from the price itself. 'upstream': a $0 price whose description states what the "
        "customer pays the upstream provider (bring-your-own-key).",
    )


def _blend(tokens_in: Decimal, tokens_out: Decimal) -> Decimal:
    return (tokens_in + _OUTPUT_WEIGHT * tokens_out) / (1 + _OUTPUT_WEIGHT)


def _unit_rank(unit: str) -> tuple[int, str]:
    return (_UNIT_PRIORITY.index(unit), "") if unit in _UNIT_PRIORITY else (len(_UNIT_PRIORITY), unit)


def _child_effective_price(price_data: Any) -> EffectivePrice | None:
    """The effective price of a nested pricing dict; None if it is unknown or invalid."""
    if not isinstance(price_data, dict):
        return None
    try:
        return validate_pricing(price_data).effective_price()
    except Exception:
        return None


def _pick(
    figures: Iterable[EffectivePrice | None],
    choose: Callable[[Iterable[EffectivePrice]], EffectivePrice],
) -> EffectivePrice | None:
    """Reduce known figures to one, comparing only within the dominant unit."""
    known = [f for f in figures if f is not None]
    if not known:
        return None
    unit = min((f.unit for f in known), key=_unit_rank)
    return choose(f for f in known if f.unit == unit)


def _largest(figures: Iterable[EffectivePrice | None]) -> EffectivePrice | None:
    return _pick(figures, lambda fs: max(fs, key=lambda f: f.amount))


def _smallest(figures: Iterable[EffectivePrice | None]) -> EffectivePrice | None:
    return _pick(figures, lambda fs: min(fs, key=lambda f: f.amount))


def _rate(amount: Decimal, unit: str) -> EffectivePrice:
    return EffectivePrice(amount=amount, unit=unit)


def _per_normalized_unit(metric: str, unit_price: Decimal) -> EffectivePrice | None:
    """Express a price per one ``metric`` in the metric dimension's normalized unit.

    ``metric`` is a pricing type or a ``based_on`` name: an equivalence-group
    unit, a token counter, ``request_count``, or a gateway byte counter. Any
    other metric (``customer_charge``, an expression) has no unit: None.
    """
    if metric in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
        metric = "one_token"
    elif metric in ("bytes_in", "bytes_out", "bytes_total"):
        metric = "one_byte"
    if metric == "request_count":
        return _rate(unit_price, UNIT_REQUEST)
    if metric == "images_generated":
        return _rate(unit_price, UNIT_IMAGE)
    if metric not in _UNIT_TO_GROUP:
        return None
    group, factor = _UNIT_TO_GROUP[metric]
    if group == "count":
        # One count is one call, event or message: compared per request.
        return _rate(unit_price / factor, UNIT_REQUEST)
    target = _GROUP_UNIT[group]
    return _rate(unit_price * EQUIVALENCE_GROUPS[group][target] / factor, target)


_DOLLARS = r"\$\s*\d+(?:\.\d+)?"
# "$1.4 / $4.4 per 1M input/output tokens", "$1.518/$4.554 / 1M input/output
# tokens", "$4 / $20 / $0.2 per 1M input/output/cached tokens",
# "$3.45/$0.345/$17.25 / 1M in/cached/out tokens", "$0.50 / 1M tokens".
_UPSTREAM_TOKEN_PRICE = re.compile(
    rf"(?P<amounts>{_DOLLARS}(?:\s*/\s*{_DOLLARS})*)\s*(?:/|per)\s*1\s*(?P<scale>[MK])\s+"
    r"(?:(?P<labels>[a-z_]+(?:\s*/\s*[a-z_]+)*)\s+)?tokens\b",
    re.IGNORECASE,
)
_UPSTREAM_LABELS = {
    "input": "in",
    "in": "in",
    "output": "out",
    "out": "out",
    "cached": "cached",
    "cache": "cached",
    "cached_input": "cached",
}


def upstream_price_from_description(text: str | None) -> Decimal | None:
    """The upstream price per 1M tokens a description states, blended; or None.

    Bring-your-own-key prices are ``0`` (we bill nothing) and their generators
    write what the provider charges into the description, e.g. "billed by
    Hugging Face directly at $1.4 / $4.4 per 1M input/output tokens".

    Fails closed: text not in a known shape (labels that don't match the
    amounts, no input/output pair, no token unit) is None, never 0.
    """
    if not text:
        return None
    match = _UPSTREAM_TOKEN_PRICE.search(text)
    if match is None:
        return None
    amounts = [Decimal(a) for a in re.findall(r"\d+(?:\.\d+)?", match["amounts"])]
    scale = Decimal(1000) if match["scale"].upper() == "K" else Decimal(1)
    if not match["labels"]:
        return amounts[0] * scale if len(amounts) == 1 else None
    labels = [_UPSTREAM_LABELS.get(label.strip().lower()) for label in match["labels"].split("/")]
    if len(labels) != len(amounts) or None in labels:
        return None
    by_label = dict(zip(labels, amounts, strict=True))
    if "in" not in by_label or "out" not in by_label:
        return None
    return _blend(by_label["in"], by_label["out"]) * scale


# ============================================================================
# Pricing Models - Discriminated Union for type-safe pricing validation
# ============================================================================


class BasePriceData(BaseModel):
    """Base class for all price data types.

    All pricing types include:
    - type: Discriminator field for the pricing type
    - price: Summary price for marketplace comparison (required for simple types,
      optional for composite types where sellers can set a nominal value)
    - description: Optional human-readable description
    - reference: Optional URL to upstream pricing page
    """

    model_config = ConfigDict(extra="forbid")

    price: PriceStr | None = Field(
        default=None,
        description="Summary price for marketplace comparison and sorting. "
        "For simple pricing types this is the billing rate. "
        "For composite types (add, multiply, tiered, graduated) this is a "
        "seller-provided nominal value for marketplace display.",
    )

    description: str | None = Field(
        default=None,
        description="Human-readable description of the pricing model",
    )

    reference: str | None = Field(
        default=None,
        description="URL to upstream provider's pricing page",
    )

    def effective_price(self) -> EffectivePrice | None:
        """What using this price realistically costs per unit, or None if unknown.

        A comparison figure for sorting, filtering and routing; never billed.
        Every pricing type defines it, so a composite price computes its figure
        from its children. Unknown is never 0. Never raises.
        """
        return None


class TokenPriceData(BasePriceData):
    """
    Price data for token-based pricing (LLMs).

    Supports two modes:
    1. **Unified pricing**: Set ``price`` only — same rate for all token types.
    2. **Separate pricing**: Set ``input`` and ``output`` (and optionally ``cached_input``)
       for different rates per token type.

    In separate pricing mode, ``price`` serves as a **summary price for marketplace
    comparison**. If not explicitly set by the seller, the backend calculates it
    during service ingestion using: ``price = (input + 4*output) / 5``.

    Price values use Decimal for precision. In JSON/TOML, specify as strings
    (e.g., "0.50") to avoid floating-point precision issues.
    """

    type: Literal["one_million_tokens", "one_thousand_tokens", "one_token"] = "one_million_tokens"

    # Summary price for marketplace comparison and sorting.
    # For unified pricing: this is the only price field needed.
    # For separate pricing: recommended for marketplace comparability.
    # If not set, the backend auto-computes during ingestion.
    price: PriceStr | None = Field(
        default=None,
        description="Summary price per million tokens for marketplace comparison. "
        "For unified pricing, this is the billing rate. "
        "For separate input/output pricing, this is a representative price for sorting/filtering.",
    )

    # Separate input/output pricing (for billing)
    input: PriceStr | None = Field(
        default=None,
        description="Price per million input tokens",
    )
    cached_input: PriceStr | None = Field(
        default=None,
        description="Price per million cached input tokens (optional, for discounted cached rates)",
    )
    output: PriceStr | None = Field(
        default=None,
        description="Price per million output tokens",
    )

    @model_validator(mode="after")
    def validate_price_fields(self) -> TokenPriceData:
        """Validate pricing field combinations and auto-compute summary price."""
        has_input_output = self.input is not None or self.output is not None

        if not self.price and not has_input_output:
            raise ValueError("Must specify either 'price' (unified) or 'input'/'output' (separate pricing).")

        if has_input_output and (self.input is None or self.output is None):
            raise ValueError("Both 'input' and 'output' must be specified for separate pricing.")

        # Auto-compute summary price from input/output if not explicitly set
        if self.price is None and has_input_output:
            self.price = self.compute_summary_price()

        return self

    def compute_summary_price(self) -> str:
        """Compute a summary price from input/output for marketplace comparison.

        Formula: (input + 4*output) / 5
        This weights output 4x higher than input, reflecting typical LLM usage
        where output tokens are more expensive and represent the dominant cost.

        Returns:
            Price string (e.g., "3.00"), or the existing price if already set.
        """
        if self.price is not None:
            return self.price
        if self.input is not None and self.output is not None:
            input_d = Decimal(self.input)
            output_d = Decimal(self.output)
            summary = (input_d + 4 * output_d) / 5
            # Round to same precision as input
            return str(summary.quantize(Decimal("0.01")))
        return "0"

    @property
    def _divisor(self) -> int:
        """Token divisor based on the pricing type."""
        return {"one_million_tokens": 1_000_000, "one_thousand_tokens": 1_000, "one_token": 1}[self.type]

    def effective_price(self) -> EffectivePrice | None:
        """``(input + 4*output) / 5`` when priced separately, else ``price``; per 1M tokens."""
        if self.input is not None and self.output is not None:
            per_unit = _blend(Decimal(self.input), Decimal(self.output))
        elif self.price is not None:
            per_unit = Decimal(self.price)
        else:
            return None
        return _per_normalized_unit(self.type, per_unit)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for token-based pricing.

        Supports ``one_million_tokens``, ``one_thousand_tokens``, and
        ``one_token`` types.  The divisor adjusts automatically.

        Args:
            usage: Usage data with token counts (input_tokens, cached_input_tokens, output_tokens)
            customer_charge: Not used for token pricing (ignored)
            request_count: Number of requests (ignored for token pricing)

        Returns:
            Calculated cost based on token usage
        """
        input_tokens = usage.input_tokens or 0
        cached_input_tokens = usage.cached_input_tokens or 0
        output_tokens = usage.output_tokens or 0

        if usage.total_tokens is not None and usage.input_tokens is None:
            input_tokens = usage.total_tokens
            output_tokens = 0

        divisor = self._divisor

        if self.input is not None and self.output is not None:
            input_cost = Decimal(self.input) * input_tokens / divisor
            # Use cached_input price if available, otherwise fall back to input price
            cached_price = Decimal(self.cached_input) if self.cached_input else Decimal(self.input)
            cached_input_cost = cached_price * cached_input_tokens / divisor
            output_cost = Decimal(self.output) * output_tokens / divisor
        else:
            price = Decimal(self.price)  # type: ignore[arg-type]
            input_cost = price * input_tokens / divisor
            cached_input_cost = price * cached_input_tokens / divisor
            output_cost = price * output_tokens / divisor

        return input_cost + cached_input_cost + output_cost


class TimePriceData(BasePriceData):
    """
    Price data for time-based pricing (audio/video, compute time, alias duration).

    Supported types: ``one_second``, ``one_minute``, ``one_hour``,
    ``one_day``, ``one_month`` (30 days).

    Usage can be provided in **any** time unit — cross-unit conversion is
    handled automatically via equivalence groups.  For example, pricing of
    ``{"type": "one_hour", "price": "1.00"}`` with
    ``UsageData(one_minute=120)`` converts 120 minutes → 2 hours → $2.00.

    Price values use Decimal for precision. In JSON/TOML, specify as strings
    (e.g., "0.006") to avoid floating-point precision issues.
    """

    type: Literal["one_second", "one_minute", "one_hour", "one_day", "one_month"] = "one_second"

    price: PriceStr = Field(
        description="Price per one unit of the specified type",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` in the dimension's normalized unit."""
        return _per_normalized_unit(self.type, Decimal(self.price))

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for time-based pricing.

        Converts usage to the unit specified by ``self.type`` via
        equivalence-group conversion, then multiplies by price.

        Args:
            usage: Usage data with any time field (seconds, one_minute, etc.)
            customer_charge: Not used for time pricing (ignored)
            request_count: Number of requests (ignored for time pricing)

        Returns:
            Calculated cost based on time usage
        """
        units = _resolve_equivalent_metric(self.type, usage)
        if units is None:
            raise ValueError(f"Time-based pricing ({self.type}) requires a time field in usage data")

        return Decimal(self.price) * units


class ImagePriceData(BasePriceData):
    """
    Price data for per-image pricing (image generation, processing).

    Price values use Decimal for precision. In JSON/TOML, specify as strings
    (e.g., "0.04") to avoid floating-point precision issues.
    """

    type: Literal["image"] = "image"

    price: PriceStr = Field(
        description="Price per image",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` per image."""
        return _rate(Decimal(self.price), UNIT_IMAGE)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for image-based pricing.

        Args:
            usage: Usage data with count
            customer_charge: Not used for image pricing (ignored)
            request_count: Number of requests (ignored for image pricing)

        Returns:
            Calculated cost based on image count
        """
        # Via the equivalence group, so the gateway's ``images_generated`` counts
        # as well as an explicit ``count``.
        units = _resolve_equivalent_metric("count", usage)
        if units is None:
            raise ValueError("Image pricing requires 'count' in usage data")

        return Decimal(self.price) * units


class StepPriceData(BasePriceData):
    """
    Price data for per-step pricing (diffusion steps, iterations).

    Price values use Decimal for precision. In JSON/TOML, specify as strings
    (e.g., "0.001") to avoid floating-point precision issues.
    """

    type: Literal["step"] = "step"

    price: PriceStr = Field(
        description="Price per step/iteration",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` per step."""
        return _rate(Decimal(self.price), UNIT_STEP)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for step-based pricing.

        Args:
            usage: Usage data with count
            customer_charge: Not used for step pricing (ignored)
            request_count: Number of requests (ignored for step pricing)

        Returns:
            Calculated cost based on step count
        """
        if usage.count is None:
            raise ValueError("Step pricing requires 'count' in usage data")

        return Decimal(self.price) * usage.count


class DataPriceData(BasePriceData):
    """
    Price data for data-volume pricing (bandwidth, storage, transfer).

    Supported types: ``one_byte``, ``one_kilobyte``, ``one_megabyte``,
    ``one_gigabyte``.  Uses binary units (1 KB = 1024 bytes).

    Usage can be provided in **any** data unit — cross-unit conversion is
    handled automatically via equivalence groups.

    The gateway ships ``bytes_in`` and ``bytes_out`` on every request. Those are
    two directions of one dimension, so they are deliberately NOT both in the
    data equivalence group: a group lookup returns whichever field the dict
    happens to yield first, which would make the billed direction depend on
    iteration order. ``based_on`` names the direction instead, so it is a
    property of the price rather than an accident of the payload.
    """

    type: Literal["one_byte", "one_kilobyte", "one_megabyte", "one_gigabyte"] = "one_megabyte"

    based_on: Literal["bytes_out", "bytes_in", "bytes_total"] = Field(
        default="bytes_out",
        description="Which byte counter to price. Defaults to egress, the "
        "conventional billable direction; 'bytes_total' sums both.",
    )

    price: PriceStr = Field(
        description="Price per one unit of the specified type",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` in the dimension's normalized unit."""
        return _per_normalized_unit(self.type, Decimal(self.price))

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for data-volume pricing.

        Args:
            usage: Usage data with any data field (one_byte, one_kilobyte, etc.)
            customer_charge: Not used (ignored)
            request_count: Not used (ignored)

        Returns:
            Calculated cost based on data usage
        """
        # The named byte counter wins when present — it is what the gateway
        # actually sends. Fall back to the canonical one_byte..one_gigabyte
        # units so a caller supplying those directly is unaffected.
        raw_bytes: Decimal | None = None
        if self.based_on == "bytes_total":
            if usage.bytes_in is not None or usage.bytes_out is not None:
                raw_bytes = Decimal(usage.bytes_in or 0) + Decimal(usage.bytes_out or 0)
        else:
            value = getattr(usage, self.based_on, None)
            if value is not None:
                raw_bytes = Decimal(str(value))

        units: Decimal | None
        if raw_bytes is not None:
            units = raw_bytes / EQUIVALENCE_GROUPS["data"][self.type]
        else:
            units = _resolve_equivalent_metric(self.type, usage)

        if units is None:
            raise ValueError(f"Data pricing ({self.type}) requires a data field in usage data")

        return Decimal(self.price) * units


class CountPriceData(BasePriceData):
    """
    Price data for count-scaled pricing (API calls, events, requests).

    Supported types: ``one_thousand``, ``one_million``.  The ``count``
    equivalence-group field is the base unit.

    Usage can be provided as ``count``, ``one_thousand``, or
    ``one_million`` — cross-unit conversion is automatic.

    Note: ``image`` and ``step`` pricing types also use ``count`` but have
    their own dedicated classes (``ImagePriceData``, ``StepPriceData``).
    """

    type: Literal["one_thousand", "one_million"] = "one_thousand"

    price: PriceStr = Field(
        description="Price per one unit of the specified type",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` in the dimension's normalized unit."""
        return _per_normalized_unit(self.type, Decimal(self.price))

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for count-scaled pricing.

        Args:
            usage: Usage data with any count field (count, one_thousand, one_million)
            customer_charge: Not used (ignored)
            request_count: Not used (ignored)

        Returns:
            Calculated cost based on count usage
        """
        units = _resolve_equivalent_metric(self.type, usage)
        if units is None:
            raise ValueError(f"Count pricing ({self.type}) requires a count field in usage data")

        return Decimal(self.price) * units


class CharacterPriceData(BasePriceData):
    """
    Price data for character-scaled pricing (text-to-speech and similar).

    Supported types: ``one_character``, ``one_thousand_characters``,
    ``one_million_characters``. The ``characters`` equivalence-group field is
    the base unit, and it is the name providers actually report — DashScope's
    TTS answers ``usage: {"characters": N}``.

    Usage may be given in any unit of the group; conversion is automatic.

    Separate from ``CountPriceData`` on purpose: characters and counts are
    different dimensions, and collapsing them would let "per 1M characters" and
    "per 1M images" resolve against each other's usage.
    """

    type: Literal["one_character", "one_thousand_characters", "one_million_characters"] = "one_million_characters"

    price: PriceStr = Field(
        description="Price per one unit of the specified type",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` in the dimension's normalized unit."""
        return _per_normalized_unit(self.type, Decimal(self.price))

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for character-scaled pricing.

        Args:
            usage: Usage data with any character field
            customer_charge: Not used (ignored)
            request_count: Not used (ignored)

        Returns:
            Calculated cost based on character usage

        Raises:
            ValueError: if no character field is populated. Raising rather than
                treating absence as zero is deliberate — a silent zero here is a
                charge of nothing for work actually done.
        """
        units = _resolve_equivalent_metric(self.type, usage)
        if units is None:
            raise ValueError(f"Character pricing ({self.type}) requires a character field in usage data")

        return Decimal(self.price) * units


def _validate_percentage_string(v: Any) -> str:
    """Validate that percentage values are strings representing valid decimals in range 0-100."""
    # First use the standard price validation
    v = _validate_price_string(v)

    # Then check the 0-100 range
    decimal_val = Decimal(v)
    if decimal_val > 100:
        raise ValueError(f"Percentage must be between 0 and 100, got '{v}'")

    return v


# Percentage string type for revenue share (0-100 range)
PercentageStr = Annotated[str, BeforeValidator(_validate_percentage_string)]


class RevenueSharePriceData(BasePriceData):
    """
    Price data for revenue share pricing (payout_price only).

    This pricing type is used exclusively for payout_price when the seller
    receives a percentage of what the customer pays. It cannot be used for
    list_price since the list price must be a concrete amount.

    The percentage represents the seller's share of the customer charge.
    For example, if percentage is "70" and the customer pays $10, the seller
    receives $7.

    Percentage values must be strings (e.g., "70.00") to avoid floating-point
    precision issues.
    """

    type: Literal["revenue_share"] = "revenue_share"

    percentage: PercentageStr = Field(
        description="Percentage of customer charge that goes to the seller (0-100)",
    )

    def effective_price(self) -> EffectivePrice | None:
        """A share of the customer charge has no per-unit rate: unknown."""
        return None

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost for revenue share pricing.

        Args:
            usage: Usage data (not used for revenue share, but kept for consistent API)
            customer_charge: Total amount charged to customer (required)
            request_count: Number of requests (ignored for revenue share)

        Returns:
            Seller's share of the customer charge

        Raises:
            ValueError: If customer_charge is not provided
        """
        if customer_charge is None:
            raise ValueError("Revenue share pricing requires 'customer_charge'")

        return customer_charge * Decimal(self.percentage) / Decimal("100")


class ConstantPriceData(BasePriceData):
    """
    Price data for a constant/fixed price.

    Used for fixed fees, discounts, or adjustments that don't depend on usage.
    Price can be positive (charge) or negative (discount/credit).
    """

    type: Literal["constant"] = "constant"

    price: AmountStr = Field(
        description="Fixed price (positive for charge, negative for discount)",
    )

    def effective_price(self) -> EffectivePrice | None:
        """``price`` per request.

        A ``0`` with a description is a bring-your-own-key price: the customer
        pays the upstream provider, at the rate the description states. A
        description that states no parseable rate, or $0/$0 (what generators
        write when they lack the provider's price), is unknown. A ``0`` with no
        description is genuinely free.
        """
        amount = Decimal(self.price)
        if amount != 0 or not self.description:
            return _rate(amount, UNIT_REQUEST)
        upstream = upstream_price_from_description(self.description)
        if upstream is None or upstream == 0:
            return None
        return EffectivePrice(amount=upstream, unit=UNIT_TOKENS, source="upstream")

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Return the constant price regardless of usage.

        Args:
            usage: Usage data (ignored for constant pricing)
            customer_charge: Customer charge (ignored for constant pricing)
            request_count: Number of requests (ignored for constant pricing)

        Returns:
            The fixed price
        """
        return Decimal(self.price)


def _extract_nominal_price(price_data: dict[str, Any]) -> str | None:
    """Extract the nominal ``price`` from a nested pricing dict.

    Validates the child pricing to obtain the computed model (which
    includes any auto-computed fields like TokenPriceData's weighted
    average), then returns its ``price``.

    Returns ``None`` if validation fails or no price can be determined.
    """
    if not isinstance(price_data, dict):
        return None

    try:
        validated = validate_pricing(price_data)
        if validated.price is not None:
            return validated.price
    except Exception:
        pass

    return None


# Forward reference for nested pricing - will be resolved after Pricing is defined
class AddPriceData(BasePriceData):
    """
    Composite pricing that sums multiple price components.

    Allows combining different pricing types, e.g., base token cost + fixed fee.

    Example:
        {
            "type": "add",
            "prices": [
                {"type": "one_million_tokens", "input": "0.50", "output": "1.50"},
                {"type": "constant", "amount": "-5.00", "description": "Platform fee"}
            ]
        }
    """

    type: Literal["add"] = "add"

    prices: list[dict[str, Any]] = Field(
        description="List of pricing components to sum together",
        min_length=1,
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> AddPriceData:
        """Auto-compute nominal price from the first child if not set."""
        if self.price is None and self.prices:
            self.price = _extract_nominal_price(self.prices[0])
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The sum of the children; unknown if any child is unknown or the units differ."""
        figures = [_child_effective_price(p) for p in self.prices]
        known = [f for f in figures if f is not None]
        if len(known) != len(figures) or len({f.unit for f in known}) != 1:
            return None
        source: Literal["rate", "upstream"] = "upstream" if any(f.source == "upstream" for f in known) else "rate"
        return EffectivePrice(amount=sum((f.amount for f in known), Decimal(0)), unit=known[0].unit, source=source)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate total cost by summing all price components.

        Args:
            usage: Usage data passed to each component
            customer_charge: Customer charge passed to each component
            request_count: Number of requests passed to each component

        Returns:
            Sum of all component costs
        """
        total = Decimal("0")
        for price_data in self.prices:
            component = validate_pricing(price_data)
            total += component.calculate_cost(usage, customer_charge, request_count, channel)
        return total


class MultiplyPriceData(BasePriceData):
    """
    Composite pricing that multiplies a base price by a factor.

    Useful for applying percentage-based adjustments to a base price.

    Example:
        {
            "type": "multiply",
            "factor": "0.70",
            "base": {"type": "one_million_tokens", "input": "0.50", "output": "1.50"}
        }
    """

    type: Literal["multiply"] = "multiply"

    factor: PriceStr = Field(
        description="Multiplication factor (e.g., '0.70' for 70%)",
    )

    base: dict[str, Any] = Field(
        description="Base pricing to multiply",
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> MultiplyPriceData:
        """Auto-compute nominal price as base price × factor if not set."""
        if self.price is None:
            base_price = _extract_nominal_price(self.base)
            if base_price is not None:
                try:
                    self.price = str(Decimal(base_price) * Decimal(self.factor))
                except Exception:
                    self.price = base_price
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The base's figure times ``factor``."""
        base = _child_effective_price(self.base)
        if base is None:
            return None
        return base.model_copy(update={"amount": base.amount * Decimal(self.factor)})

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost by multiplying base price by factor.

        Args:
            usage: Usage data passed to base component
            customer_charge: Customer charge passed to base component
            request_count: Number of requests passed to base component

        Returns:
            Base cost multiplied by factor
        """
        base_pricing = validate_pricing(self.base)
        base_cost = base_pricing.calculate_cost(usage, customer_charge, request_count, channel)
        return base_cost * Decimal(self.factor)


class MaxPriceData(BasePriceData):
    """
    Composite pricing that charges the **maximum** of multiple pricing calculations.

    Lenient: children that raise ``ValueError`` (e.g., missing usage metric)
    are silently skipped.  At least one child must succeed.

    Example — charge per-count OR per-duration, whichever is higher:

        {
            "type": "max",
            "prices": [
                {"type": "one_second", "price": "0.01"},
                {"type": "constant", "price": "0.50"}
            ]
        }
    """

    type: Literal["max"] = "max"

    prices: list[dict[str, Any]] = Field(
        description="List of pricing components — the highest result is used",
        min_length=1,
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> MaxPriceData:
        """Auto-compute nominal price from the first child if not set."""
        if self.price is None and self.prices:
            self.price = _extract_nominal_price(self.prices[0])
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The largest child, as billed."""
        return _largest(_child_effective_price(p) for p in self.prices)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Return the highest cost among applicable children.

        Children that raise ``ValueError`` are skipped.  If no child can
        produce a cost, ``ValueError`` is raised.
        """
        costs: list[Decimal] = []
        for price_data in self.prices:
            try:
                component = validate_pricing(price_data)
                costs.append(component.calculate_cost(usage, customer_charge, request_count, channel))
            except ValueError:
                continue
        if not costs:
            raise ValueError("No child pricing could handle the provided usage data")
        return max(costs)


class MinPriceData(BasePriceData):
    """
    Composite pricing that charges the **minimum** of multiple pricing calculations.

    Useful for price caps — charge usage-based pricing but never more than a
    flat rate.

    Lenient: children that raise ``ValueError`` are silently skipped.

    Example — usage-based pricing capped at $100:

        {
            "type": "min",
            "prices": [
                {"type": "one_second", "price": "0.10"},
                {"type": "constant", "price": "100.00"}
            ]
        }
    """

    type: Literal["min"] = "min"

    prices: list[dict[str, Any]] = Field(
        description="List of pricing components — the lowest result is used",
        min_length=1,
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> MinPriceData:
        """Auto-compute nominal price from the first child if not set."""
        if self.price is None and self.prices:
            self.price = _extract_nominal_price(self.prices[0])
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The smallest child, as billed."""
        return _smallest(_child_effective_price(p) for p in self.prices)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Return the lowest cost among applicable children.

        Children that raise ``ValueError`` are skipped.  If no child can
        produce a cost, ``ValueError`` is raised.
        """
        costs: list[Decimal] = []
        for price_data in self.prices:
            try:
                component = validate_pricing(price_data)
                costs.append(component.calculate_cost(usage, customer_charge, request_count, channel))
            except ValueError:
                continue
        if not costs:
            raise ValueError("No child pricing could handle the provided usage data")
        return min(costs)


class FirstPriceData(BasePriceData):
    """
    Composite pricing that returns the cost from the **first** applicable child.

    Tries each child in order.  The first one that doesn't raise ``ValueError``
    wins.  Useful when pricing should adapt to whatever metric the caller
    provides (duration OR count, etc.).

    Example — charge by duration if available, otherwise by count:

        {
            "type": "first",
            "prices": [
                {"type": "one_second", "price": "0.01"},
                {"type": "image", "price": "0.05"}
            ]
        }
    """

    type: Literal["first"] = "first"

    prices: list[dict[str, Any]] = Field(
        description="List of pricing components — the first successful result is used",
        min_length=1,
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> FirstPriceData:
        """Auto-compute nominal price from the first child if not set."""
        if self.price is None and self.prices:
            self.price = _extract_nominal_price(self.prices[0])
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The first child with a known figure, mirroring the billing order."""
        for price_data in self.prices:
            figure = _child_effective_price(price_data)
            if figure is not None:
                return figure
        return None

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Return the cost from the first child that can handle the usage.

        Children are tried in order.  The first one that doesn't raise
        ``ValueError`` wins.  If none can produce a cost, ``ValueError``
        is raised.
        """
        for price_data in self.prices:
            try:
                component = validate_pricing(price_data)
                return component.calculate_cost(usage, customer_charge, request_count, channel)
            except ValueError:
                continue
        raise ValueError("No child pricing could handle the provided usage data")


# ---------------------------------------------------------------------------
# Equivalence groups — units that measure the same dimension and can
# convert to each other.  Each group maps unit names to a factor relative
# to the group's base unit.
# ---------------------------------------------------------------------------

EQUIVALENCE_GROUPS: dict[str, dict[str, Decimal]] = {
    "time": {
        "seconds": Decimal(1),
        "duration_seconds": Decimal(1),  # what usage_event.lua ships
        "one_second": Decimal(1),
        "one_minute": Decimal(60),
        "one_hour": Decimal(3600),
        "one_day": Decimal(86400),
        "one_month": Decimal(2592000),  # 30 days
    },
    "tokens": {
        "one_token": Decimal(1),
        "one_thousand_tokens": Decimal(1000),
        "one_million_tokens": Decimal(1_000_000),
    },
    "data": {
        "one_byte": Decimal(1),
        "one_kilobyte": Decimal(1024),
        "one_megabyte": Decimal(1_048_576),
        "one_gigabyte": Decimal(1_073_741_824),
    },
    "count": {
        "count": Decimal(1),
        "images_generated": Decimal(1),  # what usage_event.lua ships
        "one_thousand": Decimal(1000),
        "one_million": Decimal(1_000_000),
    },
    "characters": {
        "characters": Decimal(1),
        "one_character": Decimal(1),
        "one_thousand_characters": Decimal(1000),
        "one_million_characters": Decimal(1_000_000),
    },
}

# Reverse lookup: unit_name → (group_name, base_factor)
_UNIT_TO_GROUP: dict[str, tuple[str, Decimal]] = {
    unit: (group, factor) for group, units in EQUIVALENCE_GROUPS.items() for unit, factor in units.items()
}


def _resolve_equivalent_metric(
    metric: str,
    usage: UsageData,
) -> Decimal | None:
    """Resolve a metric via equivalence-group conversion.

    If *metric* belongs to an equivalence group, find any populated field
    from the same group in *usage* and convert to *metric*'s unit.

    Returns ``None`` if *metric* is not in any group or no matching field
    is populated.
    """
    if metric not in _UNIT_TO_GROUP:
        return None

    target_group, target_factor = _UNIT_TO_GROUP[metric]

    # Direct match — no conversion
    direct = getattr(usage, metric, None)
    if direct is not None:
        return Decimal(str(direct))

    # Cross-unit: find any populated field in the same group
    for unit, source_factor in EQUIVALENCE_GROUPS[target_group].items():
        val = getattr(usage, unit, None)
        if val is not None:
            return Decimal(str(val)) * source_factor / target_factor

    return None


def _get_metric_value(
    based_on: str,
    usage: UsageData,
    customer_charge: Decimal | None,
    request_count: int | None,
) -> Decimal:
    """Get the value of a metric by name.

    Resolution order:
    1. Special parameters (request_count, customer_charge)
    2. Equivalence-group conversion (time units, etc.)
    3. Direct UsageData field lookup
    4. Safe arithmetic expression evaluation

    Args:
        based_on: Name of the metric (e.g., 'request_count', 'customer_charge',
            'one_minute', or any UsageData field / arithmetic expression)
        usage: Usage data object
        customer_charge: Customer charge value
        request_count: Request count value

    Returns:
        The metric value as Decimal
    """
    # Check special parameters first
    if based_on == "request_count":
        return Decimal(request_count or 0)
    elif based_on == "customer_charge":
        return customer_charge or Decimal("0")

    # Equivalence-group conversion (e.g., based_on="one_minute" with usage.one_hour)
    if based_on in _UNIT_TO_GROUP:
        equiv = _resolve_equivalent_metric(based_on, usage)
        if equiv is not None:
            return equiv
        group_name = _UNIT_TO_GROUP[based_on][0]
        raise ValueError(
            f"Metric '{based_on}' requires {group_name} data in usage "
            f"(one of: {', '.join(EQUIVALENCE_GROUPS[group_name])})"
        )

    # Try to get from UsageData fields
    if hasattr(usage, based_on):
        value = getattr(usage, based_on)
        if value is not None:
            return Decimal(str(value))

    # Build context with all available metrics
    context: dict[str, Decimal] = {
        "request_count": Decimal(request_count or 0),
        "customer_charge": customer_charge or Decimal("0"),
    }

    # Add all UsageData fields
    for field_name in UsageData.model_fields:
        value = getattr(usage, field_name)
        context[field_name] = Decimal(str(value)) if value is not None else Decimal("0")

    try:
        tree = ast.parse(based_on, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {based_on}") from e

    binary_ops: dict[type[ast.operator], Any] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }
    unary_ops: dict[type[ast.unaryop], Any] = {
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def safe_eval(node: ast.expr) -> Decimal:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int | float):
                return Decimal(str(node.value))
            raise ValueError(f"Unsupported constant type: {type(node.value)}")
        elif isinstance(node, ast.Name):
            if node.id not in context:
                raise ValueError(f"Unknown metric: {node.id}")
            return context[node.id]
        elif isinstance(node, ast.BinOp):
            bin_op_type = type(node.op)
            if bin_op_type not in binary_ops:
                raise ValueError(f"Unsupported operator: {bin_op_type.__name__}")
            return binary_ops[bin_op_type](safe_eval(node.left), safe_eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            unary_op_type = type(node.op)
            if unary_op_type not in unary_ops:
                raise ValueError(f"Unsupported unary operator: {unary_op_type.__name__}")
            return unary_ops[unary_op_type](safe_eval(node.operand))
        else:
            raise ValueError(f"Unsupported expression type: {type(node).__name__}")

    return safe_eval(tree.body)


class ExprPriceData(BasePriceData):
    """
    Expression-based pricing that evaluates an arithmetic expression using usage metrics.

    **IMPORTANT: This pricing type should only be used for `payout_price`.**
    It is NOT suitable for `list_price` because:
    1. List pricing should be predictable and transparent
    2. Expression-based pricing can lead to confusing or unexpected charges
    3. Customers should be able to easily calculate their costs before using a service

    For payout pricing, expressions are useful when the cost from an upstream provider
    involves complex calculations that can't be expressed with basic pricing types.

    The expression can use any available metrics and arithmetic operators (+, -, *, /).

    Available metrics:
    - input_tokens, output_tokens, total_tokens (token counts)
    - seconds (time-based usage)
    - count (images, steps, etc.)
    - request_count (number of API requests)
    - customer_charge (what the customer paid, for revenue share calculations)

    Example:
        {
            "type": "expr",
            "expr": "input_tokens / 1000000 * 0.50 + output_tokens / 1000000 * 1.50"
        }
    """

    type: Literal["expr"] = "expr"

    expr: str = Field(
        description="Arithmetic expression using usage metrics (e.g., 'input_tokens / 1000000 * 2.5')",
    )

    def effective_price(self) -> EffectivePrice | None:
        """An arbitrary expression has no per-unit rate: unknown."""
        return None

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost by evaluating the expression with usage data.

        Args:
            usage: Usage data providing metric values
            customer_charge: Customer charge value (available as 'customer_charge' in expression)
            request_count: Number of requests (available as 'request_count' in expression)

        Returns:
            The result of evaluating the expression
        """
        return _get_metric_value(self.expr, usage, customer_charge, request_count)


class PriceTier(BaseModel):
    """A single tier in tiered pricing."""

    model_config = ConfigDict(extra="forbid")

    up_to: int | None = Field(
        description="Upper limit for this tier (None for unlimited)",
    )
    price: dict[str, Any] = Field(
        description="Price configuration for this tier",
    )


class TieredPriceData(BasePriceData):
    """
    Volume-based tiered pricing where the tier determines price for ALL units.

    The tier is determined by the `based_on` metric, and ALL units are priced
    at that tier's rate. `based_on` can be 'request_count', 'customer_charge',
    or any field from UsageData (e.g., 'input_tokens', 'seconds', 'count').

    Example (volume pricing - all units at same rate):
        {
            "type": "tiered",
            "based_on": "request_count",
            "tiers": [
                {"up_to": 1000, "price": {"type": "constant", "amount": "10.00"}},
                {"up_to": 10000, "price": {"type": "constant", "amount": "80.00"}},
                {"up_to": null, "price": {"type": "constant", "amount": "500.00"}}
            ]
        }
    If request_count is 5000, the price is $80.00 (falls in 1001-10000 tier).
    """

    type: Literal["tiered"] = "tiered"

    based_on: str = Field(
        description="Metric for tier selection: 'request_count', 'customer_charge', or UsageData field",
    )

    tiers: list[PriceTier] = Field(
        description="List of tiers, ordered by up_to (ascending). Last tier should have up_to=null.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> TieredPriceData:
        """Auto-compute nominal price from the first tier's price if not set."""
        if self.price is None and self.tiers:
            first_tier = self.tiers[0]
            if isinstance(first_tier.price, dict):
                self.price = _extract_nominal_price(first_tier.price)
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The largest tier's figure: the worst case a customer can pay."""
        return _largest(_child_effective_price(tier.price) for tier in self.tiers)

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost based on which tier the usage falls into.

        Args:
            usage: Usage data
            customer_charge: Customer charge (used if based_on="customer_charge")
            request_count: Number of requests (used if based_on="request_count")

        Returns:
            Cost from the matching tier's price
        """
        metric_value = _get_metric_value(self.based_on, usage, customer_charge, request_count)

        # Find the matching tier
        for tier in self.tiers:
            if tier.up_to is None or metric_value <= tier.up_to:
                tier_pricing = validate_pricing(tier.price)
                return tier_pricing.calculate_cost(usage, customer_charge, request_count, channel)

        # Should not reach here if tiers are properly configured
        raise ValueError("No matching tier found")


class GraduatedTier(BaseModel):
    """A single tier in graduated pricing with per-unit price."""

    model_config = ConfigDict(extra="forbid")

    up_to: int | None = Field(
        description="Upper limit for this tier (None for unlimited)",
    )
    unit_price: PriceStr = Field(
        description="Price per unit in this tier",
    )


class GraduatedPriceData(BasePriceData):
    """
    Graduated tiered pricing where each tier's units are priced at that tier's rate.

    Like AWS pricing - first N units at price A, next M units at price B, etc.
    `based_on` can be 'request_count', 'customer_charge', or any UsageData field.

    Example (graduated pricing - different rates per tier):
        {
            "type": "graduated",
            "based_on": "request_count",
            "tiers": [
                {"up_to": 1000, "unit_price": "0.01"},
                {"up_to": 10000, "unit_price": "0.008"},
                {"up_to": null, "unit_price": "0.005"}
            ]
        }
    If request_count is 5000:
        - First 1000 at $0.01 = $10.00
        - Next 4000 at $0.008 = $32.00
        - Total = $42.00
    """

    type: Literal["graduated"] = "graduated"

    based_on: str = Field(
        description="Metric for graduated calc: 'request_count', 'customer_charge', or UsageData field",
    )

    tiers: list[GraduatedTier] = Field(
        description="List of tiers, ordered by up_to (ascending). Last tier should have up_to=null.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _auto_compute_price(self) -> GraduatedPriceData:
        """Auto-compute nominal price from the first tier's unit_price if not set."""
        if self.price is None and self.tiers:
            self.price = self.tiers[0].unit_price
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The largest ``unit_price`` (the worst case), per ``based_on``'s normalized unit.

        Unknown when ``based_on`` has no unit (``customer_charge``, an expression).
        """
        return _per_normalized_unit(self.based_on, max(Decimal(tier.unit_price) for tier in self.tiers))

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Calculate cost with graduated pricing across tiers.

        Args:
            usage: Usage data
            customer_charge: Customer charge (used if based_on="customer_charge")
            request_count: Number of requests (used if based_on="request_count")

        Returns:
            Total cost summed across all applicable tiers
        """
        metric_value = _get_metric_value(self.based_on, usage, customer_charge, request_count)
        total_cost = Decimal("0")
        remaining = metric_value
        previous_limit = Decimal("0")

        for tier in self.tiers:
            if remaining <= 0:
                break

            # Calculate units in this tier
            if tier.up_to is None:
                units_in_tier = remaining
            else:
                tier_size = Decimal(tier.up_to) - previous_limit
                units_in_tier = min(remaining, tier_size)

            # Add cost for this tier
            total_cost += units_in_tier * Decimal(tier.unit_price)
            remaining -= units_in_tier
            previous_limit = Decimal(tier.up_to) if tier.up_to else previous_limit

        return total_cost


class ChannelPriceData(BasePriceData):
    """
    Channel-based pricing that selects a sub-price by the request's resolved channel.

    The active channel is decided elsewhere — e.g. the gateway picks which keyed
    ``upstream_access_config`` (upstream access channel) a request routes through
    (``"managed"``, ``"byok"``, ``"byoe"``, …) — and threads it into
    ``calculate_cost(..., channel=...)``. The matching entry in ``channels`` is
    evaluated; when the channel is unknown or unset, the ``default`` channel is used.

    There is no special-casing on the consumer side: every pricing type accepts
    an optional ``channel`` and a channel-independent price simply ignores it, so a
    plain price behaves identically for all channels. Channel-dependent pricing is
    just this one extra optional pricing type.

    Each ``channels`` value is itself any pricing object (flat, composite, tiered,
    or even nested channel-based), validated lazily — mirroring how tiered pricing
    holds per-tier prices.

    Example:
        {
            "type": "channel",
            "default": "managed",
            "channels": {
                "managed": {"type": "one_million_tokens", "input": "0.50", "output": "1.50"},
                "byok": {"type": "constant", "price": "0"}
            }
        }
    """

    type: Literal["channel"] = "channel"

    default: str = Field(
        description="Channel name evaluated when the request's channel is unknown or unset. "
        "Must be one of the keys in `channels`.",
    )

    channels: dict[str, dict[str, Any]] = Field(
        description="Per-channel pricing keyed by channel name (e.g. 'managed', 'byok'). "
        "Each value is a nested pricing object of any type.",
        min_length=1,
    )

    @model_validator(mode="after")
    def _validate_default_and_price(self) -> ChannelPriceData:
        """Require ``default`` to exist and, when the seller set no summary
        ``price``, auto-compute the nominal as the HIGHEST member price.

        The nominal is a marketplace-comparison summary. Deriving it from the
        ``default`` channel is misleading whenever the default is a *free*
        channel — a common pattern is a free ``byok``/``apprise`` default plus a
        paid ``…-plus`` channel, where the default's price is ``0`` and the
        summary would read as "Free", hiding the paid channels. The highest
        member price is the honest representative, consistent with how tiered /
        graduated derive their nominal from their headline tier.
        """
        if self.default not in self.channels:
            raise ValueError(f"default channel {self.default!r} is not defined in channels ({sorted(self.channels)})")
        priced: list[tuple[Decimal, str]] = []
        for child in self.channels.values():
            nominal = _extract_nominal_price(child)
            if nominal is None:
                continue
            try:
                priced.append((Decimal(nominal), nominal))
            except (InvalidOperation, TypeError):
                continue
        if priced:
            lo_num, lo_str = min(priced, key=lambda p: p[0])
            hi_num, hi_str = max(priced, key=lambda p: p[0])
            # Nominal summary = the HIGHEST member price (honest representative).
            if self.price is None:
                self.price = hi_str
            # When the seller wrote no description and the channels span a price
            # range, describe that range ("Free - $0.001") so the marketplace
            # doesn't collapse a mixed free/paid offering to a single figure.
            if self.description is None and lo_num != hi_num:
                lo = "Free" if lo_num == 0 else f"${lo_str}"
                hi = "Free" if hi_num == 0 else f"${hi_str}"
                self.description = f"{lo} - {hi}"
        return self

    def effective_price(self) -> EffectivePrice | None:
        """The largest figure among all channels: the worst case a customer can pay."""
        return _largest(_child_effective_price(p) for p in self.channels.values())

    def calculate_cost(
        self,
        usage: UsageData,
        customer_charge: Decimal | None = None,
        request_count: int | None = None,
        channel: str | None = None,
    ) -> Decimal:
        """Evaluate the price for the resolved ``channel`` (falling back to ``default``).

        Args:
            usage: Usage data passed to the selected channel's price.
            customer_charge: Customer charge passed through.
            request_count: Request count passed through.
            channel: The resolved channel; unknown/``None`` falls back to ``default``.

        Returns:
            Cost from the selected channel's pricing.
        """
        selected = self.channels.get(channel) if channel is not None else None
        if selected is None:
            selected = self.channels[self.default]
        pricing = validate_pricing(selected)
        return pricing.calculate_cost(usage, customer_charge, request_count, channel)


# Discriminated union of all pricing types
# This is the type used for payout_price and list_price fields
# Note: ExprPriceData should only be used for payout_price
Pricing = Annotated[
    TokenPriceData
    | TimePriceData
    | DataPriceData
    | CountPriceData
    | CharacterPriceData
    | ImagePriceData
    | StepPriceData
    | RevenueSharePriceData
    | ConstantPriceData
    | AddPriceData
    | MultiplyPriceData
    | MaxPriceData
    | MinPriceData
    | FirstPriceData
    | TieredPriceData
    | GraduatedPriceData
    | ExprPriceData
    | ChannelPriceData,
    Field(discriminator="type"),
]


def validate_pricing(
    data: dict[str, Any],
) -> (
    TokenPriceData
    | TimePriceData
    | DataPriceData
    | CountPriceData
    | CharacterPriceData
    | ImagePriceData
    | StepPriceData
    | RevenueSharePriceData
    | ConstantPriceData
    | AddPriceData
    | MultiplyPriceData
    | MaxPriceData
    | MinPriceData
    | FirstPriceData
    | TieredPriceData
    | GraduatedPriceData
    | ExprPriceData
    | ChannelPriceData
):
    """
    Validate pricing dict and return the appropriate typed model.

    Args:
        data: Dictionary containing pricing data with 'type' field

    Returns:
        Validated Pricing model instance

    Raises:
        ValueError: If validation fails

    Example:
        >>> data = {"type": "one_million_tokens", "input": "0.5", "output": "1.5"}
        >>> validated = validate_pricing(data)
        >>> print(validated.input)  # "0.5"
    """
    from pydantic import TypeAdapter

    adapter: TypeAdapter[TokenPriceData | TimePriceData | ImagePriceData | StepPriceData | RevenueSharePriceData] = (
        TypeAdapter(Pricing)
    )
    return adapter.validate_python(data)

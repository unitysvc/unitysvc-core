"""Tests for pricing models and calculate_cost methods."""

from decimal import Decimal

import pytest

from unitysvc_core.models.pricing import UsageData, validate_pricing


class TestExprPriceData:
    """Tests for expression-based pricing (payout_price only)."""

    def test_simple_constant_expression(self) -> None:
        """Test simple constant expression."""
        pricing = validate_pricing({"type": "expr", "expr": "5.00"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("5.00")

    def test_negative_constant_expression(self) -> None:
        """Test negative constant (discount) expression."""
        pricing = validate_pricing({"type": "expr", "expr": "-10.00"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("-10.00")

    def test_simple_token_expression(self) -> None:
        """Test simple token-based expression."""
        pricing = validate_pricing({"type": "expr", "expr": "input_tokens / 1000000 * 2.5"})
        usage = UsageData(input_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("2.5")

    def test_weighted_tokens_expression(self) -> None:
        """Test expression with different weights for input/output."""
        pricing = validate_pricing(
            {"type": "expr", "expr": "input_tokens / 1000000 * 0.5 + output_tokens / 1000000 * 1.5"}
        )
        usage = UsageData(input_tokens=2_000_000, output_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        # 2M * 0.5 + 1M * 1.5 = 1.0 + 1.5 = 2.5
        assert cost == Decimal("2.5")

    def test_time_based_expression(self) -> None:
        """Test time-based expression."""
        pricing = validate_pricing({"type": "expr", "expr": "seconds * 0.01"})
        usage = UsageData(seconds=120.5)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.205")

    def test_count_based_expression(self) -> None:
        """Test count-based expression."""
        pricing = validate_pricing({"type": "expr", "expr": "count * 0.05"})
        usage = UsageData(count=100)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("5.00")

    def test_revenue_share_expression(self) -> None:
        """Test revenue share as expression."""
        pricing = validate_pricing({"type": "expr", "expr": "customer_charge * 0.70"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage, customer_charge=Decimal("100.00"))

        assert cost == Decimal("70.00")

    def test_complex_expression_with_parentheses(self) -> None:
        """Test complex expression with multiple metrics and parentheses."""
        pricing = validate_pricing({"type": "expr", "expr": "(input_tokens + output_tokens) / 1000000 * 3"})
        usage = UsageData(input_tokens=500_000, output_tokens=500_000)

        cost = pricing.calculate_cost(usage)

        # (500000 + 500000) / 1000000 * 3 = 1 * 3 = 3
        assert cost == Decimal("3")

    def test_expression_with_request_count(self) -> None:
        """Test expression using request_count."""
        pricing = validate_pricing({"type": "expr", "expr": "request_count * 0.001"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=5000)

        assert cost == Decimal("5")


class TestConstantPriceData:
    """Tests for constant pricing."""

    def test_simple_constant(self) -> None:
        """Test simple constant amount."""
        pricing = validate_pricing({"type": "constant", "price": "5.00"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("5.00")

    def test_negative_constant(self) -> None:
        """Test negative constant (discount)."""
        pricing = validate_pricing({"type": "constant", "price": "-10.00"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("-10.00")


class TestTokenPriceData:
    """Tests for token-based pricing."""

    def test_unified_token_pricing(self) -> None:
        """Test unified token pricing."""
        pricing = validate_pricing({"type": "one_million_tokens", "price": "2.50"})
        usage = UsageData(input_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("2.50")

    def test_separate_input_output_pricing(self) -> None:
        """Test separate input/output token pricing."""
        pricing = validate_pricing({"type": "one_million_tokens", "input": "0.50", "output": "1.50"})
        usage = UsageData(input_tokens=2_000_000, output_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        # 2M * $0.50 + 1M * $1.50 = $1.00 + $1.50 = $2.50
        assert cost == Decimal("2.50")


class TestTokenPriceDataUnits:
    """Tests for TokenPriceData with one_token and one_thousand_tokens types."""

    def test_one_thousand_tokens_unified(self) -> None:
        """Test per-thousand-token unified pricing."""
        pricing = validate_pricing({"type": "one_thousand_tokens", "price": "0.003"})
        usage = UsageData(input_tokens=5000)

        cost = pricing.calculate_cost(usage)

        # 5000 / 1000 * $0.003 = $0.015
        assert cost == Decimal("0.015")

    def test_one_thousand_tokens_split(self) -> None:
        """Test per-thousand-token split input/output pricing."""
        pricing = validate_pricing(
            {"type": "one_thousand_tokens", "input": "0.001", "output": "0.003"}
        )
        usage = UsageData(input_tokens=10_000, output_tokens=5_000)

        cost = pricing.calculate_cost(usage)

        # 10000/1000 * $0.001 + 5000/1000 * $0.003 = $0.01 + $0.015 = $0.025
        assert cost == Decimal("0.025")

    def test_one_token_unified(self) -> None:
        """Test per-token unified pricing."""
        pricing = validate_pricing({"type": "one_token", "price": "0.000001"})
        usage = UsageData(input_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        # 1_000_000 / 1 * $0.000001 = $1.00
        assert cost == Decimal("1.000000")

    def test_one_token_split(self) -> None:
        """Test per-token split input/output pricing."""
        pricing = validate_pricing(
            {"type": "one_token", "input": "0.0001", "output": "0.0003"}
        )
        usage = UsageData(input_tokens=100, output_tokens=200)

        cost = pricing.calculate_cost(usage)

        # 100 * $0.0001 + 200 * $0.0003 = $0.01 + $0.06 = $0.07
        assert cost == Decimal("0.0700")

    def test_backward_compat_one_million(self) -> None:
        """Existing one_million_tokens pricing still works unchanged."""
        pricing = validate_pricing({"type": "one_million_tokens", "price": "3.00"})
        usage = UsageData(input_tokens=500_000)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.50")


class TestDataPriceData:
    """Tests for data-volume pricing."""

    def test_per_megabyte_pricing(self) -> None:
        """Test per-MB pricing with usage in MB."""
        pricing = validate_pricing({"type": "one_megabyte", "price": "0.01"})
        usage = UsageData(one_megabyte=100)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.00")

    def test_per_gigabyte_pricing(self) -> None:
        """Test per-GB pricing with usage in GB."""
        pricing = validate_pricing({"type": "one_gigabyte", "price": "0.10"})
        usage = UsageData(one_gigabyte=50)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("5.0")

    def test_cross_unit_kb_to_gb(self) -> None:
        """Pricing in GB, usage in KB — auto-converts."""
        pricing = validate_pricing({"type": "one_gigabyte", "price": "0.10"})
        # 1 GB = 1048576 KB
        usage = UsageData(one_kilobyte=1048576)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("0.10")

    def test_cross_unit_bytes_to_mb(self) -> None:
        """Pricing in MB, usage in bytes — auto-converts."""
        pricing = validate_pricing({"type": "one_megabyte", "price": "0.005"})
        # 10 MB = 10485760 bytes
        usage = UsageData(one_byte=10485760)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("0.050")

    def test_no_data_field_raises(self) -> None:
        """Missing data field raises ValueError."""
        pricing = validate_pricing({"type": "one_megabyte", "price": "0.01"})
        usage = UsageData(count=100)

        import pytest

        with pytest.raises(ValueError, match="data field"):
            pricing.calculate_cost(usage)


class TestCountPriceData:
    """Tests for count-scaled pricing."""

    def test_per_thousand_pricing(self) -> None:
        """Test per-thousand pricing with count usage."""
        pricing = validate_pricing({"type": "one_thousand", "price": "0.50"})
        usage = UsageData(count=5000)

        cost = pricing.calculate_cost(usage)

        # 5000 / 1000 * $0.50 = $2.50
        assert cost == Decimal("2.50")

    def test_per_million_pricing(self) -> None:
        """Test per-million pricing."""
        pricing = validate_pricing({"type": "one_million", "price": "1.00"})
        usage = UsageData(one_million=3)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("3.00")

    def test_cross_unit_count_to_million(self) -> None:
        """Pricing in millions, usage as raw count — auto-converts."""
        pricing = validate_pricing({"type": "one_million", "price": "2.00"})
        usage = UsageData(count=500_000)

        cost = pricing.calculate_cost(usage)

        # 500_000 / 1_000_000 * $2.00 = $1.00
        assert cost == Decimal("1.00")

    def test_cross_unit_one_thousand_to_million(self) -> None:
        """Pricing in millions, usage in thousands — auto-converts."""
        pricing = validate_pricing({"type": "one_million", "price": "10.00"})
        usage = UsageData(one_thousand=2000)

        cost = pricing.calculate_cost(usage)

        # 2000 * 1000 / 1_000_000 = 2 million → $20.00
        assert cost == Decimal("20.00")

    def test_no_count_field_raises(self) -> None:
        """Missing count field raises ValueError."""
        pricing = validate_pricing({"type": "one_thousand", "price": "0.50"})
        usage = UsageData(one_hour=5)

        import pytest

        with pytest.raises(ValueError, match="count field"):
            pricing.calculate_cost(usage)


class TestTimePriceData:
    """Tests for time-based pricing."""

    def test_time_based_pricing(self) -> None:
        """Test time-based pricing."""
        pricing = validate_pricing({"type": "one_second", "price": "0.01"})
        usage = UsageData(seconds=120.5)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.205")

    def test_one_minute_pricing(self) -> None:
        """Test per-minute pricing with usage in minutes."""
        pricing = validate_pricing({"type": "one_minute", "price": "0.10"})
        usage = UsageData(one_minute=30)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("3.0")

    def test_one_hour_pricing(self) -> None:
        """Test per-hour pricing with usage in hours."""
        pricing = validate_pricing({"type": "one_hour", "price": "5.00"})
        usage = UsageData(one_hour=2.5)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("12.50")

    def test_one_month_pricing(self) -> None:
        """Test per-month pricing."""
        pricing = validate_pricing({"type": "one_month", "price": "1.00"})
        usage = UsageData(one_month=1)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.00")

    def test_cross_unit_seconds_to_minutes(self) -> None:
        """Test cross-unit conversion: usage in seconds, price in minutes."""
        pricing = validate_pricing({"type": "one_minute", "price": "0.60"})
        usage = UsageData(seconds=120)  # 2 minutes

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.20")

    def test_cross_unit_hours_to_minutes(self) -> None:
        """Test cross-unit conversion: usage in hours, price in minutes."""
        pricing = validate_pricing({"type": "one_minute", "price": "0.01"})
        usage = UsageData(one_hour=2)  # 120 minutes

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("1.20")

    def test_cross_unit_seconds_to_months(self) -> None:
        """Test cross-unit conversion: usage in seconds, price in months."""
        pricing = validate_pricing({"type": "one_month", "price": "30.00"})
        # Half a month = 2592000/2 = 1296000 seconds
        usage = UsageData(one_second=1296000)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("15.00")

    def test_cross_unit_days_to_hours(self) -> None:
        """Test cross-unit conversion: usage in days, price in hours."""
        pricing = validate_pricing({"type": "one_hour", "price": "1.00"})
        usage = UsageData(one_day=1)  # 24 hours

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("24")

    def test_backward_compat_seconds_field(self) -> None:
        """Test that the old 'seconds' field still works with all time pricing."""
        pricing = validate_pricing({"type": "one_hour", "price": "10.00"})
        usage = UsageData(seconds=7200)  # 2 hours

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("20.00")

    def test_no_time_data_raises(self) -> None:
        """Test that missing time data raises ValueError."""
        pricing = validate_pricing({"type": "one_minute", "price": "1.00"})
        usage = UsageData(count=5)  # no time fields

        import pytest

        with pytest.raises(ValueError, match="time field"):
            pricing.calculate_cost(usage)


class TestTimePricingInTiered:
    """Tests for time units in tiered/graduated pricing."""

    def test_tiered_based_on_minutes_usage_in_hours(self) -> None:
        """Tiered pricing with based_on=one_minute, usage in hours."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_minute",
                "tiers": [
                    {"up_to": 60, "price": {"type": "constant", "price": "0"}},
                    {"up_to": None, "price": {"type": "constant", "price": "10.00"}},
                ],
            }
        )
        # 2 hours = 120 minutes → exceeds 60-minute tier
        usage = UsageData(one_hour=2)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("10.00")

    def test_tiered_based_on_minutes_usage_in_seconds(self) -> None:
        """Tiered pricing with based_on=one_minute, usage in seconds."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_minute",
                "tiers": [
                    {"up_to": 100, "price": {"type": "constant", "price": "5.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "20.00"}},
                ],
            }
        )
        # 3000 seconds = 50 minutes → fits in first tier
        usage = UsageData(seconds=3000)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_graduated_based_on_minutes_usage_in_hours(self) -> None:
        """Graduated pricing with based_on=one_minute, usage in hours."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_minute",
                "tiers": [
                    {"up_to": 60, "unit_price": "0"},
                    {"up_to": None, "unit_price": "0.10"},
                ],
            }
        )
        # 2 hours = 120 minutes → first 60 free, next 60 at $0.10
        usage = UsageData(one_hour=2)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("6.0")

    def test_tiered_with_nested_time_pricing(self) -> None:
        """Tiered pricing in minutes, with nested time pricing in hours."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_minute",
                "tiers": [
                    {"up_to": 60, "price": {"type": "constant", "price": "0"}},
                    {"up_to": None, "price": {"type": "one_hour", "price": "1.00"}},
                ],
            }
        )
        # 7200 seconds = 120 minutes → tier 2, price per hour, 2 hours
        usage = UsageData(seconds=7200)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("2")


class TestTokenEquivalenceGroup:
    """Tests for token equivalence group cross-unit conversion."""

    def test_graduated_based_on_one_thousand_tokens_usage_in_one_token(self) -> None:
        """Graduated pricing in thousands, usage in raw tokens."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_thousand_tokens",
                "tiers": [
                    {"up_to": 10, "unit_price": "0"},
                    {"up_to": None, "unit_price": "0.05"},
                ],
            }
        )
        # 20_000 tokens = 20 thousand → first 10k free, next 10k at $0.05
        usage = UsageData(one_token=20_000)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("0.50")

    def test_tiered_based_on_one_million_tokens_usage_in_one_thousand(self) -> None:
        """Tiered pricing in millions, usage in thousands."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_million_tokens",
                "tiers": [
                    {"up_to": 1, "price": {"type": "constant", "price": "5.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "20.00"}},
                ],
            }
        )
        # 500 thousand = 0.5 million → fits in first tier
        usage = UsageData(one_thousand_tokens=500)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_cross_unit_one_million_to_one_token(self) -> None:
        """Usage in millions, pricing based_on one_token."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_token",
                "tiers": [
                    {"up_to": 1_000_000, "unit_price": "0"},
                    {"up_to": None, "unit_price": "0.000001"},
                ],
            }
        )
        # 2 million tokens → first 1M free, next 1M at $0.000001 each
        usage = UsageData(one_million_tokens=2)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("1.000000")


class TestDataEquivalenceGroup:
    """Tests for data equivalence group cross-unit conversion."""

    def test_graduated_based_on_megabytes_usage_in_gigabytes(self) -> None:
        """Graduated pricing in MB, usage in GB."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_megabyte",
                "tiers": [
                    {"up_to": 1024, "unit_price": "0"},  # first 1 GB free
                    {"up_to": None, "unit_price": "0.01"},
                ],
            }
        )
        # 2 GB = 2048 MB → first 1024 free, next 1024 at $0.01
        usage = UsageData(one_gigabyte=2)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("10.24")

    def test_tiered_based_on_gigabytes_usage_in_bytes(self) -> None:
        """Tiered pricing in GB, usage in bytes."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_gigabyte",
                "tiers": [
                    {"up_to": 1, "price": {"type": "constant", "price": "0"}},
                    {"up_to": None, "price": {"type": "constant", "price": "5.00"}},
                ],
            }
        )
        # 512 MB in bytes = 536870912 bytes = 0.5 GB → first tier
        usage = UsageData(one_byte=536870912)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("0")

    def test_cross_unit_kilobytes_to_megabytes(self) -> None:
        """Usage in KB, pricing in MB."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_megabyte",
                "tiers": [
                    {"up_to": None, "unit_price": "0.005"},
                ],
            }
        )
        # 2048 KB = 2 MB
        usage = UsageData(one_kilobyte=2048)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("0.010")


class TestCountEquivalenceGroup:
    """Tests for count equivalence group cross-unit conversion."""

    def test_graduated_based_on_one_thousand_usage_in_count(self) -> None:
        """Graduated pricing in thousands, usage as count."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_thousand",
                "tiers": [
                    {"up_to": 1, "unit_price": "0"},  # first 1000 free
                    {"up_to": None, "unit_price": "0.50"},
                ],
            }
        )
        # 3000 count = 3 thousand → first 1k free, next 2k at $0.50 per thousand
        usage = UsageData(count=3000)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("1.00")

    def test_tiered_based_on_one_million_usage_in_one_thousand(self) -> None:
        """Tiered pricing in millions, usage in thousands."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_million",
                "tiers": [
                    {"up_to": 1, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "50.00"}},
                ],
            }
        )
        # 500 thousand = 0.5 million → first tier
        usage = UsageData(one_thousand=500)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("10.00")

    def test_cross_unit_count_to_one_million(self) -> None:
        """Usage as count, pricing based_on one_million."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_million",
                "tiers": [
                    {"up_to": None, "unit_price": "100.00"},
                ],
            }
        )
        # 2_000_000 count = 2 million
        usage = UsageData(count=2_000_000)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("200.00")


class TestUnitMismatch:
    """Tests for pricing/usage unit mismatch — incompatible groups should fail."""

    def test_time_pricing_with_count_only(self) -> None:
        """Time pricing with only count usage → error."""
        pricing = validate_pricing({"type": "one_minute", "price": "1.00"})
        usage = UsageData(count=100)

        import pytest

        with pytest.raises(ValueError, match="time field"):
            pricing.calculate_cost(usage)

    def test_time_pricing_with_tokens_only(self) -> None:
        """Time pricing with only token usage → error."""
        pricing = validate_pricing({"type": "one_hour", "price": "5.00"})
        usage = UsageData(input_tokens=1000, output_tokens=500)

        import pytest

        with pytest.raises(ValueError, match="time field"):
            pricing.calculate_cost(usage)

    def test_time_pricing_with_empty_usage(self) -> None:
        """Time pricing with empty usage → error."""
        pricing = validate_pricing({"type": "one_second", "price": "0.01"})
        usage = UsageData()

        import pytest

        with pytest.raises(ValueError, match="time field"):
            pricing.calculate_cost(usage)

    def test_tiered_time_based_on_with_count_only(self) -> None:
        """Tiered pricing based_on=one_minute with only count usage → error."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "one_minute",
                "tiers": [
                    {"up_to": 100, "price": {"type": "constant", "price": "5.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "10.00"}},
                ],
            }
        )
        usage = UsageData(count=50)

        import pytest

        with pytest.raises(ValueError):
            pricing.calculate_cost(usage)

    def test_graduated_time_based_on_with_tokens_only(self) -> None:
        """Graduated pricing based_on=one_hour with only token usage → error."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "one_hour",
                "tiers": [
                    {"up_to": 10, "unit_price": "0"},
                    {"up_to": None, "unit_price": "1.00"},
                ],
            }
        )
        usage = UsageData(input_tokens=5000)

        import pytest

        with pytest.raises(ValueError):
            pricing.calculate_cost(usage)

    def test_image_pricing_with_time_only(self) -> None:
        """Image pricing with only time usage → error (no cross-group conversion)."""
        pricing = validate_pricing({"type": "image", "price": "0.05"})
        usage = UsageData(one_hour=5)

        import pytest

        with pytest.raises(ValueError, match="'count' in usage data"):
            pricing.calculate_cost(usage)


class TestImagePriceData:
    """Tests for image-based pricing."""

    def test_image_pricing(self) -> None:
        """Test image pricing."""
        pricing = validate_pricing({"type": "image", "price": "0.05"})
        usage = UsageData(count=100)

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("5.00")


class TestRevenueSharePriceData:
    """Tests for revenue share pricing."""

    def test_revenue_share(self) -> None:
        """Test revenue share pricing."""
        pricing = validate_pricing({"type": "revenue_share", "percentage": "70"})
        usage = UsageData()

        cost = pricing.calculate_cost(usage, customer_charge=Decimal("100.00"))

        assert cost == Decimal("70.00")


class TestAddPriceData:
    """Tests for add (sum) pricing."""

    def test_sum_two_prices(self) -> None:
        """Test summing two pricing components."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {"type": "one_million_tokens", "input": "2.00", "output": "2.00"},
                    {"type": "constant", "price": "5.00"},
                ],
            }
        )
        usage = UsageData(input_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        # 1M tokens * $2.00 + $5.00 = $2.00 + $5.00 = $7.00
        assert cost == Decimal("7.00")

    def test_sum_with_discount(self) -> None:
        """Test summing with a negative constant (discount)."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {"type": "one_million_tokens", "input": "1.00", "output": "2.00"},
                    {"type": "constant", "price": "-3.00"},
                ],
            }
        )
        usage = UsageData(input_tokens=2_000_000, output_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        # 2M input * $1.00 + 1M output * $2.00 - $3.00 = $2.00 + $2.00 - $3.00 = $1.00
        assert cost == Decimal("1.00")

    def test_sum_multiple_components(self) -> None:
        """Test summing multiple pricing components."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {"type": "constant", "price": "10.00"},
                    {"type": "constant", "price": "5.00"},
                    {"type": "constant", "price": "-2.50"},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        assert cost == Decimal("12.50")


class TestMultiplyPriceData:
    """Tests for multiply pricing."""

    def test_basic_multiply(self) -> None:
        """Test basic multiplication (70% discount factor)."""
        pricing = validate_pricing(
            {
                "type": "multiply",
                "factor": "0.70",
                "base": {"type": "one_million_tokens", "price": "10.00"},
            }
        )
        usage = UsageData(input_tokens=1_000_000)

        cost = pricing.calculate_cost(usage)

        # 1M tokens * $10.00 * 0.70 = $7.00
        assert cost == Decimal("7.00")

    def test_multiply_with_markup(self) -> None:
        """Test multiplication with markup (factor > 1)."""
        pricing = validate_pricing(
            {
                "type": "multiply",
                "factor": "1.25",
                "base": {"type": "constant", "price": "100.00"},
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        # $100.00 * 1.25 = $125.00
        assert cost == Decimal("125.00")

    def test_nested_multiply(self) -> None:
        """Test nested multiply pricing."""
        pricing = validate_pricing(
            {
                "type": "multiply",
                "factor": "0.80",
                "base": {
                    "type": "multiply",
                    "factor": "0.90",
                    "base": {"type": "constant", "price": "100.00"},
                },
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage)

        # $100.00 * 0.90 * 0.80 = $72.00
        assert cost == Decimal("72.00")


class TestTieredPriceData:
    """Tests for tiered (volume-based) pricing."""

    def test_first_tier(self) -> None:
        """Test pricing falls into first tier."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": 10000, "price": {"type": "constant", "price": "80.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "500.00"}},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=500)

        assert cost == Decimal("10.00")

    def test_second_tier(self) -> None:
        """Test pricing falls into second tier."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": 10000, "price": {"type": "constant", "price": "80.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "500.00"}},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=5000)

        assert cost == Decimal("80.00")

    def test_unlimited_tier(self) -> None:
        """Test pricing falls into unlimited tier."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "50.00"}},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=50000)

        assert cost == Decimal("50.00")

    def test_tier_boundary(self) -> None:
        """Test pricing at tier boundary (exactly 1000)."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "50.00"}},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=1000)

        # At boundary, should be tier 1
        assert cost == Decimal("10.00")

    def test_tiered_by_input_tokens(self) -> None:
        """Test tiered pricing based on input_tokens from UsageData."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens",
                "tiers": [
                    {"up_to": 1000000, "price": {"type": "one_million_tokens", "price": "5.00"}},
                    {"up_to": None, "price": {"type": "one_million_tokens", "price": "2.50"}},
                ],
            }
        )

        # Small usage - tier 1 (500k input_tokens < 1M threshold)
        # TokenPriceData uses input_tokens directly when provided
        usage_small = UsageData(input_tokens=500_000)
        cost_small = pricing.calculate_cost(usage_small)
        # 0.5M input tokens * $5.00 = $2.50
        assert cost_small == Decimal("2.50")

        # Large usage - tier 2 (5M input_tokens > 1M threshold)
        usage_large = UsageData(input_tokens=5_000_000)
        cost_large = pricing.calculate_cost(usage_large)
        # 5M input tokens * $2.50 = $12.50
        assert cost_large == Decimal("12.50")

    def test_tiered_with_usage_based_price(self) -> None:
        """Test tiered pricing where tier prices are usage-based."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "one_million_tokens", "input": "3.00", "output": "15.00"}},
                    {"up_to": None, "price": {"type": "one_million_tokens", "input": "1.50", "output": "7.50"}},
                ],
            }
        )
        usage = UsageData(input_tokens=100_000, output_tokens=50_000)

        # Small tier (high rate)
        cost_small = pricing.calculate_cost(usage, request_count=500)
        # 0.1M * $3.00 + 0.05M * $15.00 = $0.30 + $0.75 = $1.05
        assert cost_small == Decimal("1.05")

        # Large tier (low rate)
        cost_large = pricing.calculate_cost(usage, request_count=5000)
        # 0.1M * $1.50 + 0.05M * $7.50 = $0.15 + $0.375 = $0.525
        assert cost_large == Decimal("0.525")


class TestGraduatedPriceData:
    """Tests for graduated (AWS-style) pricing."""

    def test_first_tier_only(self) -> None:
        """Test graduated pricing within first tier."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "unit_price": "0.01"},
                    {"up_to": 10000, "unit_price": "0.008"},
                    {"up_to": None, "unit_price": "0.005"},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=500)

        # 500 * $0.01 = $5.00
        assert cost == Decimal("5.00")

    def test_spans_two_tiers(self) -> None:
        """Test graduated pricing spanning two tiers."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "unit_price": "0.01"},
                    {"up_to": 10000, "unit_price": "0.008"},
                    {"up_to": None, "unit_price": "0.005"},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=5000)

        # First 1000 * $0.01 + next 4000 * $0.008 = $10.00 + $32.00 = $42.00
        assert cost == Decimal("42.000")

    def test_spans_all_tiers(self) -> None:
        """Test graduated pricing spanning all tiers."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "unit_price": "0.01"},
                    {"up_to": 10000, "unit_price": "0.008"},
                    {"up_to": None, "unit_price": "0.005"},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=15000)

        # First 1000 * $0.01 + next 9000 * $0.008 + next 5000 * $0.005
        # = $10.00 + $72.00 + $25.00 = $107.00
        assert cost == Decimal("107.000")

    def test_graduated_by_tokens(self) -> None:
        """Test graduated pricing based on tokens."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "input_tokens",
                "tiers": [
                    {"up_to": 1000000, "unit_price": "0.000005"},  # $5 per 1M
                    {"up_to": None, "unit_price": "0.0000025"},  # $2.50 per 1M
                ],
            }
        )
        usage = UsageData(input_tokens=3_000_000)

        cost = pricing.calculate_cost(usage)

        # First 1M * $0.000005 + next 2M * $0.0000025
        # = $5.00 + $5.00 = $10.00
        assert cost == Decimal("10.0000000")

    def test_zero_usage(self) -> None:
        """Test graduated pricing with zero usage."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "unit_price": "0.01"},
                    {"up_to": None, "unit_price": "0.005"},
                ],
            }
        )
        usage = UsageData()

        cost = pricing.calculate_cost(usage, request_count=0)

        assert cost == Decimal("0")


class TestTieredVsGraduated:
    """Tests comparing tiered vs graduated pricing behavior."""

    def test_different_results_for_same_volume(self) -> None:
        """Verify tiered and graduated give different results for same volume."""
        tiered = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "40.00"}},
                ],
            }
        )

        graduated = validate_pricing(
            {
                "type": "graduated",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "unit_price": "0.01"},
                    {"up_to": None, "unit_price": "0.008"},
                ],
            }
        )

        usage = UsageData()

        # For 5000 requests:
        # Tiered: Falls into tier 2 → $40.00 flat
        tiered_cost = tiered.calculate_cost(usage, request_count=5000)
        assert tiered_cost == Decimal("40.00")

        # Graduated: 1000*0.01 + 4000*0.008 = $10 + $32 = $42
        graduated_cost = graduated.calculate_cost(usage, request_count=5000)
        assert graduated_cost == Decimal("42.000")

        assert tiered_cost != graduated_cost


class TestNestedCompositePricing:
    """Tests for nested composite pricing structures."""

    def test_add_with_tiered(self) -> None:
        """Test add pricing containing tiered pricing."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {
                        "type": "tiered",
                        "based_on": "request_count",
                        "tiers": [
                            {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                            {"up_to": None, "price": {"type": "constant", "price": "50.00"}},
                        ],
                    },
                    {"type": "constant", "price": "5.00"},  # Platform fee
                ],
            }
        )
        usage = UsageData()

        # Small volume: $10.00 + $5.00 = $15.00
        cost_small = pricing.calculate_cost(usage, request_count=500)
        assert cost_small == Decimal("15.00")

        # Large volume: $50.00 + $5.00 = $55.00
        cost_large = pricing.calculate_cost(usage, request_count=5000)
        assert cost_large == Decimal("55.00")

    def test_multiply_with_tiered(self) -> None:
        """Test multiply pricing with tiered base."""
        pricing = validate_pricing(
            {
                "type": "multiply",
                "factor": "0.80",  # 20% discount
                "base": {
                    "type": "tiered",
                    "based_on": "request_count",
                    "tiers": [
                        {"up_to": 1000, "price": {"type": "constant", "price": "100.00"}},
                        {"up_to": None, "price": {"type": "constant", "price": "500.00"}},
                    ],
                },
            }
        )
        usage = UsageData()

        # Small volume: $100.00 * 0.80 = $80.00
        cost_small = pricing.calculate_cost(usage, request_count=500)
        assert cost_small == Decimal("80.00")

        # Large volume: $500.00 * 0.80 = $400.00
        cost_large = pricing.calculate_cost(usage, request_count=5000)
        assert cost_large == Decimal("400.00")

    def test_deeply_nested_pricing(self) -> None:
        """Test deeply nested pricing structure."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {
                        "type": "multiply",
                        "factor": "0.90",
                        "base": {
                            "type": "tiered",
                            "based_on": "request_count",
                            "tiers": [
                                {"up_to": 1000, "price": {"type": "one_million_tokens", "price": "10.00"}},
                                {"up_to": None, "price": {"type": "one_million_tokens", "price": "5.00"}},
                            ],
                        },
                    },
                    {"type": "constant", "price": "2.00"},  # Fixed fee
                ],
            }
        )
        usage = UsageData(total_tokens=1_000_000)

        # Small volume: (1M * $10.00) * 0.90 + $2.00 = $9.00 + $2.00 = $11.00
        cost_small = pricing.calculate_cost(usage, request_count=500)
        assert cost_small == Decimal("11.00")

        # Large volume: (1M * $5.00) * 0.90 + $2.00 = $4.50 + $2.00 = $6.50
        cost_large = pricing.calculate_cost(usage, request_count=5000)
        assert cost_large == Decimal("6.50")


class TestMaxPriceData:
    """Tests for max (highest-of) composite pricing."""

    def test_max_picks_highest(self) -> None:
        """Max pricing returns the highest cost among children."""
        pricing = validate_pricing(
            {
                "type": "max",
                "prices": [
                    {"type": "constant", "price": "5.00"},
                    {"type": "constant", "price": "10.00"},
                    {"type": "constant", "price": "3.00"},
                ],
            }
        )
        usage = UsageData()
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("10.00")

    def test_max_with_usage_types(self) -> None:
        """Max pricing across different usage-based types."""
        pricing = validate_pricing(
            {
                "type": "max",
                "prices": [
                    {"type": "one_second", "price": "0.01"},   # 120 * 0.01 = 1.20
                    {"type": "constant", "price": "0.50"},      # 0.50
                ],
            }
        )
        usage = UsageData(seconds=120)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("1.20")

    def test_max_lenient_skips_incompatible(self) -> None:
        """Max pricing skips children that can't handle the usage."""
        pricing = validate_pricing(
            {
                "type": "max",
                "prices": [
                    {"type": "one_second", "price": "0.01"},  # no time data → skip
                    {"type": "image", "price": "0.05"},        # count=10 → $0.50
                    {"type": "constant", "price": "0.25"},     # $0.25
                ],
            }
        )
        usage = UsageData(count=10)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("0.50")

    def test_max_all_fail_raises(self) -> None:
        """Max pricing raises when no child can handle the usage."""
        pricing = validate_pricing(
            {
                "type": "max",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                    {"type": "image", "price": "0.05"},
                ],
            }
        )
        usage = UsageData()  # no time or count data

        import pytest

        with pytest.raises(ValueError, match="No child pricing"):
            pricing.calculate_cost(usage)


class TestMinPriceData:
    """Tests for min (lowest-of / price cap) composite pricing."""

    def test_min_picks_lowest(self) -> None:
        """Min pricing returns the lowest cost among children."""
        pricing = validate_pricing(
            {
                "type": "min",
                "prices": [
                    {"type": "constant", "price": "5.00"},
                    {"type": "constant", "price": "10.00"},
                    {"type": "constant", "price": "3.00"},
                ],
            }
        )
        usage = UsageData()
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("3.00")

    def test_min_as_price_cap(self) -> None:
        """Min pricing acts as a price cap."""
        pricing = validate_pricing(
            {
                "type": "min",
                "prices": [
                    {"type": "one_second", "price": "0.10"},   # 2000 * 0.10 = 200
                    {"type": "constant", "price": "100.00"},    # cap at 100
                ],
            }
        )
        usage = UsageData(seconds=2000)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("100.00")

    def test_min_usage_below_cap(self) -> None:
        """Min returns usage-based cost when it's below the cap."""
        pricing = validate_pricing(
            {
                "type": "min",
                "prices": [
                    {"type": "one_second", "price": "0.10"},   # 50 * 0.10 = 5
                    {"type": "constant", "price": "100.00"},    # cap at 100
                ],
            }
        )
        usage = UsageData(seconds=50)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.0")

    def test_min_lenient_skips_incompatible(self) -> None:
        """Min pricing skips children that can't handle the usage."""
        pricing = validate_pricing(
            {
                "type": "min",
                "prices": [
                    {"type": "one_second", "price": "999.00"},  # no time → skip
                    {"type": "image", "price": "0.05"},          # count=10 → $0.50
                    {"type": "constant", "price": "1.00"},       # $1.00
                ],
            }
        )
        usage = UsageData(count=10)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("0.50")

    def test_min_all_fail_raises(self) -> None:
        """Min pricing raises when no child can handle the usage."""
        pricing = validate_pricing(
            {
                "type": "min",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                ],
            }
        )
        usage = UsageData()

        import pytest

        with pytest.raises(ValueError, match="No child pricing"):
            pricing.calculate_cost(usage)


class TestFirstPriceData:
    """Tests for first (first-applicable) composite pricing."""

    def test_first_picks_first_applicable(self) -> None:
        """First pricing returns cost from the first child that works."""
        pricing = validate_pricing(
            {
                "type": "first",
                "prices": [
                    {"type": "one_second", "price": "0.01"},  # no time → skip
                    {"type": "image", "price": "0.05"},        # count=20 → $1.00
                    {"type": "constant", "price": "99.00"},    # would work, but not reached
                ],
            }
        )
        usage = UsageData(count=20)
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("1.00")

    def test_first_uses_first_when_multiple_match(self) -> None:
        """First pricing uses the first match, not the best."""
        pricing = validate_pricing(
            {
                "type": "first",
                "prices": [
                    {"type": "constant", "price": "5.00"},     # always works → $5.00
                    {"type": "constant", "price": "1.00"},     # also works, but not reached
                ],
            }
        )
        usage = UsageData()
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_first_falls_through_to_later(self) -> None:
        """First pricing falls through incompatible children."""
        pricing = validate_pricing(
            {
                "type": "first",
                "prices": [
                    {"type": "one_second", "price": "0.01"},  # no time → skip
                    {"type": "image", "price": "0.05"},        # no count → skip
                    {"type": "constant", "price": "2.00"},     # fallback → $2.00
                ],
            }
        )
        usage = UsageData()
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("2.00")

    def test_first_all_fail_raises(self) -> None:
        """First pricing raises when no child can handle the usage."""
        pricing = validate_pricing(
            {
                "type": "first",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                    {"type": "image", "price": "0.05"},
                ],
            }
        )
        usage = UsageData()

        import pytest

        with pytest.raises(ValueError, match="No child pricing"):
            pricing.calculate_cost(usage)

    def test_first_with_time_or_count(self) -> None:
        """First pricing adapts to available usage metrics."""
        pricing_config = {
            "type": "first",
            "prices": [
                {"type": "one_second", "price": "0.01"},
                {"type": "image", "price": "0.05"},
            ],
        }

        # With time data → uses time pricing
        pricing = validate_pricing(pricing_config)
        cost_time = pricing.calculate_cost(UsageData(seconds=100))
        assert cost_time == Decimal("1.00")

        # With count data → uses image pricing
        pricing = validate_pricing(pricing_config)
        cost_count = pricing.calculate_cost(UsageData(count=100))
        assert cost_count == Decimal("5.00")


class TestExpressionBasedOn:
    """Tests for expression-based `based_on` field."""

    def test_simple_addition_expression(self) -> None:
        """Test based_on with simple addition expression."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens + output_tokens",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "1.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "5.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=600, output_tokens=600)

        # 600 + 600 = 1200 > 1000, so falls in second tier
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_weighted_tokens_expression(self) -> None:
        """Test based_on with weighted tokens (output tokens cost more)."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens + output_tokens * 4",
                "tiers": [
                    {"up_to": 10000, "price": {"type": "constant", "price": "1.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "10.00"}},
                ],
            }
        )

        # 5000 + 1000 * 4 = 9000 < 10000, first tier
        usage1 = UsageData(input_tokens=5000, output_tokens=1000)
        assert pricing.calculate_cost(usage1) == Decimal("1.00")

        # 5000 + 2000 * 4 = 13000 > 10000, second tier
        usage2 = UsageData(input_tokens=5000, output_tokens=2000)
        assert pricing.calculate_cost(usage2) == Decimal("10.00")

    def test_expression_with_parentheses(self) -> None:
        """Test based_on with parentheses for operator precedence."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "(input_tokens + output_tokens) * 2",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "1.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "5.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=300, output_tokens=300)

        # (300 + 300) * 2 = 1200 > 1000, second tier
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_expression_with_numeric_literals(self) -> None:
        """Test based_on with numeric literals in expression."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens / 1000 + output_tokens / 1000",
                "tiers": [
                    {"up_to": 10, "price": {"type": "constant", "price": "1.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "5.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=5000, output_tokens=7000)

        # 5000/1000 + 7000/1000 = 5 + 7 = 12 > 10, second tier
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_graduated_with_expression(self) -> None:
        """Test graduated pricing with expression-based based_on."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "input_tokens + output_tokens * 4",
                "tiers": [
                    {"up_to": 10000, "unit_price": "0.00001"},
                    {"up_to": None, "unit_price": "0.000005"},
                ],
            }
        )
        usage = UsageData(input_tokens=5000, output_tokens=2500)

        # 5000 + 2500*4 = 15000 total weighted tokens
        # First 10000 at $0.00001 = $0.10
        # Next 5000 at $0.000005 = $0.025
        # Total = $0.125
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("0.125")

    def test_expression_with_unary_minus(self) -> None:
        """Test based_on with unary minus operator."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens - -100",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "1.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "5.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=950)

        # 950 - (-100) = 1050 > 1000, second tier
        cost = pricing.calculate_cost(usage)
        assert cost == Decimal("5.00")

    def test_expression_with_request_count(self) -> None:
        """Test that request_count is available in expressions."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count * 100 + input_tokens",
                "tiers": [
                    {"up_to": 10000, "price": {"type": "constant", "price": "1.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "5.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=5000)

        # 50 * 100 + 5000 = 10000, at boundary = first tier
        assert pricing.calculate_cost(usage, request_count=50) == Decimal("1.00")

        # 51 * 100 + 5000 = 10100 > 10000, second tier
        assert pricing.calculate_cost(usage, request_count=51) == Decimal("5.00")

    def test_invalid_expression_syntax(self) -> None:
        """Test that invalid expression syntax raises error."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens +",  # Invalid syntax
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "1.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=500)

        with pytest.raises(ValueError, match="Invalid expression syntax"):
            pricing.calculate_cost(usage)

    def test_unknown_metric_in_expression(self) -> None:
        """Test that unknown metric in expression raises error."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens + unknown_field",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "1.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=500)

        with pytest.raises(ValueError, match="Unknown metric: unknown_field"):
            pricing.calculate_cost(usage)

    def test_unsupported_expression_type(self) -> None:
        """Test that unsupported operations in expression raise error."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "input_tokens ** 2",  # Power operator not supported
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "1.00"}},
                ],
            }
        )
        usage = UsageData(input_tokens=500)

        with pytest.raises(ValueError, match="Unsupported operator"):
            pricing.calculate_cost(usage)


class TestNominalPriceAutoCompute:
    """Tests for auto-computed nominal ``price`` on composite pricing types."""

    def test_add_auto_price_from_first_child(self) -> None:
        """AddPriceData gets price from the first child."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                    {"type": "constant", "price": "5.00"},
                ],
            }
        )
        assert pricing.price == "0.01"

    def test_add_explicit_price_not_overwritten(self) -> None:
        """Explicitly set price is preserved."""
        pricing = validate_pricing(
            {
                "type": "add",
                "price": "99.00",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                ],
            }
        )
        assert pricing.price == "99.00"

    def test_multiply_auto_price(self) -> None:
        """MultiplyPriceData computes base price × factor."""
        pricing = validate_pricing(
            {
                "type": "multiply",
                "factor": "0.70",
                "base": {"type": "one_million_tokens", "price": "10.00"},
            }
        )
        assert Decimal(pricing.price) == Decimal("7.00")

    def test_multiply_explicit_price_not_overwritten(self) -> None:
        """Explicitly set price on multiply is preserved."""
        pricing = validate_pricing(
            {
                "type": "multiply",
                "price": "50.00",
                "factor": "0.70",
                "base": {"type": "one_million_tokens", "price": "10.00"},
            }
        )
        assert pricing.price == "50.00"

    def test_max_auto_price_from_first_child(self) -> None:
        """MaxPriceData gets price from the first child."""
        pricing = validate_pricing(
            {
                "type": "max",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                    {"type": "constant", "price": "0.50"},
                ],
            }
        )
        assert pricing.price == "0.01"

    def test_min_auto_price_from_first_child(self) -> None:
        """MinPriceData gets price from the first child."""
        pricing = validate_pricing(
            {
                "type": "min",
                "prices": [
                    {"type": "one_second", "price": "0.10"},
                    {"type": "constant", "price": "100.00"},
                ],
            }
        )
        assert pricing.price == "0.10"

    def test_first_auto_price_from_first_child(self) -> None:
        """FirstPriceData gets price from the first child."""
        pricing = validate_pricing(
            {
                "type": "first",
                "prices": [
                    {"type": "one_second", "price": "0.01"},
                    {"type": "image", "price": "0.05"},
                ],
            }
        )
        assert pricing.price == "0.01"

    def test_tiered_auto_price_from_first_tier(self) -> None:
        """TieredPriceData gets price from the first tier's price."""
        pricing = validate_pricing(
            {
                "type": "tiered",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "price": {"type": "constant", "price": "10.00"}},
                    {"up_to": None, "price": {"type": "constant", "price": "50.00"}},
                ],
            }
        )
        assert pricing.price == "10.00"

    def test_graduated_auto_price_from_first_tier(self) -> None:
        """GraduatedPriceData gets price from the first tier's unit_price."""
        pricing = validate_pricing(
            {
                "type": "graduated",
                "based_on": "request_count",
                "tiers": [
                    {"up_to": 1000, "unit_price": "0.01"},
                    {"up_to": None, "unit_price": "0.005"},
                ],
            }
        )
        assert pricing.price == "0.01"

    def test_nested_composite_auto_price(self) -> None:
        """Nested composite types recursively extract price."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {
                        "type": "multiply",
                        "factor": "0.80",
                        "base": {"type": "one_million_tokens", "price": "10.00"},
                    },
                    {"type": "constant", "price": "2.00"},
                ],
            }
        )
        # First child is multiply: 10.00 * 0.80 = 8.00
        assert Decimal(pricing.price) == Decimal("8.00")

    def test_token_split_pricing_auto_price_weighted(self) -> None:
        """Composite with token split pricing gets the weighted average price."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {"type": "one_million_tokens", "input": "3.00", "output": "15.00"},
                    {"type": "constant", "price": "1.00"},
                ],
            }
        )
        # TokenPriceData auto-computes price = (3 + 4*15) / 5 = 12.60
        assert pricing.price == "12.60"

    def test_token_with_explicit_price_in_composite(self) -> None:
        """Composite with token pricing that has explicit price."""
        pricing = validate_pricing(
            {
                "type": "add",
                "prices": [
                    {"type": "one_million_tokens", "price": "9.00", "input": "3.00", "output": "15.00"},
                    {"type": "constant", "price": "1.00"},
                ],
            }
        )
        # Explicit price on the child is preserved
        assert pricing.price == "9.00"

"""Tests for ``SubscriptionPlanV1`` after the move from unitysvc-admin.

Focuses on the new slug constraint on ``slug`` (the public plan
identifier) and the still-present ``currency`` / ``base_amount``
validators carried over from the old unitysvc-admin model.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from unitysvc_core.models import (
    SubscriptionPlanStatusEnum,
    SubscriptionPlanV1,
    SubscriptionTierEnum,
)


def _minimal(**overrides):
    """Return kwargs that build a valid SubscriptionPlanV1."""
    kwargs = dict(
        slug="pro-2025-v1",
        name="Pro",
        tier=SubscriptionTierEnum.individual,
        display_name="Pro Plan",
        base_amount=Decimal("19.99"),
        terms={"features": []},
        time_created=datetime(2026, 4, 24, 0, 0, 0),
    )
    kwargs.update(overrides)
    return kwargs


class TestSubscriptionPlanSlug:
    def test_valid_slug_accepted(self):
        SubscriptionPlanV1(**_minimal(slug="enterprise-acme"))

    @pytest.mark.parametrize(
        "bad",
        ["Pro 2025", "PRO", "pro.2025", "-leading", "_leading", "", "x" * 101],
    )
    def test_invalid_slug_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            SubscriptionPlanV1(**_minimal(slug=bad))


class TestSubscriptionPlanCurrencyAndAmount:
    def test_default_currency(self):
        plan = SubscriptionPlanV1(**_minimal())
        assert plan.currency == "USD"

    def test_invalid_currency_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubscriptionPlanV1(**_minimal(currency="usd"))
        assert "ISO 4217" in str(exc.value)

    def test_negative_base_amount_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SubscriptionPlanV1(**_minimal(base_amount=Decimal("-1")))
        assert "non-negative" in str(exc.value)


class TestSubscriptionPlanStatusAndTier:
    def test_default_status(self):
        plan = SubscriptionPlanV1(**_minimal())
        assert plan.status is SubscriptionPlanStatusEnum.incomplete

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            SubscriptionPlanV1(**_minimal(bogus_field="x"))

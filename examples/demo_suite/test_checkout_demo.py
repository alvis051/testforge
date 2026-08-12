"""A small suite the demo runner actually executes.

Marked with the seeded project's case keys, so pressing "Run on runner" produces real
results against the "Release 1.0 regression" plan. This directory sits outside
``testpaths``, so it never joins TestForge's own test run — and it doubles as a worked
example of marking a test.
"""

import pytest


def apply_coupon(subtotal: float, coupon: str) -> float:
    discounts = {"SAVE10": 0.10}
    return round(subtotal * (1 - discounts.get(coupon, 0.0)), 2)


def render_payment_failure(retryable: bool) -> str:
    return "Retry payment" if retryable else "Payment failed. Contact support."


@pytest.mark.case("CHK-1")
def test_coupon_applies_at_checkout():
    assert apply_coupon(100.0, "SAVE10") == 90.0


@pytest.mark.case("CHK-2")
def test_an_expired_coupon_changes_nothing():
    assert apply_coupon(100.0, "EXPIRED") == 100.0


@pytest.mark.case("CHK-3")
def test_payment_failure_shows_a_retry_prompt():
    assert render_payment_failure(retryable=True) == "Retry payment"

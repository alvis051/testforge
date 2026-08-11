import pytest
from testforge.analytics.taxonomy import UNCATEGORIZED, classify_failure


@pytest.mark.parametrize(
    ("failure_type", "expected"),
    [
        ("AssertionError", "assertion"),
        ("TimeoutError", "timeout"),
        ("ReadTimeout", "timeout"),
        ("ConnectionError", "connection"),
        ("PermissionError", "permission"),
        ("FileNotFoundError", "not_found"),
    ],
)
def test_the_exception_class_decides_the_category(failure_type, expected):
    assert classify_failure(failure_type, None) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Connection refused by payments-sandbox:8443", "connection"),
        ("Timed out after 30s waiting for the coupon field", "timeout"),
        ("permission denied", "permission"),
        ("no such element: #checkout-submit", "not_found"),
        ("assert 200 == 500", "assertion"),
    ],
)
def test_the_message_decides_when_there_is_no_exception_class(message, expected):
    assert classify_failure(None, message) == expected


def test_the_exception_class_beats_the_message():
    category = classify_failure("AssertionError", "connection refused")

    assert category == "assertion", (
        "a precise exception class must win over a loose phrase in the message"
    )


def test_message_matching_is_case_insensitive():
    assert classify_failure(None, "CONNECTION REFUSED") == "connection"


def test_an_unrecognised_failure_falls_through_to_uncategorized():
    category = classify_failure(None, "Cart total drifted by 0.01 between render and submit")

    assert category == UNCATEGORIZED


def test_a_failure_with_no_type_and_no_message_is_uncategorized():
    assert classify_failure(None, None) == UNCATEGORIZED

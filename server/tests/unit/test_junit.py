from datetime import UTC, datetime

from testforge.services.junit import parse_junit

NOW = datetime(2026, 8, 1, tzinfo=UTC)

XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4">
    <testcase classname="tests.test_checkout" name="test_coupon" time="0.125">
      <properties><property name="case_key" value="CHK-1"/></properties>
    </testcase>
    <testcase classname="tests.test_login" name="test_login CHK-2" time="0.5">
      <failure type="AssertionError" message="expected 200, got 500">trace here</failure>
    </testcase>
    <testcase classname="tests.test_misc" name="test_orphan" time="0.01"/>
    <testcase classname="tests.test_misc" name="test_skipped" time="0">
      <skipped message="not on this platform"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_case_key_comes_from_the_property_first():
    results = parse_junit(XML, default_executed_at=NOW)
    coupon = next(r for r in results if r.test_identifier.endswith("test_coupon"))
    assert coupon.case_key == "CHK-1"
    assert coupon.outcome == "passed"
    assert coupon.duration_ms == 125


def test_case_key_falls_back_to_a_pattern_match_on_the_name():
    results = parse_junit(XML, default_executed_at=NOW)
    login = next(r for r in results if "test_login" in r.test_identifier)
    assert login.case_key == "CHK-2"
    assert login.outcome == "failed"
    assert login.failure_type == "AssertionError"
    assert login.failure_message == "expected 200, got 500"


def test_unannotated_test_yields_no_case_key():
    results = parse_junit(XML, default_executed_at=NOW)
    orphan = next(r for r in results if r.test_identifier.endswith("test_orphan"))
    assert orphan.case_key is None


def test_skipped_outcome_is_mapped():
    results = parse_junit(XML, default_executed_at=NOW)
    skipped = next(r for r in results if r.test_identifier.endswith("test_skipped"))
    assert skipped.outcome == "skipped"


def test_test_identifier_combines_classname_and_name():
    results = parse_junit(XML, default_executed_at=NOW)
    assert "tests.test_checkout::test_coupon" in {r.test_identifier for r in results}

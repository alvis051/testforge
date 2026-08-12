import json

SUITE = """
import pytest

@pytest.mark.case("CHK-1")
def test_passes():
    assert True

@pytest.mark.case("CHK-2")
def test_fails():
    assert 1 == 2

@pytest.mark.case("CHK-3")
@pytest.mark.case("CHK-4")
def test_covers_two_cases():
    assert True

def test_unmarked():
    assert True
"""


def test_offline_mode_writes_the_result_payload(pytester, tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest(
        "--tf-offline", str(out), "--tf-project", "CHK", "--tf-external-id", "local-1"
    )

    result.assert_outcomes(passed=3, failed=1)
    payload = json.loads(out.read_text())

    assert payload["project_key"] == "CHK"
    assert payload["external_id"] == "local-1"
    assert payload["source"] == "local"

    by_case = {r["case_key"]: r for r in payload["results"] if r["case_key"]}
    assert by_case["CHK-1"]["outcome"] == "passed"
    assert by_case["CHK-2"]["outcome"] == "failed"
    assert "1 == 2" in by_case["CHK-2"]["failure_message"], (
        "failure_message is the exception message, not the traceback's location line"
    )
    assert by_case["CHK-2"]["failure_type"] == "AssertionError"
    assert "CHK-3" in by_case and "CHK-4" in by_case, "a repeated marker emits one result per case"


def test_unmarked_tests_are_reported_without_a_case_key(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    payload = json.loads(out.read_text())
    unmarked = [r for r in payload["results"] if r["case_key"] is None]
    assert [r["test_identifier"] for r in unmarked] == ["test_suite.py::test_unmarked"]


def test_plugin_is_inert_without_options(pytester):
    pytester.makepyfile(test_suite=SUITE)

    result = pytester.runpytest()

    result.assert_outcomes(passed=3, failed=1)


SETUP_ERROR_SUITE = """
import pytest

@pytest.fixture
def broken():
    raise RuntimeError("fixture exploded during setup")

def test_needs_broken_fixture(broken):
    assert True
"""

TEARDOWN_ERROR_SUITE = """
import pytest

@pytest.fixture
def leaky():
    yield
    raise RuntimeError("fixture exploded during teardown")

def test_passes_then_teardown_errors(leaky):
    assert True
"""


def test_setup_errors_are_recorded_rather_than_dropped(pytester, tmp_path):
    pytester.makepyfile(test_suite=SETUP_ERROR_SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    result.assert_outcomes(errors=1)
    payload = json.loads(out.read_text())

    results = [
        r
        for r in payload["results"]
        if r["test_identifier"] == "test_suite.py::test_needs_broken_fixture"
    ]
    assert len(results) == 1, "a setup error contributes exactly one result, not zero"
    assert results[0]["outcome"] == "error"
    assert results[0]["failure_type"] == "RuntimeError"
    assert "fixture exploded during setup" in results[0]["failure_message"]


def test_teardown_errors_are_not_reported_as_passed(pytester, tmp_path):
    pytester.makepyfile(test_suite=TEARDOWN_ERROR_SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    result.assert_outcomes(passed=1, errors=1)
    payload = json.loads(out.read_text())

    results = [
        r
        for r in payload["results"]
        if r["test_identifier"] == "test_suite.py::test_passes_then_teardown_errors"
    ]
    assert len(results) == 1, "the teardown failure upgrades the call result, it does not duplicate"
    assert results[0]["outcome"] == "error", "a teardown failure must not be reported as passed"
    assert "fixture exploded during teardown" in results[0]["failure_message"]


def test_unwritable_offline_path_warns_but_does_not_fail_the_run(pytester, tmp_path):
    pytester.makepyfile(test_suite="def test_ok():\n    assert True\n")
    out = tmp_path / "nonexistent_dir" / "results.json"

    result = pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    assert result.ret == 0
    result.stdout.fnmatch_lines(["*testforge: could not write results*"])


def test_unreachable_server_warns_but_does_not_fail_the_run(pytester):
    pytester.makepyfile(test_suite="def test_ok():\n    assert True\n")

    result = pytester.runpytest(
        "--tf-url", "http://127.0.0.1:9", "--tf-project", "CHK", "-p", "no:cacheprovider"
    )

    assert result.ret == 0
    result.stdout.fnmatch_lines(["*testforge: could not report results*"])


def test_tf_cases_runs_only_tests_marked_with_those_keys(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest(
        "--tf-offline", str(out), "--tf-project", "CHK", "--tf-cases", "CHK-1,CHK-3"
    )

    result.assert_outcomes(passed=2, deselected=2)
    payload = json.loads(out.read_text())
    # CHK-4 rides along because one selected test covers both CHK-3 and CHK-4 —
    # selection is per test, and a test cannot run for only some of its cases.
    assert {r["case_key"] for r in payload["results"]} == {"CHK-1", "CHK-3", "CHK-4"}


def test_tf_cases_matching_nothing_collects_nothing(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest(
        "--tf-offline", str(out), "--tf-project", "CHK", "--tf-cases", "CHK-999"
    )

    assert result.ret == 5, (
        "pytest exits 5 when nothing is collected; the worker maps that to a clean "
        "zero-result run, not a broken job"
    )
    assert json.loads(out.read_text())["results"] == []


def test_collection_is_untouched_when_tf_cases_is_absent(pytester, tmp_path):
    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"

    result = pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK")

    result.assert_outcomes(passed=3, failed=1)

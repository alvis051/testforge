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


def test_offline_mode_writes_the_result_payload(pytester, tmp_path):
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
    assert by_case["CHK-2"]["failure_message"]
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


def test_unreachable_server_warns_but_does_not_fail_the_run(pytester):
    pytester.makepyfile(test_suite="def test_ok():\n    assert True\n")

    result = pytester.runpytest(
        "--tf-url", "http://127.0.0.1:9", "--tf-project", "CHK", "-p", "no:cacheprovider"
    )

    assert result.ret == 0
    result.stdout.fnmatch_lines(["*testforge: could not report results*"])

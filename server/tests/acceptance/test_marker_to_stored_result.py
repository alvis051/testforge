"""The Slice 1 success criterion, end to end.

A marked pytest test runs, its result reaches the platform, and it is bound to the
correct case AND to the case version that was current at execution time.
"""

import json

SUITE = """
import pytest

@pytest.mark.case("CHK-1")
def test_coupon_applies():
    assert True
"""


def test_marked_test_result_is_bound_to_the_case_version_current_at_execution(
    client, pytester, tmp_path
):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon applies at checkout"})

    pytester.makepyfile(test_suite=SUITE)
    out = tmp_path / "results.json"
    pytester.runpytest("--tf-offline", str(out), "--tf-project", "CHK", "--tf-external-id", "run-1")
    payload = json.loads(out.read_text())

    run_id = client.post(
        "/api/projects/CHK/runs",
        json={"external_id": payload["external_id"], "source": "local"},
    ).json()["id"]
    summary = client.post(f"/api/runs/{run_id}/results", json={"results": payload["results"]})
    client.post(f"/api/runs/{run_id}/complete")

    assert summary.status_code == 200
    assert summary.json()["resolved"] == 1

    versions = client.get("/api/cases/CHK-1/versions").json()
    assert len(versions) == 1

    stored = client.get(f"/api/runs/{run_id}/results").json()[0]
    assert stored["outcome"] == "passed"
    assert stored["test_identifier"] == "test_suite.py::test_coupon_applies"
    assert stored["test_case_id"] is not None
    version_at_execution = stored["test_case_version_id"]

    edit = client.patch(
        "/api/cases/CHK-1", json={"expected_version_no": 1, "title": "Coupon SAVE10 applies"}
    )
    # Guard the guard: if the edit ever stopped landing, the assertion below would pass
    # vacuously and this test would quietly stop proving anything.
    assert edit.status_code == 200
    assert len(client.get("/api/cases/CHK-1/versions").json()) == 2

    unchanged = client.get(f"/api/runs/{run_id}/results").json()[0]
    assert unchanged["test_case_version_id"] == version_at_execution, (
        "editing the case must not rewrite history"
    )

    links = client.get("/api/cases/CHK-1/automation-links").json()
    assert links[0]["test_identifier"] == "test_suite.py::test_coupon_applies"

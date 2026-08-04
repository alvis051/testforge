"""The Slice 2 success criterion, end to end.

A plan selects a mix of manual and automated cases; executing it opens a run; manual
results are recorded directly while automated results arrive through the UNCHANGED
Slice 1 ingestion path; plan progress then reports full coverage from both sources.
"""

EXECUTED_AT = "2026-08-03T10:00:00Z"


def test_a_plan_run_is_covered_by_both_manual_and_ingested_results(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    for title, execution_type in [
        ("Manual refund check", "manual"),
        ("Manual a11y sweep", "manual"),
        ("Coupon applies", "automated"),
        ("Expired coupon rejected", "automated"),
        ("Payment retry prompt", "automated"),
    ]:
        client.post(
            "/api/projects/CHK/cases",
            json={"title": title, "execution_type": execution_type, "tags": ["release"]},
        )

    plan = client.post(
        "/api/projects/CHK/plans",
        json={"name": "Release 2.4", "milestone": "2.4", "filter": {"tag": "release"}},
    ).json()
    assert plan["case_count"] == 5

    run = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "release run"}).json()
    assert run["status"] == "running"

    for case_key, outcome in [("CHK-1", "passed"), ("CHK-2", "failed")]:
        recorded = client.post(
            f"/api/runs/{run['id']}/cases/{case_key}/execute",
            json={"outcome": outcome, "notes": f"manual check of {case_key}"},
            headers={"X-Actor": "alvis"},
        )
        assert recorded.status_code == 200

    ingested = client.post(
        f"/api/runs/{run['id']}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-3",
                    "test_identifier": "tests/test_checkout.py::test_coupon",
                    "framework": "pytest",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                },
                {
                    "case_key": "CHK-4",
                    "test_identifier": "tests/test_checkout.py::test_expired",
                    "framework": "pytest",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                },
                {
                    "case_key": "CHK-5",
                    "test_identifier": "tests/test_checkout.py::test_retry",
                    "framework": "pytest",
                    "outcome": "failed",
                    "executed_at": EXECUTED_AT,
                },
            ]
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["resolved"] == 3

    summary = client.get(f"/api/runs/{run['id']}").json()
    progress = summary["plan_progress"]

    assert progress["total_cases"] == 5
    assert progress["cases_with_result"] == 5
    assert progress["cases_without_result"] == []
    assert progress["by_outcome"] == {"passed": 3, "failed": 2}

    client.post(f"/api/runs/{run['id']}/complete")
    rejected = client.post(f"/api/runs/{run['id']}/cases/CHK-1/execute", json={"outcome": "passed"})
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "run_already_completed"

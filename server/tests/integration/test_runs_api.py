import pytest

EXECUTED_AT = "2026-08-01T10:00:00Z"


@pytest.fixture
def setup(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon"})
    return "CHK"


def open_run(client, external_id="gha-1"):
    return client.post(
        "/api/projects/CHK/runs",
        json={"external_id": external_id, "name": "nightly", "source": "ci"},
    )


def test_open_run_then_ingest_then_complete(client, setup):
    created = open_run(client)
    assert created.status_code == 201
    run_id = created.json()["id"]

    ingested = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "tests/test_checkout.py::test_coupon",
                    "framework": "pytest",
                    "outcome": "passed",
                    "duration_ms": 30,
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["resolved"] == 1

    completed = client.post(f"/api/runs/{run_id}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_repeat_external_id_returns_the_same_run_with_200(client, setup):
    first = open_run(client)
    second = open_run(client)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_ingesting_into_a_completed_run_returns_409(client, setup):
    run_id = open_run(client).json()["id"]
    client.post(f"/api/runs/{run_id}/complete")

    response = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "run_already_completed"


def test_malformed_result_rejects_the_whole_batch(client, setup):
    run_id = open_run(client).json()["id"]

    response = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                },
                {"case_key": "CHK-1", "test_identifier": "t.py::b", "outcome": "exploded"},
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_result_payload"
    assert client.get(f"/api/runs/{run_id}/results").json() == []


def test_unresolved_results_are_stored_and_filterable(client, setup):
    run_id = open_run(client).json()["id"]
    client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-999",
                    "test_identifier": "t.py::orphan",
                    "outcome": "failed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    summary = client.get(f"/api/runs/{run_id}").json()
    assert summary["unresolved_count"] == 1

    unresolved = client.get(f"/api/runs/{run_id}/results", params={"unresolved": True}).json()
    assert unresolved[0]["unresolved_case_key"] == "CHK-999"


def test_automation_links_appear_on_the_case(client, setup):
    run_id = open_run(client).json()["id"]
    client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "tests/test_checkout.py::test_coupon[web]",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    links = client.get("/api/cases/CHK-1/automation-links").json()
    assert [link["test_identifier"] for link in links] == [
        "tests/test_checkout.py::test_coupon[web]"
    ]


def test_run_timestamps_are_timezone_aware_across_a_fresh_request(client, setup):
    from datetime import datetime

    run_id = open_run(client).json()["id"]

    # A GET is a genuinely separate HTTP request/session from the POST above —
    # this is exactly the fresh-session path where SQLite silently drops tzinfo
    # unless the UTCDateTime type decorator (server/src/testforge/db/base.py)
    # re-attaches it on read.
    fetched = client.get(f"/api/runs/{run_id}")
    started_at = fetched.json()["started_at"]

    parsed = datetime.fromisoformat(started_at)
    assert parsed.tzinfo is not None, (
        f"started_at={started_at!r} round-tripped through a fresh session without "
        "timezone info — the UTCDateTime fix has regressed"
    )


def make_plan_run(client):
    client.post("/api/projects", json={"key": "PLN", "name": "Planned"})
    client.post("/api/projects/PLN/cases", json={"title": "Manual A", "execution_type": "manual"})
    client.post("/api/projects/PLN/cases", json={"title": "Manual B", "execution_type": "manual"})
    plan = client.post(
        "/api/projects/PLN/plans",
        json={"name": "P", "case_keys": ["PLN-1", "PLN-2"]},
    ).json()
    run = client.post(f"/api/plans/{plan['id']}/runs", json={}).json()
    return plan, run


def test_cancel_a_run(client, setup):
    run_id = open_run(client, "cancel-1").json()["id"]

    response = client.post(f"/api/runs/{run_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_ingesting_into_a_canceled_run_reports_run_canceled(client, setup):
    run_id = open_run(client, "cancel-2").json()["id"]
    client.post(f"/api/runs/{run_id}/cancel")

    response = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "run_canceled", (
        "a canceled run must not be reported as completed"
    )


def test_manual_execution_records_a_result(client):
    _, run = make_plan_run(client)

    response = client.post(
        f"/api/runs/{run['id']}/cases/PLN-1/execute",
        json={"outcome": "failed", "notes": "coupon field rejects valid codes"},
        headers={"X-Actor": "alvis"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert body["test_identifier"] == "PLN-1"


def test_manual_execution_rejects_a_case_outside_the_plan(client):
    _, run = make_plan_run(client)
    client.post("/api/projects/PLN/cases", json={"title": "Outsider"})

    response = client.post(f"/api/runs/{run['id']}/cases/PLN-3/execute", json={"outcome": "passed"})

    assert response.status_code == 404
    assert response.json()["code"] == "case_not_in_plan"


def test_plan_progress_reports_coverage(client):
    _, run = make_plan_run(client)
    client.post(f"/api/runs/{run['id']}/cases/PLN-1/execute", json={"outcome": "passed"})

    summary = client.get(f"/api/runs/{run['id']}").json()

    progress = summary["plan_progress"]
    assert progress["total_cases"] == 2
    assert progress["cases_with_result"] == 1
    assert progress["by_outcome"] == {"passed": 1}
    assert progress["cases_without_result"] == ["PLN-2"]


def test_plan_progress_keeps_a_later_manual_failure_over_an_earlier_automated_pass(client):
    _, run = make_plan_run(client)
    client.post(
        f"/api/runs/{run['id']}/cases/PLN-1/execute",
        json={"outcome": "failed", "notes": "manual is the most recent record"},
    )

    ingested = client.post(
        f"/api/runs/{run['id']}/results",
        json={
            "results": [
                {
                    "case_key": "PLN-1",
                    "test_identifier": "tests/t.py::a",
                    "outcome": "passed",
                    "executed_at": "2020-01-01T00:00:00Z",
                }
            ]
        },
    )
    assert ingested.status_code == 200

    summary = client.get(f"/api/runs/{run['id']}").json()

    assert summary["plan_progress"]["by_outcome"] == {"failed": 1}, (
        "the later manual failure must win over an earlier automated pass"
    )


def test_plan_progress_lets_a_later_automated_result_override_an_earlier_manual_one(client):
    _, run = make_plan_run(client)
    client.post(
        f"/api/runs/{run['id']}/cases/PLN-1/execute",
        json={"outcome": "failed", "notes": "manual is now the older record"},
    )

    ingested = client.post(
        f"/api/runs/{run['id']}/results",
        json={
            "results": [
                {
                    "case_key": "PLN-1",
                    "test_identifier": "tests/t.py::a",
                    "outcome": "passed",
                    "executed_at": "2030-01-01T00:00:00Z",
                }
            ]
        },
    )
    assert ingested.status_code == 200

    summary = client.get(f"/api/runs/{run['id']}").json()

    assert summary["plan_progress"]["by_outcome"] == {"passed": 1}, (
        "the later automated result must win over an earlier manual record"
    )


def test_plan_progress_is_absent_for_adhoc_runs(client, setup):
    run_id = open_run(client, "adhoc-progress").json()["id"]

    summary = client.get(f"/api/runs/{run_id}").json()

    assert summary["plan_progress"] is None

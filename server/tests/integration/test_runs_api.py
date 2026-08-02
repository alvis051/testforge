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

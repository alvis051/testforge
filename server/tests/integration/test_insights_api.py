import pytest

EXECUTED_AT = "2026-08-01T10:00:00Z"


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Flaky one"})
    client.post("/api/projects/CHK/cases", json={"title": "Broken one"})
    return "CHK"


def record(client, index, outcomes, failures=None):
    run = client.post(
        "/api/projects/CHK/runs",
        json={"external_id": f"run-{index}", "name": f"run {index}", "source": "ci"},
    ).json()
    client.post(
        f"/api/runs/{run['id']}/results",
        json={
            "results": [
                {
                    "case_key": case_key,
                    "test_identifier": f"tests/t.py::{case_key}",
                    "outcome": outcome,
                    "failure_type": (failures or {}).get(case_key, (None, None))[0],
                    "failure_message": (failures or {}).get(case_key, (None, None))[1],
                    "executed_at": EXECUTED_AT,
                }
                for case_key, outcome in outcomes.items()
            ]
        },
    )
    client.post(f"/api/runs/{run['id']}/complete")
    return run


def test_flaky_endpoint_reports_the_self_recovering_case_only(client, project_key):
    for index, (a, b) in enumerate(
        [
            ("passed", "passed"),
            ("passed", "passed"),
            ("failed", "passed"),
            ("passed", "failed"),
            ("failed", "failed"),
            ("passed", "failed"),
        ]
    ):
        record(client, index, {"CHK-1": a, "CHK-2": b})

    response = client.get("/api/projects/CHK/insights/flaky")

    assert response.status_code == 200
    body = response.json()
    assert [c["case_key"] for c in body] == ["CHK-1"]
    assert body[0]["transitions"] == 4
    assert len(body[0]["sequence"]) == 6


def test_trends_endpoint_returns_points_oldest_first(client, project_key):
    record(client, 0, {"CHK-1": "passed", "CHK-2": "passed"})
    record(client, 1, {"CHK-1": "passed", "CHK-2": "failed"})

    body = client.get("/api/projects/CHK/insights/trends").json()

    assert [p["external_id"] for p in body] == ["run-0", "run-1"]
    assert body[0]["pass_rate"] == 1.0
    assert body[1]["pass_rate"] == 0.5


def test_trends_limit_is_honoured(client, project_key):
    for index in range(5):
        record(client, index, {"CHK-1": "passed"})

    body = client.get("/api/projects/CHK/insights/trends", params={"limit": 2}).json()

    assert len(body) == 2
    assert [p["external_id"] for p in body] == ["run-3", "run-4"], (
        "the limit must take the most recent runs, then present them oldest-first"
    )


def test_failure_categories_endpoint_counts_by_category(client, project_key):
    record(
        client,
        0,
        {"CHK-1": "failed", "CHK-2": "failed"},
        failures={
            "CHK-1": ("AssertionError", "assert 1 == 2"),
            "CHK-2": (None, "Cart total drifted by 0.01"),
        },
    )

    body = client.get("/api/projects/CHK/insights/failure-categories").json()

    assert {c["category"] for c in body} == {"assertion", "uncategorized"}


def test_insights_endpoints_are_empty_not_an_error_for_a_fresh_project(client, project_key):
    assert client.get("/api/projects/CHK/insights/flaky").json() == []
    assert client.get("/api/projects/CHK/insights/trends").json() == []
    assert client.get("/api/projects/CHK/insights/failure-categories").json() == []


def test_insights_404_for_an_unknown_project(client):
    response = client.get("/api/projects/NOPE/insights/flaky")

    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"

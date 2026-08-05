import pytest

EXECUTED_AT = "2026-08-06T10:00:00Z"


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon applies"})
    client.post("/api/projects/CHK/cases", json={"title": "Refund flow"})
    return "CHK"


def open_run(client, external_id, source="ci"):
    return client.post(
        "/api/projects/CHK/runs",
        json={"external_id": external_id, "name": external_id, "source": source},
    ).json()


def ingest(client, run_id, case_key, outcome):
    return client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": case_key,
                    "test_identifier": f"tests/t.py::{case_key}_{outcome}",
                    "outcome": outcome,
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )


def test_listing_runs_returns_outcome_counts(client, project_key):
    run = open_run(client, "gha-1")
    ingest(client, run["id"], "CHK-1", "passed")
    ingest(client, run["id"], "CHK-2", "failed")

    listed = client.get("/api/projects/CHK/runs")

    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["id"] == run["id"]
    assert body[0]["total_results"] == 2
    assert body[0]["by_outcome"] == {"passed": 1, "failed": 1}


def test_listing_runs_is_empty_not_an_error_when_there_are_none(client, project_key):
    response = client.get("/api/projects/CHK/runs")

    assert response.status_code == 200
    assert response.json() == []


def test_a_run_with_no_results_reports_zero_not_missing(client, project_key):
    open_run(client, "empty-1")

    body = client.get("/api/projects/CHK/runs").json()

    assert body[0]["total_results"] == 0
    assert body[0]["by_outcome"] == {}


def test_listing_filters_by_status_and_source(client, project_key):
    open_run(client, "ci-1", source="ci")
    local = open_run(client, "local-1", source="local")
    client.post(f"/api/runs/{local['id']}/complete")

    by_source = client.get("/api/projects/CHK/runs", params={"source": "ci"}).json()
    by_status = client.get("/api/projects/CHK/runs", params={"status": "completed"}).json()

    assert [r["external_id"] for r in by_source] == ["ci-1"]
    assert [r["external_id"] for r in by_status] == ["local-1"]


def test_listing_never_leaks_another_projects_runs(client, project_key):
    client.post("/api/projects", json={"key": "OTH", "name": "Other"})
    client.post("/api/projects/OTH/runs", json={"external_id": "foreign-1", "source": "ci"})
    open_run(client, "ours-1")

    body = client.get("/api/projects/CHK/runs").json()

    assert [r["external_id"] for r in body] == ["ours-1"]


def test_listing_counts_match_what_get_run_reports(client, project_key):
    run = open_run(client, "agree-1")
    ingest(client, run["id"], "CHK-1", "passed")
    ingest(client, run["id"], "CHK-2", "failed")

    listed = client.get("/api/projects/CHK/runs").json()[0]
    detail = client.get(f"/api/runs/{run['id']}").json()

    assert listed["by_outcome"] == detail["by_outcome"], (
        "the GROUP BY aggregate must agree with get_run's in-memory count"
    )
    assert listed["total_results"] == detail["total_results"]


def test_plan_runs_returns_that_plans_run_history(client, project_key):
    plan = client.post(
        "/api/projects/CHK/plans", json={"name": "Release 1.0", "case_keys": ["CHK-1"]}
    ).json()
    first = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "first"}).json()
    second = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "second"}).json()
    open_run(client, "unrelated-1")

    body = client.get(f"/api/plans/{plan['id']}/runs").json()

    assert {r["id"] for r in body} == {first["id"], second["id"]}
    assert all(r["plan_id"] == plan["id"] for r in body)


def test_listing_returns_newest_runs_first(client, project_key):
    older = open_run(client, "older-1")
    newer = open_run(client, "newer-1")

    body = client.get("/api/projects/CHK/runs").json()

    assert [r["external_id"] for r in body] == ["newer-1", "older-1"], (
        "the design spec promises newest-first; a run listing that reorders between "
        "requests is worse than useless for spotting the run that just broke"
    )
    assert [r["id"] for r in body] == [newer["id"], older["id"]]


def test_run_responses_name_their_own_project_not_the_first_one(client, project_key):
    """A run detail page is reached at /runs/<id>, which carries no project in the URL.
    Without the key on the run itself the dashboard fell back to the first project and
    silently navigated people out of the project they were inspecting."""
    client.post("/api/projects", json={"key": "ZZZ", "name": "Zebra"})
    foreign = client.post(
        "/api/projects/ZZZ/runs", json={"external_id": "zzz-1", "source": "ci"}
    ).json()
    ours = open_run(client, "ours-1")

    detail = client.get(f"/api/runs/{foreign['id']}").json()
    listed = client.get("/api/projects/CHK/runs").json()

    assert foreign["project_key"] == "ZZZ"
    assert detail["project_key"] == "ZZZ", (
        "the single-run endpoint must report the run's own project, not the first-seeded one"
    )
    assert [r["project_key"] for r in listed] == ["CHK"]
    assert listed[0]["id"] == ours["id"]


def test_plan_runs_404s_for_an_unknown_plan(client, project_key):
    response = client.get("/api/plans/does-not-exist/runs")

    assert response.status_code == 404
    assert response.json()["code"] == "plan_not_found"

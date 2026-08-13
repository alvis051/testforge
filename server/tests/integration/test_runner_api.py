import pytest
from starlette.testclient import TestClient
from testforge.config import Settings
from testforge.db.base import Base
from testforge.main import create_app

TOKEN = "test-runner-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def build_client(tmp_path, *, token: str | None) -> TestClient:
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'runner.db'}", runner_token=token)
    )
    Base.metadata.create_all(app.state.session_factory.kw["bind"])
    return TestClient(app)


@pytest.fixture
def runner_client(tmp_path):
    with build_client(tmp_path, token=TOKEN) as client:
        yield client


def seed_plan(client: TestClient) -> str:
    client.post(
        "/api/projects",
        json={
            "key": "CHK",
            "name": "Checkout",
            "repo_url": "https://example.test/repo.git",
            "default_ref": "main",
            "test_command": "pytest -q",
        },
    )
    client.post("/api/projects/CHK/cases", json={"title": "Automated one"})
    client.post("/api/projects/CHK/cases", json={"title": "Manual one", "execution_type": "manual"})
    plan = client.post(
        "/api/projects/CHK/plans", json={"name": "Smoke", "case_keys": ["CHK-1", "CHK-2"]}
    ).json()
    return plan["id"]


def test_the_protocol_is_disabled_when_no_token_is_configured(tmp_path):
    with build_client(tmp_path, token=None) as client:
        response = client.post("/api/runner/claim", json={"worker_name": "w"})

    assert response.status_code == 503
    assert response.json()["code"] == "runner_not_configured"


def test_heartbeat_is_disabled_when_no_token_is_configured(tmp_path):
    with build_client(tmp_path, token=None) as client:
        response = client.post("/api/runner/jobs/some-job/heartbeat", json={"worker_name": "w"})

    assert response.status_code == 503
    assert response.json()["code"] == "runner_not_configured"


def test_dispatch_still_works_without_a_runner_token(tmp_path):
    with build_client(tmp_path, token=None) as client:
        plan_id = seed_plan(client)
        response = client.post(f"/api/plans/{plan_id}/dispatch", json={})

    assert response.status_code == 201, "the browser cannot hold a worker credential"
    assert response.json()["status"] == "queued"


def test_a_wrong_token_is_rejected(runner_client):
    response = runner_client.post(
        "/api/runner/claim",
        json={"worker_name": "w"},
        headers={"Authorization": "Bearer nope"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_runner_token"


def test_claim_returns_null_when_there_is_no_work(runner_client):
    response = runner_client.post("/api/runner/claim", json={"worker_name": "w"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() is None


def test_dispatch_then_claim_hands_the_worker_everything_it_needs(runner_client):
    plan_id = seed_plan(runner_client)
    run = runner_client.post(f"/api/plans/{plan_id}/dispatch", json={}).json()

    job = runner_client.post(
        "/api/runner/claim", json={"worker_name": "worker-a"}, headers=AUTH
    ).json()

    assert job["run_id"] == run["id"]
    assert job["repo_url"] == "https://example.test/repo.git"
    assert job["git_ref"] == "main"
    assert job["command"] == "pytest -q"
    assert job["case_keys"] == ["CHK-1"], "the manual case is not dispatched"


def test_heartbeat_extends_the_lease_and_is_worker_scoped(runner_client):
    plan_id = seed_plan(runner_client)
    runner_client.post(f"/api/plans/{plan_id}/dispatch", json={})
    job = runner_client.post(
        "/api/runner/claim", json={"worker_name": "worker-a"}, headers=AUTH
    ).json()

    response = runner_client.post(
        f"/api/runner/jobs/{job['job_id']}/heartbeat",
        headers=AUTH,
        json={"worker_name": "worker-a"},
    )

    assert response.status_code == 204

    wrong_worker = runner_client.post(
        f"/api/runner/jobs/{job['job_id']}/heartbeat",
        headers=AUTH,
        json={"worker_name": "worker-b"},
    )

    assert wrong_worker.status_code == 409
    assert wrong_worker.json()["code"] == "job_not_claimed_by_worker"


def test_finish_ingests_results_and_shows_the_job_on_the_run(runner_client):
    plan_id = seed_plan(runner_client)
    run = runner_client.post(f"/api/plans/{plan_id}/dispatch", json={}).json()
    job = runner_client.post(
        "/api/runner/claim", json={"worker_name": "worker-a"}, headers=AUTH
    ).json()

    finish = runner_client.post(
        f"/api/runner/jobs/{job['job_id']}/finish",
        headers=AUTH,
        json={
            "worker_name": "worker-a",
            "status": "succeeded",
            "exit_code": 0,
            "output_tail": "1 passed in 0.10s",
            "resolved_sha": "b" * 40,
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "tests/test_demo.py::test_one",
                    "framework": "pytest",
                    "outcome": "passed",
                    "executed_at": "2026-08-12T10:00:00Z",
                }
            ],
        },
    )

    assert finish.status_code == 204
    summary = runner_client.get(f"/api/runs/{run['id']}").json()
    assert summary["status"] == "completed"
    assert summary["by_outcome"] == {"passed": 1}
    assert summary["job"]["status"] == "succeeded"
    assert summary["job"]["exit_code"] == 0
    assert summary["job"]["resolved_sha"] == "b" * 40


def test_a_rejected_finish_writes_nothing(runner_client):
    plan_id = seed_plan(runner_client)
    run = runner_client.post(f"/api/plans/{plan_id}/dispatch", json={}).json()
    job = runner_client.post(
        "/api/runner/claim", json={"worker_name": "worker-a"}, headers=AUTH
    ).json()

    response = runner_client.post(
        f"/api/runner/jobs/{job['job_id']}/finish",
        headers=AUTH,
        json={
            "worker_name": "worker-b",
            "status": "succeeded",
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "tests/test_demo.py::test_one",
                    "framework": "pytest",
                    "outcome": "passed",
                    "executed_at": "2026-08-12T10:00:00Z",
                }
            ],
        },
    )

    assert response.status_code == 409
    # get_session rolls the whole request back, so the results in that payload are gone
    # too — which is what stops a reclaimed lease from double-ingesting.
    summary = runner_client.get(f"/api/runs/{run['id']}").json()
    assert summary["status"] == "running"
    assert summary["total_results"] == 0


def test_a_run_with_no_job_reports_a_null_job(runner_client):
    runner_client.post("/api/projects", json={"key": "OTH", "name": "Other"})
    run = runner_client.post(
        "/api/projects/OTH/runs", json={"external_id": "local-1", "source": "local"}
    ).json()

    assert runner_client.get(f"/api/runs/{run['id']}").json()["job"] is None

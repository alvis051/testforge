"""The whole runner loop in one process: dispatch, claim, clone, execute, ingest.

The worker talks to the app through Starlette's ``TestClient``, which is itself an
``httpx.Client`` — so the real HTTP protocol is exercised with no socket and no server.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from testforge.config import Settings
from testforge.db.base import Base
from testforge.main import create_app
from testforge_worker.cli import process_one
from testforge_worker.client import RunnerClient

TOKEN = "acceptance-token"
COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider"

MARKED_SUITE = """
import pytest


@pytest.mark.case("CHK-1")
def test_coupon_applies():
    assert True


@pytest.mark.case("CHK-2")
def test_expired_coupon():
    assert True


def test_not_linked_to_any_case():
    assert True
"""


@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
    """A throwaway git repository holding a marked suite.

    Built here rather than pointed at ``examples/demo_suite`` because the worker clones
    a URL: depending on the real repository would mean a network round trip, and would
    couple this test to whatever that suite happens to contain.
    """
    repo = tmp_path / "origin"
    repo.mkdir()
    (repo / "test_demo.py").write_text(MARKED_SUITE)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-m", "demo suite")
    return repo


@pytest.fixture
def client(tmp_path):
    """A TestClient with the runner protocol enabled.

    Overrides the shared fixture: the runner endpoints return 503 until a token is set,
    and the worker needs one to get through the door.
    """
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'acceptance.db'}", runner_token=TOKEN)
    )
    Base.metadata.create_all(app.state.session_factory.kw["bind"])
    with TestClient(app) as test_client:
        yield test_client


def test_dispatching_a_plan_runs_it_and_lands_the_results(client, demo_repo):
    client.post(
        "/api/projects",
        json={
            "key": "CHK",
            "name": "Checkout",
            "repo_url": str(demo_repo),
            "default_ref": "main",
            "test_command": COMMAND,
        },
    )
    client.post("/api/projects/CHK/cases", json={"title": "Coupon applies"})
    client.post("/api/projects/CHK/cases", json={"title": "Expired coupon"})
    plan = client.post(
        "/api/projects/CHK/plans",
        json={"name": "Smoke", "case_keys": ["CHK-1", "CHK-2"]},
    ).json()

    run = client.post(f"/api/plans/{plan['id']}/dispatch", json={}).json()
    assert run["status"] == "queued"
    assert run["source"] == "runner"

    worker = RunnerClient("http://testserver", TOKEN, client=client)
    assert process_one(worker, "acceptance-worker", 300) is True

    summary = client.get(f"/api/runs/{run['id']}").json()
    assert summary["status"] == "completed"
    assert summary["by_outcome"] == {"passed": 2}
    assert summary["job"]["status"] == "succeeded"
    assert summary["job"]["exit_code"] == 0
    assert len(summary["job"]["resolved_sha"]) == 40
    assert summary["plan_progress"]["cases_with_result"] == 2
    assert summary["unresolved_count"] == 0, (
        "--tf-cases deselected the unmarked test, so it never ran and never reported"
    )


def test_a_worker_finding_an_empty_queue_does_nothing(client):
    worker = RunnerClient("http://testserver", TOKEN, client=client)

    assert process_one(worker, "acceptance-worker", 300) is False

import subprocess
import sys
from pathlib import Path

import pytest
from testforge_worker.execute import CheckoutError, clone, run_job

MARKED_SUITE = """
import time

import pytest


@pytest.mark.case("CHK-1")
def test_one():
    assert True


@pytest.mark.case("CHK-2")
def test_two():
    assert 1 == 2


@pytest.mark.case("CHK-3")
def test_hangs():
    time.sleep(60)
"""

#: Run pytest through the current interpreter so the test does not depend on PATH.
#: A real deployment uses whatever the project configured, which is exactly the S4a
#: constraint: the worker host's environment must already have the repo's dependencies.
COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider"


@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
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


def test_clone_checks_out_the_ref_and_returns_its_sha(demo_repo, tmp_path):
    into = tmp_path / "checkout"

    sha = clone(str(demo_repo), "main", into)

    assert (into / "test_demo.py").is_file()
    assert len(sha) == 40


def test_a_clone_failure_is_reported_not_raised_as_a_crash(tmp_path):
    with pytest.raises(CheckoutError) as exc:
        clone(str(tmp_path / "does-not-exist"), "main", tmp_path / "checkout")

    assert "git clone failed" in str(exc.value)


def test_a_job_runs_only_the_requested_cases(demo_repo, tmp_path):
    execution = run_job(
        repo_url=str(demo_repo),
        git_ref="main",
        command=COMMAND,
        case_keys=["CHK-1"],
        workdir=tmp_path / "work",
        timeout=300,
    )

    assert execution.status == "succeeded"
    assert execution.exit_code == 0
    assert [r["case_key"] for r in execution.results] == ["CHK-1"]
    assert len(execution.resolved_sha) == 40


def test_a_failing_suite_is_still_a_succeeded_job(demo_repo, tmp_path):
    execution = run_job(
        repo_url=str(demo_repo),
        git_ref="main",
        command=COMMAND,
        case_keys=["CHK-1", "CHK-2"],
        workdir=tmp_path / "work",
        timeout=300,
    )

    assert execution.exit_code == 1
    assert execution.status == "succeeded", (
        "failing tests are a completed run; only infrastructure failure fails the job"
    )
    assert {r["outcome"] for r in execution.results} == {"passed", "failed"}


def test_no_matching_cases_is_a_clean_zero_result_run(demo_repo, tmp_path):
    execution = run_job(
        repo_url=str(demo_repo),
        git_ref="main",
        command=COMMAND,
        case_keys=["CHK-999"],
        workdir=tmp_path / "work",
        timeout=300,
    )

    assert execution.exit_code == 5
    assert execution.status == "succeeded"
    assert execution.results == []


def test_a_missing_command_fails_the_job(demo_repo, tmp_path):
    execution = run_job(
        repo_url=str(demo_repo),
        git_ref="main",
        command="definitely-not-a-real-binary --go",
        case_keys=["CHK-1"],
        workdir=tmp_path / "work",
        timeout=300,
    )

    assert execution.status == "failed"
    assert "command not found" in execution.error


def test_a_hanging_suite_is_killed_and_fails_the_job(demo_repo, tmp_path):
    # CHK-3 sleeps for a minute; the timeout must cut it short rather than block.
    execution = run_job(
        repo_url=str(demo_repo),
        git_ref="main",
        command=COMMAND,
        case_keys=["CHK-3"],
        workdir=tmp_path / "work",
        timeout=3,
    )

    assert execution.status == "failed"
    assert execution.exit_code is None
    assert "timed out after 3s" in execution.error

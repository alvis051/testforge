import subprocess
import sys
from pathlib import Path

import pytest
from testforge_worker.execute import CheckoutError, _read_results, clone, run_job

MARKED_SUITE = """
import subprocess
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


@pytest.mark.case("CHK-4")
def test_leaves_a_grandchild_holding_stdout():
    # A daemon-shaped grandchild: it inherits this process's stdout and is
    # deliberately never waited on, so pytest itself finishes at once while the
    # runner's pipe stays open. The runner then times out on a child that has
    # already exited.
    subprocess.Popen(["sleep", "300"])
"""

#: Run pytest through the current interpreter so the test does not depend on PATH.
#: A real deployment uses whatever the project configured, which is exactly the S4a
#: constraint: the worker host's environment must already have the repo's dependencies.
COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider"

#: ``-s`` leaves fd 1 pointing at the runner's pipe instead of pytest's capture
#: tempfile, which is what lets a grandchild inherit the pipe itself.
COMMAND_UNCAPTURED = f"{COMMAND} -s"


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


def test_a_timeout_whose_direct_child_already_exited_is_still_reported(demo_repo, tmp_path):
    # The hard case for the timeout handler: CHK-4's grandchild, not pytest itself,
    # is what holds the pipe open, so pytest is already a zombie when the timeout
    # fires. Looking the pgid up at that point raises ProcessLookupError on
    # macOS/BSD and used to take the whole worker down with it.
    execution = run_job(
        repo_url=str(demo_repo),
        git_ref="main",
        command=COMMAND_UNCAPTURED,
        case_keys=["CHK-4"],
        workdir=tmp_path / "work",
        timeout=5,
    )

    assert execution.status == "failed"
    assert execution.exit_code is None
    assert "timed out after 5s" in execution.error
    assert "passed" in (execution.output_tail or ""), (
        "the scenario only bites once pytest has printed its summary and exited, "
        "leaving the grandchild alone on the pipe"
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("[]", id="top-level-array"),
        pytest.param("5", id="top-level-scalar"),
        pytest.param("null", id="top-level-null"),
        pytest.param('"results"', id="top-level-string"),
        pytest.param('{"results": null}', id="null-results"),
        pytest.param('{"results": "not a list"}', id="string-results"),
        pytest.param('{"results": {"a": 1}}', id="object-results"),
        pytest.param("{not json at all", id="not-json"),
    ],
)
def test_a_malformed_results_file_reads_as_zero_results(tmp_path, payload):
    # A half-written file is the normal shape of a command that died mid-session, so
    # every one of these has to be zero results rather than an exception or a value
    # that is not the list the caller was promised.
    path = tmp_path / "results.json"
    path.write_text(payload)

    assert _read_results(path) == []


def test_a_well_formed_results_file_is_read_through(tmp_path):
    path = tmp_path / "results.json"
    path.write_text('{"results": [{"case_key": "CHK-1", "outcome": "passed"}]}')

    assert _read_results(path) == [{"case_key": "CHK-1", "outcome": "passed"}]

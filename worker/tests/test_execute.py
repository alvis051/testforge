import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from testforge_worker.execute import CheckoutError, _read_results, clone, run_job

MARKED_SUITE = """
import os
import pathlib
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


@pytest.mark.case("CHK-5")
def test_leaves_a_descendant_that_escaped_the_process_group():
    # Strictly harder than CHK-4: the descendant calls setsid() before spawning, so it
    # leaves the runner's process group entirely. Neither killpg on that group nor a
    # kill of the direct child can reach it, and it holds the inherited stdout pipe
    # open for five minutes. Its pid is written where the test can reap it.
    pid = os.fork()
    if pid == 0:
        os.setsid()
        escaped = subprocess.Popen(["sleep", "300"])
        pathlib.Path("descendant.pid").write_text(str(escaped.pid))
        os._exit(0)
    os.waitpid(pid, 0)
"""

#: Run pytest through the current interpreter so the test does not depend on PATH.
#: A real deployment uses whatever the project configured, which is exactly the S4a
#: constraint: the worker host's environment must already have the repo's dependencies.
COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider"

#: ``-s`` leaves fd 1 pointing at the runner's pipe instead of pytest's capture
#: tempfile, which is what lets a grandchild inherit the pipe itself.
COMMAND_UNCAPTURED = f"{COMMAND} -s"


#: Ceiling for the escaped-descendant timeout path: the job's own 3s timeout, plus the
#: handler's 5s bounded wait, plus slack for clone and interpreter start-up.
PROMPT_RETURN_SECONDS = 15


@contextlib.contextmanager
def fail_after(seconds: int) -> Iterator[None]:
    """Turn a hang into a fast, loud failure instead of a stuck suite.

    ``pytest-timeout`` is not a dependency and the worker package may not grow new
    ones, so this uses SIGALRM directly. pytest runs tests on the main thread, which is
    the only place a signal handler can be installed.
    """

    def on_alarm(signum: int, frame: object) -> None:
        raise AssertionError(f"call did not return within {seconds}s — it is hanging")

    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


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


def test_a_timeout_whose_descendant_escaped_the_process_group_returns_promptly(demo_repo, tmp_path):
    # The worst shape of the timeout path, and the one that used to hang the worker
    # forever: CHK-5's descendant called setsid(), so it is outside the group killpg
    # targets, and the direct child is a zombie so killing *it* frees nothing either.
    # Nothing can force that pipe closed, so the handler must stop waiting on it.
    workdir = tmp_path / "work"
    pid_file = workdir / "repo" / "descendant.pid"

    start = time.monotonic()
    with fail_after(PROMPT_RETURN_SECONDS):
        execution = run_job(
            repo_url=str(demo_repo),
            git_ref="main",
            command=COMMAND_UNCAPTURED,
            case_keys=["CHK-5"],
            workdir=workdir,
            timeout=3,
        )
    elapsed = time.monotonic() - start

    orphan = int(pid_file.read_text()) if pid_file.is_file() else None
    try:
        assert elapsed < PROMPT_RETURN_SECONDS, f"returned only after {elapsed:.1f}s"
        assert execution.status == "failed"
        assert execution.exit_code is None
        assert "timed out after 3s" in execution.error
        # Partial output survives: it comes off the TimeoutExpired the timed-out
        # communicate() raised, not from a second read the handler can no longer make.
        assert "passed" in (execution.output_tail or "")
        assert orphan is not None and _is_alive(orphan), (
            "the descendant is supposed to be unreachable — if it died, this test is no "
            "longer exercising the escaped-process-group case it was written for"
        )
    finally:
        if orphan is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(orphan, signal.SIGKILL)


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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

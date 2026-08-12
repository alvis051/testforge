"""Checkout and execution. Nothing in this module talks to the platform."""

import json
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

OUTPUT_TAIL_BYTES = 64 * 1024

#: pytest exit codes that mean the suite ran: 0 all passed, 1 tests failed, 5 nothing
#: collected. Exit 5 is what a plan whose cases carry no markers yet looks like, and
#: that is a clean zero-result run rather than a broken job. 2, 3 and 4 (interrupted,
#: internal error, usage error) are infrastructure problems and fail the job.
RAN_SUCCESSFULLY = (0, 1, 5)


class CheckoutError(RuntimeError):
    """The repository could not be cloned or inspected."""


@dataclass
class Execution:
    status: str
    exit_code: int | None = None
    output_tail: str | None = None
    error: str | None = None
    resolved_sha: str | None = None
    results: list[dict] = field(default_factory=list)


def clone(repo_url: str, git_ref: str, into: Path) -> str:
    """Shallow-clone one ref and return the commit it landed on.

    ``--branch`` takes a branch or a tag but not an arbitrary commit SHA; that would
    need a full clone and is out of scope for this slice.
    """
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", git_ref, repo_url, str(into)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CheckoutError(f"git clone failed: {result.stderr.strip()[:500]}")

    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=into, capture_output=True, text=True, check=False
    )
    if rev.returncode != 0:
        raise CheckoutError(f"git rev-parse failed: {rev.stderr.strip()[:500]}")
    return rev.stdout.strip()


def run_job(
    *,
    repo_url: str,
    git_ref: str,
    command: str,
    case_keys: list[str],
    workdir: Path,
    timeout: int,
) -> Execution:
    """Clone, run the project's command over the plan's cases, and collect the results."""
    workdir.mkdir(parents=True, exist_ok=True)
    checkout = workdir / "repo"
    try:
        sha = clone(repo_url, git_ref, checkout)
    except CheckoutError as exc:
        return Execution(status="failed", error=str(exc))

    results_path = workdir / "results.json"
    argv = [
        *shlex.split(command),
        f"--tf-cases={','.join(case_keys)}",
        f"--tf-offline={results_path}",
    ]

    try:
        exit_code, output, timed_out = _spawn(argv, checkout, timeout)
    except FileNotFoundError:
        return Execution(status="failed", error=f"command not found: {argv[0]}", resolved_sha=sha)

    if timed_out:
        return Execution(
            status="failed",
            error=f"timed out after {timeout}s",
            output_tail=_tail(output),
            resolved_sha=sha,
        )

    ran = exit_code in RAN_SUCCESSFULLY
    return Execution(
        status="succeeded" if ran else "failed",
        exit_code=exit_code,
        output_tail=_tail(output),
        error=None if ran else f"the test command exited {exit_code}",
        resolved_sha=sha,
        # Results are sent whichever way the job went, so a partial run still shows
        # whatever it managed to produce.
        results=_read_results(results_path),
    )


def _spawn(argv: list[str], cwd: Path, timeout: int) -> tuple[int | None, str, bool]:
    """Run argv with stderr folded into stdout. Never through a shell.

    ``shell=False`` is not about injection — the command comes from the project's own
    configuration and its owner can already run anything. It is because killing a shell
    on timeout would leave the test process running, and because an argv list has one
    unambiguous meaning. The cost: the configured command cannot use pipes or ``&&``.
    """
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output, False
    except subprocess.TimeoutExpired:
        # start_new_session gave the child its own process group; kill the group so a
        # hung pytest cannot leave its own subprocesses behind.
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        output, _ = process.communicate()
        return None, output, True


def _read_results(path: Path) -> list[dict]:
    """The plugin writes a whole payload; the runner needs only its results array.

    A missing or unreadable file is normal when the command died before session finish,
    and means zero results rather than an error.
    """
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text())["results"]
    except (OSError, ValueError, KeyError):
        return []


def _tail(text: str) -> str | None:
    if not text:
        return None
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= OUTPUT_TAIL_BYTES:
        return text
    return "…(truncated)…\n" + encoded[-OUTPUT_TAIL_BYTES:].decode("utf-8", errors="replace")

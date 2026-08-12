import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import typer

from testforge_worker.client import RunnerClient
from testforge_worker.execute import Execution, run_job

app = typer.Typer(help="TestForge runner worker")

#: Four beats fit inside the server's 60s lease, so one lost beat is survivable.
HEARTBEAT_SECONDS = 15


def default_worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def process_one(client: RunnerClient, worker_name: str, timeout: int) -> bool:
    """Claim and run at most one job. Returns False when the queue was empty."""
    job = client.claim(worker_name)
    if job is None:
        return False

    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                client.heartbeat(job["job_id"], worker_name)
            except Exception:  # noqa: BLE001 — a missed beat must not kill the job
                return

    beater = threading.Thread(target=beat, daemon=True)
    beater.start()
    try:
        with tempfile.TemporaryDirectory(prefix="tf-runner-") as tmp:
            execution = run_job(
                repo_url=job["repo_url"],
                git_ref=job["git_ref"],
                command=job["command"],
                case_keys=job["case_keys"],
                workdir=Path(tmp),
                timeout=timeout,
            )
    except Exception as exc:  # noqa: BLE001 — an unexpected failure must still report
        # Anything run_job did not turn into an Execution itself — an unwritable
        # workdir, a spawn that blew up — becomes a failed job. Letting it escape would
        # kill the worker and strand the job as `running` until its lease expired, only
        # for the requeue to hit the same failure on the next worker.
        execution = Execution(status="failed", error=f"unexpected worker error: {exc}")
    finally:
        stop.set()

    client.finish(
        job["job_id"],
        {
            "worker_name": worker_name,
            "status": execution.status,
            "exit_code": execution.exit_code,
            "output_tail": execution.output_tail,
            "error": execution.error,
            "resolved_sha": execution.resolved_sha,
            "results": execution.results,
        },
    )
    return True


@app.command()
def main(
    url: str = typer.Option(..., envvar="TESTFORGE_URL", help="Base URL of the platform"),
    token: str = typer.Option(..., envvar="TESTFORGE_RUNNER_TOKEN"),
    name: str | None = typer.Option(None, "--name", help="Defaults to hostname:pid"),
    poll_interval: float = typer.Option(5.0, help="Seconds to wait when the queue is empty"),
    timeout: int = typer.Option(1800, help="Wall-clock limit for one job, in seconds"),
    once: bool = typer.Option(False, "--once", help="Process at most one job, then exit"),
) -> None:
    worker_name = name or default_worker_name()
    client = RunnerClient(url, token)
    typer.echo(f"tf-worker {worker_name} polling {url}")

    while True:
        worked = process_one(client, worker_name, timeout)
        if once:
            typer.echo("processed one job" if worked else "no work")
            return
        if not worked:
            time.sleep(poll_interval)

from testforge_worker import cli

JOB = {
    "job_id": "job-1",
    "repo_url": "https://example.invalid/repo.git",
    "git_ref": "main",
    "command": "pytest",
    "case_keys": ["CHK-1"],
}


class FakeClient:
    """Stands in for RunnerClient: records the calls instead of making them."""

    def __init__(self, job: dict | None) -> None:
        self._job = job
        self.finished: list[tuple[str, dict]] = []

    def claim(self, worker_name: str) -> dict | None:
        return self._job

    def heartbeat(self, job_id: str, worker_name: str) -> None:
        pass

    def finish(self, job_id: str, payload: dict) -> None:
        self.finished.append((job_id, payload))


def test_an_empty_queue_finishes_nothing(monkeypatch):
    client = FakeClient(None)

    assert cli.process_one(client, "worker-1", 300) is False
    assert client.finished == []


def test_an_unexpected_error_fails_the_job_instead_of_the_worker(monkeypatch):
    # run_job owns its own failures, but anything it misses — an unwritable workdir, a
    # spawn that blows up — must not escape process_one: that would kill the worker and
    # leave the job `running` until its lease expired.
    def explode(**kwargs):
        raise PermissionError("workdir is read-only")

    monkeypatch.setattr(cli, "run_job", explode)
    client = FakeClient(JOB)

    assert cli.process_one(client, "worker-1", 300) is True

    assert len(client.finished) == 1, "the job must be reported, not abandoned"
    job_id, payload = client.finished[0]
    assert job_id == "job-1"
    assert payload["status"] == "failed"
    assert "workdir is read-only" in payload["error"]
    assert payload["worker_name"] == "worker-1"
    assert payload["results"] == []

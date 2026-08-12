import httpx


class RunnerClient:
    """The worker's only door to the platform.

    ``client`` is injectable so a test can hand in a transport that speaks to an
    in-process app instead of a socket.
    """

    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0)
        self._client.headers["Authorization"] = f"Bearer {token}"

    def claim(self, worker_name: str) -> dict | None:
        response = self._client.post("/api/runner/claim", json={"worker_name": worker_name})
        response.raise_for_status()
        return response.json()

    def heartbeat(self, job_id: str, worker_name: str) -> None:
        self._client.post(
            f"/api/runner/jobs/{job_id}/heartbeat", json={"worker_name": worker_name}
        ).raise_for_status()

    def finish(self, job_id: str, payload: dict) -> None:
        self._client.post(f"/api/runner/jobs/{job_id}/finish", json=payload).raise_for_status()

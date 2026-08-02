import os

import httpx
import typer


class ApiClient:
    """Thin HTTP wrapper. The CLI never touches the database directly."""

    def __init__(self, base_url: str | None = None, actor: str | None = None) -> None:
        default_url = os.environ.get("TESTFORGE_URL", "http://localhost:8000")
        self.base_url = (base_url or default_url).rstrip("/")
        self.actor = actor or os.environ.get("TESTFORGE_ACTOR", "local")

    def _http(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, headers={"X-Actor": self.actor}, timeout=30.0)

    def request(self, method: str, path: str, **kwargs) -> dict | list:
        client = self._http()
        response = client.request(method, path, **kwargs)
        if response.status_code >= 400:
            body = response.json()
            typer.secho(
                f"{body.get('code', 'error')}: {body.get('message', response.text)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        return response.json()

    def get(self, path: str, **kwargs) -> dict | list:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> dict | list:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs) -> dict | list:
        return self.request("PATCH", path, **kwargs)

import pytest
from testforge.cli import app
from testforge.cli import client as client_module
from typer.testing import CliRunner


@pytest.fixture
def cli(monkeypatch, client):
    """Point the CLI's HTTP client at the in-process test app."""

    def fake_client(self, *_args, **_kwargs):
        return client

    monkeypatch.setattr(client_module.ApiClient, "_http", fake_client, raising=False)
    return CliRunner()


def test_project_create_and_case_list(cli, client):
    result = cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    assert result.exit_code == 0, result.output
    assert "CHK" in result.output

    cli.invoke(app, ["case", "new", "CHK", "Coupon applies"])
    listed = cli.invoke(app, ["case", "list", "CHK"])

    assert listed.exit_code == 0
    assert "CHK-1" in listed.output
    assert "Coupon applies" in listed.output


def test_run_show_reports_counts(cli, client):
    cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    cli.invoke(app, ["case", "new", "CHK", "Coupon"])
    run_id = client.post(
        "/api/projects/CHK/runs", json={"external_id": "cli-1", "source": "local"}
    ).json()["id"]
    client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": "2026-08-01T10:00:00Z",
                }
            ]
        },
    )

    result = cli.invoke(app, ["run", "show", run_id])

    assert result.exit_code == 0
    assert "passed" in result.output


def test_plan_create_and_list(cli, client):
    cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    cli.invoke(app, ["case", "new", "CHK", "Coupon applies"])

    created = cli.invoke(app, ["plan", "create", "CHK", "Release 2.4", "--case-key", "CHK-1"])
    assert created.exit_code == 0, created.output
    assert "Release 2.4" in created.output

    listed = cli.invoke(app, ["plan", "list", "CHK"])
    assert listed.exit_code == 0
    assert "Release 2.4" in listed.output


def test_plan_show_reports_the_case_snapshot(cli, client):
    cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    cli.invoke(app, ["case", "new", "CHK", "Coupon applies"])
    plan_id = client.post(
        "/api/projects/CHK/plans", json={"name": "P", "case_keys": ["CHK-1"]}
    ).json()["id"]

    result = cli.invoke(app, ["plan", "show", plan_id])

    assert result.exit_code == 0
    assert "CHK-1" in result.output
    assert "Coupon applies" in result.output

import json
from pathlib import Path

import typer

from testforge.cli.client import ApiClient
from testforge.config import get_settings
from testforge.db.session import create_session_factory
from testforge.seed import seed_demo

app = typer.Typer(help="TestForge CLI")
project_app = typer.Typer(help="Manage projects")
case_app = typer.Typer(help="Manage test cases")
run_app = typer.Typer(help="Inspect and import runs")
plan_app = typer.Typer(help="Manage test plans")
app.add_typer(project_app, name="project")
app.add_typer(case_app, name="case")
app.add_typer(run_app, name="run")
app.add_typer(plan_app, name="plan")


@project_app.command("create")
def project_create(key: str, name: str, description: str | None = None) -> None:
    project = ApiClient().post(
        "/api/projects", json={"key": key, "name": name, "description": description}
    )
    typer.echo(f"{project['key']}  {project['name']}")


@project_app.command("list")
def project_list() -> None:
    for project in ApiClient().get("/api/projects"):
        typer.echo(f"{project['key']:<8} {project['name']}")


@case_app.command("new")
def case_new(
    project_key: str,
    title: str,
    execution_type: str = "automated",
    priority: str = "p2",
    tag: list[str] = typer.Option([], "--tag", help="Attach a tag (repeatable)"),
) -> None:
    case = ApiClient().post(
        f"/api/projects/{project_key}/cases",
        json={
            "title": title,
            "execution_type": execution_type,
            "priority": priority,
            "tags": list(tag),
        },
    )
    typer.echo(f"{case['case_key']}  {case['title']}")


@case_app.command("list")
def case_list(
    project_key: str,
    suite_id: str | None = None,
    tag: str | None = None,
    execution_type: str | None = None,
) -> None:
    params = {
        k: v
        for k, v in {"suite_id": suite_id, "tag": tag, "execution_type": execution_type}.items()
        if v is not None
    }
    for case in ApiClient().get(f"/api/projects/{project_key}/cases", params=params):
        typer.echo(f"{case['case_key']:<12} {case['priority']:<4} {case['title']}")


@run_app.command("show")
def run_show(run_id: str) -> None:
    run = ApiClient().get(f"/api/runs/{run_id}")
    typer.echo(f"run {run['id']}  status={run['status']}  results={run['total_results']}")
    for outcome, count in sorted(run["by_outcome"].items()):
        typer.echo(f"  {outcome:<10} {count}")
    if run["unresolved_count"]:
        typer.echo(f"  unresolved {run['unresolved_count']}")


@run_app.command("import-junit")
def run_import_junit(
    project_key: str, xml_path: Path, external_id: str, name: str | None = None
) -> None:
    summary = ApiClient().post(
        f"/api/projects/{project_key}/runs/import-junit",
        json={"external_id": external_id, "name": name, "xml": xml_path.read_text()},
    )
    typer.echo(json.dumps(summary, indent=2))


@run_app.command("upload")
def run_upload(payload_path: Path) -> None:
    """Upload an offline payload written by ``pytest --tf-offline``."""
    payload = json.loads(payload_path.read_text())
    api = ApiClient()
    run = api.post(
        f"/api/projects/{payload['project_key']}/runs",
        json={
            "external_id": payload["external_id"],
            "name": payload.get("name"),
            "source": payload.get("source", "local"),
        },
    )
    summary = api.post(f"/api/runs/{run['id']}/results", json={"results": payload["results"]})
    api.post(f"/api/runs/{run['id']}/complete")
    typer.echo(json.dumps(summary, indent=2))


@plan_app.command("create")
def plan_create(
    project_key: str,
    name: str,
    milestone: str | None = None,
    environment: str | None = None,
    case_key: list[str] = typer.Option([], "--case-key", help="Include this case (repeatable)"),
    tag: str | None = typer.Option(None, "--tag", help="Include every case with this tag"),
) -> None:
    payload: dict = {"name": name, "case_keys": list(case_key)}
    if milestone is not None:
        payload["milestone"] = milestone
    if environment is not None:
        payload["environment"] = environment
    if tag is not None:
        payload["filter"] = {"tag": tag}
    plan = ApiClient().post(f"/api/projects/{project_key}/plans", json=payload)
    typer.echo(f"{plan['id']}  {plan['name']}  ({plan['case_count']} cases)")


@plan_app.command("list")
def plan_list(project_key: str, milestone: str | None = None) -> None:
    params = {"milestone": milestone} if milestone is not None else {}
    for plan in ApiClient().get(f"/api/projects/{project_key}/plans", params=params):
        typer.echo(
            f"{plan['id']}  {plan['status']:<9} {plan['case_count']:>3} cases  {plan['name']}"
        )


@plan_app.command("show")
def plan_show(plan_id: str) -> None:
    api = ApiClient()
    plan = api.get(f"/api/plans/{plan_id}")
    typer.echo(f"{plan['name']}  status={plan['status']}  cases={plan['case_count']}")
    for case in api.get(f"/api/plans/{plan_id}/cases"):
        typer.echo(f"  {case['case_key']:<12} {case['execution_type']:<10} {case['title']}")


@app.command("seed")
def seed() -> None:
    """Create the demo project directly in the database."""
    factory = create_session_factory(get_settings().database_url)
    session = factory()
    try:
        counts = seed_demo(session)
        session.commit()
    finally:
        session.close()
    typer.echo(json.dumps(counts, indent=2))

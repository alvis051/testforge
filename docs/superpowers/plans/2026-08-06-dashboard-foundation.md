# Dashboard Foundation (Slice 5a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first user interface — a read-only React dashboard focused on run inspection — plus the two run-listing endpoints that any interface needs and that do not exist yet.

**Architecture:** Two backend endpoints returning runs with outcome counts computed by a single `GROUP BY`, then a Vite/React/TypeScript app in `frontend/` whose types are **generated** from FastAPI's own `/openapi.json` so UI-vs-API schema drift becomes a compile error. In production FastAPI serves the built assets, so the whole platform is one process — and Playwright tests run against exactly that production shape.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.x (backend); React, TypeScript, Vite, React Router, TanStack Query, `openapi-typescript`, Playwright (frontend).

Source spec: `docs/superpowers/specs/2026-08-06-dashboard-foundation-design.md`

## Global Constraints

- Python `>=3.12`. Python dependencies via `uv add` / `uv add --dev`; never hand-edit `[project.dependencies]`.
- All commands run from the **repository root** (Alembic's `script_location` and pytest's `testpaths` are repo-root-relative). Frontend npm commands run from `frontend/` and are wrapped in Makefile targets that `cd` for you.
- `frontend/` is an **npm project, not a uv workspace member**. `[tool.uv.workspace] members = ["server", "plugin"]` stays unchanged.
- No new database tables, columns, or Alembic migrations in this slice. If you believe you need one, stop and flag it.
- Every error response body is exactly `{"code": str, "message": str, "details": object}`. The frontend parses this contract; it never discards `code`.
- The dashboard is **read-only**. No POST/PATCH/PUT from the UI in this slice.
- `frontend/src/api/schema.d.ts` is generated, committed to git, and never hand-edited.
- `docs/` is excluded from ruff's scope (`extend-exclude = ["docs"]` in root `pyproject.toml`); markdown code fences do not need to satisfy the formatter.
- Before declaring any task done, run **both** `uv run ruff check .` and `uv run ruff format --check .` from the repo root and read the real output. Prior slices repeatedly had "clean" claims that were not.

## File Structure

```
server/src/testforge/
  schemas/runs.py            + RunListItemOut                              [modify]
  services/run_service.py    + list_for_project, list_for_plan, outcome_counts [modify]
  api/routes/runs.py         + GET /api/projects/{key}/runs, build_run_list_items [modify]
  api/routes/plans.py        + GET /api/plans/{plan_id}/runs               [modify]
  seed.py                    + demo plan, run, and mixed results           [modify]
  cli/__init__.py            + tf openapi                                  [modify]
  main.py                    + static mount for the built frontend         [modify]

frontend/
  package.json  vite.config.ts  tsconfig.json  tsconfig.node.json
  index.html  playwright.config.ts
  src/
    main.tsx  App.tsx  styles.css
    api/schema.d.ts          generated, committed
    api/types.ts             named aliases over the generated schema
    api/client.ts            ApiError + apiFetch
    api/queries.ts           query-key factory + hooks
    pages/RunsPage.tsx  pages/PlansPage.tsx  pages/RunDetailPage.tsx
    components/OutcomeBadge.tsx  components/ResultsTable.tsx
    components/PlanCoverage.tsx  components/ErrorState.tsx
  tests/e2e/runs.spec.ts  tests/e2e/run-detail.spec.ts
```

Split rationale: `api/` owns everything about talking to the backend (types, transport, cache keys) so the pages never construct a URL or a query key by hand — that is what keeps the future write slice from having to hunt down call sites.

---

### Task 1: Run-listing endpoints

**Files:**
- Modify: `server/src/testforge/schemas/runs.py`, `server/src/testforge/services/run_service.py`, `server/src/testforge/api/routes/runs.py`, `server/src/testforge/api/routes/plans.py`
- Test: `server/tests/integration/test_run_listing_api.py`

**Interfaces:**
- Consumes: `RunOut` (schemas/runs.py), `ProjectService.get_by_key(key) -> Project`, `PlanService.get(plan_id) -> TestPlan`, `RunService(session)`.
- Produces: `RunListItemOut` (RunOut + `total_results: int` + `by_outcome: dict[str, int]`); `RunService.list_for_project(project, *, status, source, plan_id, limit) -> list[Run]`; `RunService.list_for_plan(plan_id, *, limit) -> list[Run]`; `RunService.outcome_counts(run_ids: list[str]) -> dict[str, dict[str, int]]`; `build_run_list_items(service, runs) -> list[RunListItemOut]` (in `api/routes/runs.py`, imported by `plans.py`).

Route placement follows the existing convention exactly: `runs.py` already owns `POST /api/projects/{project_key}/runs`, so the GET goes there; `plans.py` already owns `POST /api/plans/{plan_id}/runs`, so that GET goes there. `build_run_list_items` lives in `runs.py` and is imported by `plans.py` rather than duplicated.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/integration/test_run_listing_api.py`:

```python
import pytest

EXECUTED_AT = "2026-08-06T10:00:00Z"


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Coupon applies"})
    client.post("/api/projects/CHK/cases", json={"title": "Refund flow"})
    return "CHK"


def open_run(client, external_id, source="ci"):
    return client.post(
        f"/api/projects/CHK/runs",
        json={"external_id": external_id, "name": external_id, "source": source},
    ).json()


def ingest(client, run_id, case_key, outcome):
    return client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": case_key,
                    "test_identifier": f"tests/t.py::{case_key}_{outcome}",
                    "outcome": outcome,
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )


def test_listing_runs_returns_outcome_counts(client, project_key):
    run = open_run(client, "gha-1")
    ingest(client, run["id"], "CHK-1", "passed")
    ingest(client, run["id"], "CHK-2", "failed")

    listed = client.get("/api/projects/CHK/runs")

    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["id"] == run["id"]
    assert body[0]["total_results"] == 2
    assert body[0]["by_outcome"] == {"passed": 1, "failed": 1}


def test_listing_runs_is_empty_not_an_error_when_there_are_none(client, project_key):
    response = client.get("/api/projects/CHK/runs")

    assert response.status_code == 200
    assert response.json() == []


def test_a_run_with_no_results_reports_zero_not_missing(client, project_key):
    open_run(client, "empty-1")

    body = client.get("/api/projects/CHK/runs").json()

    assert body[0]["total_results"] == 0
    assert body[0]["by_outcome"] == {}


def test_listing_filters_by_status_and_source(client, project_key):
    open_run(client, "ci-1", source="ci")
    local = open_run(client, "local-1", source="local")
    client.post(f"/api/runs/{local['id']}/complete")

    by_source = client.get("/api/projects/CHK/runs", params={"source": "ci"}).json()
    by_status = client.get("/api/projects/CHK/runs", params={"status": "completed"}).json()

    assert [r["external_id"] for r in by_source] == ["ci-1"]
    assert [r["external_id"] for r in by_status] == ["local-1"]


def test_listing_never_leaks_another_projects_runs(client, project_key):
    client.post("/api/projects", json={"key": "OTH", "name": "Other"})
    client.post(
        "/api/projects/OTH/runs", json={"external_id": "foreign-1", "source": "ci"}
    )
    open_run(client, "ours-1")

    body = client.get("/api/projects/CHK/runs").json()

    assert [r["external_id"] for r in body] == ["ours-1"]


def test_listing_counts_match_what_get_run_reports(client, project_key):
    run = open_run(client, "agree-1")
    ingest(client, run["id"], "CHK-1", "passed")
    ingest(client, run["id"], "CHK-2", "failed")

    listed = client.get("/api/projects/CHK/runs").json()[0]
    detail = client.get(f"/api/runs/{run['id']}").json()

    assert listed["by_outcome"] == detail["by_outcome"], (
        "the GROUP BY aggregate must agree with get_run's in-memory count"
    )
    assert listed["total_results"] == detail["total_results"]


def test_plan_runs_returns_that_plans_run_history(client, project_key):
    plan = client.post(
        "/api/projects/CHK/plans", json={"name": "Release 1.0", "case_keys": ["CHK-1"]}
    ).json()
    first = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "first"}).json()
    second = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "second"}).json()
    open_run(client, "unrelated-1")

    body = client.get(f"/api/plans/{plan['id']}/runs").json()

    assert {r["id"] for r in body} == {first["id"], second["id"]}
    assert all(r["plan_id"] == plan["id"] for r in body)


def test_plan_runs_404s_for_an_unknown_plan(client, project_key):
    response = client.get("/api/plans/does-not-exist/runs")

    assert response.status_code == 404
    assert response.json()["code"] == "plan_not_found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_run_listing_api.py -v`
Expected: FAIL — 404s, the listing routes do not exist.

- [ ] **Step 3: Add the schema**

Append to `server/src/testforge/schemas/runs.py`:

```python
class RunListItemOut(RunOut):
    total_results: int
    by_outcome: dict[str, int]
```

- [ ] **Step 4: Add the service methods**

In `server/src/testforge/services/run_service.py`, add `func` to the SQLAlchemy import so the line reads:

```python
from sqlalchemy import func, select
```

and add `Result` to the models import so the line reads:

```python
from testforge.models.run import Result, Run
```

Then add these three methods to `RunService`:

```python
    def list_for_project(
        self,
        project: Project,
        *,
        status: str | None = None,
        source: str | None = None,
        plan_id: str | None = None,
        limit: int = 50,
    ) -> list[Run]:
        stmt = select(Run).where(Run.project_id == project.id)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        if source is not None:
            stmt = stmt.where(Run.source == source)
        if plan_id is not None:
            stmt = stmt.where(Run.plan_id == plan_id)
        stmt = stmt.order_by(Run.started_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def list_for_plan(self, plan_id: str, *, limit: int = 50) -> list[Run]:
        stmt = (
            select(Run)
            .where(Run.plan_id == plan_id)
            .order_by(Run.started_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def outcome_counts(self, run_ids: list[str]) -> dict[str, dict[str, int]]:
        """Outcome tallies for many runs in one GROUP BY.

        The single-run path (``get_run``) counts in Python, which is fine for one run
        but would be N queries' worth of rows here.
        """
        if not run_ids:
            return {}
        rows = self.session.execute(
            select(Result.run_id, Result.outcome, func.count())
            .where(Result.run_id.in_(run_ids))
            .group_by(Result.run_id, Result.outcome)
        )
        counts: dict[str, dict[str, int]] = {}
        for run_id, outcome, count in rows:
            counts.setdefault(run_id, {})[outcome] = count
        return counts
```

- [ ] **Step 5: Add the project-runs route**

In `server/src/testforge/api/routes/runs.py`, add `Query` to the FastAPI import so the line reads:

```python
from fastapi import APIRouter, Depends, Query, Response
```

add `RunListItemOut` to the schemas import from `testforge.schemas.runs`, then add:

```python
def build_run_list_items(service: RunService, runs: list[Run]) -> list[RunListItemOut]:
    """Attach outcome tallies to run rows. Imported by plans.py so the two
    listing endpoints cannot drift apart."""
    counts = service.outcome_counts([run.id for run in runs])
    return [
        RunListItemOut(
            **RunOut.model_validate(run).model_dump(),
            total_results=sum(counts.get(run.id, {}).values()),
            by_outcome=counts.get(run.id, {}),
        )
        for run in runs
    ]


@router.get("/api/projects/{project_key}/runs", response_model=list[RunListItemOut])
def list_project_runs(
    project_key: str,
    status: str | None = None,
    source: str | None = None,
    plan_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[RunListItemOut]:
    project = ProjectService(session).get_by_key(project_key)
    service = RunService(session)
    runs = service.list_for_project(
        project, status=status, source=source, plan_id=plan_id, limit=limit
    )
    return build_run_list_items(service, runs)
```

`Run` must be importable in this module for the helper's type hint — add `from testforge.models.run import Run` if it is not already imported (check the file's existing imports first; `Result` is already imported there).

- [ ] **Step 6: Add the plan-runs route**

In `server/src/testforge/api/routes/plans.py`, add these imports:

```python
from fastapi import Query

from testforge.api.routes.runs import build_run_list_items
from testforge.schemas.runs import RunListItemOut
from testforge.services.run_service import RunService
```

(merge `Query` into the existing `from fastapi import ...` line rather than adding a second import statement), then add:

```python
@router.get("/api/plans/{plan_id}/runs", response_model=list[RunListItemOut])
def list_plan_runs(
    plan_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[RunListItemOut]:
    PlanService(session).get(plan_id)
    service = RunService(session)
    return build_run_list_items(service, service.list_for_plan(plan_id, limit=limit))
```

The bare `PlanService(session).get(plan_id)` call exists for its side effect: it raises `plan_not_found` for an unknown plan, so the endpoint 404s instead of returning an empty list that looks like "this plan has no runs."

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_run_listing_api.py -v`
Expected: PASS — 8 passed

- [ ] **Step 8: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 142 passed (134 existing + 8 new).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add run-listing endpoints with aggregated outcome counts"
```

---

### Task 2: Demo seed with a real run

**Files:**
- Modify: `server/src/testforge/seed.py`
- Test: `server/tests/integration/test_seed.py` (existing — append)

**Interfaces:**
- Consumes: `PlanService.create(...)`, `RunService.open_for_plan(*, plan, name, external_id, actor) -> Run`, `RunService.complete(run_id)`, `IngestionService.ingest(*, run, results) -> IngestSummary`, `ResultIn`.
- Produces: `seed_demo(session)` returning `{"projects", "suites", "cases", "plans", "runs", "results"}`.

The dashboard is a run-inspection tool and the current seed creates zero runs, so `make demo` would open an empty dashboard. This task gives it something real to show: passes, a failure with a genuine-looking message and stack trace, a skip, and one unresolved result.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/integration/test_seed.py`:

```python
def test_seed_creates_a_completed_run_with_mixed_results(db_session):
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["plans"] == 1
    assert counts["runs"] == 1
    assert counts["results"] >= 5

    project = ProjectService(db_session).get_by_key("CHK")
    runs = RunService(db_session).list_for_project(project)
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].plan_id is not None


def test_seeded_run_has_a_failure_a_skip_and_an_unresolved_result(db_session):
    seed_demo(db_session)
    db_session.commit()

    project = ProjectService(db_session).get_by_key("CHK")
    run = RunService(db_session).list_for_project(project)[0]
    results = IngestionService(db_session).results_for_run(run)

    outcomes = {r.outcome for r in results}
    assert "passed" in outcomes
    assert "failed" in outcomes
    assert "skipped" in outcomes

    failed = next(r for r in results if r.outcome == "failed")
    assert failed.failure_message, "a demo failure with no message shows an empty dashboard panel"
    assert failed.stack_trace

    unresolved = [r for r in results if r.test_case_id is None]
    assert len(unresolved) == 1
    assert unresolved[0].unresolved_case_key is not None


def test_seed_is_still_idempotent_including_the_run(db_session):
    seed_demo(db_session)
    db_session.commit()
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts == {
        "projects": 0,
        "suites": 0,
        "cases": 0,
        "plans": 0,
        "runs": 0,
        "results": 0,
    }
```

Add the imports these need to the top of the file:

```python
from testforge.services.ingestion_service import IngestionService
from testforge.services.run_service import RunService
```

(`ProjectService` and `seed_demo` are already imported there.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_seed.py -v`
Expected: FAIL — `KeyError: 'plans'`, the seed returns no such key.

- [ ] **Step 3: Extend the seed**

In `server/src/testforge/seed.py`, add these imports:

```python
from datetime import UTC, datetime

from testforge.schemas.results import ResultIn
from testforge.services.ingestion_service import IngestionService
from testforge.services.plan_service import PlanService
from testforge.services.run_service import RunService
```

Add this module-level constant after `DEMO_CASES`:

```python
DEMO_STACK_TRACE = """Traceback (most recent call last):
  File "tests/test_checkout.py", line 42, in test_payment_retry_prompt
    assert "Retry payment" in page.text
AssertionError: assert 'Retry payment' in 'Payment failed. Contact support.'"""

DEMO_RESULTS = [
    ("CHK-1", "tests/test_checkout.py::test_coupon_applies", "passed", None, None),
    ("CHK-2", "tests/test_checkout.py::test_expired_coupon", "passed", None, None),
    (
        "CHK-3",
        "tests/test_checkout.py::test_payment_retry_prompt",
        "failed",
        "assert 'Retry payment' in 'Payment failed. Contact support.'",
        DEMO_STACK_TRACE,
    ),
    ("CHK-4", "tests/test_mobile.py::test_order_history", "skipped", None, None),
    ("CHK-5", "tests/test_auth.py::test_session_expiry", "passed", None, None),
    ("CHK-404", "tests/test_search.py::test_orphaned_check", "passed", None, None),
]
```

`CHK-404` is deliberate: no such case exists, so it lands as an unresolved result — the dashboard needs one to demonstrate that callout, and Slice 1 specifically chose to store rather than drop these.

Change the early-return dict so its keys match the full return shape:

```python
    if existing is not None:
        return {"projects": 0, "suites": 0, "cases": 0, "plans": 0, "runs": 0, "results": 0}
```

Then, immediately before the existing `return` at the end of `seed_demo`, add:

```python
    plan = PlanService(session).create(
        project=project,
        name="Release 1.0 regression",
        description="Everything tagged for the 1.0 release.",
        milestone="1.0",
        environment="staging",
        case_keys=[],
        filter_={"tag": "checkout"},
        actor="seed",
    )

    runs = RunService(session)
    run = runs.open_for_plan(
        plan=plan, name="nightly regression", external_id="demo-run-1", actor="seed"
    )
    executed_at = datetime.now(UTC)
    IngestionService(session).ingest(
        run=run,
        results=[
            ResultIn(
                case_key=case_key,
                test_identifier=identifier,
                framework="pytest",
                outcome=outcome,
                duration_ms=120,
                failure_message=message,
                stack_trace=trace,
                executed_at=executed_at,
            )
            for case_key, identifier, outcome, message, trace in DEMO_RESULTS
        ],
    )
    runs.complete(run.id)
```

and change the final return to:

```python
    return {
        "projects": 1,
        "suites": 2,
        "cases": len(DEMO_CASES),
        "plans": 1,
        "runs": 1,
        "results": len(DEMO_RESULTS),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_seed.py -v`
Expected: PASS — 5 passed (2 existing + 3 new).

- [ ] **Step 5: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 145 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: seed a completed demo run with mixed results"
```

---

### Task 3: Frontend scaffold, generated types, and API layer

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/.gitignore`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/queries.ts`
- Create (generated): `frontend/src/api/schema.d.ts`
- Modify: `server/src/testforge/cli/__init__.py`, `Makefile`
- Test: `frontend/src/api/client.test.ts` is **not** created — this slice has no component/unit test layer (see the spec's §7); this task is verified by typecheck and build.

**Interfaces:**
- Consumes: the running backend's `/openapi.json`.
- Produces: `tf openapi` CLI command; `make types`; `ApiError` (fields `status`, `code`, `message`, `details`); `apiFetch<T>(path)`; `keys` query-key factory; type aliases `Project`, `RunListItem`, `RunSummary`, `ResultRow`, `Plan`, `PlanCase`.

- [ ] **Step 1: Add the `tf openapi` command**

The frontend's types are generated from the backend's schema. Dumping it via the CLI rather than curling a running server keeps type generation hermetic — no server needs to be up.

In `server/src/testforge/cli/__init__.py`, add this command (the file already imports `json` and `typer`, and already imports `create_app`'s siblings — add `from testforge.main import create_app` to the imports):

```python
@app.command("openapi")
def openapi() -> None:
    """Print the OpenAPI schema. Feeds the frontend's TypeScript type generation."""
    typer.echo(json.dumps(create_app().openapi(), indent=2, sort_keys=True))
```

`sort_keys=True` matters: it makes the output stable across runs so the committed `schema.d.ts` only changes when the API genuinely changes.

- [ ] **Step 2: Verify it works**

Run: `uv run tf openapi | head -5`
Expected: JSON beginning with `{` and an `"openapi"` key. No server needed.

- [ ] **Step 3: Scaffold the npm project**

```bash
mkdir -p frontend/src/api frontend/src/pages frontend/src/components frontend/tests/e2e
```

Create `frontend/package.json`:

```json
{
  "name": "testforge-dashboard",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "typecheck": "tsc -b --noEmit",
    "gen:types": "openapi-typescript ../openapi.json -o src/api/schema.d.ts",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.59.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.27.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "@types/react": "^18.3.11",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.2",
    "openapi-typescript": "^7.4.1",
    "typescript": "^5.6.3",
    "vite": "^5.4.9"
  }
}
```

Create `frontend/.gitignore`:

```
node_modules/
dist/
test-results/
playwright-report/
```

Create `frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>TestForge</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `frontend/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "resolveJsonModule": true
  },
  "include": ["src", "tests"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true
  },
  "include": ["vite.config.ts", "playwright.config.ts"]
}
```

- [ ] **Step 4: Install and generate types**

```bash
cd frontend && npm install && cd ..
uv run tf openapi > openapi.json
cd frontend && npm run gen:types && cd ..
```

Expected: `frontend/src/api/schema.d.ts` exists and contains `RunListItemOut`.

Verify: `grep -c "RunListItemOut" frontend/src/api/schema.d.ts` → at least 1.

- [ ] **Step 5: Add the Makefile targets**

Replace the `.PHONY` line in `Makefile` and add the new targets:

```makefile
.PHONY: install dev test lint fmt migrate seed demo types ui build-ui e2e

types:
	uv run tf openapi > openapi.json
	cd frontend && npm run gen:types

ui:
	cd frontend && npm run dev

build-ui:
	cd frontend && npm ci && npm run build

e2e:
	cd frontend && npm run test:e2e
```

Add `openapi.json` to the repo-root `.gitignore` — it is an intermediate artifact; the committed output is `schema.d.ts`:

```
openapi.json
```

- [ ] **Step 6: Write the API layer**

Create `frontend/src/api/types.ts`:

```ts
import type { components } from "./schema";

/** Named aliases over the generated schema, so pages never index into `components`. */
export type Project = components["schemas"]["ProjectOut"];
export type RunListItem = components["schemas"]["RunListItemOut"];
export type RunSummary = components["schemas"]["RunSummaryOut"];
export type ResultRow = components["schemas"]["ResultOut"];
export type Plan = components["schemas"]["PlanOut"];
export type PlanCase = components["schemas"]["PlanCaseOut"];
export type PlanProgress = components["schemas"]["PlanProgress"];
```

Create `frontend/src/api/client.ts`:

```ts
/** The platform's error contract: every backend error is {code, message, details}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const parsed = body as
      | { code?: string; message?: string; details?: Record<string, unknown> }
      | null;
    throw new ApiError(
      response.status,
      parsed?.code ?? "unknown_error",
      parsed?.message ?? response.statusText,
      parsed?.details ?? {},
    );
  }

  return (await response.json()) as T;
}
```

Create `frontend/src/api/queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "./client";
import type { Plan, Project, ResultRow, RunListItem, RunSummary } from "./types";

export type RunFilters = { status?: string; source?: string };

/**
 * Hierarchical query keys. TanStack Query invalidates by key prefix, so
 * invalidating ['runs', id] catches the run and its results — which is what
 * keeps the eventual write slice from having to normalise every call site.
 */
export const keys = {
  projects: () => ["projects"] as const,
  runs: (projectKey: string, filters: RunFilters = {}) =>
    ["projects", projectKey, "runs", filters] as const,
  plans: (projectKey: string) => ["projects", projectKey, "plans"] as const,
  run: (runId: string) => ["runs", runId] as const,
  runResults: (runId: string) => ["runs", runId, "results"] as const,
  planRuns: (planId: string) => ["plans", planId, "runs"] as const,
};

function withParams(path: string, params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export function useProjects() {
  return useQuery({
    queryKey: keys.projects(),
    queryFn: () => apiFetch<Project[]>("/api/projects"),
  });
}

export function useRuns(projectKey: string, filters: RunFilters = {}) {
  return useQuery({
    queryKey: keys.runs(projectKey, filters),
    queryFn: () =>
      apiFetch<RunListItem[]>(
        withParams(`/api/projects/${projectKey}/runs`, filters),
      ),
    enabled: Boolean(projectKey),
  });
}

export function usePlans(projectKey: string) {
  return useQuery({
    queryKey: keys.plans(projectKey),
    queryFn: () => apiFetch<Plan[]>(`/api/projects/${projectKey}/plans`),
    enabled: Boolean(projectKey),
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: keys.run(runId),
    queryFn: () => apiFetch<RunSummary>(`/api/runs/${runId}`),
  });
}

export function useRunResults(runId: string) {
  return useQuery({
    queryKey: keys.runResults(runId),
    queryFn: () => apiFetch<ResultRow[]>(`/api/runs/${runId}/results`),
  });
}

export function usePlanRuns(planId: string) {
  return useQuery({
    queryKey: keys.planRuns(planId),
    queryFn: () => apiFetch<RunListItem[]>(`/api/plans/${planId}/runs`),
  });
}
```

- [ ] **Step 7: Write the app shell**

Create `frontend/src/styles.css`:

```css
:root {
  --bg: #ffffff;
  --fg: #16181d;
  --muted: #656d76;
  --line: #d8dee4;
  --pass: #1a7f37;
  --fail: #cf222e;
  --warn: #9a6700;
  --accent: #0969da;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}

* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.shell-header {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0.75rem 1.5rem;
  border-bottom: 1px solid var(--line);
}
.shell-header h1 { font-size: 1rem; margin: 0; }
.shell-header nav { display: flex; gap: 1rem; }
.shell-main { padding: 1.5rem; max-width: 1100px; }

table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line); }
th { font-size: 0.8rem; text-transform: uppercase; color: var(--muted); font-weight: 600; }

.badge {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
}
.badge-passed { background: #dafbe1; color: var(--pass); }
.badge-failed, .badge-error { background: #ffebe9; color: var(--fail); }
.badge-skipped, .badge-blocked { background: #fff8c5; color: var(--warn); }
.badge-neutral { background: #eaeef2; color: var(--muted); }

.muted { color: var(--muted); }
.panel { border: 1px solid var(--line); border-radius: 6px; padding: 1rem; margin-bottom: 1.5rem; }
.panel h2 { margin: 0 0 0.75rem; font-size: 0.95rem; }
.callout { border-left: 3px solid var(--warn); background: #fff8c5; padding: 0.75rem 1rem; }
pre {
  background: #f6f8fa;
  padding: 0.75rem;
  overflow-x: auto;
  border-radius: 6px;
  font-size: 0.8rem;
}
```

Create `frontend/src/App.tsx`:

```tsx
import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { useProjects } from "./api/queries";
import { ErrorState } from "./components/ErrorState";
import { PlansPage } from "./pages/PlansPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";

function Shell({ children }: { children: React.ReactNode }) {
  const { data: projects } = useProjects();
  const { projectKey } = useParams<{ projectKey: string }>();
  const navigate = useNavigate();
  const active = projectKey ?? projects?.[0]?.key ?? "";

  return (
    <>
      <header className="shell-header">
        <h1>TestForge</h1>
        {active && (
          <nav>
            <Link to={`/projects/${active}/runs`}>Runs</Link>
            <Link to={`/projects/${active}/plans`}>Plans</Link>
          </nav>
        )}
        {projects && projects.length > 0 && (
          <select
            data-testid="project-picker"
            value={active}
            onChange={(e) => navigate(`/projects/${e.target.value}/runs`)}
          >
            {projects.map((project) => (
              <option key={project.key} value={project.key}>
                {project.key} — {project.name}
              </option>
            ))}
          </select>
        )}
      </header>
      <main className="shell-main">{children}</main>
    </>
  );
}

function Landing() {
  const { data: projects, isLoading, error } = useProjects();
  if (isLoading) return <p className="muted">Loading…</p>;
  if (error) return <ErrorState error={error} />;
  if (!projects?.length) return <p className="muted">No projects yet.</p>;
  return <Navigate to={`/projects/${projects[0].key}/runs`} replace />;
}

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/projects/:projectKey/runs" element={<RunsPage />} />
        <Route path="/projects/:projectKey/plans" element={<PlansPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
      </Routes>
    </Shell>
  );
}
```

Create `frontend/src/main.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
```

`retry: false` is deliberate — the default retries three times, which turns a 404 into a multi-second wait before the error state renders, and makes the E2E error test slow and flaky.

Create `frontend/src/components/ErrorState.tsx`:

```tsx
import { ApiError } from "../api/client";

const MESSAGES: Record<string, string> = {
  run_not_found: "That run does not exist.",
  plan_not_found: "That plan does not exist.",
  project_not_found: "That project does not exist.",
};

export function ErrorState({ error }: { error: unknown }) {
  if (error instanceof ApiError) {
    return (
      <div className="panel" data-testid="error-state" data-code={error.code}>
        <h2>{MESSAGES[error.code] ?? "Something went wrong"}</h2>
        <p className="muted">
          {error.code} · {error.message}
        </p>
      </div>
    );
  }
  return (
    <div className="panel" data-testid="error-state" data-code="unknown_error">
      <h2>Something went wrong</h2>
    </div>
  );
}
```

- [ ] **Step 8: Add placeholder pages so the build succeeds**

The three page files are written properly in Tasks 4 and 5. Create minimal versions now so this task's build and typecheck pass:

Create `frontend/src/pages/RunsPage.tsx`:

```tsx
export function RunsPage() {
  return <p className="muted">Runs</p>;
}
```

Create `frontend/src/pages/PlansPage.tsx`:

```tsx
export function PlansPage() {
  return <p className="muted">Plans</p>;
}
```

Create `frontend/src/pages/RunDetailPage.tsx`:

```tsx
export function RunDetailPage() {
  return <p className="muted">Run detail</p>;
}
```

- [ ] **Step 9: Verify typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build && cd ..`
Expected: no type errors; `frontend/dist/index.html` exists.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: scaffold the React dashboard with generated API types"
```

---

### Task 4: Runs list and plans list pages

**Files:**
- Create: `frontend/src/components/OutcomeBadge.tsx`
- Modify: `frontend/src/pages/RunsPage.tsx`, `frontend/src/pages/PlansPage.tsx`

**Interfaces:**
- Consumes: `useRuns`, `usePlans`, `usePlanRuns` (Task 3), `ErrorState`, types `RunListItem`/`Plan`.
- Produces: `OutcomeBadge({ outcome, count })`; the two list pages.

- [ ] **Step 1: Write the outcome badge**

Create `frontend/src/components/OutcomeBadge.tsx`:

```tsx
const KNOWN = ["passed", "failed", "error", "skipped", "blocked"];

export function OutcomeBadge({ outcome, count }: { outcome: string; count?: number }) {
  const cls = KNOWN.includes(outcome) ? `badge-${outcome}` : "badge-neutral";
  return (
    <span className={`badge ${cls}`} data-testid={`outcome-${outcome}`}>
      {outcome}
      {count === undefined ? "" : ` ${count}`}
    </span>
  );
}
```

- [ ] **Step 2: Write the runs page**

Replace `frontend/src/pages/RunsPage.tsx`:

```tsx
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useRuns } from "../api/queries";
import { ErrorState } from "../components/ErrorState";
import { OutcomeBadge } from "../components/OutcomeBadge";

const STATUSES = ["", "running", "completed", "canceled"];
const SOURCES = ["", "ci", "local", "manual", "runner"];

export function RunsPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const [params, setParams] = useSearchParams();
  const filters = {
    status: params.get("status") ?? undefined,
    source: params.get("source") ?? undefined,
  };
  const { data: runs, isLoading, error } = useRuns(projectKey, filters);

  function setFilter(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  }

  if (error) return <ErrorState error={error} />;

  return (
    <>
      <h2>Runs</h2>
      <div className="panel">
        <label>
          Status{" "}
          <select
            data-testid="filter-status"
            value={filters.status ?? ""}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "any"}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Source{" "}
          <select
            data-testid="filter-source"
            value={filters.source ?? ""}
            onChange={(e) => setFilter("source", e.target.value)}
          >
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {s || "any"}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {runs && runs.length === 0 && <p className="muted">No runs yet.</p>}
      {runs && runs.length > 0 && (
        <table data-testid="runs-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Source</th>
              <th>Started</th>
              <th>Results</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} data-testid="run-row">
                <td>
                  <Link to={`/runs/${run.id}`}>{run.name ?? run.external_id}</Link>
                  {run.plan_id && <span className="muted"> · plan</span>}
                </td>
                <td>{run.status}</td>
                <td>{run.source}</td>
                <td className="muted">
                  {new Date(run.started_at).toLocaleString()}
                </td>
                <td>
                  {Object.entries(run.by_outcome).length === 0 ? (
                    <span className="muted">none</span>
                  ) : (
                    Object.entries(run.by_outcome)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([outcome, count]) => (
                        <OutcomeBadge key={outcome} outcome={outcome} count={count} />
                      ))
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
```

- [ ] **Step 3: Write the plans page**

Replace `frontend/src/pages/PlansPage.tsx`:

```tsx
import { Link, useParams } from "react-router-dom";

import { usePlanRuns, usePlans } from "../api/queries";
import { ErrorState } from "../components/ErrorState";

function PlanRuns({ planId }: { planId: string }) {
  const { data: runs } = usePlanRuns(planId);
  if (!runs?.length) return <span className="muted">no runs</span>;
  return (
    <>
      {runs.map((run) => (
        <span key={run.id}>
          <Link to={`/runs/${run.id}`}>{run.name ?? run.external_id}</Link>{" "}
        </span>
      ))}
    </>
  );
}

export function PlansPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const { data: plans, isLoading, error } = usePlans(projectKey);

  if (error) return <ErrorState error={error} />;

  return (
    <>
      <h2>Plans</h2>
      {isLoading && <p className="muted">Loading…</p>}
      {plans && plans.length === 0 && <p className="muted">No plans yet.</p>}
      {plans && plans.length > 0 && (
        <table data-testid="plans-table">
          <thead>
            <tr>
              <th>Plan</th>
              <th>Milestone</th>
              <th>Environment</th>
              <th>Cases</th>
              <th>Status</th>
              <th>Runs</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((plan) => (
              <tr key={plan.id} data-testid="plan-row">
                <td>{plan.name}</td>
                <td>{plan.milestone ?? <span className="muted">—</span>}</td>
                <td>{plan.environment ?? <span className="muted">—</span>}</td>
                <td>{plan.case_count}</td>
                <td>{plan.status}</td>
                <td>
                  <PlanRuns planId={plan.id} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
```

- [ ] **Step 4: Verify typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build && cd ..`
Expected: no type errors, build succeeds.

- [ ] **Step 5: Look at it**

Run the backend and the dev server in two shells:

```bash
make seed && make dev
```

```bash
make ui
```

Open `http://localhost:5173`. Expected: redirects to the seeded project's runs page, showing one completed run named "nightly regression" with badges for passed/failed/skipped. Confirm the plans page lists "Release 1.0 regression".

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add runs and plans list pages"
```

---

### Task 5: Run detail page

**Files:**
- Create: `frontend/src/components/PlanCoverage.tsx`, `frontend/src/components/ResultsTable.tsx`
- Modify: `frontend/src/pages/RunDetailPage.tsx`

**Interfaces:**
- Consumes: `useRun`, `useRunResults` (Task 3), `OutcomeBadge` (Task 4), `ErrorState`, types `RunSummary`/`ResultRow`/`PlanProgress`.
- Produces: `PlanCoverage({ progress })`; `ResultsTable({ results })`; the run detail page.

This is the slice's centerpiece: failures first, readable without leaving the page, with unresolved results called out rather than buried.

- [ ] **Step 1: Write the coverage panel**

Create `frontend/src/components/PlanCoverage.tsx`:

```tsx
import type { PlanProgress } from "../api/types";
import { OutcomeBadge } from "./OutcomeBadge";

export function PlanCoverage({ progress }: { progress: PlanProgress }) {
  return (
    <section className="panel" data-testid="plan-coverage">
      <h2>Plan coverage</h2>
      <p>
        <strong data-testid="coverage-ratio">
          {progress.cases_with_result} of {progress.total_cases}
        </strong>{" "}
        cases have a result{" "}
        {Object.entries(progress.by_outcome)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([outcome, count]) => (
            <OutcomeBadge key={outcome} outcome={outcome} count={count} />
          ))}
      </p>
      {progress.cases_without_result.length > 0 && (
        <p className="muted" data-testid="cases-without-result">
          Not executed: {progress.cases_without_result.join(", ")}
        </p>
      )}
    </section>
  );
}
```

- [ ] **Step 2: Write the results table**

Create `frontend/src/components/ResultsTable.tsx`:

```tsx
import { Fragment, useState } from "react";

import type { ResultRow } from "../api/types";
import { OutcomeBadge } from "./OutcomeBadge";

/** Failures first — this dashboard exists to answer "what broke?". */
const ORDER: Record<string, number> = {
  failed: 0,
  error: 1,
  blocked: 2,
  skipped: 3,
  passed: 4,
};

function rank(outcome: string): number {
  return ORDER[outcome] ?? 99;
}

function ResultDetail({ result }: { result: ResultRow }) {
  return (
    <tr data-testid="result-detail">
      <td colSpan={4}>
        {result.failure_type && (
          <p className="muted">{result.failure_type}</p>
        )}
        {result.failure_message && <pre>{result.failure_message}</pre>}
        {result.stack_trace && <pre>{result.stack_trace}</pre>}
        {!result.failure_message && !result.stack_trace && (
          <p className="muted">No failure detail recorded.</p>
        )}
      </td>
    </tr>
  );
}

export function ResultsTable({ results }: { results: ResultRow[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const sorted = [...results].sort(
    (a, b) =>
      rank(a.outcome) - rank(b.outcome) ||
      a.test_identifier.localeCompare(b.test_identifier),
  );

  return (
    <table data-testid="results-table">
      <thead>
        <tr>
          <th>Outcome</th>
          <th>Test</th>
          <th>Case</th>
          <th>Duration</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((result) => (
          <Fragment key={result.id}>
            <tr
              data-testid="result-row"
              data-outcome={result.outcome}
              onClick={() => setExpanded(expanded === result.id ? null : result.id)}
              style={{ cursor: "pointer" }}
            >
              <td>
                <OutcomeBadge outcome={result.outcome} />
              </td>
              <td>{result.test_identifier}</td>
              <td className="muted">
                {result.unresolved_case_key ?? (result.test_case_id ? "linked" : "—")}
              </td>
              <td className="muted">
                {result.duration_ms === null ? "—" : `${result.duration_ms} ms`}
              </td>
            </tr>
            {expanded === result.id && <ResultDetail result={result} />}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}
```

`Fragment` (rather than the `<>` shorthand) is required here: a fragment produced inside `map`
needs a `key`, and the shorthand syntax cannot take one.
```

- [ ] **Step 3: Write the run detail page**

Replace `frontend/src/pages/RunDetailPage.tsx`:

```tsx
import { Link, useParams } from "react-router-dom";

import { useRun, useRunResults } from "../api/queries";
import { ErrorState } from "../components/ErrorState";
import { PlanCoverage } from "../components/PlanCoverage";
import { ResultsTable } from "../components/ResultsTable";

export function RunDetailPage() {
  const { runId = "" } = useParams<{ runId: string }>();
  const { data: run, isLoading, error } = useRun(runId);
  const { data: results } = useRunResults(runId);

  if (error) return <ErrorState error={error} />;
  if (isLoading || !run) return <p className="muted">Loading…</p>;

  const unresolved = (results ?? []).filter((r) => r.test_case_id === null);

  return (
    <>
      <h2 data-testid="run-name">{run.name ?? run.external_id}</h2>
      <p className="muted">
        {run.status} · {run.source} ·{" "}
        {new Date(run.started_at).toLocaleString()} · <Link to="/">all runs</Link>
      </p>

      {run.plan_progress && <PlanCoverage progress={run.plan_progress} />}

      {unresolved.length > 0 && (
        <div className="callout" data-testid="unresolved-callout">
          <strong>{unresolved.length} unresolved result(s).</strong> These tests ran but
          match no test case:{" "}
          {unresolved.map((r) => r.unresolved_case_key ?? r.test_identifier).join(", ")}
        </div>
      )}

      <h3>Results</h3>
      {results && results.length > 0 ? (
        <ResultsTable results={results} />
      ) : (
        <p className="muted">No results recorded.</p>
      )}
    </>
  );
}
```

The "all runs" link points at `/` rather than at the project's runs list: `RunSummaryOut` carries
`project_id` (a UUID), not the project *key* the route needs, and the landing route already
redirects to the first project's runs. Adding a project-key lookup here would be the only reason
this page needed a second query.

- [ ] **Step 4: Verify typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build && cd ..`
Expected: no type errors.

- [ ] **Step 5: Look at it**

With `make dev` and `make ui` running, open the seeded run from the runs list. Expected: the failed result sorts to the top; clicking it reveals the assertion message and stack trace; the coverage panel reports cases with and without results; the unresolved callout names `CHK-404`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add the run detail page with coverage and failure inspection"
```

---

### Task 6: Serve the built frontend from FastAPI

**Files:**
- Modify: `server/src/testforge/main.py`, `server/src/testforge/config.py`, `Makefile`
- Test: `server/tests/integration/test_static_serving.py`

**Interfaces:**
- Consumes: `create_app(settings)`, `Settings`.
- Produces: `Settings.frontend_dist: str | None`; SPA serving with an `index.html` fallback.

One process serves both API and UI, which is what the Playwright suite (Task 7) will drive, and what a Docker image would ship.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/integration/test_static_serving.py`:

```python
from testforge.config import Settings
from testforge.main import create_app
from starlette.testclient import TestClient


def build_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>TestForge</title>")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('app');")
    return dist


def test_api_routes_still_win_over_the_static_mount(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist)
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "testforge"}


def test_index_is_served_at_the_root(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TestForge" in response.text


def test_a_client_side_route_falls_back_to_index(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/some-run-id")

    assert response.status_code == 200, (
        "React Router owns this path; a hard refresh must not 404"
    )
    assert "TestForge" in response.text


def test_an_unknown_api_path_still_404s_as_json(tmp_path):
    dist = build_dist(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=str(dist)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/nope")

    assert response.status_code == 404
    assert "TestForge" not in response.text, (
        "an unknown API path must not silently return the SPA shell"
    )


def test_the_app_starts_fine_with_no_frontend_built(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", frontend_dist=None)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_static_serving.py -v`
Expected: FAIL — `Settings` has no `frontend_dist` field.

- [ ] **Step 3: Add the setting**

In `server/src/testforge/config.py`, add this field to `Settings`:

```python
    frontend_dist: str | None = None
```

- [ ] **Step 4: Mount the SPA**

In `server/src/testforge/main.py`, add these imports:

```python
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

and insert this block just before `return app` in `create_app`:

```python
    dist = Path(resolved.frontend_dist) if resolved.frontend_dist else None
    if dist is not None and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:
            """Serve the SPA shell for any path the API did not claim.

            Registered last, so every real API route matches first. `/api/*` is
            excluded explicitly: an unknown API path must 404 as JSON rather than
            returning HTML a client would try to parse.
            """
            if full_path.startswith("api/"):
                raise AppError("not_found", f"no such path: /{full_path}", 404)
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
```

Add `AppError` to the imports from `testforge.errors` (the module already imports `register_error_handlers` from there).

`not_found` is a new error code, used only for this catch-all. Every other code in the platform names a domain concept; this one names an HTTP fact, because the catch-all has no domain to speak of.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_static_serving.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Wire it into the Makefile**

Add to `Makefile`:

```makefile
serve: build-ui migrate
	TESTFORGE_FRONTEND_DIST=frontend/dist uv run uvicorn testforge.main:create_app --factory --port 8000
```

and add `serve` to the `.PHONY` line. Update the `demo` target so it points at the real thing:

```makefile
demo: seed build-ui
	@echo "Now run 'make serve' and open http://localhost:8000"
```

- [ ] **Step 7: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 150 passed.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: serve the built dashboard from FastAPI with an SPA fallback"
```

---

### Task 7: Playwright end-to-end tests and CI

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/tests/e2e/runs.spec.ts`, `frontend/tests/e2e/run-detail.spec.ts`
- Modify: `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6; the seeded data from Task 2.
- Produces: `npm run test:e2e`; two CI additions (E2E job, schema-drift check).

Tests drive a real browser against a real FastAPI process serving the real built frontend against a real SQLite database — the same shape production runs. Generated types (Task 3) cover the static half of the UI/API contract; these cover the behavioural half.

- [ ] **Step 1: Write the Playwright config**

Create `frontend/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

const PORT = 8123;
const DB = "e2e.db";

/**
 * One server: FastAPI serving the built SPA, exactly as production does.
 * The database is rebuilt and seeded per run so specs can assert on known data.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "on-first-retry" },
  webServer: {
    command: [
      `rm -f ${DB}`,
      "uv run alembic -c server/alembic.ini upgrade head",
      "uv run tf seed",
      `uv run uvicorn testforge.main:create_app --factory --port ${PORT}`,
    ].join(" && "),
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      TESTFORGE_DATABASE_URL: `sqlite:///./${DB}`,
      TESTFORGE_FRONTEND_DIST: "frontend/dist",
    },
  },
});
```

`cwd: ".."` matters: Alembic's `script_location` and the `tf` entry point both assume the repo root.

- [ ] **Step 2: Write the runs-list spec**

Create `frontend/tests/e2e/runs.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

test("the landing page redirects to the seeded project's runs", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\/CHK\/runs$/);
});

test("the seeded run appears with its outcome counts", async ({ page }) => {
  await page.goto("/projects/CHK/runs");

  const rows = page.getByTestId("run-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("nightly regression");
  await expect(rows.first().getByTestId("outcome-passed")).toContainText("4");
  await expect(rows.first().getByTestId("outcome-failed")).toContainText("1");
});

test("filtering by a status with no runs empties the table", async ({ page }) => {
  await page.goto("/projects/CHK/runs");
  await page.getByTestId("filter-status").selectOption("running");

  await expect(page.getByText("No runs yet.")).toBeVisible();
});

test("the plans page lists the seeded plan", async ({ page }) => {
  await page.goto("/projects/CHK/plans");

  const rows = page.getByTestId("plan-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Release 1.0 regression");
  await expect(rows.first()).toContainText("1.0");
});
```

These numbers come from Task 2's `DEMO_RESULTS` and are worth checking by hand rather than
adjusting to whatever the page happens to show. Six results: `CHK-1` passed, `CHK-2` passed,
`CHK-3` failed, `CHK-4` skipped, `CHK-5` passed, and the unresolved `CHK-404` — which is also
`passed`, since an unresolved result still carries an outcome. So passed 4, failed 1, skipped 1.
If `DEMO_RESULTS` changes, these change with it.

- [ ] **Step 3: Write the run-detail spec**

Create `frontend/tests/e2e/run-detail.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

async function openSeededRun(page: import("@playwright/test").Page) {
  await page.goto("/projects/CHK/runs");
  await page.getByTestId("run-row").first().getByRole("link").click();
  await expect(page.getByTestId("run-name")).toContainText("nightly regression");
}

test("failures sort above passes", async ({ page }) => {
  await openSeededRun(page);

  const outcomes = await page.getByTestId("result-row").evaluateAll((rows) =>
    rows.map((row) => row.getAttribute("data-outcome")),
  );

  expect(outcomes[0]).toBe("failed");
  expect(outcomes.at(-1)).toBe("passed");
});

test("a failure's message is readable without leaving the page", async ({ page }) => {
  await openSeededRun(page);
  await page.locator('[data-testid="result-row"][data-outcome="failed"]').click();

  const detail = page.getByTestId("result-detail");
  await expect(detail).toContainText("Retry payment");
  await expect(detail).toContainText("AssertionError");
});

test("plan coverage reports which cases were not executed", async ({ page }) => {
  await openSeededRun(page);

  const coverage = page.getByTestId("plan-coverage");
  // The seeded plan selects tag "checkout" — CHK-1, CHK-2, CHK-3, CHK-8 — and the
  // seeded results cover the first three. CHK-8 is the manual accessibility case,
  // which nothing executed.
  await expect(coverage.getByTestId("coverage-ratio")).toHaveText("3 of 4");
  await expect(coverage.getByTestId("cases-without-result")).toContainText("CHK-8");
});

test("unresolved results get their own callout", async ({ page }) => {
  await openSeededRun(page);

  const callout = page.getByTestId("unresolved-callout");
  await expect(callout).toBeVisible();
  await expect(callout).toContainText("CHK-404");
});

test("an unknown run renders the error state from the API's error code", async ({ page }) => {
  await page.goto("/runs/does-not-exist");

  const error = page.getByTestId("error-state");
  await expect(error).toBeVisible();
  await expect(error).toHaveAttribute("data-code", "run_not_found");
});
```

- [ ] **Step 4: Run the suite**

```bash
cd frontend && npm run build && npx playwright install --with-deps chromium && npm run test:e2e && cd ..
```

Expected: 9 passed.

If the seeded `passed` count assertion fails, read the actual number off the page and reconcile it against `DEMO_RESULTS` in `server/src/testforge/seed.py` — do not weaken the assertion to make it pass; the point is that the dashboard's arithmetic matches the seed's.

- [ ] **Step 5: Add the CI job**

Replace `.github/workflows/ci.yml` with:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --all-extras --dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest -v
      - name: Smoke — migrate and seed
        run: |
          uv run alembic -c server/alembic.ini upgrade head
          uv run tf seed
        env:
          TESTFORGE_DATABASE_URL: sqlite:///./ci.db

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: uv sync --all-extras --dev
      - run: cd frontend && npm ci

      - name: API types are in sync with the backend
        run: |
          uv run tf openapi > openapi.json
          cd frontend && npm run gen:types
          git diff --exit-code src/api/schema.d.ts
        # A backend schema change the frontend has not absorbed fails here.

      - run: cd frontend && npm run typecheck
      - run: cd frontend && npm run build
      - run: cd frontend && npx playwright install --with-deps chromium
      - run: cd frontend && npm run test:e2e
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: frontend/playwright-report/
```

- [ ] **Step 6: Commit the lockfile**

`frontend/package-lock.json` must be committed — CI's `npm ci` and the cache key both require it.

Run: `git status --short frontend/package-lock.json`
Expected: the file exists and is tracked or staged.

- [ ] **Step 7: Update the README**

In `README.md`, add this section immediately before the `## Design` heading:

````markdown
## Dashboard

A read-only React dashboard for inspecting runs.

```bash
make demo     # migrate, seed a demo run, build the UI
make serve    # then open http://localhost:8000
```

For frontend development, run the API and the Vite dev server separately — Vite proxies
`/api` through, so the two hot-reload independently:

```bash
make dev      # API on :8000
make ui       # dashboard on :5173
```

The dashboard is built around one question: *a run finished — what happened?* Failures sort
above passes, a result expands in place to show its message and stack trace, plan coverage
reports which cases have no result yet, and results whose case key matched nothing are called
out separately rather than buried.

TypeScript types are **generated** from the backend's own OpenAPI schema, so a UI/API schema
mismatch is a compile error rather than a runtime surprise:

```bash
make types    # regenerate frontend/src/api/schema.d.ts
```

CI regenerates them and fails if the committed copy is stale. The dashboard's own tests are
Playwright specs driving a real browser against a real server and database.
````

- [ ] **Step 8: Run everything**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 150 passed.

Run: `cd frontend && npm run typecheck && npm run build && npm run test:e2e && cd ..`
Expected: 9 passed.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "test: add Playwright end-to-end specs and frontend CI"
```

---

## Verification

After Task 7:

```bash
make demo && make serve
```

Open `http://localhost:8000`, confirm the seeded run's failure is the first row, click it, and read the assertion message — that is the slice's success criterion.

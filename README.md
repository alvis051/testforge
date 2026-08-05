# TestForge

Test case and test plan management with automation result ingestion — a system of record that
links the test cases you write to the automated tests that actually run.

Slice 1 (this repo's current state) covers projects, a suite tree, versioned test cases (manual
and automated), discovered automation links, ad-hoc runs, and result ingestion from pytest and
JUnit XML.

## Quickstart

```bash
make install
make seed
make dev
```

Then, in another shell:

```bash
uv run tf case list CHK
```

## Reporting results from your own suite

Install the plugin into the repository under test:

```bash
uv add --dev pytest-testforge
```

Mark a test with the case it covers:

```python
import pytest


@pytest.mark.case("CHK-1")
def test_coupon_applies(): ...
```

Run it, reporting to a local server:

```bash
uv run pytest --tf-url http://localhost:8000 --tf-project CHK
```

Or write the payload to disk and upload it later — useful in air-gapped CI:

```bash
uv run pytest --tf-offline results.json --tf-project CHK
uv run tf run upload results.json
```

Importing an existing suite that has no markers yet is a useful first step: every result lands as
*unresolved*, which enumerates the tests still awaiting a case link.

```bash
uv run tf run import-junit CHK ./junit.xml ci-1234
```

## Test plans

Group cases into a plan, then execute it:

```bash
uv run tf plan create CHK "Release 2.4" --milestone 2.4 --tag release
uv run tf plan show <plan_id>
```

A plan freezes its case list at creation — adding a matching case later does not join an
existing plan, so two runs of the same plan stay comparable. Executing a plan opens a run
scoped to those cases; results arrive either from automation (the same ingestion path) or
from manual execution:

```bash
curl -X POST localhost:8000/api/runs/<run_id>/cases/CHK-1/execute \
  -H 'Content-Type: application/json' \
  -d '{"outcome": "failed", "notes": "coupon field rejects valid codes"}'
```

`GET /api/runs/<run_id>` then reports plan coverage — how many of the plan's cases have
results, broken down by outcome, and which are still outstanding.

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

## Design

- Specs: `docs/superpowers/specs/`
- The full product decomposition (S1–S8, including the LLM-evaluation subsystem) is in
  `docs/superpowers/specs/2026-08-02-test-management-core-design.md`.

Key properties:

- **Case keys are stable.** `CHK-42` never changes, so renames and suite moves don't orphan history.
- **Results pin a case version.** Editing a case later never rewrites what a past run executed.
- **Unresolved results are stored, not dropped.** A system of record that silently discards data
  isn't one.
- **One ingestion path.** The pytest plugin, JUnit import, and the future runner all land in
  `IngestionService.ingest`.

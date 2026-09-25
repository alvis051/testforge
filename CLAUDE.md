# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python is managed with `uv` (a workspace: `server/`, `plugin/`, `worker/`); the dashboard lives in `frontend/` (npm).

```bash
make install                 # uv sync
make test                    # uv run pytest -v   (all three packages)
make lint / make fmt         # ruff check . / ruff format .
make dev                     # API with reload on :8000 (SQLite ./testforge.db)
make seed                    # alembic upgrade head + demo data
make types                   # regenerate frontend/src/api/schema.d.ts from the backend OpenAPI
make e2e                     # build UI, then Playwright specs against a real server + DB
```

Single test: `uv run pytest server/tests/unit/test_flakiness.py::test_name -v`

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, pytest, a migrate+seed smoke test, then in the frontend job: regenerates API types and fails on `git diff` of `schema.d.ts`, `npm run typecheck`, build, and Playwright e2e. **Any change to a backend schema/route requires `make types` and committing the regenerated `schema.d.ts`.**

New migration: `uv run alembic -c server/alembic.ini revision --autogenerate -m "..."`. `server/tests/integration/test_migrations.py` fails if migrations and models disagree, and new models must be imported in `server/src/testforge/models/__init__.py` to be registered.

## Architecture

**Three Python packages, one ingestion path.**
- `server/` (`testforge`): FastAPI app built by the factory `testforge.main:create_app` (always run uvicorn with `--factory`). Also the `tf` Typer CLI (`testforge.cli`), which is mostly a thin HTTP client over the API (`cli/client.py`), plus `tf seed` / `tf openapi`.
- `plugin/` (`pytest-testforge`): pytest plugin registered via the `pytest11` entry point. `@pytest.mark.case("CHK-1")` links a test to a case; `--tf-url`/`--tf-offline` report results; `--tf-cases=KEY,...` deselects everything not marked with one of those keys.
- `worker/` (`testforge-worker`, `tf-worker`): polls the runner protocol, clones the repo, runs the project's test command with `--tf-cases` appended, reads the plugin's offline results file, and reports back.

Every result source (plugin upload, JUnit import, runner worker) ends up in `IngestionService.ingest` (`services/ingestion_service.py`). Keep it that way: don't add a second write path for results.

**Server layering:** `api/routes/*` (HTTP, response shaping) → `services/*` (domain logic, one class per aggregate, constructed with a `Session`) → `models/*` (SQLAlchemy 2.0). `schemas/*` are the Pydantic request/response models and are the source of the OpenAPI schema the frontend types are generated from. The request-scoped session comes from `api/deps.get_session`, which commits on success and rolls back on exception; services `flush()`, they don't commit.

**Error contract:** every error response is `{code, message, details}`. Raise `AppError(code, message, status, details)` from services; `errors.register_error_handlers` also remaps validation errors, Starlette HTTP errors, and unique-constraint `IntegrityError`s (→ 409) into this shape. Other `IntegrityError`s intentionally propagate as 500s. The frontend's `ApiError` (`frontend/src/api/client.ts`) parses the same shape.

**Domain invariants** (from the README and specs; tests enforce them):
- Case keys (`CHK-42`) are stable; results pin a `TestCaseVersion`, so editing a case never rewrites history.
- Results whose case key matches nothing are stored as *unresolved*, never dropped.
- A test plan freezes its case list at creation.
- Job status ≠ run status: a suite that ran with failures is a succeeded job with a completed run; only infrastructure failure fails the job and marks the run `errored`. Only `completed` runs count toward the pass-rate trend.
- Flakiness = pass/fail transitions in a case's history (≥2 transitions, ≥5 results); a single transition is a regression, not flakiness. Skips are excluded from pass rate.

**Runner:** no scheduler. `RunnerService.claim` sweeps expired leases first, then claims the oldest queued job with a compare-and-swap `UPDATE ... WHERE status='queued'` (portable across SQLite/Postgres, no `SKIP LOCKED`). The runner endpoints return 503 unless `TESTFORGE_RUNNER_TOKEN` is set, and require `Authorization: Bearer <token>`.

**Database:** SQLite for now (`TESTFORGE_DATABASE_URL`, default `sqlite:///./testforge.db`). Foreign keys are turned on per connection in `db/session.py`. Timestamps use the `UTCDateTime` type in `db/base.py` so they come back tz-aware from SQLite; use `utcnow()`, not naive datetimes. Migrations use `render_as_batch=True` (SQLite ALTER limits), and `TESTFORGE_DATABASE_URL` overrides the URL in `alembic.ini`.

**Settings:** `pydantic-settings`, prefix `TESTFORGE_` (`database_url`, `frontend_dist`, `runner_token`), also read from `.env`. When `TESTFORGE_FRONTEND_DIST` points at a built `frontend/dist`, the API serves the SPA itself, with a catch-all route registered last that still returns JSON 404s for unknown `/api/*` paths.

**Frontend:** React 18 + Vite + TanStack Query + react-router. `src/api/schema.d.ts` is generated (don't hand-edit); `types.ts`/`queries.ts` build on it. The Vite dev server proxies `/api` to `127.0.0.1:8000`. Playwright (`playwright.config.ts`) rebuilds and seeds `e2e.db`, then serves the built SPA from FastAPI on port 8123, so the specs assert against `tf seed` data.

## Testing

- `server/tests/`: `unit/` (services against a real SQLite DB via the `db_session` fixture), `integration/` (HTTP via `client` / `client_factory` fixtures in `server/tests/conftest.py`, schema created with `Base.metadata.create_all`), `contract/`, and `acceptance/` (end-to-end flows; `test_runner_loop.py` drives the real worker against the app through `TestClient`, with no socket).
- `pytester` is enabled at the root, which is how the plugin tests and acceptance tests run nested pytest sessions.

## Design docs

Specs and implementation plans per slice live in `docs/superpowers/specs/` and `docs/superpowers/plans/`. The overall product decomposition (S1–S8) is in `docs/superpowers/specs/2026-08-02-test-management-core-design.md`. Read the relevant spec before changing behaviour in that area.

## Agent skills

### Issue tracker

GitHub Issues on `alvis051/testforge` (via `gh` locally, GitHub MCP tools in cloud sessions). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

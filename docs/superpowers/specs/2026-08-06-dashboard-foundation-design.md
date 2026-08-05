# Dashboard Foundation — Design (Slice 5a)

Status: approved design, pending implementation plan
Date: 2026-08-06
Builds on: Slice 1 (`2026-08-02-test-management-core-design.md`) and Slice 2
(`2026-08-03-plans-and-runs-design.md`), both complete.

## 1. Context

Slices 1 and 2 built a complete backend: projects, suites, versioned cases, automation links,
result ingestion (pytest plugin + JUnit import), test plans with frozen case snapshots, plan
execution, manual per-case execution, and computed plan coverage. All of it is reachable only by
HTTP or the `tf` CLI.

This slice adds the first user interface, and closes two API gaps that block any interface at all.

### Decomposition note

The original decomposition (Slice 1 spec, §2) listed S5 as "React UI, flakiness detection, trend
charts, plan coverage, failure taxonomy." That is several independent subsystems. Flakiness
detection and failure taxonomy each carry their own unresolved design questions — what counts as
flaky, what the taxonomy's categories are and who assigns them — and bundling them with a first
frontend would produce a spec that stalls. S5 is therefore split:

| | Sub-slice | Owns |
| --- | --- | --- |
| **S5a** (this spec) | Dashboard foundation | Run-listing endpoints, React app, run inspection, plan coverage display. Read-only. |
| **S5b** (later) | Analytics | Flakiness detection, trend charts, failure taxonomy. Depends on S5a's endpoints and UI surface. |

### Scope

**In scope:** two run-listing endpoints, a React + TypeScript dashboard focused on run inspection,
generated API types, Playwright end-to-end tests, and an extended demo seed.

**Out of scope:** any mutation from the UI (creating plans, executing runs, recording manual
results — all remain CLI/API-only this slice); flakiness and trend analysis (S5b); authentication
(S6); component-level frontend tests (see §7); refactoring `get_run`'s existing in-memory
aggregation (see §2).

### Success criterion

`make demo` followed by `make dev` opens a dashboard showing a seeded completed run: its failures
listed first with readable failure messages, its plan coverage reporting which cases have no
result, and its unresolved results called out separately — with no manual data setup beyond the
seed.

### Why read-only

The main write feature Slice 2 unlocked is manual test execution in the browser. Deferring it costs
little if two conventions are established now (§4), and it is not the feature this project's owner
needs first. The write slice then becomes close to purely additive: new forms and mutation calls,
no rewriting of what this slice ships.

## 2. API additions

The dashboard is blocked without these, so they belong to this slice rather than a later one.

```
GET /api/projects/{project_key}/runs    filters: status, source, plan_id, limit (default 50)
GET /api/plans/{plan_id}/runs           a plan's run history, newest first
```

Both are project-scoped rather than a global `GET /api/runs`, consistent with how cases, plans, and
suites already scope, and with the project scoping S6's multi-tenancy will require.

There is currently **no way to list runs at all** — the API has `GET /api/runs/{run_id}` and
`GET /api/runs/{run_id}/results` and nothing else. This also means "two runs of the same plan are
comparable", the property Slice 2 was built toward, has no endpoint that returns those two runs
together. `GET /api/plans/{plan_id}/runs` closes that.

### `RunListItemOut`

Both endpoints return a new schema: the existing `RunOut` fields plus `total_results` and
`by_outcome`. A runs list without pass/fail counts is not usable, and forcing the client to issue
one `GET /api/runs/{id}` per row is worse.

**These counts are computed by a single `GROUP BY` query across all listed runs**, not by reusing
the existing `get_run` path. `get_run` loads every result row for one run into Python and counts
them with a `Counter`; Slice 1's final review flagged that as acceptable at single-run scale but
noted it would need to move into SQL. A list endpoint is where that bites — N runs multiplied by
all their results. The aggregate is:

```sql
SELECT run_id, outcome, COUNT(*) FROM results WHERE run_id IN (...) GROUP BY run_id, outcome
```

`get_run` itself is deliberately **not** refactored in this slice. It is correct for a single run,
changing it is unrelated to the dashboard, and Slice 1's reviews established that unrelated
refactoring stays out of a slice's scope.

## 3. Frontend architecture

`frontend/` sits alongside `server/` and `plugin/` as an npm project. It is **not** a uv workspace
member — the uv workspace stays Python-only (`members = ["server", "plugin"]` is unchanged).

```
frontend/
  package.json  vite.config.ts  tsconfig.json  playwright.config.ts
  src/
    main.tsx  App.tsx
    api/
      schema.d.ts      generated from /openapi.json, committed to git
      client.ts        typed fetch wrapper; parses the error contract
      queries.ts       query-key factory and TanStack Query hooks
    pages/
      RunsPage.tsx  RunDetailPage.tsx  PlansPage.tsx
    components/
      ResultsTable.tsx  PlanCoverage.tsx  OutcomeBadge.tsx  ErrorState.tsx
  tests/e2e/
    runs.spec.ts  run-detail.spec.ts
```

**Stack:** React, TypeScript, Vite, React Router, TanStack Query. No component library — a small
hand-written component set keeps the dependency surface honest for a three-page app.

**Dev:** Vite's dev server proxies `/api` to FastAPI on :8000. `make dev` starts both.

**Production:** `npm run build` emits to `frontend/dist`; FastAPI mounts it as static files at `/`,
so the whole platform is one process and one container. Every existing API route lives under
`/api`, and `/health` is the sole exception; both are registered before the static mount, so
FastAPI matches them ahead of it and the SPA claims only what is left. The mount must also serve
`index.html` for unmatched paths, since React Router owns client-side routes like `/runs/{id}`
that have no file behind them.

### Generated types

TypeScript learns the API's shapes from FastAPI's own `/openapi.json`, via `openapi-typescript`.

This matters more than usual here: "the UI and the API silently disagree" is precisely the class of
bug this product exists to catch, so hand-maintaining a second copy of the backend's schemas in
TypeScript would be self-defeating. Generation makes drift a **compile error** — the same argument
as Slice 1's plugin/server contract test, which has already earned its keep twice.

`schema.d.ts` is committed to git, so the build is hermetic and schema changes are visible in
review diffs. CI regenerates it and fails if the result differs from the committed copy (§7).

Runtime validation (Zod or similar) was considered and rejected: it is a second hand-maintained
copy of the same schemas, carrying the same drift risk plus a runtime cost. It is the right tool
for an API you do not control; this one we control, and it publishes a schema.

## 4. The two conventions

Both exist so the eventual write slice is additive rather than a rewrite. Both cost nothing now.

### Query keys

Hierarchical, from a single factory:

```ts
keys.runs(projectKey, filters)   // ['projects', projectKey, 'runs', filters]
keys.run(runId)                  // ['runs', runId]
keys.runResults(runId, filters)  // ['runs', runId, 'results', filters]
keys.plans(projectKey)           // ['projects', projectKey, 'plans']
keys.plan(planId)                // ['plans', planId]
keys.planRuns(planId)            // ['plans', planId, 'runs']
```

TanStack Query invalidates by key prefix, so invalidating `['runs', runId]` catches both the run
and its results. Ad-hoc, inconsistent keys would force the write slice to normalize every call site
before invalidation worked at all.

### Error handling

An `ApiError` carrying `status`, `code`, `message`, and `details`, parsed from the platform's own
contract — every backend error returns exactly `{code, message, details}` with a stable code.

The UI branches on `code`: `run_not_found` renders a different empty state than a network failure.
A generic "something went wrong" handler that discarded `code` would have to be rebuilt when the
write slice starts hitting `409 run_already_completed`, `409 plan_archived`, and
`422 empty_plan_selection`.

## 5. Pages

The dashboard is optimized for one question: *a run finished — what happened?* Failures are the
visual center of gravity.

### `/projects/:key/runs` — runs list (landing)

Table: status badge, run name, source, started-at, pass/fail counts, and a link to the run's plan
where it has one. Filters for status and source. Failed runs are visually distinct at a glance.

### `/runs/:runId` — run detail

The centerpiece.

- **Header:** status, timing, and the plan link (using `RunOut.plan_id`, added in Slice 2).
- **Plan coverage panel**, shown only for plan runs: total cases, how many have results, the
  outcome breakdown, and which case keys are still outstanding. This renders Slice 2's computed
  `plan_progress` directly — no new backend work.
- **Results table, sorted failures first**, with rows expanding to show `failure_message` and
  `stack_trace`.
- **Unresolved results as their own callout.** A result whose `case_key` did not resolve means a
  test is running that no case covers. Slice 1 deliberately stores these rather than dropping them;
  burying them in the main table would waste that decision.

### `/projects/:key/plans` — plans list

Deliberately thin: name, milestone, environment, case count, status, and that plan's runs. The plan
*catalogue* view is S5b/later territory; this exists so a run's plan link has somewhere to go.

Project selection is a header dropdown, not a page — with one or two projects a dedicated page is
ceremony.

## 6. Demo seed extension

`seed_demo` currently creates a project, two suites, and eight cases — and **zero runs or results**.
A run-inspection dashboard opened against it shows nothing.

This slice extends it to also create one completed plan run containing a realistic mix: several
passed, at least one failed with a genuine-looking failure message and stack trace, one skipped,
and one unresolved result (a `case_key` matching no case).

This serves the tests (§7) and the product equally: `make demo` currently produces an empty
dashboard, which is a poor first impression for exactly the audience this project is built for.

The seed stays idempotent, as it is today.

## 7. Testing

End-to-end only, with Playwright driving a real browser against a real FastAPI server and a real
SQLite database. Playwright's `webServer` starts both the API (against a temporary seeded database)
and the frontend.

Component-level tests with a mocked API were considered and rejected for this slice. They prove
components render given *assumed* data — a schema drift between UI and API passes green, which is
the exact failure mode this platform exists to catch. Generated types (§3) already cover the
static half of that contract; Playwright against the real stack covers the behavioural half.

Specs cover:

- The runs list renders the seeded run with correct pass/fail counts.
- Run detail shows the seeded failure, with its message readable without leaving the page.
- Plan coverage reports the correct outstanding case keys.
- Unresolved results appear in their own callout, not silently merged into the results table.
- An unknown run id renders a proper error state driven by the `run_not_found` code, not a blank
  page or an unhandled rejection.

Backend additions from §2 get their own pytest coverage in the existing style: filters work,
project scoping holds (a run from another project never appears), the `GROUP BY` counts match what
`get_run` reports for the same run, and an empty list is an empty list rather than an error.

### CI

Two additions to the existing workflow:

1. Build the frontend, install Chromium, run the Playwright suite.
2. Regenerate `schema.d.ts` from the running app's `/openapi.json` and fail if it differs from the
   committed copy — a backend schema change the frontend has not absorbed becomes a red build.

## 8. Open questions

None blocking. Two judgment calls to revisit if they chafe:

- **Playwright asserts against seeded data**, which couples the E2E suite to `seed_demo`'s
  contents. This is a deliberate trade — the seed becomes a fixture with a contract — but if seed
  churn starts breaking tests, the alternative is per-test setup through the API.
- **No component library.** Fine for three pages; revisit before the surface grows much past that.

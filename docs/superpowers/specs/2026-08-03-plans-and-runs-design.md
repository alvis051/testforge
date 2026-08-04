# Test Plans & Runs — Design (Slice 2)

Status: approved design, pending implementation plan
Date: 2026-08-03
Builds on: `docs/superpowers/specs/2026-08-02-test-management-core-design.md` (Slice 1, complete)

## 1. Context

Slice 1 delivered the core test domain (projects, suites, versioned cases, tags), discovered
automation links, ad-hoc runs, and the shared result-ingestion path that the pytest plugin and
JUnit import both feed through. It deliberately shipped a **minimal run concept** — a run not tied
to any plan — and reserved a nullable `runs.plan_id` column for this slice.

S2 gives that column something real to point at: **test plans**. A plan is a named, frozen
selection of test cases plus execution context (milestone, environment). Executing a plan opens a
run scoped to that plan's cases.

This is the sub-project the whole product is named for — "test case *and test plan* management".
S1 made results storable; S2 makes them *planned*.

### Scope

**In scope:** test plans (create, list, archive), frozen case snapshots, plan execution to a run,
manual per-case execution, run cancellation, computed plan progress/coverage.

**Out of scope:** web UI (S5), the runner (S4), auth (S6), flakiness analysis and trend charts
(S5), scheduling/queueing of runs, per-step manual results, plan templates, plan cloning.

### Success criterion

Create a plan selecting 5 cases (2 manual, 3 automated) by suite and tag; execute it to open a run;
manually record outcomes for the 2 manual cases; ingest results for the 3 automated cases through
the *existing* Slice 1 pytest-plugin path with no changes to that path; then `GET /api/runs/{id}`
and see accurate plan progress showing 5 of 5 cases with results, broken down by outcome.

## 2. Data model

### New tables

```
test_plans          id, project_id, name, description?, milestone?, environment?,
                    status ("active"|"archived"), created_by, updated_by,
                    created_at, updated_at, archived_at?

test_plan_cases     plan_id, test_case_id, test_case_version_id, position
                    PRIMARY KEY (plan_id, test_case_id)
```

### Existing tables, extended

- `runs.plan_id` — gains a real FK to `test_plans.id`. It has existed as a bare nullable
  `String(36)` since Slice 1 specifically so this slice would not have to rework the table.
- `runs.status` — gains `"canceled"` as a third value alongside `"running"` and `"completed"`.

No other Slice 1 table changes. `results` is untouched — manual execution writes the same row
shape automated ingestion does (see §4).

### Design decisions

**Case selection is a static snapshot, not a live filter.** Creating a plan evaluates the
selection *once* and writes the resulting case list into `test_plan_cases`. That list is then
immutable for the plan's life. A plan therefore always executes exactly the cases it said it
would, even if the underlying suite gains or loses cases afterward — which is what makes two runs
of the same plan comparable, the property the whole regression story depends on.

The alternative (a stored filter re-evaluated per run) was rejected: it means a run's case list can
silently change between two runs of "the same" plan, which quietly breaks coverage and regression
comparison — exactly the thing this platform exists to make trustworthy.

**`test_plan_cases` pins `test_case_version_id` at snapshot time**, for the same reason
`results` pins it in Slice 1: the plan's contents stay reproducible after the source cases are
edited. Note these are two independent pins with different meanings, and they can legitimately
differ: the plan pin says "this is the version of the case that was selected"; the result pin says
"this is the version that was actually executed". A plan built in March and run in June against an
edited case will show both, and the difference is real, useful information — not a bug to
reconcile.

**`milestone` and `environment` are plain strings on the plan**, not first-class entities. Nothing
in this slice or the next needs them filterable or relational. Promoting them later is cheap;
building the tables now is speculative.

**Plan status is `active` or `archived` only** — there is deliberately no `draft`. A draft state
with no behavior attached to it (can a draft be executed? does it validate differently?) invites
three different interpretations during implementation. A plan exists and is executable, or it is
archived and is not.

**`test_plan_cases.position`** preserves the order cases were selected in, so
`GET /api/plans/{id}/cases` returns a stable, meaningful sequence rather than an arbitrary one —
manual testers work down a list, and a list that reorders between requests is hostile. Explicit
`case_keys` keep their given order; filter-matched cases follow, ordered by `case_key`.

**Progress and coverage are computed, never stored.** "12 of 20 cases have a result" for a
plan-run is a LEFT JOIN from `test_plan_cases` to `results` filtered by run. There is no
per-run "expected result" row to create, update, or drift out of sync.

**One plan, many runs.** Executing a plan opens a new `Run` with `plan_id` set. Results accumulate
against that run independently of any other run of the same plan.

## 3. Architecture

Follows the Slice 1 module pattern exactly: each module owns one table group and exposes a service
as its only entry point; routes stay thin; only a coordinating service reaches across boundaries,
and it does so through other services, never their tables.

| Module | Owns | Public surface |
| ------ | ---- | -------------- |
| `plans` | test_plans, test_plan_cases | `PlanService.create/get/list/cases/archive` |
| `manual` | no tables | `ManualExecutionService.record(...)` — writes to `results` via the runs module |

`RunService` (Slice 1) gains `open_for_plan(...)` and `cancel(...)`. `PlanService.create()` is the
only writer of `test_plan_cases`.

## 4. Manual execution

Manual execution gets its own narrow service rather than reusing `IngestionService`.

The two have genuinely different shapes. `IngestionService.ingest()` accepts a *batch* of results
from an *external* producer that does not know the platform's internal state — so it resolves case
keys by string, tolerates unresolved ones by storing them rather than rejecting, and pins versions
per entry. Manual execution is the inverse: a human is looking at one specific, already-resolved
case in one specific run and records one outcome. There is no batch, no key resolution, and no
tolerance for "unresolved" — an unknown case there is a genuine client error.

Forcing manual execution through the batch/`ResultIn` contract would mean constructing a fake
single-entry batch and defeating its unresolved-tolerance, to reuse machinery whose entire value is
handling a situation manual execution never faces.

So: **two entry points, one table.** `ManualExecutionService.record()` and
`IngestionService.ingest()` both write `results` rows of identical shape, which is what keeps every
downstream reporting query (S5) uniform regardless of how a result was produced.

### Result field mapping for manual results

- `framework` = `"manual"` — distinguishes manual rows without a new column.
- `test_identifier` = the case's `case_key` — every row keeps a readable "what ran" value, again
  with no schema change.
- `failure_message` = the tester's free-text note, on **any** outcome, not just failures. The name
  reads slightly off for a passing case with a note, but a separate `notes` column would be a
  second field meaning the same thing. Stretching one name beats carrying two.
- `test_case_version_id` = pinned at execution time, exactly as ingestion does it.
- `automation_link_id` = null (manual results have no automated test behind them).

### Scope guard

If the run belongs to a plan, the case being executed must be in that plan's frozen snapshot;
otherwise `404 case_not_in_plan`. The plan is a scope contract on the run. Ad-hoc runs (no
`plan_id`) impose no such restriction — any resolvable case in the project is valid, matching
Slice 1 behavior.

## 5. Run state machine

```
running ──→ completed     (POST /api/runs/{id}/complete, from Slice 1)
        └─→ canceled      (POST /api/runs/{id}/cancel, new)
```

`running` is the initial state — a run starts running the moment it is opened, exactly as in
Slice 1. There is no `queued` state: nothing in this slice queues or dispatches work, and adding a
state with no behavior behind it invites divergent interpretations before S4's runner defines what
queueing actually means.

Both `completed` and `canceled` are terminal. Writes to a terminal run are rejected:
- a completed run rejects with the existing `409 run_already_completed`
- a canceled run rejects with a new `409 run_canceled`

Two codes rather than one overloaded code, so an automated caller can distinguish "this finished
normally, your results are late" from "this was abandoned" — a distinction that matters for
deciding whether to retry.

## 6. API

```
POST   /api/projects/{project_key}/plans      create (snapshots cases once)
GET    /api/projects/{project_key}/plans      list; filters: status, milestone, environment
GET    /api/plans/{plan_id}                   detail incl. case_count
GET    /api/plans/{plan_id}/cases             the frozen snapshot
POST   /api/plans/{plan_id}/archive

POST   /api/plans/{plan_id}/runs              execute → opens Run(plan_id, status="running")
POST   /api/runs/{run_id}/cancel              running → canceled
POST   /api/runs/{run_id}/cases/{case_key}/execute   record one manual result

GET    /api/runs/{run_id}                     extended with plan_progress when plan_id is set
```

### Plan creation payload

Case selection accepts explicit keys, a filter, or both — unioned and de-duplicated, then frozen:

```json
{
  "name": "Release 2.4 regression",
  "milestone": "2.4",
  "environment": "staging",
  "case_keys": ["CHK-1", "CHK-7"],
  "filter": {"suite_id": "...", "tag": "smoke", "execution_type": "manual"}
}
```

The `filter` object reuses the same field names as Slice 1's existing case-list query parameters,
so one mental model covers both. A plan created with a selection matching zero cases is rejected
with `422 empty_plan_selection` — an empty plan is never intentional and fails confusingly at
execution time instead.

### Manual execution payload

```json
{"outcome": "failed", "notes": "Coupon field rejects valid codes", "duration_ms": 45000}
```

`outcome` is the Slice 1 `Outcome` literal (`passed`/`failed`/`skipped`/`error`/`blocked`).
Re-executing the same case in the same run **replaces** that case's prior manual result rather than
appending a second one — a tester correcting a mis-click is the common case, and two rows for one
case in one run would double-count in progress. (Automated re-ingestion keeps Slice 1's
append-only behavior; this replacement rule applies only to the manual path.)

### Plan progress

`GET /api/runs/{run_id}` gains, when `plan_id` is set:

```json
{
  "plan_progress": {
    "total_cases": 20,
    "cases_with_result": 12,
    "by_outcome": {"passed": 9, "failed": 2, "blocked": 1},
    "cases_without_result": ["CHK-14", "CHK-15"]
  }
}
```

Computed per request from the join. Absent entirely for ad-hoc runs.

## 7. Error handling

New codes, added to Slice 1's fixed set: `plan_not_found`, `case_not_in_plan`, `run_canceled`,
`empty_plan_selection`, `plan_archived`.

Consistent with Slice 1's established rules:
- Every write endpoint accepts `X-Actor`, defaulting to `local`.
- Error bodies are exactly `{"code", "message", "details"}`.
- Cross-project references are rejected as not-found rather than leaking existence: a plan
  referencing another project's case, or a run/case pair from different projects, returns the
  relevant `*_not_found` code. This mirrors the project-scoping fixes made at the end of Slice 1.
- Executing a run from an archived plan is rejected with `409 plan_archived`. Existing runs of a
  since-archived plan continue to accept results — archiving stops new executions, it does not
  freeze work already in flight.

## 8. Testing strategy

Following Slice 1's approach, where the platform's own suite is part of the argument.

- **Unit.** Snapshot immutability (editing a case after plan creation does not change
  `test_plan_cases`); selection union/de-duplication across `case_keys` + `filter`; empty-selection
  rejection; the plan-scope guard accepting in-plan and rejecting out-of-plan cases; manual
  re-execution replacing rather than appending; state-machine transitions including both terminal
  rejections.
- **Integration.** Full plan lifecycle over HTTP; `plan_progress` correctness as results arrive
  from *both* paths; cross-project rejection cases.
- **Acceptance.** The §1 success criterion end to end — critically, the automated half must flow
  through the unmodified Slice 1 pytest-plugin path, proving plans layer *on top of* ingestion
  rather than replacing it.
- **Regression guard.** Slice 1's existing 87 tests must continue to pass untouched. Ad-hoc
  (planless) runs must keep working exactly as before — `plan_progress` simply absent.

## 9. Open questions

None blocking. Two judgment calls to revisit if they chafe in use:

- `failure_message` doubling as the manual note (§4) — a dedicated `notes` column is a small
  migration away if the overload proves confusing in the S5 UI.
- Plans are immutable once created; "editing" means creating a new plan. If re-selecting cases on
  an existing plan becomes a real need, the natural shape is plan versioning mirroring case
  versioning, not in-place mutation of the snapshot.

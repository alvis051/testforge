# Test Management Core — Design (Slice 1)

Status: approved design, pending implementation plan
Date: 2026-08-02
Supersedes: nothing. Reframes `docs/evalforge-development-plan.md` as a later sub-project (S7).

## 1. Context

The existing `evalforge-development-plan.md` specs an LLM evaluation platform. This document
reframes that work: the product being built is a **test management and automation execution
platform**, and LLM evaluation becomes one execution type plugged into it later.

Goals, in priority order:

1. Portfolio and interview artifact.
2. Learning vehicle for testing fundamentals and platform engineering.
3. A tool genuinely usable against real test suites, with a path to being sold as a freelance
   service.

Goal 3 is why multi-tenancy, auth, and product ergonomics appear in the roadmap at all. None of
them are in Slice 1.

### Product shape

Closest reference points are TestRail, Zephyr, and Xray: a system of record for test cases and
test plans, with automated results flowing in from real suites. The differentiators aimed for are
a first-class runner (the platform dispatches execution, not just receives results) and,
eventually, an agent-drivable API.

## 2. Decomposition

The full product is several independent subsystems. Each gets its own spec, plan, and
implementation cycle.

| #  | Sub-project | Owns | Depends on |
| -- | ----------- | ---- | ---------- |
| S1 | Core test domain | Projects, test cases (manual steps or automation marker), suites, tags, case versioning. API + CLI. | — |
| S2 | Plans & runs | Test plan = case selection + config (env, milestone). Plan execution to run. Manual execution view. Run state machine. | S1 |
| S3 | Result ingestion | Reporting contract, pytest plugin, JUnit XML import, marker-to-case resolution, unlinked-result handling. | S1, S2 |
| S4 | Runner | Job queue, worker agent, repo checkout, containerized execution, log/artifact streaming, secrets. | S3 |
| S5 | Reporting & dashboard | React UI, flakiness detection, trend charts, plan coverage, failure taxonomy. | S2, S3 |
| S6 | Multi-tenancy & auth | Orgs, project scoping, API keys, RBAC. Prerequisite for selling the service. | S1 |
| S7 | LLM evaluation | The existing `evalforge-development-plan.md` spec, replumbed as one execution type behind the S3 result contract. | S3 |
| S8 | MCP server | Agent-drivable interface over the HTTP API. Tool schemas, read/write scoping. | S1, S6 |

**Build order: S1 → S3 (thin) → S2 → S5 (thin) → S4 → S6 → S7/S8.**

Two ordering choices are deliberate:

- **S3 before S2.** Getting one real pytest run's results into the platform is what proves the
  concept. Modeling plans on top of a working result pipeline is easier than the reverse.
- **S4 deferred, not dropped.** The runner remains the goal. S3 defines the result contract the
  runner must satisfy, so building S3 first means the runner slots in behind an interface that
  already has a working consumer. Runner-first risks producing a CI clone with nothing
  distinctive above it.

## 3. Slice 1 scope

**In scope:** projects, suite tree, test cases (manual and automated) with version history, tags,
discovered automation links, ad-hoc runs, result ingestion, pytest plugin, CLI.

**Out of scope:** test plans, web UI, runner, authentication, flakiness analysis, multi-tenancy.

Because ingestion lands before plans exist, Slice 1 includes a **minimal run concept** — an
ad-hoc run not tied to any plan. S2 later adds a nullable `plan_id` rather than reworking the
table.

### Success criterion

Add `@pytest.mark.case("CHK-042")` to a real test in an existing repository, run pytest, and the
result appears in the platform bound to that case and to the exact case version that was current
at execution time.

## 4. Architecture

Monolith FastAPI service, layered `routes → services → models`. SQLAlchemy 2.x with Alembic
migrations from the first commit. SQLite for development, schema kept Postgres-compatible.
Python dependency management via `uv`.

### Modules

Each module owns one table group and exposes a service as its only entry point.

| Module | Owns | Public surface |
| ------ | ---- | -------------- |
| `projects` | projects, case-key sequence | `create`, `get`, `list`, `allocate_case_key()` |
| `suites` | suite tree | `create`, `move`, `list_tree` |
| `cases` | cases, case versions, tags | `create`, `update`, `get`, `list`, `archive`. Sole writer of version rows. |
| `automation` | automation links | `bind(case, framework, identifier)`, `links_for_case` |
| `runs` | runs, results | `RunService.open/close`, `ResultService.record_batch` |
| `ingestion` | no tables | `ingest(payload)` — resolves case keys, pins versions, delegates to `runs` and `automation` |

`ingestion` is the only module that reaches across boundaries, and does so through other modules'
services, never their tables. This keeps the S4 runner and JUnit import as alternative *callers*
of one ingestion path rather than parallel implementations.

**The CLI talks to the HTTP API, not the database.** Marginally slower, but the API contract is
dogfooded from day one and the CLI works against a remote instance — which goal 3 requires.

## 5. Data model

```
projects            id, key ("CHK"), name, description, case_seq, created_at, updated_at

suites              id, project_id, parent_id?, name, path ("/3/7/"), position,
                    created_at, updated_at

test_cases          id, project_id, suite_id, case_key ("CHK-42"), title, execution_type,
                    priority, status, owner, preconditions, steps_json, expected_result,
                    current_version_no, created_by, updated_by, created_at, updated_at,
                    archived_at?

test_case_versions  id, test_case_id, version_no, title, execution_type, priority,
                    preconditions, steps_json, expected_result, change_note,
                    created_by, created_at                              [immutable]

tags                id, project_id, name
test_case_tags      case_id, tag_id

automation_links    id, test_case_id, framework, test_identifier, first_seen_at,
                    last_seen_at, active
                    unique (framework, test_identifier)

runs                id, project_id, external_id, name, source, status, plan_id?,
                    ci_metadata_json, created_by, started_at, completed_at?
                    unique (project_id, external_id)

results             id, run_id, test_case_id?, test_case_version_id?, automation_link_id?,
                    test_identifier, unresolved_case_key?, outcome, duration_ms,
                    failure_type?, failure_message?, stack_trace?, attachments_json,
                    executed_at
```

Field notes:

- `execution_type` is `manual` or `automated`. Manual cases carry `steps_json` as an ordered list
  of `{action, expected}`; automated cases normally leave it empty.
- `priority` is `p0`–`p3`. `status` is `draft`, `active`, or `deprecated`.
- `source` on runs is `ci`, `local`, `manual`, or `runner`. `status` is `running`, `completed`,
  or `aborted`.
- `outcome` on results is `passed`, `failed`, `skipped`, `error`, or `blocked`.
- `created_by` / `updated_by` hold the actor string (see §8).
- `attachments_json` stores references (URLs, CI artifact links) only. There is no upload endpoint
  or blob storage in Slice 1; that belongs with the runner in S4.
- **Tags are not versioned.** They are organizational metadata rather than test content, so
  `test_case_versions` does not snapshot them and retagging does not bump `current_version_no`.

### Versioning model

Approach chosen: **head row plus snapshot history**. `test_cases` holds the current editable
state; every save appends an immutable row to `test_case_versions` and bumps `current_version_no`.
Results pin the `test_case_version_id` that was current at execution time.

Consequences, accepted knowingly:

- `test_cases` duplicates the authored fields that `test_case_versions` snapshots. That is the
  cost of this approach. Reads never join; writes must maintain both in one transaction.
  `CaseService` is the sole writer, so the invariant lives in exactly one place.
- The alternative pure immutable chain (no head row) was rejected: every read would join to
  resolve head, list endpoints get awkward, and incremental authoring edits explode version count.
- No versioning at all was rejected outright. Losing the run-to-case-version link removes the
  traceability that distinguishes this from a spreadsheet, and retrofitting versioning after
  results exist is painful.

### Identity

- **UUID primary keys.** Generatable without a database round-trip, which S4's worker and the
  plugin's offline mode need. They do not leak record counts.
- **`case_key` is the external identity.** Human-readable, allocated per project from `case_seq`
  with the project key as prefix (`CHK-42`). It is what appears in pytest markers, so it never
  changes — renames and moves between suites do not touch it.

### Automation links are discovered, not registered

Ingestion sees `CHK-042` on node `tests/test_checkout.py::test_coupon[web]` and upserts the
binding, updating `last_seen_at`. "Which automated tests cover this case" becomes a byproduct of
running them, with no maintenance burden, and stale links surface via `last_seen_at`.

The relationship is **one case to many automated tests** — one case may be covered across web and
mobile, or by parametrized variants. Modeling it one-to-one looks simpler but breaks on the first
cross-platform suite.

## 6. HTTP API

```
POST   /api/projects                            create project (key, name)
GET    /api/projects/{project_key}
GET    /api/projects/{project_key}/suites       tree
POST   /api/projects/{project_key}/suites
PATCH  /api/suites/{id}                         rename / move (cycle-checked)

POST   /api/projects/{project_key}/cases        allocates next key, writes v1
GET    /api/projects/{project_key}/cases        filters: suite, tag, status, execution_type, q
GET    /api/cases/{case_key}
PATCH  /api/cases/{case_key}                    bumps version, appends snapshot
GET    /api/cases/{case_key}/versions
GET    /api/cases/{case_key}/automation-links
POST   /api/cases/{case_key}/archive

POST   /api/projects/{project_key}/runs         {external_id, name, source, ci_metadata}
POST   /api/runs/{id}/results                   batch ingest
POST   /api/runs/{id}/complete
POST   /api/projects/{project_key}/runs/import-junit
GET    /api/runs/{id}                           summary + counts
GET    /api/runs/{id}/results                   filters: outcome, case_key, unresolved
```

Resources are addressable by human-readable key (`/api/cases/CHK-42`) as well as UUID. Callers —
humans, CLI, agents — reason in case keys; a UUID-only API forces a lookup round-trip on every
operation.

`import-junit` creates its own run and then feeds the same ingestion path. JUnit XML has no
marker concept, so case keys are extracted in this order: a `<property name="case_key">` element
on the `<testcase>` (which is what the pytest plugin emits when writing JUnit output), otherwise a
`PROJ-123` pattern match against the test name. Anything unmatched becomes an unresolved result
per §9 — which makes importing an unannotated legacy suite a useful first step rather than an
error, since it surfaces every test awaiting a case link.

### Result payload

The single most important artifact in the slice. The pytest plugin, JUnit import, and the S4
runner all produce exactly this shape.

```json
{
  "results": [
    {
      "case_key": "CHK-42",
      "test_identifier": "tests/test_checkout.py::test_coupon[web]",
      "framework": "pytest",
      "outcome": "failed",
      "duration_ms": 1840,
      "failure_type": "AssertionError",
      "failure_message": "expected 200, got 500",
      "stack_trace": "...",
      "executed_at": "2026-08-01T10:22:31Z"
    }
  ]
}
```

## 7. Clients

### pytest plugin

A separate installable package (`pytest-testforge`) living in the same repository, so client
projects can install it without pulling in the platform.

- `@pytest.mark.case("CHK-42")`, repeatable — one test may cover several cases.
- Collects outcomes across the session, posts one batch at session end.
- `--tf-offline=results.json` writes the payload to disk instead of posting. Required for
  air-gapped CI, and it is what the S4 runner will consume.
- Failure to reach the server never fails the test run: warning on stderr, exit code untouched.
  A reporting tool that breaks builds is a tool people uninstall.

### CLI

`typer`-based, HTTP-only. Commands: `tf project create`, `tf case list`, `tf case new`,
`tf run import-junit`, `tf run show`. Configuration in `~/.config/testforge/config.toml`
(base URL, actor).

## 8. Cross-cutting decisions made now for later sub-projects

Four constraints adopted in Slice 1 because retrofitting them is expensive. Each is defensible on
its own merits, independent of whether S8 (MCP) is ever built.

1. **Readable-key routing.** Covered in §6.
2. **Actor on every write.** A nullable string from the `X-Actor` request header, defaulting to
   `local`, stored in `created_by` / `updated_by`. This is not authentication — S6 owns that —
   but "who created this case" exists from day one. Adding an actor column to tables that already
   carry history is painful, and an agent that cannot be distinguished from a human editor is an
   audit problem.
3. **Idempotent run creation.** Client-supplied `external_id`, unique per project. Re-POSTing the
   same one returns the existing run instead of creating a duplicate. Needed regardless, since CI
   jobs retry.
4. **Machine-readable errors.** `{code, message, details}` with stable string codes. The
   difference for an automated caller is between recovering and looping.

The MCP server itself, its tool schemas, and the question of which operations to expose are
deferred entirely to S8.

## 9. Error handling

Stable error codes: `project_not_found`, `case_key_not_found`, `duplicate_case_key`,
`suite_cycle`, `case_version_conflict`, `invalid_result_payload`, `run_already_completed`.

The substantive decisions concern what counts as an error at all:

- **Syntactic problems reject the batch; semantic ones do not.** A malformed result payload
  (missing `outcome`, unparseable timestamp) is a client bug: `422` with per-index details, whole
  batch rejected, safe to retry because run creation is idempotent. A `case_key` that does not
  resolve is **not** an error — the result is stored with `unresolved_case_key` set and
  `test_case_id` null, and counted in the run summary. Silently dropping results would be the
  worst possible failure mode for a system of record.
- **Posting results to a completed run is rejected** with `409 run_already_completed`. Run
  creation is idempotent, but result batches are not, so this rule is what prevents a retried CI
  job from double-recording results. A retry that re-creates the run gets the existing run back,
  finds it completed, and stops. Slice 1 deliberately does not attempt per-batch deduplication;
  if that proves too blunt in real use, a `batch_id` can be added without changing the payload.
- **Concurrent case edits use optimistic concurrency.** `PATCH` carries the `version_no` the
  client read; a mismatch returns `409 case_version_conflict` rather than silently losing an
  update.
- **Archived cases still accept results.** Flagged in the run summary, never dropped.
- **Suite moves are cycle-checked** before the materialized `path` rewrite, so a failed move
  leaves the tree unchanged.
- **The pytest plugin never fails a test run.** Network error produces a warning; with
  `--tf-offline` the payload is still written to disk.

## 10. Testing strategy

This is a testing platform, so its own suite is part of the portfolio argument. A thin one would
undercut the pitch.

- **Unit.** The case-version invariant (every update appends exactly one snapshot and bumps
  `current_version_no`); case-key allocation under concurrent creates; suite cycle detection;
  `path` correctness after moves; idempotent run creation.
- **Contract.** The result payload is defined by one Pydantic model shared between the plugin
  package and the server. A test asserts the plugin's emitted JSON validates against the server's
  model. This is what stops the S4 runner from drifting later.
- **Integration.** API exercised through `httpx` ASGI transport against a temporary SQLite
  database. The ingestion path gets explicit cases for resolved, unresolved, archived-case, and
  duplicate-`external_id` scenarios.
- **Plugin.** Tested with pytest's `pytester` fixture — real subprocess pytest runs against a
  generated suite carrying real markers.
- **Acceptance.** One end-to-end test that is the §3 success criterion: marked test runs, result
  lands, bound to the correct case and to the version current at execution time.
- **Migrations.** A test asserting the Alembic chain builds the schema the models declare.
- **CI.** GitHub Actions using `astral-sh/setup-uv` and `uv sync`, running ruff and pytest.

## 11. Open questions

- **Product name.** "EvalForge" fits S7 better than S1–S6. This document uses `testforge` /
  `tf` / `pytest-testforge` as provisional names. Worth settling before the package names are
  published anywhere, but it does not block implementation.

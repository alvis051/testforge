# The Runner — Design (Slice 4a)

Status: approved design, pending implementation plan
Date: 2026-08-12
Builds on: Slice 1 (`2026-08-02`), Slice 2 (`2026-08-03`), Slice 5a (`2026-08-06`),
Slice 5b (`2026-08-07`) — all complete.

## 1. Context

Every slice so far has made the platform a better *recipient* of test results. The pytest plugin
pushes them, JUnit import pulls them, plans organize them, the dashboard displays them, and
analytics interprets them. In all of it, something else runs the tests.

This slice inverts that. **The platform dispatches execution.** That is the differentiator the
Slice 1 spec named — "a first-class runner (the platform dispatches execution, not just receives
results)" — and the one capability that separates this from a system of record.

### Decomposition: why 4a and 4b

The roadmap's S4 row covers job queue, worker agent, repo checkout, containerized execution,
log/artifact streaming, and secrets. That is several subsystems, and larger than S5 was before it
was split into 5a and 5b. This slice takes the same shape:

- **S4a (this document)** — the vertical slice that makes dispatch real end to end: queue, worker,
  checkout, subprocess execution, results flowing back through the existing ingestion path,
  job state in the API and UI.
- **S4b** — hardening: containerized execution, secrets and private-repo credentials, live log
  streaming, artifact storage.

S4a is deliberately the smaller half of a capability that works completely, rather than the larger
half of one that doesn't.

### Scope

**In scope:** a `run_jobs` queue table, per-project runner configuration, a plan dispatch endpoint,
a runner protocol (claim / heartbeat / finish) over HTTP, a separate `testforge-worker` package,
case-key-based test selection in the pytest plugin, lease-based recovery from dead workers, a
dispatch button and live job panel in the UI, and a demo suite that makes the whole loop
runnable from `make demo`.

**Out of scope:** everything in S4b (above), plus retry-with-backoff and dead-lettering, a queue
admin page, per-worker concurrency, scheduled or triggered runs, and authentication (S6).

### Success criterion

`make demo`, click **Run on runner** on the seeded plan, and watch the run move
queued → running → completed without refreshing the page — with a real `git clone` and a real
`pytest` in between, and results attributed to exactly the plan's automated cases.

## 2. Architecture

```
PlansPage  ──POST /api/plans/{id}/dispatch──▶  Run(status=queued, source=runner)
                                               RunJob(status=queued, case keys frozen)

tf-worker  ──POST /api/runner/claim─────────▶  sweep expired leases, then
           ◀─job: repo, ref, command, keys──   compare-and-swap the oldest queued job

           git clone --depth 1 --branch <ref>
           <command> --tf-cases CHK-1,CHK-3 --tf-offline=results.json
           (heartbeat every 15s throughout)

           ──POST /api/runner/jobs/{id}/finish▶  one transaction:
              {status, exit_code, output_tail,     IngestionService.ingest(results)
               error, results:[...]}                run  → completed | errored
                                                    job  → succeeded | failed
```

### The worker is a separate package

A third `uv` workspace member, `worker/` → `testforge-worker`, entrypoint `tf-worker`. This
follows the `pytest-testforge` precedent and its reasoning: a build machine should not install
FastAPI, SQLAlchemy, and Alembic in order to run tests. The worker's dependencies are `httpx` and
`typer`.

The packaging boundary is doing real work here. Because the worker cannot import the server, it
**cannot reach the database even by accident** — it can only do what the HTTP protocol allows. That
is the property that makes a worker deployable on a machine that holds no database credentials,
and it is enforced structurally rather than by discipline.

This also extends the Slice 1 rule that "the CLI talks to the HTTP API, not the database" to its
natural conclusion for a component that genuinely runs elsewhere.

### Reporting results and finishing are one call

`POST /api/runner/jobs/{job_id}/finish` carries the results *and* the terminal status, and the
server ingests them, closes the run, and closes the job in a single transaction.

The obvious alternative — have the worker POST to the existing `/api/runs/{id}/results` and then
call a separate finish endpoint — was rejected for a specific reason. It opens a window in which
results exist but the job is unfinished. A worker that dies in that window leaves a job whose lease
eventually expires and is requeued, and the requeued run then ingests a second copy of every
result. Making finish atomic closes the window entirely: **either a job reported everything or it
reported nothing, so requeuing is always safe and never needs partial-result reconciliation.**

Underneath, `finish` calls `IngestionService.ingest()` — the same service the plugin and JUnit
import call. Slice 1's "one ingestion path" rule is preserved: the runner is a third *caller*, not
a third implementation.

## 3. Data model

### New table

```
run_jobs        id, run_id (FK runs.id, unique), status, repo_url, git_ref, resolved_sha?,
                command, case_keys_json, claimed_by?, claimed_at?, lease_expires_at?,
                attempts, exit_code?, output_tail?, error?, created_at, finished_at?
```

`status` is `queued`, `running`, `succeeded`, or `failed`.

### Existing tables, extended

```
projects        + repo_url?, default_ref (default "main"), test_command?
runs.status     + "queued", + "errored"
runs.source     "runner" — reserved by the Slice 1 spec, first actual use
```

### Design decisions

**Two new run statuses, not one.** `queued` is the dispatched-but-unclaimed state. `errored` is
infrastructure failure: the checkout failed, the command was not found, the job timed out, or its
lease expired past the attempt limit. Both are states the system can genuinely be in and neither
has an honest existing home. In particular, a run whose repository failed to clone must not appear
as a green `completed` in the runs list, and it was not `canceled` — nobody cancelled it.

`RunService.assert_writable` learns to reject `errored` alongside `completed` and `canceled`.

**Job status and run status mean different things, which is the point of separating them.** A test
suite that ran and reported failures is a *successful job* with a *completed run*. Only
infrastructure failure makes the job fail:

| Situation | Exit code | Job | Run |
| --- | --- | --- | --- |
| Tests ran, all passed | 0 | succeeded | completed |
| Tests ran, some failed | 1 | succeeded | completed |
| Nothing collected (no marked tests) | 5 | succeeded | completed, zero results |
| Interrupted / internal error / usage error | 2, 3, 4 | failed | errored |
| Clone failed, command not found, timeout | — | failed | errored |
| Lease expired past attempt limit | — | failed | errored |

The worker sends whatever results it collected regardless of outcome, so a partially-completed run
still shows the results it produced. One rule, no special cases.

**`case_keys_json` is frozen at dispatch,** resolved from the plan's cases where
`execution_type == "automated"`. This matches Slice 2's decision to freeze a plan's selection
rather than re-evaluate it: the job executes exactly the cases it was dispatched with. Dispatching
a plan with zero automated cases is a `409` — there is nothing to run.

**Runner config lives on the project.** One repository and one test command per project, matching
how case keys already scope. The dispatch request may override `git_ref`, so "run the regression
plan against branch `feature-x`" needs no new configuration. Per-plan config was rejected because
the case-key filter (§5) means the *command* does not need to vary by plan — the plan's case keys
are the selection — so per-plan config would only duplicate one repo URL across every plan and let
it drift.

## 4. Dispatch and the runner protocol

These are two different surfaces with two different callers, and they are guarded differently.

**Dispatch is a user action from the browser.** It is an ordinary endpoint, unauthenticated like
every other endpoint until S6:

```
POST /api/plans/{plan_id}/dispatch
     body  {git_ref?, name?}
     → 201 RunOut (status "queued")
     409 runner_not_configured_for_project | plan_has_no_automated_cases
     409 plan_archived (existing error, existing meaning)
```

**The runner protocol is called by workers**, and all three of its endpoints require the bearer
token described in §7. The frontend never holds that token and never calls these:

```
POST /api/runner/claim
     body  {worker_name}
     → 200 RunnerJobOut, or 200 null when there is no work
     RunnerJobOut: {job_id, run_id, repo_url, git_ref, command,
                    case_keys[], lease_expires_at}

POST /api/runner/jobs/{job_id}/heartbeat
     body  {worker_name}
     → 204; extends the lease
     409 job_not_claimed_by_worker  (the lease was reclaimed underneath it)

POST /api/runner/jobs/{job_id}/finish
     body  {worker_name, status, exit_code?, output_tail?, error?, results:[ResultIn]}
     → 204
     409 job_not_claimed_by_worker
```

`claim` returning `null` rather than a 204 keeps the generated TypeScript types simple and gives
the worker a plain `if job is None: sleep(...)`.

Heartbeat and finish identify their caller by `worker_name`, which the server compares against
`claimed_by`. This is a *safety* check against acting on a lease that was already reclaimed — not a
security boundary, which is the token's job. It assumes worker names are distinct; the default,
`hostname:pid`, is.

### Claim and sweep are one endpoint

There is no scheduler, cron, or background thread anywhere in this design. Expired leases are
reclaimed by an opportunistic sweep at the top of every claim request — the next worker to ask for
work does the cleaning.

```sql
UPDATE run_jobs SET status='running', claimed_by=?, claimed_at=?, lease_expires_at=?,
                    attempts = attempts + 1
WHERE id = (SELECT id FROM run_jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1)
  AND status='queued'
RETURNING ...
```

The `AND status='queued'` guard on the outer `UPDATE` makes this a compare-and-swap. If two workers
select the same row, one `UPDATE` affects zero rows and that worker simply gets "no work" — no
duplicate execution. This is portable to both SQLite and Postgres and needs no `FOR UPDATE SKIP
LOCKED`, which SQLite does not have.

The sweep, run before the claim in the same request:

- a `running` job whose `lease_expires_at` has passed and whose `attempts < 3` returns to `queued`,
  and its run returns to `queued`;
- one whose `attempts >= 3` becomes `failed` with an explanatory `error`, and its run becomes
  `errored`.

**Parameters:** lease 60s, heartbeat every 15s (four missed beats before reclaim), 3 attempts.
These are reasoned defaults, not measured ones, and live as constants in one module.

## 5. Test selection: the pytest plugin change

The plugin gains `--tf-cases CHK-1,CHK-3`, which deselects every collected test not carrying a
matching `@pytest.mark.case` marker, via `pytest_collection_modifyitems` reporting through
`pytest_deselected` so the terminal summary stays truthful.

**Selection is by case key, not by node id.** The alternative — resolving the plan's cases to their
discovered `automation_links` and passing those node ids to pytest — was rejected on two counts: a
case that has never been run has no links yet (a cold start the platform can't escape), and node
ids go stale the moment a test is renamed or reparametrized. A case key is the stable external
identity the Slice 1 spec built precisely so it would never change.

One consequence the worker must handle: **pytest exits `5` when nothing is collected.** A plan
whose cases have no marked automated tests yet must read as "ran fine, produced zero results" —
plan progress already renders those cases as unexecuted, which is the correct and informative
answer — and not as an infrastructure failure.

## 6. The worker

```
tf-worker --url URL --token TOKEN [--name NAME] [--poll-interval 5] [--timeout 1800] [--once]
```

`--name` defaults to `hostname:pid`. `--once` claims at most one job and exits, which is what makes
the worker testable and usable as a CI step.

Per job:

1. `git clone --depth 1 --branch <git_ref> <repo_url> <tmpdir>`, then `git rev-parse HEAD` for
   `resolved_sha`.
2. Run the project's `test_command`, split with `shlex.split` into an argv list and executed with
   **`shell=False`** and `start_new_session=True`, appending `--tf-cases=<keys>` and
   `--tf-offline=<tmpdir>/results.json`.
3. Heartbeat every 15s on a background thread for the duration.
4. On timeout, kill the process *group* (which is why `start_new_session` is set — killing only the
   direct child would orphan its grandchildren).
5. Read `results.json` if it exists, take `payload["results"]`, and call `finish` with the results,
   exit code, error, and the **last 64 KB** of combined stdout/stderr.
6. Remove the temp directory in a `finally`.

`shell=False` is not about injection — the command comes from the project's own configuration, and
its owner can already run anything. It is because killing a shell on timeout leaves the test process
running, and because an argv list has one unambiguous meaning. The cost, stated plainly: the
configured command cannot use pipes, `&&`, or shell expansion.

**Two constraints S4a accepts and S4b removes.** The command runs in the worker host's own Python
environment, so the target repository's dependencies — including `pytest-testforge` — must already
be available there. And `--branch` accepts branches and tags but not arbitrary commit SHAs, which
would need a full clone.

## 7. Security posture

Stated plainly rather than implied away.

**S4a executes the project's configured command as a subprocess on the worker host with no
isolation.** That is arbitrary code execution by design — it is a test runner — but it means: do
not point a worker at a repository you do not trust. Containerization in S4b is the mitigation, and
this document does not pretend S4a has one.

**The three runner-protocol endpoints require a bearer token** from `TESTFORGE_RUNNER_TOKEN`. If
the setting is unset, those three return `503 runner_not_configured` — there is no insecure default
and no silent skip. This is **not authentication**; S6 owns that. It exists so that a stray client
on the network cannot claim jobs intended for your workers, or report fabricated results for one.

Dispatch is deliberately *not* behind the token: it is called by the browser, which has no way to
hold a worker credential, and it is no more privileged than the endpoints that already exist. With
the token unset, dispatch still queues jobs — they simply sit there, exactly as they would with no
worker running.

Private-repository credentials are S4b. S4a clones with whatever the worker host already has —
public HTTPS, or the host's existing SSH agent and git configuration.

## 8. Closing the analytics gap S5b deferred

`AnalyticsService._recent_runs` gains a `status == "completed"` filter.

Slice 5b's final review noted that a non-terminal run consumes a slot in the 20-run trend window
and contributes a null point the chart silently drops. Adding a `queued` state turns that latent
issue into a routine one: dispatch three plans and the trend chart loses three slots to runs that
have no data yet.

A queued run has no data, a running run has misleading *partial* data, and an errored run has
neither. None of them belong in a pass-rate trend. Because trends and failure categories share
`_recent_runs` — a property Slice 5b established deliberately so the two panels can never disagree
about their window — both are fixed by the one change.

## 9. UI

No new page. Two existing pages gain surface:

- **`PlansPage`** — a per-row **Run on runner** button. When the project has no `repo_url` or
  `test_command`, it renders disabled with the reason, rather than failing on click.
- **`RunDetailPage`** — a job panel showing state, git ref, resolved SHA, attempts, exit code, and
  the captured output tail. While the run's status is `queued` or `running`, the page polls every
  3s (TanStack Query `refetchInterval`), so the state transition is visible without a refresh.

The polling is what makes the demo land: a button that queues something invisible is not a
demonstration that the platform dispatches execution.

## 10. Demo seed

Two changes, both small, and the second is what makes §1's success criterion actually reachable.

**The newest seeded run becomes a runner-sourced run** with a completed `run_jobs` row attached.
It keeps its existing results and shape, so the job panel gains demo content without changing any
existing assertion. This is deliberate: the seed is a fixture with a contract (Slice 5b, §6).
Converting a run changes no existing assertion; adding a seventh run would have changed two — the
runs-list count and the trend chart's dot count.

**A new `examples/demo_suite/` directory** holds a handful of real pytest tests marked with the
demo project's case keys, and the seed points the project's `repo_url` at this repository with
`test_command` targeting that directory. Dispatching the seeded plan therefore clones a real
repository, runs real tests, and produces real results for the plan's cases — the complete loop,
from `make demo`, with nothing stubbed.

`examples/` sits outside `testpaths` (`server/tests`, `plugin/tests`), so the demo suite does not
join the project's own test run. It doubles as worked documentation of how to mark a test.

## 11. Testing

- **Unit — claim and sweep.** The compare-and-swap under contention (two workers, one queued job,
  exactly one wins); lease expiry requeues; the attempt limit fails the job *and* errors the run.
  These encode the queue's correctness claims, so they should read as statements of them.
- **Unit — the plugin.** `--tf-cases` deselects non-matching tests, keeps matching ones, reports
  deselection, and leaves collection untouched when the option is absent.
- **Unit — the worker.** Checkout and execution against a **fixture git repository built in a temp
  directory** (`git init`, one marked test, one commit). Hermetic, no network. Covers the exit-code
  mapping table in §3 directly, including exit 5.
- **Integration — dispatch and the protocol.** Dispatch and all three worker endpoints through
  `TestClient`, including `503 runner_not_configured` when the token is unset, that dispatch keeps
  working without one, `409` on a stolen lease, and the atomicity of finish (results and both
  status transitions land together, or not at all).
- **Acceptance — the whole loop in-process.** Dispatch a plan, run one `--once` worker against a
  fixture repository with the worker's `httpx` client pointed at the app through `ASGITransport`,
  and assert results land against the plan's cases.
- **E2E.** The dispatch button creates a queued run; the job panel renders the seeded completed
  job. The full execution loop stays in the Python acceptance test — running a worker subprocess
  inside Playwright would buy coverage that already exists at a large cost in fragility.

CI needs no new steps: the backend job covers the protocol, worker, and acceptance tests, and the
frontend job already builds, typechecks, and runs Playwright.

## 12. Open questions

None blocking. Three judgment calls to revisit with real use:

- **Lease, heartbeat, and attempt values** (60s / 15s / 3) are reasoned defaults. They are constants
  in one module.
- **Commit-SHA refs** need a full clone rather than `--depth 1 --branch`, and are deferred. Nothing
  in S4a or S4b requires them.
- **One job per worker** is the concurrency model; parallelism comes from running more workers.
  Per-worker concurrency would need output demultiplexing and a lease per slot, which is a real
  design rather than a flag.

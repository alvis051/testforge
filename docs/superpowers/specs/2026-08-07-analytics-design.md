# Analytics — Design (Slice 5b)

Status: approved design, pending implementation plan
Date: 2026-08-07
Builds on: Slice 1 (`2026-08-02`), Slice 2 (`2026-08-03`), Slice 5a (`2026-08-06`) — all complete.

## 1. Context

Slice 5a shipped the platform's first UI: a read-only dashboard for inspecting a single run. It
answers *"a run finished — what happened?"*

This slice answers the question a run-by-run view cannot: **"is this suite trustworthy, and where
is it rotting?"** That question is only answerable across many runs, so it needs analysis over
history rather than inspection of one record.

### Scope

**In scope:** flakiness detection, a pass-rate trend, rule-based failure categorization, three
read-only analytics endpoints, one new Insights page, and a demo seed with enough run history to
make all of it real.

**Out of scope:** any mutation (this slice stays read-only, like 5a); human-assigned failure labels
(see §4); per-case drill-down history views; alerting or thresholds-as-policy; the runner (S4);
auth (S6).

### Success criterion

`make demo` seeds enough history that opening `/projects/CHK/insights` shows a flaky test correctly
flagged, a genuinely-broken test correctly **not** flagged as flaky, a pass-rate trend across
several runs, and a failure-category breakdown — with no manual data setup.

### Why this stays read-only

Failure taxonomy is auto-classified from rules rather than human-labeled (§4). That decision is
what keeps this slice read-only and shippable as one piece. Human labeling is genuinely more
accurate — a person knows "that's a known infra flake" better than a regex ever will — but it needs
a table, write endpoints, and forms, which is a different slice with a different shape.

## 2. The flakiness rule

This is the heart of the slice and the part most worth stating precisely.

For each test case, take its results ordered by `executed_at`, **excluding `skipped` and
`blocked`** (a skip carries no information about stability), and reduce each remaining result to a
binary class: `passed`, or not-passed (`failed`/`error`). Count **transitions** — adjacent pairs
where the class differs.

**A case is flaky if it has ≥ 2 transitions over a window of at least 5 results.**

### Why two transitions

The threshold is not a tuning knob; it is the structural difference between two distinct
situations:

| Sequence | Transitions | What it is |
| --- | --- | --- |
| `P P P F F F` | 1 | A regression. Broke, stayed broken. |
| `F F F P P P` | 1 | A fix. Was broken, now works. |
| `P P F P P` | 2 | **Flaky.** It recovered with nobody fixing it. |
| `P F P F P F` | 5 | Severely flaky. |

A genuine regression transitions exactly once. So does a fix. Only a test that **recovered on its
own** transitions twice — and unexplained self-recovery is precisely what flakiness *is*. The rule
therefore encodes the actual definition rather than approximating it with a tuned percentage.

### Parameters

- **Window:** the most recent 20 results per case. A count-based window rather than a time-based
  one, because run cadence is irregular — "last 30 days" means 100 results for one project and 2
  for another.
- **Minimum sample:** 5 results. Without it, a case with two results (`P F`) would report a
  transition rate of 1.0 and top the list on no evidence.
- **Score:** `transitions / (n - 1)`, in `[0, 1]`. Used for **ranking only**, not classification —
  classification is the ≥ 2 rule above. A test failing once in twenty runs is genuinely flaky but
  scores low; it should appear, ranked below a test that flips constantly.

### Why not the textbook definition

The rigorous definition of a flaky test is "the same code producing different outcomes," which
requires comparing results at a fixed commit. That is not available: `runs.ci_metadata_json` exists
as a column and the API accepts it, but **nothing populates it** — the pytest plugin sends
`project_key`, `external_id`, `name`, `source`, and `results`, with no commit SHA. Even with commit
capture added, same-commit comparison only produces signal when a commit is executed more than
once, which ordinary CI does not do.

Transition counting extracts the same signal from data that already exists, by reading the *shape*
of the history rather than requiring an external code identity. Adding commit capture later does
not invalidate it.

### Scope of a "case"

Flakiness is computed per **test case**, not per `test_identifier`. A case may be covered by
several automated tests (Slice 1 models `automation_links` as one-to-many deliberately), and the
case is the unit a human reasons about. Results whose `test_case_id` is null (unresolved) are
excluded — there is no case to attribute them to.

## 3. Architecture

A read-only `AnalyticsService`, computing on read. No new tables, no precomputed scores, no
refresh path.

At this scale — thousands of results — the queries are trivial. A materialized flakiness table
would trade a real problem (staleness, invalidation, a refresh path that can silently stop running)
for a hypothetical one. If a project ever accumulates enough history to make this slow, the
service's interface stays the same and only its internals change.

### Endpoints

```
GET /api/projects/{project_key}/insights/flaky
    → cases with ≥2 transitions, ranked by score desc

GET /api/projects/{project_key}/insights/trends?limit=20
    → per-run pass rate over the most recent N runs, oldest-first for plotting

GET /api/projects/{project_key}/insights/failure-categories
    → counts by category over the same run window as trends
```

Three details that are otherwise easy to interpret two ways:

- **The outcome sequence** on a flaky case is a list of the window's binary classes, oldest-first —
  `["passed", "failed", "passed", ...]`, not a packed string. It is what the UI renders to make the
  flag inspectable, and a list survives adding a third class later without a format change.
- **Pass rate** is `passed / (passed + failed + error)` per run — skipped and blocked are excluded
  from *both* numerator and denominator, matching §2's treatment. Counting skips in the denominator
  would make a run look worse the more tests it skipped, which inverts the intended meaning. A run
  with no countable results reports `null` rather than `0`, so the chart can break the line instead
  of plotting a false zero.
- **The failure-category window** is the same most-recent-N-runs window the trend uses (default
  20), not all history. A category breakdown over all time answers a different question than the
  trend beside it, and two panels on one page silently disagreeing about their window is exactly
  the kind of thing nobody notices until it misleads someone.

Three endpoints rather than one combined payload: they have different shapes, and a page that
loads them as three parallel queries gets three independent loading and error states, which
Slice 5a's query-key factory already handles cleanly.

Every endpoint is project-scoped, consistent with the rest of the API and with S6's eventual
multi-tenancy.

## 4. Failure taxonomy

An ordered rule list in a Python module — versioned, reviewable in a diff, no new table and no
seed-data indirection. Each rule matches against `failure_type` first, then `failure_message`;
first match wins.

Default categories: `assertion`, `timeout`, `connection`, `permission`, `not_found`, and
`uncategorized`.

**`uncategorized` is a feature, not a gap.** A visibly large uncategorized bucket is the signal
that the rules need extending for this codebase's actual failure modes. Silently forcing every
failure into a named category would hide exactly that.

Rules are matched case-insensitively against the message, since failure text is not normalized
across frameworks.

## 5. UI

One new route, `/projects/:projectKey/insights`, registered inside the existing `Shell` layout
route, plus an "Insights" link in `Shell`'s nav alongside Runs and Plans.

**No existing page's content changes.** `App.tsx` gains a route and `Shell` gains a nav link —
those two edits are unavoidable for a new page to be reachable — but `RunsPage`, `PlansPage`, and
`RunDetailPage` are untouched. Slice 5a's surface was verified end to end only recently, and
distributing analytics into those pages would put that at risk for no gain here.

The page holds three panels, each fed by its own query:

1. **Flaky tests** — a ranked table: case key, title, score, transitions, sample size, and a
   compact rendering of the pass/fail sequence so the flag is inspectable rather than a black box.
2. **Pass-rate trend** — a line chart across the most recent runs.
3. **Failure categories** — a breakdown of counts by category.

### Charts

Hand-rolled inline SVG, with no charting dependency.

Slice 5a's spec chose "no component library — a small hand-written component set keeps the
dependency surface honest," and one pass-rate line over ~20 points does not justify reversing that.
A charting library is the right call when charts become a recurring need with real interaction
requirements; adding one for a single line chart is a dependency in search of a use.

The chart must remain legible in both light and dark themes and must not rely on colour alone to
convey pass versus fail.

## 6. Demo seed

The seed currently creates exactly one run. Flakiness needs history and trends need a series, so an
Insights page against today's seed would render three empty panels.

This slice extends the seed to create **several runs over time** containing, deliberately:

- a **flaky** case — a pass/fail sequence with ≥ 2 transitions
- a **genuinely broken** case — one transition, failing and staying failed
- a **stable** case — no transitions
- failures whose messages exercise more than one taxonomy category, including at least one that
  falls through to `uncategorized`

These are what make the Insights page demonstrate its own distinctions, and what the E2E tests
assert against.

**This is the third slice to extend the seed, which makes the pattern worth naming: the seed is a
fixture with a contract, not decoration.** Tests depend on its exact contents. Changing it means
checking the E2E assertions that read it. Treating that as a known property is better than
rediscovering it each slice.

The seed remains idempotent.

## 7. Testing

- **Unit — the algorithm, against hand-built sequences.** The load-bearing cases: `P P P F F F` is
  **not** flaky (1 transition) while `P P F P P` **is** (2 transitions); skips are excluded from
  the sequence rather than treated as failures; a 4-result sequence is never flagged regardless of
  shape; unresolved results are excluded. These tests encode the design's central claim, so they
  should read as statements of it.
- **Unit — taxonomy.** Each default rule matches its intended input, first-match-wins ordering
  holds, and an unmatched failure lands in `uncategorized`.
- **Integration.** The three endpoints against a seeded database: correct ranking, project scoping
  (another project's results never appear), and empty-but-valid responses for a project with no
  history — an empty analytics page is a legitimate state, not an error.
- **E2E.** The Insights page flags the seeded flaky case, does **not** flag the seeded broken one,
  renders the trend across the seeded runs, and shows the category breakdown.

CI needs no new steps: the existing backend job covers the endpoints and the existing frontend job
already builds, typechecks, and runs Playwright.

## 8. Open questions

None blocking. Two judgment calls to revisit with real data:

- **Window and minimum-sample values** (20 / 5) are reasoned defaults, not measured ones. They are
  constants in one module, easy to change once real suites exercise them.
- **Commit SHA capture** stays out of scope, but if the plugin later records it, a stricter
  same-commit flakiness view becomes possible alongside — not instead of — transition counting.

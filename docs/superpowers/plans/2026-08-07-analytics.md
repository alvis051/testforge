# Analytics (Slice 5b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer "is this suite trustworthy, and where is it rotting?" — flakiness detection by outcome-transition counting, a pass-rate trend, and rule-based failure categorisation, surfaced on one new Insights page.

**Architecture:** Two pure functions (transition scoring, failure classification) with no database knowledge, unit-tested against hand-built sequences; a read-only `AnalyticsService` that feeds them from SQL and returns Pydantic models directly (matching `IngestionService.ingest`'s existing precedent); three project-scoped endpoints; one new React page with two hand-rolled SVG charts and no charting dependency.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2.x (backend); React, TypeScript, Vite, TanStack Query, Playwright (frontend). No new runtime dependencies in either stack.

Source spec: `docs/superpowers/specs/2026-08-07-analytics-design.md`

## Global Constraints

- Python `>=3.12`. Python deps via `uv add` / `uv add --dev`; never hand-edit `[project.dependencies]`. **This slice should need no new dependency in either stack** — if you believe you need one, stop and flag it.
- All commands run from the **repository root** (Alembic's `script_location` and pytest's `testpaths` are repo-root-relative; running pytest from `server/` produces a spurious migration failure).
- **No new database tables, columns, or Alembic migrations.** Analytics computes on read.
- The slice is **strictly read-only**: no POST/PATCH/PUT anywhere, no `useMutation` in the frontend.
- Every error response body is exactly `{"code": str, "message": str, "details": object}`.
- `frontend/src/api/schema.d.ts` is generated (`make types`) and committed; never hand-edited. CI fails if regenerating produces a diff.
- Before declaring any task done, run **both** `uv run ruff check .` and `uv run ruff format --check .` from the repo root, plus (for frontend tasks) `npm run typecheck` and `npm run build`. Paste the real output. Prior slices repeatedly had "clean" claims that were not.
- Charts encode **magnitude with a single hue**; identity comes from text labels, never from colour alone. No categorical palette is introduced, so none needs validating.

## File Structure

```
server/src/testforge/
  analytics/__init__.py                                            [new]
  analytics/flakiness.py     score_outcomes() — pure, no DB        [new]
  analytics/taxonomy.py      classify_failure() — pure, no DB      [new]
  schemas/insights.py        FlakyCaseOut, RunTrendPointOut, …     [new]
  services/analytics_service.py  SQL → the pure functions          [new]
  api/routes/insights.py     three read-only endpoints             [new]
  main.py                    register the insights router          [modify]
  seed.py                    six runs of history                   [modify]

frontend/src/
  api/schema.d.ts            regenerated                           [modify]
  api/types.ts               three new aliases                     [modify]
  api/queries.ts             three keys + three hooks              [modify]
  pages/InsightsPage.tsx     the page, three panels                [new]
  components/FlakyTable.tsx  ranked table + sequence rendering     [new]
  components/TrendChart.tsx  single-series SVG line                [new]
  components/CategoryBars.tsx single-hue SVG bars                  [new]
  App.tsx                    one route                             [modify]
  styles.css                 chart-specific classes                [modify]
frontend/tests/e2e/insights.spec.ts                                [new]
```

Split rationale: the two pure functions know nothing about SQLAlchemy, so their tests are instant and their logic is reviewable in isolation — which matters most for the flakiness rule, the slice's central claim. Everything database-shaped lives behind `AnalyticsService`.

---

### Task 1: Failure taxonomy (pure)

**Files:**
- Create: `server/src/testforge/analytics/__init__.py`, `server/src/testforge/analytics/taxonomy.py`
- Test: `server/tests/unit/test_taxonomy.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `classify_failure(failure_type: str | None, failure_message: str | None) -> str`; constant `UNCATEGORIZED = "uncategorized"`; `RULES` (ordered list).

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_taxonomy.py`:

```python
import pytest

from testforge.analytics.taxonomy import UNCATEGORIZED, classify_failure


@pytest.mark.parametrize(
    ("failure_type", "expected"),
    [
        ("AssertionError", "assertion"),
        ("TimeoutError", "timeout"),
        ("ReadTimeout", "timeout"),
        ("ConnectionError", "connection"),
        ("PermissionError", "permission"),
        ("FileNotFoundError", "not_found"),
    ],
)
def test_the_exception_class_decides_the_category(failure_type, expected):
    assert classify_failure(failure_type, None) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Connection refused by payments-sandbox:8443", "connection"),
        ("Timed out after 30s waiting for the coupon field", "timeout"),
        ("permission denied", "permission"),
        ("no such element: #checkout-submit", "not_found"),
        ("assert 200 == 500", "assertion"),
    ],
)
def test_the_message_decides_when_there_is_no_exception_class(message, expected):
    assert classify_failure(None, message) == expected


def test_the_exception_class_beats_the_message():
    category = classify_failure("AssertionError", "connection refused")

    assert category == "assertion", (
        "a precise exception class must win over a loose phrase in the message"
    )


def test_message_matching_is_case_insensitive():
    assert classify_failure(None, "CONNECTION REFUSED") == "connection"


def test_an_unrecognised_failure_falls_through_to_uncategorized():
    category = classify_failure(None, "Cart total drifted by 0.01 between render and submit")

    assert category == UNCATEGORIZED


def test_a_failure_with_no_type_and_no_message_is_uncategorized():
    assert classify_failure(None, None) == UNCATEGORIZED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_taxonomy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.analytics'`

- [ ] **Step 3: Write the module**

Create `server/src/testforge/analytics/__init__.py`:

```python
"""Read-only analysis over run history. Nothing here touches the database."""
```

Create `server/src/testforge/analytics/taxonomy.py`:

```python
"""Rule-based failure categorisation.

Two ordered passes: every rule is tried against ``failure_type`` first, and only
if none match is ``failure_message`` tried. A precise exception class therefore
beats a loose phrase — ``AssertionError`` whose message happens to mention a
refused connection is an assertion failure, not a connection failure.
"""

import re

UNCATEGORIZED = "uncategorized"

# (category, exception-class pattern, message pattern). Either pattern may be None.
RULES: list[tuple[str, str | None, str | None]] = [
    ("timeout", r"Timeout|TimedOut", r"timed?\s?out"),
    (
        "connection",
        r"Connection|Network|Socket",
        r"connection (refused|reset|aborted)|network is unreachable",
    ),
    (
        "permission",
        r"Permission|Forbidden|Unauthori[sz]ed",
        r"permission denied|forbidden|unauthori[sz]ed",
    ),
    ("not_found", r"NotFound|NoSuchElement", r"not found|no such (file|element|table|column)"),
    ("assertion", r"AssertionError", r"^\s*assert\b"),
]


def classify_failure(failure_type: str | None, failure_message: str | None) -> str:
    """Categorise one failure. Returns ``uncategorized`` when nothing matches.

    An ``uncategorized`` bucket that grows large is useful signal that the rules
    need extending for a given codebase — it is deliberately not a catch-all
    that silently absorbs everything.
    """
    if failure_type:
        for category, type_pattern, _ in RULES:
            if type_pattern and re.search(type_pattern, failure_type, re.IGNORECASE):
                return category
    if failure_message:
        for category, _, message_pattern in RULES:
            if message_pattern and re.search(message_pattern, failure_message, re.IGNORECASE):
                return category
    return UNCATEGORIZED
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_taxonomy.py -v`
Expected: PASS — 15 passed (11 parametrised cases + 4 discrete tests).

- [ ] **Step 5: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 171 passed (156 existing + 15 new).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add rule-based failure categorisation"
```

---

### Task 2: Flakiness scoring (pure)

This is the slice's central claim. The tests below are written to read as statements of the design, because that is what they are.

**Files:**
- Create: `server/src/testforge/analytics/flakiness.py`
- Test: `server/tests/unit/test_flakiness.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `score_outcomes(outcomes: list[str]) -> FlakinessResult`; frozen dataclass `FlakinessResult(sequence: list[str], sample_size: int, transitions: int, score: float, is_flaky: bool)`; constants `WINDOW = 20`, `MIN_SAMPLE = 5`, `MIN_TRANSITIONS = 2`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_flakiness.py`:

```python
from testforge.analytics.flakiness import MIN_SAMPLE, WINDOW, score_outcomes

PASS = "passed"
FAIL = "failed"


def sequence(letters: str) -> list[str]:
    """'PPFPP' -> the outcome strings, for tests that read like the design."""
    return [{"P": "passed", "F": "failed", "E": "error", "S": "skipped"}[c] for c in letters]


def test_a_regression_that_stays_broken_is_not_flaky():
    result = score_outcomes(sequence("PPPFFF"))

    assert result.transitions == 1
    assert result.is_flaky is False, (
        "breaking once and staying broken is a regression, not flakiness"
    )


def test_a_fix_is_not_flaky():
    result = score_outcomes(sequence("FFFPPP"))

    assert result.transitions == 1
    assert result.is_flaky is False


def test_a_test_that_recovers_on_its_own_is_flaky():
    result = score_outcomes(sequence("PPFPP"))

    assert result.transitions == 2
    assert result.is_flaky is True, (
        "recovering with nobody fixing it is exactly what flakiness is"
    )


def test_constant_flip_flopping_scores_one():
    result = score_outcomes(sequence("PFPFPF"))

    assert result.transitions == 5
    assert result.score == 1.0
    assert result.is_flaky is True


def test_a_stable_suite_scores_zero():
    result = score_outcomes(sequence("PPPPPP"))

    assert result.transitions == 0
    assert result.score == 0.0
    assert result.is_flaky is False


def test_errors_count_as_failures():
    result = score_outcomes(sequence("PPEPP"))

    assert result.transitions == 2
    assert result.is_flaky is True


def test_skips_are_excluded_from_the_sequence_entirely():
    result = score_outcomes(sequence("PPSFPP"))

    assert result.sequence == sequence("PPFPP"), "a skip says nothing about stability"
    assert result.sample_size == 5
    assert result.transitions == 2
    assert result.is_flaky is True


def test_a_short_history_is_never_flagged_however_it_flips():
    result = score_outcomes(sequence("PFPF"))

    assert result.transitions == 3
    assert result.sample_size == 4
    assert result.is_flaky is False, (
        f"fewer than {MIN_SAMPLE} results is not evidence, whatever the shape"
    )


def test_only_the_most_recent_window_is_considered():
    result = score_outcomes(sequence("PF" * 20))

    assert result.sample_size == WINDOW


def test_an_empty_history_is_handled():
    result = score_outcomes([])

    assert result.sample_size == 0
    assert result.transitions == 0
    assert result.score == 0.0
    assert result.is_flaky is False


def test_a_history_of_only_skips_is_handled():
    result = score_outcomes(sequence("SSSSSS"))

    assert result.sequence == []
    assert result.is_flaky is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_flakiness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.analytics.flakiness'`

- [ ] **Step 3: Write the module**

Create `server/src/testforge/analytics/flakiness.py`:

```python
"""Flakiness scoring by counting outcome transitions.

A genuine regression transitions exactly once (P P P F F F). So does a fix
(F F F P P P). Only a test that recovered with nobody fixing it transitions
twice or more (P P F P P), and that unexplained self-recovery is what flakiness
*is*. The >= 2 threshold therefore encodes the definition rather than tuning a
percentage against it — which also means it needs no commit SHA, reading the
shape of the history instead of requiring an external code identity.
"""

from dataclasses import dataclass

WINDOW = 20
MIN_SAMPLE = 5
MIN_TRANSITIONS = 2

#: Outcomes that carry stability information. Skips and blocks are excluded
#: entirely rather than treated as failures — a skipped test says nothing.
COUNTED = ("passed", "failed", "error")


@dataclass(frozen=True)
class FlakinessResult:
    sequence: list[str]
    sample_size: int
    transitions: int
    score: float
    is_flaky: bool


def score_outcomes(outcomes: list[str]) -> FlakinessResult:
    """Score one case's outcome history. ``outcomes`` is oldest-first."""
    sequence = [outcome for outcome in outcomes if outcome in COUNTED][-WINDOW:]
    sample_size = len(sequence)

    transitions = sum(
        1
        for earlier, later in zip(sequence, sequence[1:], strict=False)
        if (earlier == "passed") != (later == "passed")
    )

    # Ranking only. Classification is the >= MIN_TRANSITIONS rule below: a test
    # failing once in twenty runs is genuinely flaky but scores low, and should
    # appear ranked beneath one that flips constantly.
    score = transitions / (sample_size - 1) if sample_size > 1 else 0.0

    return FlakinessResult(
        sequence=sequence,
        sample_size=sample_size,
        transitions=transitions,
        score=score,
        is_flaky=sample_size >= MIN_SAMPLE and transitions >= MIN_TRANSITIONS,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_flakiness.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 182 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add flakiness scoring by outcome-transition counting"
```

---

### Task 3: Insights schemas and AnalyticsService

**Files:**
- Create: `server/src/testforge/schemas/insights.py`, `server/src/testforge/services/analytics_service.py`
- Test: `server/tests/unit/test_analytics_service.py`

**Interfaces:**
- Consumes: `score_outcomes` (Task 2), `classify_failure` (Task 1), `RunService.outcome_counts(run_ids) -> dict[str, dict[str, int]]` (Slice 5a), models `Run`, `Result`, `TestCase`, `Project`.
- Produces: schemas `FlakyCaseOut`, `RunTrendPointOut`, `FailureCategoryOut`; `AnalyticsService(session)` with `flaky_cases(project) -> list[FlakyCaseOut]`, `run_trends(project, *, limit=20) -> list[RunTrendPointOut]`, `failure_categories(project, *, limit=20) -> list[FailureCategoryOut]`.

The service returns Pydantic models directly. That matches `IngestionService.ingest`, which already returns `IngestSummary` — analytics has no ORM objects to hand back, so inventing a dataclass layer purely to convert it at the route would be ceremony.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_analytics_service.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from testforge.schemas.results import ResultIn
from testforge.services.analytics_service import AnalyticsService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

BASE = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def make_case(session, project, title):
    return CaseService(session).create(
        project=project,
        suite_id=None,
        title=title,
        execution_type="automated",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )


def record(session, project, index, outcomes, failures=None):
    """One run at BASE+index days, with {case_key: outcome} results."""
    runs = RunService(session)
    run, _ = runs.open(
        project=project,
        external_id=f"run-{index}",
        name=f"run {index}",
        source="ci",
        ci_metadata={},
        actor="local",
    )
    run.started_at = BASE + timedelta(days=index)
    session.flush()
    IngestionService(session).ingest(
        run=run,
        results=[
            ResultIn(
                case_key=case_key,
                test_identifier=f"tests/t.py::{case_key}",
                framework="pytest",
                outcome=outcome,
                failure_type=(failures or {}).get(case_key, (None, None))[0],
                failure_message=(failures or {}).get(case_key, (None, None))[1],
                executed_at=BASE + timedelta(days=index),
            )
            for case_key, outcome in outcomes.items()
        ],
    )
    runs.complete(run.id)
    session.commit()
    return run


def test_flaky_cases_flags_self_recovery_and_not_a_regression(db_session, project):
    make_case(db_session, project, "Flaky one")
    make_case(db_session, project, "Broken one")
    db_session.commit()
    # CHK-1 recovers on its own; CHK-2 breaks once and stays broken.
    for index, (a, b) in enumerate(
        [("passed", "passed"), ("passed", "passed"), ("failed", "passed"),
         ("passed", "failed"), ("failed", "failed"), ("passed", "failed")]
    ):
        record(db_session, project, index, {"CHK-1": a, "CHK-2": b})

    flaky = AnalyticsService(db_session).flaky_cases(project)

    assert [c.case_key for c in flaky] == ["CHK-1"]
    assert flaky[0].transitions == 4
    assert flaky[0].sample_size == 6
    assert flaky[0].sequence[0] == "passed"


def test_flaky_cases_are_ranked_by_score(db_session, project):
    make_case(db_session, project, "Very flaky")
    make_case(db_session, project, "Mildly flaky")
    db_session.commit()
    for index, (a, b) in enumerate(
        [("passed", "passed"), ("failed", "passed"), ("passed", "passed"),
         ("failed", "failed"), ("passed", "passed"), ("failed", "passed")]
    ):
        record(db_session, project, index, {"CHK-1": a, "CHK-2": b})

    flaky = AnalyticsService(db_session).flaky_cases(project)

    assert [c.case_key for c in flaky] == ["CHK-1", "CHK-2"]
    assert flaky[0].score > flaky[1].score


def test_flaky_cases_never_include_another_projects_history(db_session, project):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    db_session.commit()
    make_case(db_session, other, "Foreign flaky")
    db_session.commit()
    for index, outcome in enumerate(["passed", "failed", "passed", "failed", "passed"]):
        record(db_session, other, index, {"OTH-1": outcome})

    assert AnalyticsService(db_session).flaky_cases(project) == []


def test_run_trends_are_oldest_first_and_skip_does_not_count(db_session, project):
    make_case(db_session, project, "A")
    make_case(db_session, project, "B")
    db_session.commit()
    record(db_session, project, 0, {"CHK-1": "passed", "CHK-2": "skipped"})
    record(db_session, project, 1, {"CHK-1": "passed", "CHK-2": "failed"})

    trends = AnalyticsService(db_session).run_trends(project)

    assert [p.external_id for p in trends] == ["run-0", "run-1"]
    assert trends[0].pass_rate == 1.0, "a skip must not drag the pass rate down"
    assert trends[0].total == 1
    assert trends[1].pass_rate == 0.5


def test_a_run_with_nothing_countable_reports_a_null_pass_rate(db_session, project):
    make_case(db_session, project, "A")
    db_session.commit()
    record(db_session, project, 0, {"CHK-1": "skipped"})

    trends = AnalyticsService(db_session).run_trends(project)

    assert trends[0].pass_rate is None, "a null breaks the line rather than plotting a false zero"


def test_failure_categories_are_counted_and_ranked(db_session, project):
    make_case(db_session, project, "A")
    make_case(db_session, project, "B")
    db_session.commit()
    record(
        db_session,
        project,
        0,
        {"CHK-1": "failed", "CHK-2": "failed"},
        failures={
            "CHK-1": ("AssertionError", "assert 1 == 2"),
            "CHK-2": ("TimeoutError", "timed out"),
        },
    )
    record(
        db_session,
        project,
        1,
        {"CHK-1": "failed", "CHK-2": "passed"},
        failures={"CHK-1": ("AssertionError", "assert 3 == 4")},
    )

    categories = AnalyticsService(db_session).failure_categories(project)

    assert [(c.category, c.count) for c in categories] == [("assertion", 2), ("timeout", 1)]


def test_a_project_with_no_history_returns_empty_not_an_error(db_session, project):
    service = AnalyticsService(db_session)

    assert service.flaky_cases(project) == []
    assert service.run_trends(project) == []
    assert service.failure_categories(project) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_analytics_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.analytics_service'`

- [ ] **Step 3: Write the schemas**

Create `server/src/testforge/schemas/insights.py`:

```python
from datetime import datetime

from pydantic import BaseModel


class FlakyCaseOut(BaseModel):
    case_key: str
    title: str
    score: float
    transitions: int
    sample_size: int
    #: The window's binary outcomes, oldest first. A list rather than a packed
    #: string, so adding a third class later is not a format change.
    sequence: list[str]


class RunTrendPointOut(BaseModel):
    run_id: str
    external_id: str
    name: str | None
    started_at: datetime
    #: passed / (passed + failed + error). ``None`` when the run had nothing
    #: countable, so the chart breaks the line instead of plotting a false zero.
    pass_rate: float | None
    passed: int
    failed: int
    total: int


class FailureCategoryOut(BaseModel):
    category: str
    count: int
```

- [ ] **Step 4: Write the service**

Create `server/src/testforge/services/analytics_service.py`:

```python
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.analytics.flakiness import score_outcomes
from testforge.analytics.taxonomy import classify_failure
from testforge.models.case import TestCase
from testforge.models.project import Project
from testforge.models.run import Result, Run
from testforge.schemas.insights import FailureCategoryOut, FlakyCaseOut, RunTrendPointOut
from testforge.services.run_service import RunService

DEFAULT_RUN_WINDOW = 20


class AnalyticsService:
    """Read-only analysis over run history. Computes on read: no stored scores,
    no refresh path, and therefore no way for an analytic to go quietly stale."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def flaky_cases(self, project: Project) -> list[FlakyCaseOut]:
        rows = self.session.execute(
            select(Result.test_case_id, Result.outcome)
            .join(Run, Result.run_id == Run.id)
            .where(Run.project_id == project.id, Result.test_case_id.is_not(None))
            .order_by(Result.test_case_id, Result.executed_at)
        )
        history: dict[str, list[str]] = {}
        for case_id, outcome in rows:
            history.setdefault(case_id, []).append(outcome)

        scored = {case_id: score_outcomes(o) for case_id, o in history.items()}
        flaky_ids = [case_id for case_id, result in scored.items() if result.is_flaky]
        if not flaky_ids:
            return []

        # One batched lookup rather than one per flaky case.
        cases = {
            case.id: case
            for case in self.session.scalars(
                select(TestCase).where(TestCase.id.in_(flaky_ids))
            )
        }

        out = [
            FlakyCaseOut(
                case_key=cases[case_id].case_key,
                title=cases[case_id].title,
                score=round(scored[case_id].score, 3),
                transitions=scored[case_id].transitions,
                sample_size=scored[case_id].sample_size,
                sequence=scored[case_id].sequence,
            )
            for case_id in flaky_ids
        ]
        out.sort(key=lambda case: (-case.score, case.case_key))
        return out

    def run_trends(
        self, project: Project, *, limit: int = DEFAULT_RUN_WINDOW
    ) -> list[RunTrendPointOut]:
        runs = self._recent_runs(project, limit)
        counts = RunService(self.session).outcome_counts([run.id for run in runs])

        points: list[RunTrendPointOut] = []
        for run in reversed(runs):  # oldest first, for plotting left to right
            by_outcome = counts.get(run.id, {})
            passed = by_outcome.get("passed", 0)
            failed = by_outcome.get("failed", 0) + by_outcome.get("error", 0)
            total = passed + failed
            points.append(
                RunTrendPointOut(
                    run_id=run.id,
                    external_id=run.external_id,
                    name=run.name,
                    started_at=run.started_at,
                    pass_rate=(passed / total) if total else None,
                    passed=passed,
                    failed=failed,
                    total=total,
                )
            )
        return points

    def failure_categories(
        self, project: Project, *, limit: int = DEFAULT_RUN_WINDOW
    ) -> list[FailureCategoryOut]:
        run_ids = [run.id for run in self._recent_runs(project, limit)]
        if not run_ids:
            return []
        rows = self.session.execute(
            select(Result.failure_type, Result.failure_message).where(
                Result.run_id.in_(run_ids), Result.outcome.in_(("failed", "error"))
            )
        )
        counter = Counter(classify_failure(kind, message) for kind, message in rows)
        return [
            FailureCategoryOut(category=category, count=count)
            for category, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def _recent_runs(self, project: Project, limit: int) -> list[Run]:
        """Newest first. Both the trend and the category breakdown read the same
        window, so the two panels can never disagree about their time range."""
        stmt = (
            select(Run)
            .where(Run.project_id == project.id)
            .order_by(Run.started_at.desc(), Run.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_analytics_service.py -v`
Expected: PASS — 7 passed

- [ ] **Step 6: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 189 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add AnalyticsService for flakiness, trends, and failure categories"
```

---

### Task 4: Insights endpoints

**Files:**
- Create: `server/src/testforge/api/routes/insights.py`
- Modify: `server/src/testforge/main.py`
- Test: `server/tests/integration/test_insights_api.py`

**Interfaces:**
- Consumes: `AnalyticsService` (Task 3), `ProjectService.get_by_key`, `get_session`.
- Produces: `GET /api/projects/{project_key}/insights/flaky`, `.../insights/trends?limit=`, `.../insights/failure-categories?limit=`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/integration/test_insights_api.py`:

```python
import pytest

EXECUTED_AT = "2026-08-01T10:00:00Z"


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post("/api/projects/CHK/cases", json={"title": "Flaky one"})
    client.post("/api/projects/CHK/cases", json={"title": "Broken one"})
    return "CHK"


def record(client, index, outcomes, failures=None):
    run = client.post(
        "/api/projects/CHK/runs",
        json={"external_id": f"run-{index}", "name": f"run {index}", "source": "ci"},
    ).json()
    client.post(
        f"/api/runs/{run['id']}/results",
        json={
            "results": [
                {
                    "case_key": case_key,
                    "test_identifier": f"tests/t.py::{case_key}",
                    "outcome": outcome,
                    "failure_type": (failures or {}).get(case_key, (None, None))[0],
                    "failure_message": (failures or {}).get(case_key, (None, None))[1],
                    "executed_at": EXECUTED_AT,
                }
                for case_key, outcome in outcomes.items()
            ]
        },
    )
    client.post(f"/api/runs/{run['id']}/complete")
    return run


def test_flaky_endpoint_reports_the_self_recovering_case_only(client, project_key):
    for index, (a, b) in enumerate(
        [("passed", "passed"), ("passed", "passed"), ("failed", "passed"),
         ("passed", "failed"), ("failed", "failed"), ("passed", "failed")]
    ):
        record(client, index, {"CHK-1": a, "CHK-2": b})

    response = client.get("/api/projects/CHK/insights/flaky")

    assert response.status_code == 200
    body = response.json()
    assert [c["case_key"] for c in body] == ["CHK-1"]
    assert body[0]["transitions"] == 4
    assert len(body[0]["sequence"]) == 6


def test_trends_endpoint_returns_points_oldest_first(client, project_key):
    record(client, 0, {"CHK-1": "passed", "CHK-2": "passed"})
    record(client, 1, {"CHK-1": "passed", "CHK-2": "failed"})

    body = client.get("/api/projects/CHK/insights/trends").json()

    assert [p["external_id"] for p in body] == ["run-0", "run-1"]
    assert body[0]["pass_rate"] == 1.0
    assert body[1]["pass_rate"] == 0.5


def test_trends_limit_is_honoured(client, project_key):
    for index in range(5):
        record(client, index, {"CHK-1": "passed"})

    body = client.get("/api/projects/CHK/insights/trends", params={"limit": 2}).json()

    assert len(body) == 2
    assert [p["external_id"] for p in body] == ["run-3", "run-4"], (
        "the limit must take the most recent runs, then present them oldest-first"
    )


def test_failure_categories_endpoint_counts_by_category(client, project_key):
    record(
        client,
        0,
        {"CHK-1": "failed", "CHK-2": "failed"},
        failures={
            "CHK-1": ("AssertionError", "assert 1 == 2"),
            "CHK-2": (None, "Cart total drifted by 0.01"),
        },
    )

    body = client.get("/api/projects/CHK/insights/failure-categories").json()

    assert {c["category"] for c in body} == {"assertion", "uncategorized"}


def test_insights_endpoints_are_empty_not_an_error_for_a_fresh_project(client, project_key):
    assert client.get("/api/projects/CHK/insights/flaky").json() == []
    assert client.get("/api/projects/CHK/insights/trends").json() == []
    assert client.get("/api/projects/CHK/insights/failure-categories").json() == []


def test_insights_404_for_an_unknown_project(client):
    response = client.get("/api/projects/NOPE/insights/flaky")

    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_insights_api.py -v`
Expected: FAIL — 404s, the routes do not exist.

- [ ] **Step 3: Write the routes**

Create `server/src/testforge/api/routes/insights.py`:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from testforge.api.deps import get_session
from testforge.schemas.insights import FailureCategoryOut, FlakyCaseOut, RunTrendPointOut
from testforge.services.analytics_service import AnalyticsService
from testforge.services.project_service import ProjectService

router = APIRouter(tags=["insights"])


@router.get(
    "/api/projects/{project_key}/insights/flaky", response_model=list[FlakyCaseOut]
)
def list_flaky_cases(
    project_key: str, session: Session = Depends(get_session)
) -> list[FlakyCaseOut]:
    project = ProjectService(session).get_by_key(project_key)
    return AnalyticsService(session).flaky_cases(project)


@router.get(
    "/api/projects/{project_key}/insights/trends", response_model=list[RunTrendPointOut]
)
def run_trends(
    project_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[RunTrendPointOut]:
    project = ProjectService(session).get_by_key(project_key)
    return AnalyticsService(session).run_trends(project, limit=limit)


@router.get(
    "/api/projects/{project_key}/insights/failure-categories",
    response_model=list[FailureCategoryOut],
)
def failure_categories(
    project_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[FailureCategoryOut]:
    project = ProjectService(session).get_by_key(project_key)
    return AnalyticsService(session).failure_categories(project, limit=limit)
```

- [ ] **Step 4: Register the router**

In `server/src/testforge/main.py`, add `insights` to the route imports so the line reads:

```python
from testforge.api.routes import cases, health, insights, plans, projects, runs, suites
```

and add one include line alongside the others, **before** the SPA static-mount block:

```python
    app.include_router(insights.router)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_insights_api.py -v`
Expected: PASS — 6 passed

- [ ] **Step 6: Run the full suite and linters**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 195 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add the three insights endpoints"
```

---

### Task 5: Demo seed with run history

**Files:**
- Modify: `server/src/testforge/seed.py`
- Test: `server/tests/integration/test_seed.py` (existing — modify one test, add two)

**Interfaces:**
- Consumes: `PlanService.create`, `RunService.open_for_plan/complete`, `IngestionService.ingest`, `ResultIn`.
- Produces: `seed_demo(session)` returning `{"projects", "suites", "cases", "plans", "runs", "results"}` with `runs == 6`.

**Read this before writing code.** The seed is a fixture with a contract: Slice 5a's Playwright specs assert against its exact contents. This task deliberately keeps the **newest** run identical in shape to today's single run — same six cases, same outcomes (4 passed, 1 failed, 1 skipped), same `CHK-3` assertion failure text and stack trace — and adds five *older* runs before it. That is what keeps `run-detail.spec.ts` passing untouched. Exactly two existing assertions still change, both listed in Step 5.

- [ ] **Step 1: Write the failing tests**

In `server/tests/integration/test_seed.py`, replace `test_seed_creates_a_completed_run_with_mixed_results` with:

```python
def test_seed_creates_a_history_of_completed_runs(db_session):
    counts = seed_demo(db_session)
    db_session.commit()

    assert counts["plans"] == 1
    assert counts["runs"] == 6
    assert counts["results"] == 36

    project = ProjectService(db_session).get_by_key("CHK")
    runs = RunService(db_session).list_for_project(project)
    assert len(runs) == 6
    assert all(run.status == "completed" for run in runs)
    assert all(run.plan_id is not None for run in runs)
    assert runs[0].started_at > runs[-1].started_at, "list_for_project is newest-first"
```

and append these two:

```python
def test_the_seeded_history_distinguishes_a_flaky_case_from_a_broken_one(db_session):
    seed_demo(db_session)
    db_session.commit()
    project = ProjectService(db_session).get_by_key("CHK")

    flaky = AnalyticsService(db_session).flaky_cases(project)

    keys = [case.case_key for case in flaky]
    assert "CHK-1" in keys, "CHK-1 recovers on its own and must be flagged"
    assert "CHK-3" not in keys, (
        "CHK-3 breaks once and stays broken — a regression, and the demo exists "
        "to show that the dashboard tells the two apart"
    )


def test_the_seeded_history_exercises_several_failure_categories(db_session):
    seed_demo(db_session)
    db_session.commit()
    project = ProjectService(db_session).get_by_key("CHK")

    categories = {c.category for c in AnalyticsService(db_session).failure_categories(project)}

    assert {"assertion", "timeout", "connection"} <= categories
    assert "uncategorized" in categories, (
        "one failure deliberately falls through, so the bucket is visible in the demo"
    )
```

Add the import these need to the top of the file:

```python
from testforge.services.analytics_service import AnalyticsService
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_seed.py -v`
Expected: FAIL — `assert 1 == 6` on the run count.

- [ ] **Step 3: Rewrite the seed's run section**

In `server/src/testforge/seed.py`, add `timedelta` to the datetime import so the line reads:

```python
from datetime import UTC, datetime, timedelta
```

Replace the `DEMO_RESULTS` constant with these five:

```python
DEMO_RUN_COUNT = 6

#: Per-case outcome sequence across the demo runs, oldest first.
#: CHK-1 recovers on its own twice, so it is flaky. CHK-3 breaks once and stays
#: broken, so it is a regression and must NOT be flagged — the demo exists to
#: show the dashboard telling those apart. CHK-4 is always skipped, which the
#: scorer ignores entirely. The final column reproduces the run Slice 5a's
#: Playwright specs already assert against.
DEMO_SEQUENCES = {
    "CHK-1": "PPFPFP",
    "CHK-2": "PPPPPP",
    "CHK-3": "PPPFFF",
    "CHK-4": "SSSSSS",
    "CHK-5": "PPPPPP",
    "CHK-404": "PPPPPP",
}

OUTCOME_BY_LETTER = {"P": "passed", "F": "failed", "S": "skipped"}

DEMO_IDENTIFIERS = {
    "CHK-1": "tests/test_checkout.py::test_coupon_applies",
    "CHK-2": "tests/test_checkout.py::test_expired_coupon",
    "CHK-3": "tests/test_checkout.py::test_payment_retry_prompt",
    "CHK-4": "tests/test_mobile.py::test_order_history",
    "CHK-5": "tests/test_auth.py::test_session_expiry",
    "CHK-404": "tests/test_search.py::test_orphaned_check",
}

#: (case_key, run index) -> (failure_type, failure_message, stack_trace).
#: Chosen so the category breakdown has four distinct buckets, one of which
#: deliberately falls through to "uncategorized".
DEMO_FAILURE_DETAIL = {
    ("CHK-1", 2): ("TimeoutError", "Timed out after 30s waiting for the coupon field", None),
    ("CHK-1", 4): ("ConnectionError", "Connection refused by payments-sandbox:8443", None),
    ("CHK-3", 3): (None, "Cart total drifted by 0.01 between render and submit", None),
    ("CHK-3", 4): (
        "AssertionError",
        "assert 'Retry payment' in 'Payment failed. Contact support.'",
        DEMO_STACK_TRACE,
    ),
    ("CHK-3", 5): (
        "AssertionError",
        "assert 'Retry payment' in 'Payment failed. Contact support.'",
        DEMO_STACK_TRACE,
    ),
}
```

Then replace everything from `runs = RunService(session)` down to (but not including) the final `return`, with:

```python
    runs = RunService(session)
    ingestion = IngestionService(session)
    now = datetime.now(UTC)
    result_count = 0

    for index in range(DEMO_RUN_COUNT):
        # One run per day, oldest first, so the trend chart has a real x-axis.
        started = now - timedelta(days=DEMO_RUN_COUNT - 1 - index)
        run = runs.open_for_plan(
            plan=plan,
            name=f"nightly regression {started.date().isoformat()}",
            external_id=f"demo-run-{index + 1}",
            actor="seed",
        )
        run.started_at = started
        session.flush()

        batch = []
        for case_key, sequence in DEMO_SEQUENCES.items():
            failure_type, failure_message, stack_trace = DEMO_FAILURE_DETAIL.get(
                (case_key, index), (None, None, None)
            )
            batch.append(
                ResultIn(
                    case_key=case_key,
                    test_identifier=DEMO_IDENTIFIERS[case_key],
                    framework="pytest",
                    outcome=OUTCOME_BY_LETTER[sequence[index]],
                    duration_ms=120,
                    failure_type=failure_type,
                    failure_message=failure_message,
                    stack_trace=stack_trace,
                    executed_at=started,
                )
            )
        ingestion.ingest(run=run, results=batch)
        result_count += len(batch)

        completed = runs.complete(run.id)
        completed.completed_at = started
        session.flush()
```

and change the final return to:

```python
    return {
        "projects": 1,
        "suites": 2,
        "cases": len(DEMO_CASES),
        "plans": 1,
        "runs": DEMO_RUN_COUNT,
        "results": result_count,
    }
```

- [ ] **Step 4: Run the seed tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_seed.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Update the two Slice 5a assertions the new seed invalidates**

Run the full backend suite and the E2E suite to find them:

Run: `uv run pytest -q`
Expected: PASS — 197 passed. (The backend suite should be unaffected; if anything else fails, read it before changing it.)

Run: `cd frontend && npm run build && npm run test:e2e && cd ..`
Expected: **one failing spec** — `runs.spec.ts`'s "the seeded run appears with its outcome counts", which asserts `toHaveCount(1)`.

Fix exactly that assertion in `frontend/tests/e2e/runs.spec.ts` — the seed now has six runs, and the newest is still the one with the previously-asserted counts:

```ts
test("the seeded runs appear, newest first, with outcome counts", async ({ page }) => {
  await page.goto("/projects/CHK/runs");

  const rows = page.getByTestId("run-row");
  await expect(rows).toHaveCount(6);
  await expect(rows.first()).toContainText("nightly regression");
  await expect(rows.first().getByTestId("outcome-passed")).toContainText("4");
  await expect(rows.first().getByTestId("outcome-failed")).toContainText("1");
});
```

Do **not** weaken any other assertion to make something pass. If a spec other than this one fails, that is a real signal the seed change broke behaviour — investigate rather than adjust the test.

- [ ] **Step 6: Re-run everything**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 197 passed.

Run: `cd frontend && npm run test:e2e && cd ..`
Expected: 9 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: seed six runs of history so flakiness and trends have data"
```

---

### Task 6: Insights page and the flaky-tests panel

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (regenerated), `frontend/src/api/types.ts`, `frontend/src/api/queries.ts`, `frontend/src/App.tsx`, `frontend/src/styles.css`
- Create: `frontend/src/pages/InsightsPage.tsx`, `frontend/src/components/FlakyTable.tsx`

**Interfaces:**
- Consumes: `apiFetch`, the `keys` factory, `ErrorState` (Slice 5a).
- Produces: type aliases `FlakyCase`, `RunTrendPoint`, `FailureCategory`; hooks `useFlakyCases(projectKey)`, `useRunTrends(projectKey, limit?)`, `useFailureCategories(projectKey, limit?)`; `FlakyTable`; the `/projects/:projectKey/insights` route.

- [ ] **Step 1: Regenerate the API types**

Run: `make types`
Expected: `frontend/src/api/schema.d.ts` updated. Verify the new schemas landed:

Run: `grep -c "FlakyCaseOut" frontend/src/api/schema.d.ts`
Expected: at least 1.

- [ ] **Step 2: Add the type aliases**

In `frontend/src/api/types.ts`, add these three lines:

```ts
export type FlakyCase = components["schemas"]["FlakyCaseOut"];
export type RunTrendPoint = components["schemas"]["RunTrendPointOut"];
export type FailureCategory = components["schemas"]["FailureCategoryOut"];
```

- [ ] **Step 3: Add the query keys and hooks**

In `frontend/src/api/queries.ts`, add the three types to the existing import from `./types` (so it reads `import type { FailureCategory, FlakyCase, Plan, Project, ResultRow, RunListItem, RunSummary, RunTrendPoint } from "./types";`), add these three entries to the `keys` object:

```ts
  insightsFlaky: (projectKey: string) =>
    ["projects", projectKey, "insights", "flaky"] as const,
  insightsTrends: (projectKey: string, limit: number) =>
    ["projects", projectKey, "insights", "trends", limit] as const,
  insightsCategories: (projectKey: string, limit: number) =>
    ["projects", projectKey, "insights", "categories", limit] as const,
```

and append these three hooks:

```ts
export function useFlakyCases(projectKey: string) {
  return useQuery({
    queryKey: keys.insightsFlaky(projectKey),
    queryFn: () =>
      apiFetch<FlakyCase[]>(`/api/projects/${projectKey}/insights/flaky`),
    enabled: Boolean(projectKey),
  });
}

export function useRunTrends(projectKey: string, limit = 20) {
  return useQuery({
    queryKey: keys.insightsTrends(projectKey, limit),
    queryFn: () =>
      apiFetch<RunTrendPoint[]>(
        `/api/projects/${projectKey}/insights/trends?limit=${limit}`,
      ),
    enabled: Boolean(projectKey),
  });
}

export function useFailureCategories(projectKey: string, limit = 20) {
  return useQuery({
    queryKey: keys.insightsCategories(projectKey, limit),
    queryFn: () =>
      apiFetch<FailureCategory[]>(
        `/api/projects/${projectKey}/insights/failure-categories?limit=${limit}`,
      ),
    enabled: Boolean(projectKey),
  });
}
```

- [ ] **Step 4: Write the flaky-tests panel**

Create `frontend/src/components/FlakyTable.tsx`:

```tsx
import type { FlakyCase } from "../api/types";

/** One outcome as a letter plus colour — never colour alone, so the sequence
 *  survives a colourblind reader, a screenshot, and forced-colours mode. */
function OutcomeMark({ outcome }: { outcome: string }) {
  const passed = outcome === "passed";
  return (
    <abbr
      className={`seq-mark ${passed ? "seq-pass" : "seq-fail"}`}
      title={outcome}
    >
      {passed ? "P" : "F"}
    </abbr>
  );
}

export function FlakyTable({ cases }: { cases: FlakyCase[] }) {
  if (cases.length === 0) {
    return (
      <p className="muted">
        No flaky tests detected. A test is flagged once it recovers on its own —
        two or more pass/fail transitions across at least five runs.
      </p>
    );
  }

  return (
    <table data-testid="flaky-table">
      <thead>
        <tr>
          <th>Case</th>
          <th>Title</th>
          <th>Score</th>
          <th>Flips</th>
          <th>Runs</th>
          <th>History (oldest first)</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((flaky) => (
          <tr key={flaky.case_key} data-testid="flaky-row">
            <td>{flaky.case_key}</td>
            <td>{flaky.title}</td>
            <td>{flaky.score.toFixed(2)}</td>
            <td>{flaky.transitions}</td>
            <td>{flaky.sample_size}</td>
            <td className="seq">
              {flaky.sequence.map((outcome, index) => (
                <OutcomeMark key={index} outcome={outcome} />
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 5: Write the page**

Create `frontend/src/pages/InsightsPage.tsx`:

```tsx
import { useParams } from "react-router-dom";

import { useFailureCategories, useFlakyCases, useRunTrends } from "../api/queries";
import { ErrorState } from "../components/ErrorState";
import { FlakyTable } from "../components/FlakyTable";

export function InsightsPage() {
  const { projectKey = "" } = useParams<{ projectKey: string }>();
  const flaky = useFlakyCases(projectKey);
  const trends = useRunTrends(projectKey);
  const categories = useFailureCategories(projectKey);

  return (
    <>
      <h2>Insights</h2>

      <section className="panel">
        <h3>Flaky tests</h3>
        {flaky.error && <ErrorState error={flaky.error} />}
        {flaky.isLoading && <p className="muted">Loading…</p>}
        {flaky.data && <FlakyTable cases={flaky.data} />}
      </section>

      <section className="panel">
        <h3>Pass rate over recent runs</h3>
        {trends.error && <ErrorState error={trends.error} />}
        {trends.isLoading && <p className="muted">Loading…</p>}
        {trends.data && <p className="muted">{trends.data.length} runs</p>}
      </section>

      <section className="panel">
        <h3>Failure categories</h3>
        {categories.error && <ErrorState error={categories.error} />}
        {categories.isLoading && <p className="muted">Loading…</p>}
        {categories.data && <p className="muted">{categories.data.length} categories</p>}
      </section>
    </>
  );
}
```

The last two panels are placeholders here on purpose — Task 7 replaces their bodies with the charts. Splitting it this way keeps the routing/data wiring reviewable separately from the SVG.

- [ ] **Step 6: Add the route and the nav link**

In `frontend/src/App.tsx`, add the import:

```tsx
import { InsightsPage } from "./pages/InsightsPage";
```

add the route inside the `Shell` layout route, after the plans route:

```tsx
        <Route path="/projects/:projectKey/insights" element={<InsightsPage />} />
```

and add the nav link inside the existing `<nav>` block, after the Plans link:

```tsx
            <Link to={`/projects/${active}/insights`}>Insights</Link>
```

- [ ] **Step 7: Add the sequence styles**

Append to `frontend/src/styles.css`:

```css
.panel h3 { margin: 0 0 0.75rem; font-size: 0.95rem; }

.seq { white-space: nowrap; }
.seq-mark {
  display: inline-block;
  width: 1.1rem;
  text-align: center;
  font-size: 0.7rem;
  font-weight: 700;
  line-height: 1.3rem;
  border-radius: 3px;
  margin-right: 2px;
  text-decoration: none;
}
.seq-pass { background: #dafbe1; color: var(--pass); }
.seq-fail { background: #ffebe9; color: var(--fail); }
```

- [ ] **Step 8: Verify typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build && cd ..`
Expected: no type errors; build succeeds.

- [ ] **Step 9: Look at it**

Run `make seed && make dev` in one shell and `make ui` in another, then open `http://localhost:5173/projects/CHK/insights`. Expected: the flaky table lists `CHK-1` with a P/F history and **does not** list `CHK-3`; the other two panels show their placeholder counts.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: add the Insights page and flaky-tests panel"
```

---

### Task 7: Trend and category charts

Two hand-rolled SVG charts, no charting dependency. Both encode magnitude with a **single hue**; neither uses colour to carry identity, so there is no categorical palette here to validate.

**Files:**
- Create: `frontend/src/components/TrendChart.tsx`, `frontend/src/components/CategoryBars.tsx`
- Modify: `frontend/src/pages/InsightsPage.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: types `RunTrendPoint`, `FailureCategory` (Task 6).
- Produces: `TrendChart({ points })`, `CategoryBars({ categories })`.

- [ ] **Step 1: Write the trend chart**

Create `frontend/src/components/TrendChart.tsx`:

```tsx
import { useState } from "react";

import type { RunTrendPoint } from "../api/types";

const WIDTH = 640;
const HEIGHT = 200;
const PAD = { top: 12, right: 16, bottom: 30, left: 44 };

/** A point we can actually plot: pass_rate is null for runs with nothing countable. */
type Plotted = RunTrendPoint & { pass_rate: number };

export function TrendChart({ points }: { points: RunTrendPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const plotted: Plotted[] = points.filter(
    (point): point is Plotted => point.pass_rate !== null,
  );

  if (plotted.length < 2) {
    return <p className="muted">Not enough runs yet to show a trend.</p>;
  }

  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const x = (index: number) => PAD.left + (innerW * index) / (plotted.length - 1);
  const y = (rate: number) => PAD.top + innerH * (1 - rate);

  const line = plotted
    .map((point, index) => `${index === 0 ? "M" : "L"} ${x(index)} ${y(point.pass_rate)}`)
    .join(" ");

  const active = hover === null ? null : plotted[hover];

  return (
    <figure className="chart" data-testid="trend-chart">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Pass rate across the last ${plotted.length} runs`}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.5, 1].map((tick) => (
          <g key={tick}>
            <line
              className="chart-grid"
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(tick)}
              y2={y(tick)}
            />
            <text className="chart-tick" x={PAD.left - 8} y={y(tick) + 4} textAnchor="end">
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}

        <path className="chart-line" d={line} fill="none" />

        {plotted.map((point, index) => (
          <circle
            key={point.run_id}
            className="chart-dot"
            cx={x(index)}
            cy={y(point.pass_rate)}
            r={4}
          />
        ))}

        {/* Full-height hit strips: a 4px dot is too small to aim at. */}
        {plotted.map((point, index) => (
          <rect
            key={`hit-${point.run_id}`}
            x={x(index) - innerW / (2 * (plotted.length - 1))}
            y={PAD.top}
            width={innerW / (plotted.length - 1)}
            height={innerH}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
          />
        ))}

        {active && (
          <line
            className="chart-crosshair"
            x1={x(hover as number)}
            x2={x(hover as number)}
            y1={PAD.top}
            y2={PAD.top + innerH}
          />
        )}

        <text className="chart-tick" x={PAD.left} y={HEIGHT - 8}>
          {new Date(plotted[0].started_at).toLocaleDateString()}
        </text>
        <text
          className="chart-tick"
          x={WIDTH - PAD.right}
          y={HEIGHT - 8}
          textAnchor="end"
        >
          {new Date(plotted[plotted.length - 1].started_at).toLocaleDateString()}
        </text>
      </svg>

      <figcaption className="muted" data-testid="trend-caption">
        {active
          ? `${active.name ?? active.external_id}: ${Math.round(active.pass_rate * 100)}% (${active.passed}/${active.total})`
          : `Latest: ${Math.round(plotted[plotted.length - 1].pass_rate * 100)}%`}
      </figcaption>
    </figure>
  );
}
```

- [ ] **Step 2: Write the category bars**

Create `frontend/src/components/CategoryBars.tsx`:

```tsx
import type { FailureCategory } from "../api/types";

/** Horizontal bars: magnitude is bar length, identity is the text label.
 *  One hue throughout — colour is not carrying identity here, so a
 *  multi-hue categorical palette would add nothing but a legend to read. */
export function CategoryBars({ categories }: { categories: FailureCategory[] }) {
  if (categories.length === 0) {
    return <p className="muted">No failures in the recent runs.</p>;
  }
  const max = Math.max(...categories.map((category) => category.count));

  return (
    <ul className="bars" data-testid="category-bars">
      {categories.map((category) => (
        <li key={category.category} data-testid="category-row">
          <span className="bar-label">{category.category}</span>
          <span className="bar-track">
            <span
              className="bar-fill"
              style={{ width: `${(category.count / max) * 100}%` }}
            />
          </span>
          <span className="bar-value">{category.count}</span>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 3: Add the chart styles**

Append to `frontend/src/styles.css`:

```css
.chart { margin: 0; }
.chart svg { width: 100%; height: auto; }
.chart-grid { stroke: var(--line); stroke-width: 1; }
.chart-tick { fill: var(--muted); font-size: 11px; }
.chart-line { stroke: var(--accent); stroke-width: 2; }
.chart-dot { fill: var(--accent); }
.chart-crosshair { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }

.bars { list-style: none; margin: 0; padding: 0; }
.bars li {
  display: grid;
  grid-template-columns: 9rem 1fr 3rem;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.4rem;
}
.bar-track { background: #eaeef2; border-radius: 3px; height: 0.9rem; }
.bar-fill { display: block; background: var(--accent); height: 100%; border-radius: 3px; }
.bar-value { text-align: right; font-variant-numeric: tabular-nums; }
```

- [ ] **Step 4: Wire the charts into the page**

In `frontend/src/pages/InsightsPage.tsx`, add the two imports:

```tsx
import { CategoryBars } from "../components/CategoryBars";
import { TrendChart } from "../components/TrendChart";
```

replace `{trends.data && <p className="muted">{trends.data.length} runs</p>}` with:

```tsx
        {trends.data && <TrendChart points={trends.data} />}
```

and replace `{categories.data && <p className="muted">{categories.data.length} categories</p>}` with:

```tsx
        {categories.data && <CategoryBars categories={categories.data} />}
```

- [ ] **Step 5: Verify typecheck and build**

Run: `cd frontend && npm run typecheck && npm run build && cd ..`
Expected: no type errors; build succeeds.

- [ ] **Step 6: Look at it, and check the layout — the type checker cannot**

With `make dev` and `make ui` running, open `http://localhost:5173/projects/CHK/insights`. Confirm: the trend line renders across six points with 0/50/100% gridlines; hovering moves the crosshair and updates the caption; the category bars are sorted longest-first with readable labels and counts; nothing overflows its panel and no labels collide.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add the pass-rate trend and failure-category charts"
```

---

### Task 8: End-to-end tests and README

**Files:**
- Create: `frontend/tests/e2e/insights.spec.ts`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-7 plus the seeded history from Task 5.
- Produces: five E2E specs; a README section.

- [ ] **Step 1: Write the specs**

Create `frontend/tests/e2e/insights.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

test("insights is reachable from the nav", async ({ page }) => {
  await page.goto("/projects/CHK/runs");
  await page.getByRole("link", { name: "Insights" }).click();

  await expect(page).toHaveURL(/\/projects\/CHK\/insights$/);
});

test("the self-recovering test is flagged and the broken one is not", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const rows = page.getByTestId("flaky-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("CHK-1");

  // CHK-3 fails in the three most recent runs and stays failed — a regression,
  // not flakiness. Flagging it would defeat the whole point of the rule.
  await expect(page.getByTestId("flaky-table")).not.toContainText("CHK-3");
});

test("the flaky test's history is shown, not just a score", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const history = page.getByTestId("flaky-row").first().locator(".seq-mark");
  await expect(history).toHaveCount(6);
  await expect(history.first()).toHaveText("P");
});

test("the trend chart plots the seeded runs", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const chart = page.getByTestId("trend-chart");
  await expect(chart).toBeVisible();
  await expect(chart.locator(".chart-dot")).toHaveCount(6);
});

test("failure categories include the deliberately uncategorised one", async ({ page }) => {
  await page.goto("/projects/CHK/insights");

  const bars = page.getByTestId("category-bars");
  await expect(bars).toContainText("assertion");
  await expect(bars).toContainText("uncategorized");
});
```

- [ ] **Step 2: Run the E2E suite**

Run: `cd frontend && npm run test:e2e && cd ..`
Expected: 14 passed (9 existing + 5 new).

If the flaky-row count is not 1, read the actual page before changing the assertion — the seeded sequences in `server/src/testforge/seed.py` are the source of truth, and a mismatch means either the seed or the scorer disagrees with the design, which is worth understanding rather than papering over.

- [ ] **Step 3: Update the README**

In `README.md`, add this section immediately before the `## Design` heading:

````markdown
## Insights

Beyond a single run, the dashboard answers whether the suite is trustworthy:

```bash
make demo && make serve   # then open http://localhost:8000/projects/CHK/insights
```

**Flaky tests** are found by counting pass/fail transitions in each case's history. A
test that broke once and stayed broken transitioned once — that is a regression, and it
is not listed here. A test that recovered with nobody fixing it transitioned twice or
more, and that unexplained self-recovery is what flakiness is. Each flagged test shows
the history that earned the flag, so the score is inspectable rather than a verdict.

**Pass rate** is charted across recent runs, counting only executed tests — skips are
excluded from both sides of the ratio, so skipping tests never flatters the number.

**Failure categories** are matched from the exception type and message. A large
`uncategorized` bucket is useful signal that the rules need extending for your codebase,
so failures are never forced into a category that does not fit.
````

- [ ] **Step 4: Run everything**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -q`
Expected: ruff clean; 197 passed.

Run: `cd frontend && npm run typecheck && npm run build && npm run test:e2e && cd ..`
Expected: 14 passed.

Run: `make types && git diff --exit-code frontend/src/api/schema.d.ts`
Expected: no diff — the committed types match the backend.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: add insights end-to-end specs and README section"
```

---

## Verification

After Task 8:

```bash
make demo && make serve
```

Open `http://localhost:8000/projects/CHK/insights` and confirm the slice's success criterion: `CHK-1` is flagged flaky with its history visible, `CHK-3` is **not** flagged despite failing in the three most recent runs, the trend plots six runs, and the category breakdown includes `uncategorized`.

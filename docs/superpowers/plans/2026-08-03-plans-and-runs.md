# Test Plans & Runs (Slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add test plans — a named, frozen selection of test cases plus execution context — that can be executed into runs, with manual per-case execution and computed plan progress.

**Architecture:** Follows Slice 1's module pattern exactly: one service per table group as the sole entry point, thin routes, no cross-module table access. `PlanService` owns the new tables and is the only writer of `test_plan_cases`. `ManualExecutionService` is a second, narrow write path into the *existing* `results` table, deliberately separate from `IngestionService` (batch, external, unresolved-tolerant) because manual execution is single, internal, and intolerant of unknown cases. Both produce identical row shapes so downstream reporting stays uniform.

**Tech Stack:** Python 3.12+, uv, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, SQLite (Postgres-compatible), pytest, ruff.

Source spec: `docs/superpowers/specs/2026-08-03-plans-and-runs-design.md`

## Global Constraints

- Python `>=3.12`. All dependency management via `uv add` / `uv add --dev`; never hand-edit `[project.dependencies]`.
- All commands run from the **repository root** (Alembic's `script_location` is repo-root-relative; `pytest`'s `testpaths` assumes root).
- Primary keys are UUID strings generated in Python (`testforge.ids.new_id()`), never database sequences.
- Schema must stay Postgres-compatible: `sqlalchemy.JSON`, `String(n)` with explicit lengths, and `UTCDateTime` (the project's `DateTime(timezone=True)` wrapper in `testforge.db.base` that re-attaches UTC on read — use it, never bare `DateTime`).
- Every task that adds or changes a model also adds an Alembic migration: `uv run alembic -c server/alembic.ini revision --autogenerate -m "..."`. If the generated file trips `ruff check`, run `ruff check --fix` + `ruff format` on it (standard practice throughout Slice 1).
- All timestamps are timezone-aware UTC.
- Every error response body is exactly `{"code": str, "message": str, "details": object}`.
- Error codes added by this slice: `plan_not_found`, `case_not_in_plan`, `run_canceled`, `empty_plan_selection`, `plan_archived`. Existing Slice 1 codes remain valid.
- Every write endpoint accepts an `X-Actor` header, defaulting to `local`, via `actor: str = Depends(get_actor)`.
- Cross-project references are rejected as not-found, never leaked as existing.
- Slice 1's 87 existing tests must continue to pass untouched. Ad-hoc (planless) runs must keep working exactly as before.

## File Structure

```
server/src/testforge/
  models/plan.py                 TestPlan, TestPlanCase                    [new]
  models/__init__.py             register the two new models               [modify]
  models/run.py                  plan_id gains a real FK                   [modify]
  services/plan_service.py       PlanService — sole writer of plan tables   [new]
  services/manual_service.py     ManualExecutionService                     [new]
  services/run_service.py        + open_for_plan(), cancel()                [modify]
  schemas/plans.py               PlanCreate/PlanOut/PlanCaseOut/CaseFilter  [new]
  schemas/runs.py                + PlanProgress, RunSummaryOut.plan_progress [modify]
  api/routes/plans.py            plan CRUD + execute                        [new]
  api/routes/runs.py             + cancel, manual execute, progress         [modify]
  main.py                        register plans router                      [modify]
server/tests/
  unit/test_plan_service.py                                                 [new]
  unit/test_manual_service.py                                               [new]
  integration/test_plans_api.py                                             [new]
  acceptance/test_plan_execution.py                                         [new]
```

Split rationale: `plans` and `manual` are separate modules because they own different concerns — one owns the plan tables, one writes results. Putting manual execution in `plan_service.py` would couple "what should be tested" to "recording what happened," which is exactly the boundary S5's reporting will need to cross cleanly.

---

### Task 1: Plan models and migration

**Files:**
- Create: `server/src/testforge/models/plan.py`
- Modify: `server/src/testforge/models/__init__.py`, `server/src/testforge/models/run.py`
- Test: `server/tests/integration/test_migrations.py` (existing — must still pass, no edit)

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `UTCDateTime`, `new_id` from `testforge.db.base` / `testforge.ids`.
- Produces: models `TestPlan` (`id`, `project_id`, `name`, `description`, `milestone`, `environment`, `status`, `created_by`, `updated_by`, `archived_at`) and `TestPlanCase` (`plan_id`, `test_case_id`, `test_case_version_id`, `position`); `Run.plan_id` now FK-constrained to `test_plans.id`.

- [ ] **Step 1: Write the models**

Create `server/src/testforge/models/plan.py`:

```python
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, TimestampMixin, UTCDateTime
from testforge.ids import new_id


class TestPlan(Base, TimestampMixin):
    __tablename__ = "test_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    milestone: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    environment: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class TestPlanCase(Base):
    """A frozen member of a plan's case selection. Immutable once written."""

    __tablename__ = "test_plan_cases"

    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_plans.id"), primary_key=True
    )
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id"), primary_key=True
    )
    test_case_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_case_versions.id"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

- [ ] **Step 2: Register the models**

Replace `server/src/testforge/models/__init__.py`:

```python
"""Importing this module registers every table on ``Base.metadata``."""

from testforge.models.automation import AutomationLink
from testforge.models.case import Tag, TestCase, TestCaseVersion, test_case_tags
from testforge.models.plan import TestPlan, TestPlanCase
from testforge.models.project import Project
from testforge.models.run import Result, Run
from testforge.models.suite import Suite

__all__ = [
    "AutomationLink",
    "Project",
    "Result",
    "Run",
    "Suite",
    "Tag",
    "TestCase",
    "TestCaseVersion",
    "TestPlan",
    "TestPlanCase",
    "test_case_tags",
]
```

- [ ] **Step 3: Add the FK to `Run.plan_id`**

In `server/src/testforge/models/run.py`, replace the `plan_id` line:

```python
    plan_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_plans.id"), nullable=True, index=True
    )
```

- [ ] **Step 4: Generate the migration**

```bash
uv run alembic -c server/alembic.ini revision --autogenerate -m "add test plans"
```

Expected: a new version file creating `test_plans` and `test_plan_cases`, and adding an FK on `runs.plan_id`.

Note `runs.status` needs **no** migration to accept `"canceled"` — it is a plain `String(20)` with no DB-level enum or check constraint, so the new value is purely an application-level concern (Task 3).

Note: SQLite cannot add a constraint to an existing table in place, so the generated migration must use `batch_alter_table` for the `runs.plan_id` FK. `env.py` already sets `render_as_batch=True`, so autogenerate should emit this correctly. If the generated file does NOT wrap the `runs` change in `with op.batch_alter_table("runs") as batch_op:`, edit it so it does — otherwise the migration fails on SQLite.

- [ ] **Step 5: Verify migration and schema parity**

Run: `uv run pytest server/tests/integration/test_migrations.py -v`
Expected: PASS — the schema-parity test confirms the migration matches the models exactly.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: 87 passed (all Slice 1 tests still green — this task adds no tests of its own, the parity test is the gate).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add test plan models with frozen case snapshots"
```

---

### Task 2: PlanService — creation, snapshot, retrieval

**Files:**
- Create: `server/src/testforge/services/plan_service.py`
- Test: `server/tests/unit/test_plan_service.py`

**Interfaces:**
- Consumes: `CaseService.list_for_project(project, *, suite_id, tag, status, execution_type, q) -> list[TestCase]`, `CaseService.find_by_key_in_project(case_key, project_id) -> TestCase | None`, `ProjectService.get_by_key(key) -> Project`, `AppError(code, message, status_code, details)`.
- Produces: `PlanService(session)` with `create(*, project, name, description, milestone, environment, case_keys, filter_, actor) -> TestPlan`, `get(plan_id) -> TestPlan`, `list_for_project(project, *, status, milestone, environment) -> list[TestPlan]`, `cases(plan_id) -> list[TestPlanCase]`, `case_count(plan_id) -> int`, `archive(plan_id, actor) -> TestPlan`, `is_case_in_plan(plan_id, test_case_id) -> bool`.

`filter_` is a `dict` with optional keys `suite_id`, `tag`, `status`, `execution_type`, `q` — the same names `CaseService.list_for_project` takes.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_plan_service.py`:

```python
import pytest

from testforge.errors import AppError
from testforge.services.case_service import CaseService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService


@pytest.fixture
def project(db_session):
    project = ProjectService(db_session).create(
        key="CHK", name="Checkout", description=None, actor="local"
    )
    db_session.commit()
    return project


def make_case(session, project, title, **overrides):
    payload = {
        "project": project,
        "suite_id": None,
        "title": title,
        "execution_type": "automated",
        "priority": "p2",
        "preconditions": None,
        "steps": [],
        "expected_result": None,
        "tags": [],
        "actor": "local",
    }
    payload.update(overrides)
    return CaseService(session).create(**payload)


def test_create_snapshots_explicit_case_keys(db_session, project):
    make_case(db_session, project, "A")
    make_case(db_session, project, "B")
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Release 1.0",
        description=None,
        milestone="1.0",
        environment="staging",
        case_keys=["CHK-1"],
        filter_=None,
        actor="alvis",
    )
    db_session.commit()

    assert plan.name == "Release 1.0"
    assert plan.status == "active"
    assert plan.created_by == "alvis"
    assert service.case_count(plan.id) == 1


def test_create_snapshots_from_a_filter(db_session, project):
    make_case(db_session, project, "Smoke A", tags=["smoke"])
    make_case(db_session, project, "Slow B", tags=["slow"])
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Smoke only",
        description=None,
        milestone=None,
        environment=None,
        case_keys=[],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    members = service.cases(plan.id)
    assert len(members) == 1


def test_explicit_keys_and_filter_are_unioned_and_deduplicated(db_session, project):
    make_case(db_session, project, "Smoke A", tags=["smoke"])
    make_case(db_session, project, "Manual B", execution_type="manual")
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Union",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1", "CHK-2"],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    assert service.case_count(plan.id) == 2, "CHK-1 matches both selectors, must appear once"


def test_explicit_keys_keep_their_order_then_filter_matches_follow(db_session, project):
    make_case(db_session, project, "A", tags=["smoke"])
    make_case(db_session, project, "B")
    make_case(db_session, project, "C", tags=["smoke"])
    db_session.commit()
    service = PlanService(db_session)

    plan = service.create(
        project=project,
        name="Ordered",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-2"],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    members = service.cases(plan.id)
    assert [m.position for m in members] == [0, 1, 2]


def test_snapshot_pins_the_case_version_and_survives_later_edits(db_session, project):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Pinned",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()
    pinned = service.cases(plan.id)[0].test_case_version_id
    assert pinned is not None

    CaseService(db_session).update(
        case_key="CHK-1", expected_version_no=1, actor="editor", title="Coupon SAVE10"
    )
    db_session.commit()

    assert service.cases(plan.id)[0].test_case_version_id == pinned
    assert service.case_count(plan.id) == 1, "editing a case must not change plan membership"


def test_adding_a_matching_case_later_does_not_join_an_existing_plan(db_session, project):
    make_case(db_session, project, "A", tags=["smoke"])
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Frozen",
        description=None,
        milestone=None,
        environment=None,
        case_keys=[],
        filter_={"tag": "smoke"},
        actor="local",
    )
    db_session.commit()

    make_case(db_session, project, "B", tags=["smoke"])
    db_session.commit()

    assert service.case_count(plan.id) == 1, "the snapshot is static, not a live filter"


def test_empty_selection_is_rejected(db_session, project):
    service = PlanService(db_session)

    with pytest.raises(AppError) as excinfo:
        service.create(
            project=project,
            name="Empty",
            description=None,
            milestone=None,
            environment=None,
            case_keys=[],
            filter_={"tag": "nonexistent"},
            actor="local",
        )

    assert excinfo.value.code == "empty_plan_selection"
    assert excinfo.value.status_code == 422


def test_unknown_case_key_is_rejected(db_session, project):
    service = PlanService(db_session)

    with pytest.raises(AppError) as excinfo:
        service.create(
            project=project,
            name="Bad key",
            description=None,
            milestone=None,
            environment=None,
            case_keys=["CHK-999"],
            filter_=None,
            actor="local",
        )

    assert excinfo.value.code == "case_key_not_found"


def test_a_case_from_another_project_is_rejected(db_session, project):
    other = ProjectService(db_session).create(
        key="OTH", name="Other", description=None, actor="local"
    )
    db_session.commit()
    make_case(db_session, other, "Foreign")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        PlanService(db_session).create(
            project=project,
            name="Cross-project",
            description=None,
            milestone=None,
            environment=None,
            case_keys=["OTH-1"],
            filter_=None,
            actor="local",
        )

    assert excinfo.value.code == "case_key_not_found"


def test_get_unknown_plan_raises(db_session):
    with pytest.raises(AppError) as excinfo:
        PlanService(db_session).get("missing")
    assert excinfo.value.code == "plan_not_found"
    assert excinfo.value.status_code == 404


def test_archive_marks_the_plan(db_session, project):
    make_case(db_session, project, "A")
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Doomed",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()

    service.archive(plan.id, actor="alvis")
    db_session.commit()

    assert service.get(plan.id).status == "archived"
    assert service.get(plan.id).archived_at is not None


def test_list_filters_by_milestone(db_session, project):
    make_case(db_session, project, "A")
    db_session.commit()
    service = PlanService(db_session)
    for milestone in ("1.0", "2.0"):
        service.create(
            project=project,
            name=f"Release {milestone}",
            description=None,
            milestone=milestone,
            environment=None,
            case_keys=["CHK-1"],
            filter_=None,
            actor="local",
        )
    db_session.commit()

    found = service.list_for_project(project, milestone="2.0")

    assert [p.milestone for p in found] == ["2.0"]


def test_is_case_in_plan(db_session, project):
    case_a = make_case(db_session, project, "A")
    case_b = make_case(db_session, project, "B")
    db_session.commit()
    service = PlanService(db_session)
    plan = service.create(
        project=project,
        name="Only A",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()

    assert service.is_case_in_plan(plan.id, case_a.id) is True
    assert service.is_case_in_plan(plan.id, case_b.id) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_plan_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.plan_service'`

- [ ] **Step 3: Write the service**

Create `server/src/testforge/services/plan_service.py`:

```python
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.case import TestCase
from testforge.models.plan import TestPlan, TestPlanCase
from testforge.models.project import Project
from testforge.services.case_service import CaseService

FILTER_FIELDS = ("suite_id", "tag", "status", "execution_type", "q")


class PlanService:
    """Owns test plans and their frozen case snapshots.

    The snapshot is written exactly once, at creation. Nothing else in the
    codebase writes ``test_plan_cases`` — that immutability is what makes two
    runs of the same plan comparable.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.cases_service = CaseService(session)

    def create(
        self,
        *,
        project: Project,
        name: str,
        description: str | None,
        milestone: str | None,
        environment: str | None,
        case_keys: list[str],
        filter_: dict[str, Any] | None,
        actor: str,
    ) -> TestPlan:
        selected = self._select_cases(project, case_keys, filter_)
        if not selected:
            raise AppError(
                "empty_plan_selection",
                "the case selection matched no cases",
                422,
                {"case_keys": case_keys, "filter": filter_ or {}},
            )

        plan = TestPlan(
            project_id=project.id,
            name=name,
            description=description,
            milestone=milestone,
            environment=environment,
            created_by=actor,
            updated_by=actor,
        )
        self.session.add(plan)
        self.session.flush()

        for position, case in enumerate(selected):
            self.session.add(
                TestPlanCase(
                    plan_id=plan.id,
                    test_case_id=case.id,
                    test_case_version_id=self._current_version_id(case),
                    position=position,
                )
            )
        self.session.flush()
        return plan

    def get(self, plan_id: str) -> TestPlan:
        plan = self.session.get(TestPlan, plan_id)
        if plan is None:
            raise AppError("plan_not_found", f"no plan {plan_id}", 404, {"plan_id": plan_id})
        return plan

    def list_for_project(
        self,
        project: Project,
        *,
        status: str | None = None,
        milestone: str | None = None,
        environment: str | None = None,
    ) -> list[TestPlan]:
        stmt = select(TestPlan).where(TestPlan.project_id == project.id)
        if status is not None:
            stmt = stmt.where(TestPlan.status == status)
        if milestone is not None:
            stmt = stmt.where(TestPlan.milestone == milestone)
        if environment is not None:
            stmt = stmt.where(TestPlan.environment == environment)
        return list(self.session.scalars(stmt.order_by(TestPlan.created_at)))

    def cases(self, plan_id: str) -> list[TestPlanCase]:
        return list(
            self.session.scalars(
                select(TestPlanCase)
                .where(TestPlanCase.plan_id == plan_id)
                .order_by(TestPlanCase.position)
            )
        )

    def case_count(self, plan_id: str) -> int:
        return self.session.scalar(
            select(func.count())
            .select_from(TestPlanCase)
            .where(TestPlanCase.plan_id == plan_id)
        )

    def is_case_in_plan(self, plan_id: str, test_case_id: str) -> bool:
        found = self.session.scalar(
            select(TestPlanCase.test_case_id).where(
                TestPlanCase.plan_id == plan_id,
                TestPlanCase.test_case_id == test_case_id,
            )
        )
        return found is not None

    def archive(self, plan_id: str, actor: str) -> TestPlan:
        plan = self.get(plan_id)
        plan.status = "archived"
        plan.archived_at = utcnow()
        plan.updated_by = actor
        self.session.flush()
        return plan

    def _select_cases(
        self, project: Project, case_keys: list[str], filter_: dict[str, Any] | None
    ) -> list[TestCase]:
        """Explicit keys first, in the order given; then filter matches by case key."""
        selected: list[TestCase] = []
        seen: set[str] = set()

        for key in case_keys:
            case = self.cases_service.find_by_key_in_project(key, project.id)
            if case is None:
                raise AppError(
                    "case_key_not_found",
                    f"no case {key} in project {project.key}",
                    404,
                    {"case_key": key, "project_key": project.key},
                )
            if case.id not in seen:
                seen.add(case.id)
                selected.append(case)

        if filter_:
            kwargs = {k: v for k, v in filter_.items() if k in FILTER_FIELDS and v is not None}
            for case in self.cases_service.list_for_project(project, **kwargs):
                if case.id not in seen:
                    seen.add(case.id)
                    selected.append(case)

        return selected

    def _current_version_id(self, case: TestCase) -> str | None:
        from testforge.models.case import TestCaseVersion

        return self.session.scalar(
            select(TestCaseVersion.id).where(
                TestCaseVersion.test_case_id == case.id,
                TestCaseVersion.version_no == case.current_version_no,
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_plan_service.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: 100 passed (87 Slice 1 + 13 new).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add PlanService with frozen, version-pinned case snapshots"
```

---

### Task 3: Run state machine — plan execution and cancellation

**Files:**
- Modify: `server/src/testforge/services/run_service.py`, `server/src/testforge/services/ingestion_service.py`
- Test: `server/tests/unit/test_run_service.py` (existing file — append)

**Interfaces:**
- Consumes: `PlanService.get(plan_id) -> TestPlan`, existing `RunService.open/get/complete`.
- Produces: `RunService.open_for_plan(*, plan, name, external_id, actor) -> Run`, `RunService.cancel(run_id) -> Run`, `RunService.assert_writable(run) -> None`.

`assert_writable` centralises the terminal-state check so ingestion and manual execution report the same, correct code for each terminal state.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/unit/test_run_service.py`:

```python
def test_cancel_sets_status_and_timestamp(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="c-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    canceled = service.cancel(run.id)
    db_session.commit()

    assert canceled.status == "canceled"
    assert canceled.completed_at is not None


def test_assert_writable_allows_a_running_run(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="w-1", name="a", source="ci", ci_metadata={}, actor="local"
    )
    db_session.commit()

    service.assert_writable(run)  # must not raise


def test_assert_writable_rejects_a_completed_run(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="w-2", name="a", source="ci", ci_metadata={}, actor="local"
    )
    service.complete(run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.assert_writable(run)

    assert excinfo.value.code == "run_already_completed"
    assert excinfo.value.status_code == 409


def test_assert_writable_rejects_a_canceled_run_with_its_own_code(db_session, project):
    service = RunService(db_session)
    run, _ = service.open(
        project=project, external_id="w-3", name="a", source="ci", ci_metadata={}, actor="local"
    )
    service.cancel(run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        service.assert_writable(run)

    assert excinfo.value.code == "run_canceled", (
        "a canceled run must be distinguishable from a completed one"
    )
    assert excinfo.value.status_code == 409


def test_open_for_plan_links_the_run_to_the_plan(db_session, project):
    from testforge.services.case_service import CaseService
    from testforge.services.plan_service import PlanService

    CaseService(db_session).create(
        project=project,
        suite_id=None,
        title="A",
        execution_type="manual",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )
    db_session.commit()
    plan = PlanService(db_session).create(
        project=project,
        name="P",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()

    run = RunService(db_session).open_for_plan(
        plan=plan, name="exec 1", external_id=None, actor="alvis"
    )
    db_session.commit()

    assert run.plan_id == plan.id
    assert run.status == "running"
    assert run.source == "manual"
    assert run.project_id == plan.project_id
    assert run.external_id, "a plan run still needs a unique external_id"


def test_open_for_plan_rejects_an_archived_plan(db_session, project):
    from testforge.services.case_service import CaseService
    from testforge.services.plan_service import PlanService

    CaseService(db_session).create(
        project=project,
        suite_id=None,
        title="A",
        execution_type="manual",
        priority="p2",
        preconditions=None,
        steps=[],
        expected_result=None,
        tags=[],
        actor="local",
    )
    db_session.commit()
    plans = PlanService(db_session)
    plan = plans.create(
        project=project,
        name="P",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()
    plans.archive(plan.id, actor="local")
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        RunService(db_session).open_for_plan(
            plan=plan, name=None, external_id=None, actor="local"
        )

    assert excinfo.value.code == "plan_archived"
    assert excinfo.value.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_run_service.py -v`
Expected: FAIL — `AttributeError: 'RunService' object has no attribute 'cancel'`

- [ ] **Step 3: Extend RunService**

In `server/src/testforge/services/run_service.py`, add these imports at the top:

```python
from testforge.ids import new_id
from testforge.models.plan import TestPlan
```

and add these methods to the `RunService` class:

```python
    def open_for_plan(
        self, *, plan: TestPlan, name: str | None, external_id: str | None, actor: str
    ) -> Run:
        if plan.status == "archived":
            raise AppError(
                "plan_archived",
                f"plan {plan.id} is archived and cannot be executed",
                409,
                {"plan_id": plan.id},
            )
        run = Run(
            project_id=plan.project_id,
            external_id=external_id or f"plan-{plan.id}-{new_id()}",
            name=name or plan.name,
            source="manual",
            plan_id=plan.id,
            ci_metadata_json={},
            created_by=actor,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def cancel(self, run_id: str) -> Run:
        run = self.get(run_id)
        run.status = "canceled"
        run.completed_at = utcnow()
        self.session.flush()
        return run

    def assert_writable(self, run: Run) -> None:
        """Reject writes to a terminal run, naming which terminal state it is."""
        if run.status == "completed":
            raise AppError(
                "run_already_completed",
                f"run {run.id} is completed and no longer accepts results",
                409,
                {"run_id": run.id, "status": run.status},
            )
        if run.status == "canceled":
            raise AppError(
                "run_canceled",
                f"run {run.id} was canceled and no longer accepts results",
                409,
                {"run_id": run.id, "status": run.status},
            )
```

- [ ] **Step 4: Route ingestion through the shared check**

In `server/src/testforge/services/ingestion_service.py`, `ingest()` currently starts with its own inline status check. Replace that block:

```python
        if run.status != "running":
            raise AppError(
                "run_already_completed",
                f"run {run.id} is {run.status} and no longer accepts results",
                409,
                {"run_id": run.id, "status": run.status},
            )
```

with a delegation to the shared check:

```python
        RunService(self.session).assert_writable(run)
```

and add the import at the top of the file:

```python
from testforge.services.run_service import RunService
```

This keeps one definition of "which terminal state is this and what code does it report", so ingestion automatically reports `run_canceled` for canceled runs instead of misreporting them as completed.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_run_service.py server/tests/unit/test_ingestion_service.py -v`
Expected: PASS — including the existing `test_ingesting_into_a_completed_run_is_rejected`, which must still report `run_already_completed`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: 106 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add plan execution, run cancellation, and a shared terminal-state check"
```

---

### Task 4: ManualExecutionService

**Files:**
- Create: `server/src/testforge/services/manual_service.py`
- Test: `server/tests/unit/test_manual_service.py`

**Interfaces:**
- Consumes: `RunService.assert_writable(run)`, `PlanService.is_case_in_plan(plan_id, test_case_id) -> bool`, `CaseService.find_by_key_in_project(case_key, project_id) -> TestCase | None`, `Result` model.
- Produces: `Result.framework` column; `ManualExecutionService(session)` with `record(*, run, case_key, outcome, notes, duration_ms, actor) -> Result`.

**Prerequisite this task must handle first:** the `Result` model has **no `framework` column** today. Slice 1's `ResultIn` schema carries `framework`, but `IngestionService` uses it only to key the automation link and then discards it — it is never persisted on the result row. The spec's manual-result mapping (§4) requires `framework="manual"` to distinguish manual rows from automated ones without adding a bespoke column, so Step 3 adds the column and backfills ingestion to persist it too. Do Step 3 before writing the service.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/unit/test_manual_service.py`:

```python
import pytest
from sqlalchemy import select

from testforge.errors import AppError
from testforge.models.run import Result
from testforge.services.case_service import CaseService
from testforge.services.manual_service import ManualExecutionService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService


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
        execution_type="manual",
        priority="p2",
        preconditions=None,
        steps=[{"action": "Open cart", "expected": "Cart renders"}],
        expected_result=None,
        tags=[],
        actor="local",
    )


@pytest.fixture
def adhoc_run(db_session, project):
    run, _ = RunService(db_session).open(
        project=project, external_id="m-1", name="n", source="manual", ci_metadata={}, actor="local"
    )
    db_session.commit()
    return run


def results_for(session, run):
    return list(session.scalars(select(Result).where(Result.run_id == run.id)))


def test_record_writes_a_result_row(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()

    result = ManualExecutionService(db_session).record(
        run=adhoc_run,
        case_key="CHK-1",
        outcome="passed",
        notes="looked fine",
        duration_ms=45000,
        actor="alvis",
    )
    db_session.commit()

    assert result.outcome == "passed"
    assert result.framework == "manual"
    assert result.test_identifier == "CHK-1"
    assert result.failure_message == "looked fine"
    assert result.duration_ms == 45000
    assert result.test_case_id is not None
    assert result.automation_link_id is None


def test_record_pins_the_case_version_at_execution_time(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    service = ManualExecutionService(db_session)

    result = service.record(
        run=adhoc_run, case_key="CHK-1", outcome="passed", notes=None, duration_ms=None, actor="a"
    )
    db_session.commit()
    pinned = result.test_case_version_id
    assert pinned is not None

    CaseService(db_session).update(
        case_key="CHK-1", expected_version_no=1, actor="editor", title="Coupon SAVE10"
    )
    db_session.commit()

    assert results_for(db_session, adhoc_run)[0].test_case_version_id == pinned


def test_notes_are_kept_on_a_passing_outcome(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()

    result = ManualExecutionService(db_session).record(
        run=adhoc_run,
        case_key="CHK-1",
        outcome="passed",
        notes="minor cosmetic wobble, not worth failing",
        duration_ms=None,
        actor="a",
    )
    db_session.commit()

    assert result.failure_message == "minor cosmetic wobble, not worth failing"


def test_re_executing_the_same_case_replaces_the_prior_result(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    service = ManualExecutionService(db_session)

    service.record(
        run=adhoc_run, case_key="CHK-1", outcome="failed", notes="mis-click", duration_ms=None,
        actor="a",
    )
    db_session.commit()
    service.record(
        run=adhoc_run, case_key="CHK-1", outcome="passed", notes="actually fine", duration_ms=None,
        actor="a",
    )
    db_session.commit()

    stored = results_for(db_session, adhoc_run)
    assert len(stored) == 1, "a correction must replace, not append"
    assert stored[0].outcome == "passed"
    assert stored[0].failure_message == "actually fine"


def test_unknown_case_key_is_rejected(db_session, adhoc_run):
    with pytest.raises(AppError) as excinfo:
        ManualExecutionService(db_session).record(
            run=adhoc_run, case_key="CHK-999", outcome="passed", notes=None, duration_ms=None,
            actor="a",
        )

    assert excinfo.value.code == "case_key_not_found", (
        "unlike ingestion, manual execution does not tolerate unresolved keys"
    )


def test_a_case_outside_the_runs_plan_is_rejected(db_session, project):
    make_case(db_session, project, "In plan")
    make_case(db_session, project, "Out of plan")
    db_session.commit()
    plan = PlanService(db_session).create(
        project=project,
        name="P",
        description=None,
        milestone=None,
        environment=None,
        case_keys=["CHK-1"],
        filter_=None,
        actor="local",
    )
    db_session.commit()
    run = RunService(db_session).open_for_plan(
        plan=plan, name=None, external_id=None, actor="local"
    )
    db_session.commit()
    service = ManualExecutionService(db_session)

    service.record(
        run=run, case_key="CHK-1", outcome="passed", notes=None, duration_ms=None, actor="a"
    )

    with pytest.raises(AppError) as excinfo:
        service.record(
            run=run, case_key="CHK-2", outcome="passed", notes=None, duration_ms=None, actor="a"
        )

    assert excinfo.value.code == "case_not_in_plan"
    assert excinfo.value.status_code == 404


def test_a_terminal_run_rejects_manual_results(db_session, project, adhoc_run):
    make_case(db_session, project, "Coupon")
    db_session.commit()
    RunService(db_session).cancel(adhoc_run.id)
    db_session.commit()

    with pytest.raises(AppError) as excinfo:
        ManualExecutionService(db_session).record(
            run=adhoc_run, case_key="CHK-1", outcome="passed", notes=None, duration_ms=None,
            actor="a",
        )

    assert excinfo.value.code == "run_canceled"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/unit/test_manual_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'testforge.services.manual_service'`

- [ ] **Step 3: Add the `framework` column to `Result` (do this before Step 4)**

In `server/src/testforge/models/run.py`, add to the `Result` class, immediately after `test_identifier`:

```python
    framework: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
```

In `server/src/testforge/services/ingestion_service.py`, in the `Result(...)` construction inside `ingest()`, add the field so automated results record their producer too:

```python
                    framework=incoming.framework,
```

Generate the migration:

```bash
uv run alembic -c server/alembic.ini revision --autogenerate -m "add framework to results"
```

Then confirm the schema-parity test still passes:

Run: `uv run pytest server/tests/integration/test_migrations.py -v`
Expected: PASS

- [ ] **Step 4: Write the service**

Create `server/src/testforge/services/manual_service.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.case import TestCase, TestCaseVersion
from testforge.models.run import Result, Run
from testforge.services.case_service import CaseService
from testforge.services.plan_service import PlanService
from testforge.services.run_service import RunService


class ManualExecutionService:
    """Records one human-executed result for one case in one run.

    Deliberately separate from ``IngestionService``: that path takes a batch from an
    external producer and tolerates unresolved case keys by storing them. Here a human
    is looking at one already-resolved case, so an unknown key is a client error, and a
    re-execution is a correction that replaces rather than appends.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        run: Run,
        case_key: str,
        outcome: str,
        notes: str | None,
        duration_ms: int | None,
        actor: str,
    ) -> Result:
        RunService(self.session).assert_writable(run)

        case = CaseService(self.session).find_by_key_in_project(case_key, run.project_id)
        if case is None:
            raise AppError(
                "case_key_not_found",
                f"no case {case_key} in this run's project",
                404,
                {"case_key": case_key, "run_id": run.id},
            )

        if run.plan_id is not None and not PlanService(self.session).is_case_in_plan(
            run.plan_id, case.id
        ):
            raise AppError(
                "case_not_in_plan",
                f"case {case_key} is not part of this run's plan",
                404,
                {"case_key": case_key, "plan_id": run.plan_id},
            )

        existing = self.session.scalar(
            select(Result).where(
                Result.run_id == run.id,
                Result.test_case_id == case.id,
                Result.framework == "manual",
            )
        )
        if existing is not None:
            self.session.delete(existing)
            self.session.flush()

        result = Result(
            run_id=run.id,
            test_case_id=case.id,
            test_case_version_id=self._current_version_id(case),
            automation_link_id=None,
            test_identifier=case.case_key,
            unresolved_case_key=None,
            outcome=outcome,
            duration_ms=duration_ms,
            failure_type=None,
            failure_message=notes,
            stack_trace=None,
            attachments_json=[],
            executed_at=utcnow(),
        )
        self.session.add(result)
        self.session.flush()
        return result

    def _current_version_id(self, case: TestCase) -> str | None:
        return self.session.scalar(
            select(TestCaseVersion.id).where(
                TestCaseVersion.test_case_id == case.id,
                TestCaseVersion.version_no == case.current_version_no,
            )
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/unit/test_manual_service.py -v`
Expected: PASS — 8 passed

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: 114 passed, including the schema-parity test with the new column.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add manual per-case execution as a second write path into results"
```

---

### Task 5: Plan API and schemas

**Files:**
- Create: `server/src/testforge/schemas/plans.py`, `server/src/testforge/api/routes/plans.py`
- Modify: `server/src/testforge/main.py`
- Test: `server/tests/integration/test_plans_api.py`

**Interfaces:**
- Consumes: `PlanService` (Task 2), `RunService.open_for_plan` (Task 3), `ProjectService.get_by_key`, `get_session`, `get_actor`.
- Produces: routes `POST/GET /api/projects/{project_key}/plans`, `GET /api/plans/{plan_id}`, `GET /api/plans/{plan_id}/cases`, `POST /api/plans/{plan_id}/archive`, `POST /api/plans/{plan_id}/runs`; schemas `CaseFilter`, `PlanCreate`, `PlanOut`, `PlanCaseOut`, `PlanRunCreate`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/integration/test_plans_api.py`:

```python
import pytest


@pytest.fixture
def project_key(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    client.post(
        "/api/projects/CHK/cases",
        json={"title": "Coupon applies", "execution_type": "manual", "tags": ["smoke"]},
    )
    client.post("/api/projects/CHK/cases", json={"title": "Refund flow", "tags": ["slow"]})
    return "CHK"


def test_create_plan_snapshots_cases(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/plans",
        json={
            "name": "Release 2.4",
            "milestone": "2.4",
            "environment": "staging",
            "case_keys": ["CHK-1"],
        },
        headers={"X-Actor": "alvis"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Release 2.4"
    assert body["status"] == "active"
    assert body["case_count"] == 1
    assert body["created_by"] == "alvis"


def test_create_plan_from_a_filter(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Smoke", "filter": {"tag": "smoke"}},
    )

    assert response.status_code == 201
    assert response.json()["case_count"] == 1


def test_empty_selection_is_rejected(client, project_key):
    response = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Nothing", "filter": {"tag": "nonexistent"}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "empty_plan_selection"


def test_list_and_get_a_plan(client, project_key):
    created = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Release 2.4", "milestone": "2.4", "case_keys": ["CHK-1"]},
    ).json()

    listed = client.get(f"/api/projects/{project_key}/plans", params={"milestone": "2.4"})
    fetched = client.get(f"/api/plans/{created['id']}")

    assert [p["name"] for p in listed.json()] == ["Release 2.4"]
    assert fetched.status_code == 200
    assert fetched.json()["case_count"] == 1


def test_plan_cases_are_returned_in_snapshot_order(client, project_key):
    plan = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Ordered", "case_keys": ["CHK-2", "CHK-1"]},
    ).json()

    cases = client.get(f"/api/plans/{plan['id']}/cases").json()

    assert [c["case_key"] for c in cases] == ["CHK-2", "CHK-1"]
    assert cases[0]["title"] == "Refund flow"
    assert cases[0]["test_case_version_id"] is not None


def test_unknown_plan_returns_the_error_contract(client):
    response = client.get("/api/plans/missing")

    assert response.status_code == 404
    assert response.json()["code"] == "plan_not_found"


def test_execute_a_plan_opens_a_linked_run(client, project_key):
    plan = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Release 2.4", "case_keys": ["CHK-1"]},
    ).json()

    response = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "nightly"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "running"
    assert body["name"] == "nightly"


def test_archived_plan_cannot_be_executed(client, project_key):
    plan = client.post(
        f"/api/projects/{project_key}/plans",
        json={"name": "Doomed", "case_keys": ["CHK-1"]},
    ).json()
    client.post(f"/api/plans/{plan['id']}/archive")

    response = client.post(f"/api/plans/{plan['id']}/runs", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "plan_archived"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_plans_api.py -v`
Expected: FAIL — 404s, the routes do not exist.

- [ ] **Step 3: Write the schemas**

Create `server/src/testforge/schemas/plans.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaseFilter(BaseModel):
    """Mirrors the case-list query parameters, so one mental model covers both."""

    suite_id: str | None = None
    tag: str | None = None
    status: str | None = None
    execution_type: Literal["manual", "automated"] | None = None
    q: str | None = None


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    milestone: str | None = Field(default=None, max_length=100)
    environment: str | None = Field(default=None, max_length=100)
    case_keys: list[str] = Field(default_factory=list)
    filter: CaseFilter | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    milestone: str | None
    environment: str | None
    status: str
    case_count: int
    created_by: str
    created_at: datetime
    archived_at: datetime | None


class PlanCaseOut(BaseModel):
    case_key: str
    title: str
    execution_type: str
    test_case_version_id: str | None
    position: int


class PlanRunCreate(BaseModel):
    name: str | None = None
    external_id: str | None = None
```

- [ ] **Step 4: Write the routes**

Create `server/src/testforge/api/routes/plans.py`:

```python
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.models.case import TestCase
from testforge.schemas.plans import PlanCaseOut, PlanCreate, PlanOut, PlanRunCreate
from testforge.schemas.runs import RunOut
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

router = APIRouter(tags=["plans"])


def _to_out(plan, service: PlanService) -> PlanOut:
    return PlanOut(
        **{
            field: getattr(plan, field)
            for field in (
                "id",
                "project_id",
                "name",
                "description",
                "milestone",
                "environment",
                "status",
                "created_by",
                "created_at",
                "archived_at",
            )
        },
        case_count=service.case_count(plan.id),
    )


@router.post("/api/projects/{project_key}/plans", response_model=PlanOut, status_code=201)
def create_plan(
    project_key: str,
    payload: PlanCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> PlanOut:
    project = ProjectService(session).get_by_key(project_key)
    service = PlanService(session)
    plan = service.create(
        project=project,
        name=payload.name,
        description=payload.description,
        milestone=payload.milestone,
        environment=payload.environment,
        case_keys=payload.case_keys,
        filter_=payload.filter.model_dump(exclude_none=True) if payload.filter else None,
        actor=actor,
    )
    return _to_out(plan, service)


@router.get("/api/projects/{project_key}/plans", response_model=list[PlanOut])
def list_plans(
    project_key: str,
    status: str | None = None,
    milestone: str | None = None,
    environment: str | None = None,
    session: Session = Depends(get_session),
) -> list[PlanOut]:
    project = ProjectService(session).get_by_key(project_key)
    service = PlanService(session)
    plans = service.list_for_project(
        project, status=status, milestone=milestone, environment=environment
    )
    return [_to_out(plan, service) for plan in plans]


@router.get("/api/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, session: Session = Depends(get_session)) -> PlanOut:
    service = PlanService(session)
    return _to_out(service.get(plan_id), service)


@router.get("/api/plans/{plan_id}/cases", response_model=list[PlanCaseOut])
def list_plan_cases(plan_id: str, session: Session = Depends(get_session)) -> list[PlanCaseOut]:
    service = PlanService(session)
    service.get(plan_id)
    members = service.cases(plan_id)
    out: list[PlanCaseOut] = []
    for member in members:
        case = session.get(TestCase, member.test_case_id)
        out.append(
            PlanCaseOut(
                case_key=case.case_key,
                title=case.title,
                execution_type=case.execution_type,
                test_case_version_id=member.test_case_version_id,
                position=member.position,
            )
        )
    return out


@router.post("/api/plans/{plan_id}/archive", response_model=PlanOut)
def archive_plan(
    plan_id: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> PlanOut:
    service = PlanService(session)
    return _to_out(service.archive(plan_id, actor), service)


@router.post("/api/plans/{plan_id}/runs", response_model=RunOut, status_code=201)
def execute_plan(
    plan_id: str,
    payload: PlanRunCreate,
    response: Response,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> RunOut:
    plan = PlanService(session).get(plan_id)
    run = RunService(session).open_for_plan(
        plan=plan, name=payload.name, external_id=payload.external_id, actor=actor
    )
    return RunOut.model_validate(run)
```

- [ ] **Step 5: Register the router**

In `server/src/testforge/main.py`, add `plans` to the route imports and add one include line alongside the existing ones:

```python
from testforge.api.routes import cases, health, plans, projects, runs, suites
...
    app.include_router(plans.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_plans_api.py -v`
Expected: PASS — 8 passed

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -v`
Expected: 122 passed.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add the plan API and plan execution endpoint"
```

---

### Task 6: Run API — cancel, manual execute, plan progress

**Files:**
- Modify: `server/src/testforge/api/routes/runs.py`, `server/src/testforge/schemas/runs.py`
- Test: `server/tests/integration/test_runs_api.py` (existing file — append)

**Interfaces:**
- Consumes: `RunService.cancel`, `ManualExecutionService.record`, `PlanService.cases/get`.
- Produces: routes `POST /api/runs/{run_id}/cancel`, `POST /api/runs/{run_id}/cases/{case_key}/execute`; schemas `PlanProgress`, `ManualExecute`; `RunSummaryOut.plan_progress`.

- [ ] **Step 1: Write the failing tests**

Append to `server/tests/integration/test_runs_api.py`:

```python
def make_plan_run(client):
    client.post("/api/projects", json={"key": "PLN", "name": "Planned"})
    client.post(
        "/api/projects/PLN/cases", json={"title": "Manual A", "execution_type": "manual"}
    )
    client.post(
        "/api/projects/PLN/cases", json={"title": "Manual B", "execution_type": "manual"}
    )
    plan = client.post(
        "/api/projects/PLN/plans",
        json={"name": "P", "case_keys": ["PLN-1", "PLN-2"]},
    ).json()
    run = client.post(f"/api/plans/{plan['id']}/runs", json={}).json()
    return plan, run


def test_cancel_a_run(client, setup):
    run_id = open_run(client, "cancel-1").json()["id"]

    response = client.post(f"/api/runs/{run_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_ingesting_into_a_canceled_run_reports_run_canceled(client, setup):
    run_id = open_run(client, "cancel-2").json()["id"]
    client.post(f"/api/runs/{run_id}/cancel")

    response = client.post(
        f"/api/runs/{run_id}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-1",
                    "test_identifier": "t.py::a",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                }
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "run_canceled", (
        "a canceled run must not be reported as completed"
    )


def test_manual_execution_records_a_result(client):
    _, run = make_plan_run(client)

    response = client.post(
        f"/api/runs/{run['id']}/cases/PLN-1/execute",
        json={"outcome": "failed", "notes": "coupon field rejects valid codes"},
        headers={"X-Actor": "alvis"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "failed"
    assert body["test_identifier"] == "PLN-1"


def test_manual_execution_rejects_a_case_outside_the_plan(client):
    _, run = make_plan_run(client)
    client.post("/api/projects/PLN/cases", json={"title": "Outsider"})

    response = client.post(
        f"/api/runs/{run['id']}/cases/PLN-3/execute", json={"outcome": "passed"}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "case_not_in_plan"


def test_plan_progress_reports_coverage(client):
    _, run = make_plan_run(client)
    client.post(f"/api/runs/{run['id']}/cases/PLN-1/execute", json={"outcome": "passed"})

    summary = client.get(f"/api/runs/{run['id']}").json()

    progress = summary["plan_progress"]
    assert progress["total_cases"] == 2
    assert progress["cases_with_result"] == 1
    assert progress["by_outcome"] == {"passed": 1}
    assert progress["cases_without_result"] == ["PLN-2"]


def test_plan_progress_is_absent_for_adhoc_runs(client, setup):
    run_id = open_run(client, "adhoc-progress").json()["id"]

    summary = client.get(f"/api/runs/{run_id}").json()

    assert summary["plan_progress"] is None
```

These tests use the file's existing `setup` fixture and its `open_run(client, external_id="gha-1")` helper (defined at the top of the file), passing a unique `external_id` per test so run creation's idempotency does not silently return a previous test's run.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest server/tests/integration/test_runs_api.py -v`
Expected: FAIL — 404s on the new routes.

- [ ] **Step 3: Add the schemas**

Append to `server/src/testforge/schemas/runs.py`:

```python
class PlanProgress(BaseModel):
    total_cases: int
    cases_with_result: int
    by_outcome: dict[str, int]
    cases_without_result: list[str]


class ManualExecute(BaseModel):
    outcome: Literal["passed", "failed", "skipped", "error", "blocked"]
    notes: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
```

and add `plan_progress` to `RunSummaryOut`:

```python
class RunSummaryOut(RunOut):
    total_results: int
    unresolved_count: int
    by_outcome: dict[str, int]
    plan_progress: PlanProgress | None = None
```

`Literal` and `Field` are already imported in this file; confirm and add them to the import line if not.

- [ ] **Step 4: Add the routes and progress computation**

In `server/src/testforge/api/routes/runs.py`, add these imports:

```python
from testforge.models.case import TestCase
from testforge.schemas.runs import ManualExecute, PlanProgress
from testforge.services.manual_service import ManualExecutionService
from testforge.services.plan_service import PlanService
```

Add the two new routes:

```python
@router.post("/api/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(
    run_id: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> RunOut:
    return RunOut.model_validate(RunService(session).cancel(run_id))


@router.post("/api/runs/{run_id}/cases/{case_key}/execute", response_model=ResultOut)
def execute_case_manually(
    run_id: str,
    case_key: str,
    payload: ManualExecute,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> ResultOut:
    run = RunService(session).get(run_id)
    result = ManualExecutionService(session).record(
        run=run,
        case_key=case_key,
        outcome=payload.outcome,
        notes=payload.notes,
        duration_ms=payload.duration_ms,
        actor=actor,
    )
    return ResultOut.model_validate(result)
```

Replace the body of the existing `get_run` route so it computes plan progress when the run belongs to a plan:

```python
@router.get("/api/runs/{run_id}", response_model=RunSummaryOut)
def get_run(run_id: str, session: Session = Depends(get_session)) -> RunSummaryOut:
    run = RunService(session).get(run_id)
    results = IngestionService(session).results_for_run(run)
    outcomes = Counter(result.outcome for result in results)
    return RunSummaryOut(
        **RunOut.model_validate(run).model_dump(),
        total_results=len(results),
        unresolved_count=sum(1 for r in results if r.test_case_id is None),
        by_outcome=dict(outcomes),
        plan_progress=_plan_progress(session, run, results),
    )


def _plan_progress(session: Session, run, results) -> PlanProgress | None:
    """Coverage of a plan's frozen case list by this run's results. Computed, never stored."""
    if run.plan_id is None:
        return None
    members = PlanService(session).cases(run.plan_id)
    results_by_case = {r.test_case_id: r for r in results if r.test_case_id is not None}

    covered = [m for m in members if m.test_case_id in results_by_case]
    missing_ids = [m.test_case_id for m in members if m.test_case_id not in results_by_case]
    missing_keys = [
        session.get(TestCase, case_id).case_key for case_id in missing_ids
    ]
    outcomes = Counter(results_by_case[m.test_case_id].outcome for m in covered)

    return PlanProgress(
        total_cases=len(members),
        cases_with_result=len(covered),
        by_outcome=dict(outcomes),
        cases_without_result=missing_keys,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_runs_api.py -v`
Expected: PASS — all existing run tests plus the 6 new ones.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: 128 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add run cancellation, manual execution route, and computed plan progress"
```

---

### Task 7: Acceptance test and CLI plan commands

**Files:**
- Create: `server/tests/acceptance/test_plan_execution.py`
- Modify: `server/src/testforge/cli/__init__.py`
- Test: `server/tests/integration/test_cli.py` (existing file — append)

**Interfaces:**
- Consumes: the full HTTP API from Tasks 5-6; `ApiClient` from `testforge.cli.client`.
- Produces: CLI commands `tf plan create`, `tf plan list`, `tf plan show`.

- [ ] **Step 1: Write the acceptance test**

Create `server/tests/acceptance/test_plan_execution.py`:

```python
"""The Slice 2 success criterion, end to end.

A plan selects a mix of manual and automated cases; executing it opens a run; manual
results are recorded directly while automated results arrive through the UNCHANGED
Slice 1 ingestion path; plan progress then reports full coverage from both sources.
"""

EXECUTED_AT = "2026-08-03T10:00:00Z"


def test_a_plan_run_is_covered_by_both_manual_and_ingested_results(client):
    client.post("/api/projects", json={"key": "CHK", "name": "Checkout"})
    for title, execution_type in [
        ("Manual refund check", "manual"),
        ("Manual a11y sweep", "manual"),
        ("Coupon applies", "automated"),
        ("Expired coupon rejected", "automated"),
        ("Payment retry prompt", "automated"),
    ]:
        client.post(
            "/api/projects/CHK/cases",
            json={"title": title, "execution_type": execution_type, "tags": ["release"]},
        )

    plan = client.post(
        "/api/projects/CHK/plans",
        json={"name": "Release 2.4", "milestone": "2.4", "filter": {"tag": "release"}},
    ).json()
    assert plan["case_count"] == 5

    run = client.post(f"/api/plans/{plan['id']}/runs", json={"name": "release run"}).json()
    assert run["status"] == "running"

    for case_key, outcome in [("CHK-1", "passed"), ("CHK-2", "failed")]:
        recorded = client.post(
            f"/api/runs/{run['id']}/cases/{case_key}/execute",
            json={"outcome": outcome, "notes": f"manual check of {case_key}"},
            headers={"X-Actor": "alvis"},
        )
        assert recorded.status_code == 200

    ingested = client.post(
        f"/api/runs/{run['id']}/results",
        json={
            "results": [
                {
                    "case_key": "CHK-3",
                    "test_identifier": "tests/test_checkout.py::test_coupon",
                    "framework": "pytest",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                },
                {
                    "case_key": "CHK-4",
                    "test_identifier": "tests/test_checkout.py::test_expired",
                    "framework": "pytest",
                    "outcome": "passed",
                    "executed_at": EXECUTED_AT,
                },
                {
                    "case_key": "CHK-5",
                    "test_identifier": "tests/test_checkout.py::test_retry",
                    "framework": "pytest",
                    "outcome": "failed",
                    "executed_at": EXECUTED_AT,
                },
            ]
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["resolved"] == 3

    summary = client.get(f"/api/runs/{run['id']}").json()
    progress = summary["plan_progress"]

    assert progress["total_cases"] == 5
    assert progress["cases_with_result"] == 5
    assert progress["cases_without_result"] == []
    assert progress["by_outcome"] == {"passed": 3, "failed": 2}

    client.post(f"/api/runs/{run['id']}/complete")
    rejected = client.post(
        f"/api/runs/{run['id']}/cases/CHK-1/execute", json={"outcome": "passed"}
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "run_already_completed"
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest server/tests/acceptance/test_plan_execution.py -v`
Expected: PASS — 1 passed. (Everything it exercises was built in Tasks 1-6; this test proves they compose.)

- [ ] **Step 3: Write the failing CLI tests**

Append to `server/tests/integration/test_cli.py`:

```python
def test_plan_create_and_list(cli, client):
    cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    cli.invoke(app, ["case", "new", "CHK", "Coupon applies"])

    created = cli.invoke(app, ["plan", "create", "CHK", "Release 2.4", "--case-key", "CHK-1"])
    assert created.exit_code == 0, created.output
    assert "Release 2.4" in created.output

    listed = cli.invoke(app, ["plan", "list", "CHK"])
    assert listed.exit_code == 0
    assert "Release 2.4" in listed.output


def test_plan_show_reports_the_case_snapshot(cli, client):
    cli.invoke(app, ["project", "create", "CHK", "Checkout"])
    cli.invoke(app, ["case", "new", "CHK", "Coupon applies"])
    plan_id = client.post(
        "/api/projects/CHK/plans", json={"name": "P", "case_keys": ["CHK-1"]}
    ).json()["id"]

    result = cli.invoke(app, ["plan", "show", plan_id])

    assert result.exit_code == 0
    assert "CHK-1" in result.output
    assert "Coupon applies" in result.output
```

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest server/tests/integration/test_cli.py -v`
Expected: FAIL — no such command `plan`.

- [ ] **Step 5: Add the CLI commands**

In `server/src/testforge/cli/__init__.py`, add the typer sub-app next to the existing ones:

```python
plan_app = typer.Typer(help="Manage test plans")
app.add_typer(plan_app, name="plan")


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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest server/tests/integration/test_cli.py -v`
Expected: PASS — the existing CLI tests plus the 2 new ones.

- [ ] **Step 7: Run everything**

Run: `uv run ruff format . && uv run ruff check . && uv run pytest -v`
Expected: ruff clean; 131 passed.

- [ ] **Step 8: Update the README**

In `README.md`, add this section immediately before the `## Design` heading:

````markdown
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
````

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add plan CLI commands, acceptance test, and README section"
```

---

## Verification

After Task 7, confirm the slice's success criterion by hand:

```bash
uv run pytest -v
```

Then exercise a real plan against a running server:

```bash
make dev
```

In a second shell:

```bash
uv run tf plan create CHK "Release 2.4" --milestone 2.4 --tag release
```

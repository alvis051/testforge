from sqlalchemy import func, select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.ids import new_id
from testforge.models.plan import TestPlan
from testforge.models.project import Project
from testforge.models.run import Result, Run


class RunService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def open(
        self,
        *,
        project: Project,
        external_id: str,
        name: str | None,
        source: str,
        ci_metadata: dict,
        actor: str,
    ) -> tuple[Run, bool]:
        existing = self.session.scalar(
            select(Run).where(Run.project_id == project.id, Run.external_id == external_id)
        )
        if existing is not None:
            return existing, False
        run = Run(
            project_id=project.id,
            external_id=external_id,
            name=name,
            source=source,
            ci_metadata_json=ci_metadata,
            created_by=actor,
        )
        self.session.add(run)
        self.session.flush()
        return run, True

    def get(self, run_id: str) -> Run:
        run = self.session.get(Run, run_id)
        if run is None:
            raise AppError("run_not_found", f"no run {run_id}", 404, {"run_id": run_id})
        return run

    def complete(self, run_id: str) -> Run:
        run = self.get(run_id)
        self.assert_writable(run)
        run.status = "completed"
        run.completed_at = utcnow()
        self.session.flush()
        return run

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
        self.assert_writable(run)
        run.status = "canceled"
        run.completed_at = utcnow()
        self.session.flush()
        return run

    def list_for_project(
        self,
        project: Project,
        *,
        status: str | None = None,
        source: str | None = None,
        plan_id: str | None = None,
        limit: int = 50,
    ) -> list[Run]:
        """Newest first. ``id`` is a secondary sort key so runs opened inside the same
        clock tick still come back in a stable, repeatable order."""
        stmt = select(Run).where(Run.project_id == project.id)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        if source is not None:
            stmt = stmt.where(Run.source == source)
        if plan_id is not None:
            stmt = stmt.where(Run.plan_id == plan_id)
        stmt = stmt.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def list_for_plan(self, plan_id: str, *, limit: int = 50) -> list[Run]:
        stmt = (
            select(Run)
            .where(Run.plan_id == plan_id)
            .order_by(Run.started_at.desc(), Run.id.desc())
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

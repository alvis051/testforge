from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.ids import new_id
from testforge.models.plan import TestPlan
from testforge.models.project import Project
from testforge.models.run import Run


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

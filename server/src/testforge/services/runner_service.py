from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.case import TestCase
from testforge.models.plan import TestPlan
from testforge.models.run import Run
from testforge.models.run_job import RunJob
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

#: How long a claim is good for before another worker may take the job.
LEASE_SECONDS = 60
#: Claims allowed before a repeatedly-abandoned job is given up on.
MAX_ATTEMPTS = 3


class RunnerService:
    """Dispatch and the worker-facing queue.

    There is no scheduler anywhere in this system: expired leases are reclaimed by
    ``sweep_expired`` at the top of every ``claim``, so the next worker to ask for
    work is the one that does the cleaning.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def dispatch(self, *, plan: TestPlan, git_ref: str | None, name: str | None, actor: str) -> Run:
        project = ProjectService(self.session).get(plan.project_id)
        if not project.repo_url or not project.test_command:
            raise AppError(
                "runner_not_configured_for_project",
                f"project {project.key} has no repo_url or test_command",
                409,
                {"project_key": project.key},
            )

        case_keys = self._automated_case_keys(plan)
        if not case_keys:
            raise AppError(
                "plan_has_no_automated_cases",
                f"plan {plan.id} has no automated cases to run",
                409,
                {"plan_id": plan.id},
            )

        # open_for_plan already rejects an archived plan, so that check is not repeated.
        run = RunService(self.session).open_for_plan(
            plan=plan, name=name, external_id=None, actor=actor
        )
        run.source = "runner"
        run.status = "queued"
        self.session.add(
            RunJob(
                run_id=run.id,
                repo_url=project.repo_url,
                git_ref=git_ref or project.default_ref,
                command=project.test_command,
                case_keys_json=case_keys,
            )
        )
        self.session.flush()
        return run

    def claim(self, worker_name: str) -> RunJob | None:
        """Take the oldest queued job, or return ``None`` when there is no work.

        The ``status == "queued"`` guard on the outer UPDATE makes this a
        compare-and-swap: if two workers select the same row, one UPDATE matches zero
        rows and that worker simply gets no work instead of a duplicate execution.
        Portable to SQLite and Postgres alike, with no ``FOR UPDATE SKIP LOCKED``.
        """
        self.sweep_expired()

        now = utcnow()
        oldest = (
            select(RunJob.id)
            .where(RunJob.status == "queued")
            .order_by(RunJob.created_at, RunJob.id)
            .limit(1)
            .scalar_subquery()
        )
        row = self.session.execute(
            update(RunJob)
            .where(RunJob.id == oldest, RunJob.status == "queued")
            .values(
                status="running",
                claimed_by=worker_name,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                attempts=RunJob.attempts + 1,
            )
            .returning(RunJob.id)
            .execution_options(synchronize_session=False)
        ).first()
        if row is None:
            return None

        job = self.session.get(RunJob, row[0])
        self.session.refresh(job)  # the UPDATE bypassed the identity map
        RunService(self.session).get(job.run_id).status = "running"
        self.session.flush()
        return job

    def sweep_expired(self) -> int:
        """Reclaim jobs whose worker stopped heartbeating. Returns how many were swept."""
        now = utcnow()
        stale = list(
            self.session.scalars(
                select(RunJob).where(RunJob.status == "running", RunJob.lease_expires_at < now)
            )
        )
        runs = RunService(self.session)
        for job in stale:
            run = runs.get(job.run_id)
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "failed"
                job.error = (
                    f"lease expired after {job.attempts} attempt(s); no worker reported back"
                )
                job.finished_at = now
                job.lease_expires_at = None
                run.status = "errored"
                run.completed_at = now
            else:
                job.status = "queued"
                job.claimed_by = None
                job.claimed_at = None
                job.lease_expires_at = None
                run.status = "queued"
        self.session.flush()
        return len(stale)

    def _automated_case_keys(self, plan: TestPlan) -> list[str]:
        """The plan's automated cases, in the order the plan froze them."""
        members = PlanService(self.session).cases(plan.id)
        if not members:
            return []
        rows = self.session.execute(
            select(TestCase.id, TestCase.case_key).where(
                TestCase.id.in_([m.test_case_id for m in members]),
                TestCase.execution_type == "automated",
            )
        )
        by_id = {case_id: case_key for case_id, case_key in rows}
        return [by_id[m.test_case_id] for m in members if m.test_case_id in by_id]

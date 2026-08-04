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
            framework="manual",
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

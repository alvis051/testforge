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
            select(func.count()).select_from(TestPlanCase).where(TestPlanCase.plan_id == plan_id)
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

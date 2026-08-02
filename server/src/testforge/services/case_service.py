from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from testforge.db.base import utcnow
from testforge.errors import AppError
from testforge.models.case import Tag, TestCase, TestCaseVersion
from testforge.models.project import Project
from testforge.services.project_service import ProjectService

VERSIONED_FIELDS = (
    "title",
    "execution_type",
    "priority",
    "preconditions",
    "steps_json",
    "expected_result",
)


class CaseService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        project: Project,
        suite_id: str | None,
        title: str,
        execution_type: str,
        priority: str,
        preconditions: str | None,
        steps: list[dict[str, Any]],
        expected_result: str | None,
        tags: list[str],
        actor: str,
    ) -> TestCase:
        case_key = ProjectService(self.session).allocate_case_key(project)
        case = TestCase(
            project_id=project.id,
            suite_id=suite_id,
            case_key=case_key,
            title=title,
            execution_type=execution_type,
            priority=priority,
            preconditions=preconditions,
            steps_json=steps,
            expected_result=expected_result,
            current_version_no=1,
            created_by=actor,
            updated_by=actor,
        )
        case.tags = [self._tag(project.id, name) for name in tags]
        self.session.add(case)
        self.session.flush()
        self._snapshot(case, change_note="initial version", actor=actor)
        return case

    def get_by_key(self, case_key: str) -> TestCase:
        case = self.session.scalar(select(TestCase).where(TestCase.case_key == case_key))
        if case is None:
            raise AppError("case_key_not_found", f"no case {case_key}", 404, {"case_key": case_key})
        return case

    def find_by_key(self, case_key: str) -> TestCase | None:
        """Lookup that returns ``None`` instead of raising — used by ingestion."""
        return self.session.scalar(select(TestCase).where(TestCase.case_key == case_key))

    def update(
        self,
        *,
        case_key: str,
        expected_version_no: int,
        actor: str,
        change_note: str | None = None,
        **fields: Any,
    ) -> TestCase:
        case = self.get_by_key(case_key)
        if case.current_version_no != expected_version_no:
            raise AppError(
                "case_version_conflict",
                f"case {case_key} has moved on to version {case.current_version_no}",
                409,
                {"case_key": case_key, "current_version_no": case.current_version_no},
            )
        for name, value in fields.items():
            if name not in VERSIONED_FIELDS and name not in {"suite_id", "status", "owner"}:
                raise AppError("invalid_result_payload", f"unknown case field {name}", 422)
            if value is not None:
                setattr(case, name, value)
        case.current_version_no += 1
        case.updated_by = actor
        self.session.flush()
        self._snapshot(case, change_note=change_note, actor=actor)
        return case

    def set_tags(self, case_key: str, tag_names: list[str], actor: str = "local") -> TestCase:
        """Tags are organisational metadata, not test content: no version bump."""
        case = self.get_by_key(case_key)
        case.tags = [self._tag(case.project_id, name) for name in tag_names]
        case.updated_by = actor
        self.session.flush()
        return case

    def archive(self, case_key: str, actor: str) -> TestCase:
        case = self.get_by_key(case_key)
        case.archived_at = utcnow()
        case.status = "deprecated"
        case.updated_by = actor
        self.session.flush()
        return case

    def versions(self, case_key: str) -> list[TestCaseVersion]:
        case = self.get_by_key(case_key)
        return list(
            self.session.scalars(
                select(TestCaseVersion)
                .where(TestCaseVersion.test_case_id == case.id)
                .order_by(TestCaseVersion.version_no)
            )
        )

    def current_version_count(self, case_key: str) -> int:
        case = self.get_by_key(case_key)
        return self.session.scalar(
            select(func.count())
            .select_from(TestCaseVersion)
            .where(TestCaseVersion.test_case_id == case.id)
        )

    def list_for_project(
        self,
        project: Project,
        *,
        suite_id: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        execution_type: str | None = None,
        q: str | None = None,
    ) -> list[TestCase]:
        stmt = select(TestCase).where(TestCase.project_id == project.id)
        if suite_id is not None:
            stmt = stmt.where(TestCase.suite_id == suite_id)
        if status is not None:
            stmt = stmt.where(TestCase.status == status)
        if execution_type is not None:
            stmt = stmt.where(TestCase.execution_type == execution_type)
        if q is not None:
            stmt = stmt.where(func.lower(TestCase.title).contains(q.lower()))
        if tag is not None:
            stmt = stmt.where(TestCase.tags.any(Tag.name == tag))
        return list(self.session.scalars(stmt.order_by(TestCase.case_key)))

    def _tag(self, project_id: str, name: str) -> Tag:
        tag = self.session.scalar(select(Tag).where(Tag.project_id == project_id, Tag.name == name))
        if tag is None:
            tag = Tag(project_id=project_id, name=name)
            self.session.add(tag)
            self.session.flush()
        return tag

    def _snapshot(self, case: TestCase, *, change_note: str | None, actor: str) -> None:
        self.session.add(
            TestCaseVersion(
                test_case_id=case.id,
                version_no=case.current_version_no,
                title=case.title,
                execution_type=case.execution_type,
                priority=case.priority,
                preconditions=case.preconditions,
                steps_json=case.steps_json,
                expected_result=case.expected_result,
                change_note=change_note,
                created_by=actor,
                created_at=utcnow(),
            )
        )
        self.session.flush()

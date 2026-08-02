from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.errors import AppError
from testforge.models.case import TestCaseVersion
from testforge.models.run import Result, Run
from testforge.schemas.results import IngestSummary, ResultIn
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService


class IngestionService:
    """The single path from a raw result to a stored, case-bound row."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.cases = CaseService(session)
        self.automation = AutomationService(session)

    def ingest(self, *, run: Run, results: list[ResultIn]) -> IngestSummary:
        if run.status != "running":
            raise AppError(
                "run_already_completed",
                f"run {run.id} is {run.status} and no longer accepts results",
                409,
                {"run_id": run.id, "status": run.status},
            )

        resolved = unresolved = archived = 0
        outcomes: Counter[str] = Counter()

        for incoming in results:
            case = self.cases.find_by_key(incoming.case_key) if incoming.case_key else None
            version_id = None
            link_id = None

            if case is None:
                unresolved += 1
            else:
                resolved += 1
                if case.archived_at is not None:
                    archived += 1
                version_id = self._current_version_id(case.id, case.current_version_no)
                link = self.automation.bind(
                    case=case,
                    framework=incoming.framework,
                    test_identifier=incoming.test_identifier,
                    seen_at=incoming.executed_at,
                )
                link_id = link.id

            self.session.add(
                Result(
                    run_id=run.id,
                    test_case_id=case.id if case else None,
                    test_case_version_id=version_id,
                    automation_link_id=link_id,
                    test_identifier=incoming.test_identifier,
                    unresolved_case_key=incoming.case_key if case is None else None,
                    outcome=incoming.outcome,
                    duration_ms=incoming.duration_ms,
                    failure_type=incoming.failure_type,
                    failure_message=incoming.failure_message,
                    stack_trace=incoming.stack_trace,
                    attachments_json=incoming.attachments,
                    executed_at=incoming.executed_at,
                )
            )
            outcomes[incoming.outcome] += 1

        self.session.flush()
        return IngestSummary(
            run_id=run.id,
            recorded=len(results),
            resolved=resolved,
            unresolved=unresolved,
            archived_case_results=archived,
            by_outcome=dict(outcomes),
        )

    def results_for_run(self, run: Run) -> list[Result]:
        return list(
            self.session.scalars(
                select(Result).where(Result.run_id == run.id).order_by(Result.test_identifier)
            )
        )

    def _current_version_id(self, case_id: str, version_no: int) -> str | None:
        return self.session.scalar(
            select(TestCaseVersion.id).where(
                TestCaseVersion.test_case_id == case_id,
                TestCaseVersion.version_no == version_no,
            )
        )

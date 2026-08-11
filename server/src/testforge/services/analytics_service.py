from collections import Counter
from itertools import groupby

from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.analytics.flakiness import COUNTED, score_outcomes
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
        """Score each case's flakiness from its per-run history.

        A case can be covered by more than one automated test (one-to-many
        automation_links), so a run can contribute more than one Result row per
        case; these are reduced to one class per (case, run) before scoring.
        A case's class for a run is "passed" only if every countable result for
        that case in that run passed — any countable failure/error makes the
        run's class "failed" for that case. Rows are ordered by run identity
        (started_at, id), not Result.executed_at, which is an unreliable
        tiebreak: batch imports commonly stamp one identical timestamp across
        every result in a run.
        """
        rows = self.session.execute(
            select(Result.test_case_id, Run.id, Result.outcome)
            .join(Run, Result.run_id == Run.id)
            .where(
                Run.project_id == project.id,
                Result.test_case_id.is_not(None),
                Result.outcome.in_(COUNTED),
            )
            .order_by(Result.test_case_id, Run.started_at, Run.id)
        )
        history: dict[str, list[str]] = {}
        for (case_id, _run_id), group in groupby(rows, key=lambda row: (row[0], row[1])):
            outcomes_in_run = [outcome for _, _, outcome in group]
            run_class = "passed" if all(o == "passed" for o in outcomes_in_run) else "failed"
            history.setdefault(case_id, []).append(run_class)

        scored = {case_id: score_outcomes(o) for case_id, o in history.items()}
        flaky_ids = [case_id for case_id, result in scored.items() if result.is_flaky]
        if not flaky_ids:
            return []

        # One batched lookup rather than one per flaky case.
        cases = {
            case.id: case
            for case in self.session.scalars(select(TestCase).where(TestCase.id.in_(flaky_ids)))
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

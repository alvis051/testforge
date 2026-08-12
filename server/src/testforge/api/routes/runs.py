from collections import Counter

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.db.base import utcnow
from testforge.models.case import TestCase
from testforge.models.run import Result, Run
from testforge.schemas.results import IngestSummary, ResultBatch
from testforge.schemas.runner import RunJobOut
from testforge.schemas.runs import (
    AutomationLinkOut,
    ManualExecute,
    PlanProgress,
    ResultOut,
    RunCreate,
    RunListItemOut,
    RunOut,
    RunSummaryOut,
)
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.junit import parse_junit
from testforge.services.manual_service import ManualExecutionService
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService
from testforge.services.runner_service import RunnerService

router = APIRouter(tags=["runs"])

_RUN_FIELDS = (
    "id",
    "project_id",
    "plan_id",
    "external_id",
    "name",
    "source",
    "status",
    "created_by",
    "started_at",
    "completed_at",
)

_RESULT_FIELDS = (
    "id",
    "test_case_id",
    "test_case_version_id",
    "test_identifier",
    "framework",
    "unresolved_case_key",
    "outcome",
    "duration_ms",
    "failure_type",
    "failure_message",
    "stack_trace",
    "executed_at",
)


def run_out(run: Run, project_key: str) -> RunOut:
    """A ``Run`` row references its project by id, but the UI routes on the project
    *key* — so the key is passed in rather than looked up here, letting listing
    endpoints reuse the ``Project`` they already fetched."""
    return RunOut(**{field: getattr(run, field) for field in _RUN_FIELDS}, project_key=project_key)


def run_out_for(session: Session, run: Run) -> RunOut:
    """For endpoints that hold only a ``Run``: one lookup for its project's key."""
    return run_out(run, ProjectService(session).get(run.project_id).key)


def result_out(result: Result, case_key: str | None) -> ResultOut:
    return ResultOut(
        **{field: getattr(result, field) for field in _RESULT_FIELDS}, case_key=case_key
    )


def case_keys_for(session: Session, results: list[Result]) -> dict[str, str]:
    """Case keys for a whole result list in one query — never one query per row."""
    case_ids = {r.test_case_id for r in results if r.test_case_id is not None}
    if not case_ids:
        return {}
    rows = session.execute(select(TestCase.id, TestCase.case_key).where(TestCase.id.in_(case_ids)))
    return {case_id: case_key for case_id, case_key in rows}


@router.post("/api/projects/{project_key}/runs", response_model=RunOut)
def open_run(
    project_key: str,
    payload: RunCreate,
    response: Response,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> RunOut:
    project = ProjectService(session).get_by_key(project_key)
    run, created = RunService(session).open(
        project=project,
        external_id=payload.external_id,
        name=payload.name,
        source=payload.source,
        ci_metadata=payload.ci_metadata,
        actor=actor,
    )
    response.status_code = 201 if created else 200
    return run_out(run, project.key)


def build_run_list_items(
    service: RunService, runs: list[Run], project_key: str
) -> list[RunListItemOut]:
    """Attach outcome tallies to run rows. Imported by plans.py so the two
    listing endpoints cannot drift apart."""
    counts = service.outcome_counts([run.id for run in runs])
    return [
        RunListItemOut(
            **run_out(run, project_key).model_dump(),
            total_results=sum(counts.get(run.id, {}).values()),
            by_outcome=counts.get(run.id, {}),
        )
        for run in runs
    ]


@router.get("/api/projects/{project_key}/runs", response_model=list[RunListItemOut])
def list_project_runs(
    project_key: str,
    status: str | None = None,
    source: str | None = None,
    plan_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[RunListItemOut]:
    project = ProjectService(session).get_by_key(project_key)
    service = RunService(session)
    runs = service.list_for_project(
        project, status=status, source=source, plan_id=plan_id, limit=limit
    )
    return build_run_list_items(service, runs, project.key)


@router.post("/api/runs/{run_id}/results", response_model=IngestSummary)
def ingest_results(
    run_id: str,
    payload: ResultBatch,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> IngestSummary:
    run = RunService(session).get(run_id)
    return IngestionService(session).ingest(run=run, results=payload.results)


@router.post("/api/runs/{run_id}/complete", response_model=RunOut)
def complete_run(
    run_id: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> RunOut:
    return run_out_for(session, RunService(session).complete(run_id))


@router.post("/api/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(
    run_id: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> RunOut:
    return run_out_for(session, RunService(session).cancel(run_id))


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
    return result_out(result, case_key)


@router.get("/api/runs/{run_id}", response_model=RunSummaryOut)
def get_run(run_id: str, session: Session = Depends(get_session)) -> RunSummaryOut:
    run = RunService(session).get(run_id)
    results = IngestionService(session).results_for_run(run)
    outcomes = Counter(result.outcome for result in results)
    job = RunnerService(session).job_for_run(run.id)
    return RunSummaryOut(
        **run_out_for(session, run).model_dump(),
        total_results=len(results),
        unresolved_count=sum(1 for r in results if r.test_case_id is None),
        by_outcome=dict(outcomes),
        plan_progress=_plan_progress(session, run, results),
        job=RunJobOut.model_validate(job) if job is not None else None,
    )


def _plan_progress(session: Session, run, results) -> PlanProgress | None:
    """Coverage of a plan's frozen case list by this run's results. Computed, never stored."""
    if run.plan_id is None:
        return None
    members = PlanService(session).cases(run.plan_id)
    results_by_case: dict[str, Result] = {}
    for result in results:
        if result.test_case_id is None:
            continue
        current = results_by_case.get(result.test_case_id)
        if current is None or result.executed_at > current.executed_at:
            results_by_case[result.test_case_id] = result

    covered = [m for m in members if m.test_case_id in results_by_case]
    missing_ids = [m.test_case_id for m in members if m.test_case_id not in results_by_case]
    missing_keys = [session.get(TestCase, case_id).case_key for case_id in missing_ids]
    outcomes = Counter(results_by_case[m.test_case_id].outcome for m in covered)

    return PlanProgress(
        total_cases=len(members),
        cases_with_result=len(covered),
        by_outcome=dict(outcomes),
        cases_without_result=missing_keys,
    )


@router.get("/api/runs/{run_id}/results", response_model=list[ResultOut])
def list_results(
    run_id: str,
    outcome: str | None = None,
    case_key: str | None = None,
    unresolved: bool | None = None,
    session: Session = Depends(get_session),
) -> list[ResultOut]:
    run = RunService(session).get(run_id)
    results = IngestionService(session).results_for_run(run)
    if outcome is not None:
        results = [r for r in results if r.outcome == outcome]
    if unresolved is not None:
        results = [r for r in results if (r.test_case_id is None) is unresolved]
    if case_key is not None:
        case = CaseService(session).get_by_key(case_key)
        results = [r for r in results if r.test_case_id == case.id]
    keys = case_keys_for(session, results)
    return [result_out(r, keys.get(r.test_case_id)) for r in results]


@router.get("/api/cases/{case_key}/automation-links", response_model=list[AutomationLinkOut])
def list_automation_links(
    case_key: str, session: Session = Depends(get_session)
) -> list[AutomationLinkOut]:
    case = CaseService(session).get_by_key(case_key)
    links = AutomationService(session).links_for_case(case)
    return [AutomationLinkOut.model_validate(link) for link in links]


class JUnitImport(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    name: str | None = None
    xml: str


@router.post("/api/projects/{project_key}/runs/import-junit", response_model=IngestSummary)
def import_junit(
    project_key: str,
    payload: JUnitImport,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> IngestSummary:
    project = ProjectService(session).get_by_key(project_key)
    executed_at = utcnow()
    results = parse_junit(payload.xml, project_key=project.key, default_executed_at=executed_at)

    run_service = RunService(session)
    run, _ = run_service.open(
        project=project,
        external_id=payload.external_id,
        name=payload.name,
        source="ci",
        ci_metadata={"import": "junit"},
        actor=actor,
    )
    summary = IngestionService(session).ingest(run=run, results=results)
    run_service.complete(run.id)
    return summary

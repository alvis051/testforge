from collections import Counter

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.db.base import utcnow
from testforge.schemas.results import IngestSummary, ResultBatch
from testforge.schemas.runs import AutomationLinkOut, ResultOut, RunCreate, RunOut, RunSummaryOut
from testforge.services.automation_service import AutomationService
from testforge.services.case_service import CaseService
from testforge.services.ingestion_service import IngestionService
from testforge.services.junit import parse_junit
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

router = APIRouter(tags=["runs"])


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
    return RunOut.model_validate(run)


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
    return RunOut.model_validate(RunService(session).complete(run_id))


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
    return [ResultOut.model_validate(r) for r in results]


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
    results = parse_junit(payload.xml, default_executed_at=executed_at)

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

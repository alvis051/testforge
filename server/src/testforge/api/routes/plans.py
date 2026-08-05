from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.api.routes.runs import build_run_list_items, run_out
from testforge.models.case import TestCase
from testforge.schemas.plans import PlanCaseOut, PlanCreate, PlanOut, PlanRunCreate
from testforge.schemas.runs import RunListItemOut, RunOut
from testforge.services.plan_service import PlanService
from testforge.services.project_service import ProjectService
from testforge.services.run_service import RunService

router = APIRouter(tags=["plans"])


def _to_out(plan, service: PlanService) -> PlanOut:
    return PlanOut(
        **{
            field: getattr(plan, field)
            for field in (
                "id",
                "project_id",
                "name",
                "description",
                "milestone",
                "environment",
                "status",
                "created_by",
                "created_at",
                "archived_at",
            )
        },
        case_count=service.case_count(plan.id),
    )


@router.post("/api/projects/{project_key}/plans", response_model=PlanOut, status_code=201)
def create_plan(
    project_key: str,
    payload: PlanCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> PlanOut:
    project = ProjectService(session).get_by_key(project_key)
    service = PlanService(session)
    plan = service.create(
        project=project,
        name=payload.name,
        description=payload.description,
        milestone=payload.milestone,
        environment=payload.environment,
        case_keys=payload.case_keys,
        filter_=payload.filter.model_dump(exclude_none=True) if payload.filter else None,
        actor=actor,
    )
    return _to_out(plan, service)


@router.get("/api/projects/{project_key}/plans", response_model=list[PlanOut])
def list_plans(
    project_key: str,
    status: str | None = None,
    milestone: str | None = None,
    environment: str | None = None,
    session: Session = Depends(get_session),
) -> list[PlanOut]:
    project = ProjectService(session).get_by_key(project_key)
    service = PlanService(session)
    plans = service.list_for_project(
        project, status=status, milestone=milestone, environment=environment
    )
    return [_to_out(plan, service) for plan in plans]


@router.get("/api/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, session: Session = Depends(get_session)) -> PlanOut:
    service = PlanService(session)
    return _to_out(service.get(plan_id), service)


@router.get("/api/plans/{plan_id}/cases", response_model=list[PlanCaseOut])
def list_plan_cases(plan_id: str, session: Session = Depends(get_session)) -> list[PlanCaseOut]:
    service = PlanService(session)
    service.get(plan_id)
    members = service.cases(plan_id)
    out: list[PlanCaseOut] = []
    for member in members:
        case = session.get(TestCase, member.test_case_id)
        out.append(
            PlanCaseOut(
                case_key=case.case_key,
                title=case.title,
                execution_type=case.execution_type,
                test_case_version_id=member.test_case_version_id,
                position=member.position,
            )
        )
    return out


@router.post("/api/plans/{plan_id}/archive", response_model=PlanOut)
def archive_plan(
    plan_id: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> PlanOut:
    service = PlanService(session)
    return _to_out(service.archive(plan_id, actor), service)


@router.post("/api/plans/{plan_id}/runs", response_model=RunOut, status_code=201)
def execute_plan(
    plan_id: str,
    payload: PlanRunCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> RunOut:
    plan = PlanService(session).get(plan_id)
    project = ProjectService(session).get(plan.project_id)
    run = RunService(session).open_for_plan(
        plan=plan, name=payload.name, external_id=payload.external_id, actor=actor
    )
    return run_out(run, project.key)


@router.get("/api/plans/{plan_id}/runs", response_model=list[RunListItemOut])
def list_plan_runs(
    plan_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[RunListItemOut]:
    plan = PlanService(session).get(plan_id)
    project = ProjectService(session).get(plan.project_id)
    service = RunService(session)
    return build_run_list_items(service, service.list_for_plan(plan_id, limit=limit), project.key)

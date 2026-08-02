from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.suites import SuiteCreate, SuiteOut, SuiteUpdate
from testforge.services.project_service import ProjectService
from testforge.services.suite_service import SuiteService

router = APIRouter(tags=["suites"])


@router.post("/api/projects/{project_key}/suites", response_model=SuiteOut, status_code=201)
def create_suite(
    project_key: str,
    payload: SuiteCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> SuiteOut:
    project = ProjectService(session).get_by_key(project_key)
    suite = SuiteService(session).create(
        project=project, name=payload.name, parent_id=payload.parent_id, actor=actor
    )
    return SuiteOut.model_validate(suite)


@router.get("/api/projects/{project_key}/suites", response_model=list[SuiteOut])
def list_suites(project_key: str, session: Session = Depends(get_session)) -> list[SuiteOut]:
    project = ProjectService(session).get_by_key(project_key)
    return [SuiteOut.model_validate(s) for s in SuiteService(session).list_for_project(project)]


@router.patch("/api/suites/{suite_id}", response_model=SuiteOut)
def update_suite(
    suite_id: str, payload: SuiteUpdate, session: Session = Depends(get_session)
) -> SuiteOut:
    service = SuiteService(session)
    suite = service.get(suite_id)
    if payload.name is not None:
        suite.name = payload.name
    if payload.move:
        suite = service.move(suite_id, payload.parent_id)
    return SuiteOut.model_validate(suite)

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.projects import ProjectCreate, ProjectOut
from testforge.services.project_service import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> ProjectOut:
    project = ProjectService(session).create(
        key=payload.key,
        name=payload.name,
        description=payload.description,
        actor=actor,
        repo_url=payload.repo_url,
        default_ref=payload.default_ref,
        test_command=payload.test_command,
    )
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_session)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in ProjectService(session).list()]


@router.get("/{project_key}", response_model=ProjectOut)
def get_project(project_key: str, session: Session = Depends(get_session)) -> ProjectOut:
    return ProjectOut.model_validate(ProjectService(session).get_by_key(project_key))

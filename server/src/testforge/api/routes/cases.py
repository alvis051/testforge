from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from testforge.api.deps import get_actor, get_session
from testforge.schemas.cases import CaseCreate, CaseOut, CaseUpdate, CaseVersionOut, TagsSet
from testforge.services.case_service import CaseService
from testforge.services.project_service import ProjectService

router = APIRouter(tags=["cases"])


@router.post("/api/projects/{project_key}/cases", response_model=CaseOut, status_code=201)
def create_case(
    project_key: str,
    payload: CaseCreate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> CaseOut:
    project = ProjectService(session).get_by_key(project_key)
    case = CaseService(session).create(
        project=project,
        suite_id=payload.suite_id,
        title=payload.title,
        execution_type=payload.execution_type,
        priority=payload.priority,
        preconditions=payload.preconditions,
        steps=[step.model_dump() for step in payload.steps],
        expected_result=payload.expected_result,
        tags=payload.tags,
        actor=actor,
    )
    return CaseOut.model_validate(case)


@router.get("/api/projects/{project_key}/cases", response_model=list[CaseOut])
def list_cases(
    project_key: str,
    suite_id: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    execution_type: str | None = None,
    q: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[CaseOut]:
    project = ProjectService(session).get_by_key(project_key)
    cases = CaseService(session).list_for_project(
        project, suite_id=suite_id, tag=tag, status=status, execution_type=execution_type, q=q
    )
    return [CaseOut.model_validate(case) for case in cases]


@router.get("/api/cases/{case_key}", response_model=CaseOut)
def get_case(case_key: str, session: Session = Depends(get_session)) -> CaseOut:
    return CaseOut.model_validate(CaseService(session).get_by_key(case_key))


@router.patch("/api/cases/{case_key}", response_model=CaseOut)
def update_case(
    case_key: str,
    payload: CaseUpdate,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> CaseOut:
    fields = payload.model_dump(exclude_none=True, exclude={"expected_version_no", "change_note"})
    if "steps" in fields:
        fields["steps_json"] = [dict(step) for step in fields.pop("steps")]
    case = CaseService(session).update(
        case_key=case_key,
        expected_version_no=payload.expected_version_no,
        actor=actor,
        change_note=payload.change_note,
        **fields,
    )
    return CaseOut.model_validate(case)


@router.get("/api/cases/{case_key}/versions", response_model=list[CaseVersionOut])
def list_versions(case_key: str, session: Session = Depends(get_session)) -> list[CaseVersionOut]:
    return [CaseVersionOut.model_validate(v) for v in CaseService(session).versions(case_key)]


@router.put("/api/cases/{case_key}/tags", response_model=CaseOut)
def set_tags(
    case_key: str,
    payload: TagsSet,
    session: Session = Depends(get_session),
    actor: str = Depends(get_actor),
) -> CaseOut:
    return CaseOut.model_validate(CaseService(session).set_tags(case_key, payload.tags, actor))


@router.post("/api/cases/{case_key}/archive", response_model=CaseOut)
def archive_case(
    case_key: str, session: Session = Depends(get_session), actor: str = Depends(get_actor)
) -> CaseOut:
    return CaseOut.model_validate(CaseService(session).archive(case_key, actor))

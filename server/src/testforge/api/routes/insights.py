from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from testforge.api.deps import get_session
from testforge.schemas.insights import FailureCategoryOut, FlakyCaseOut, RunTrendPointOut
from testforge.services.analytics_service import AnalyticsService
from testforge.services.project_service import ProjectService

router = APIRouter(tags=["insights"])


@router.get("/api/projects/{project_key}/insights/flaky", response_model=list[FlakyCaseOut])
def list_flaky_cases(
    project_key: str, session: Session = Depends(get_session)
) -> list[FlakyCaseOut]:
    project = ProjectService(session).get_by_key(project_key)
    return AnalyticsService(session).flaky_cases(project)


@router.get("/api/projects/{project_key}/insights/trends", response_model=list[RunTrendPointOut])
def run_trends(
    project_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[RunTrendPointOut]:
    project = ProjectService(session).get_by_key(project_key)
    return AnalyticsService(session).run_trends(project, limit=limit)


@router.get(
    "/api/projects/{project_key}/insights/failure-categories",
    response_model=list[FailureCategoryOut],
)
def failure_categories(
    project_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[FailureCategoryOut]:
    project = ProjectService(session).get_by_key(project_key)
    return AnalyticsService(session).failure_categories(project, limit=limit)

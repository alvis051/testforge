from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from testforge.api.deps import get_session, require_runner_token
from testforge.schemas.runner import RunnerClaim, RunnerFinish, RunnerHeartbeat, RunnerJobOut
from testforge.services.runner_service import RunnerService

router = APIRouter(tags=["runner"], dependencies=[Depends(require_runner_token)])


@router.post("/api/runner/claim", response_model=RunnerJobOut | None)
def claim_job(payload: RunnerClaim, session: Session = Depends(get_session)) -> RunnerJobOut | None:
    job = RunnerService(session).claim(payload.worker_name)
    if job is None:
        return None
    return RunnerJobOut(
        job_id=job.id,
        run_id=job.run_id,
        repo_url=job.repo_url,
        git_ref=job.git_ref,
        command=job.command,
        case_keys=job.case_keys_json,
        lease_expires_at=job.lease_expires_at,
    )


@router.post("/api/runner/jobs/{job_id}/heartbeat", status_code=204)
def heartbeat_job(
    job_id: str, payload: RunnerHeartbeat, session: Session = Depends(get_session)
) -> Response:
    RunnerService(session).heartbeat(job_id, payload.worker_name)
    return Response(status_code=204)


@router.post("/api/runner/jobs/{job_id}/finish", status_code=204)
def finish_job(
    job_id: str, payload: RunnerFinish, session: Session = Depends(get_session)
) -> Response:
    RunnerService(session).finish(
        job_id=job_id,
        worker_name=payload.worker_name,
        status=payload.status,
        exit_code=payload.exit_code,
        output_tail=payload.output_tail,
        error=payload.error,
        resolved_sha=payload.resolved_sha,
        results=payload.results,
    )
    return Response(status_code=204)

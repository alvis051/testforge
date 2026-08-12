from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from testforge.schemas.results import ResultIn


class PlanDispatch(BaseModel):
    git_ref: str | None = Field(default=None, max_length=200)
    name: str | None = None


class RunnerClaim(BaseModel):
    worker_name: str = Field(min_length=1, max_length=200)


class RunnerJobOut(BaseModel):
    """Everything a worker needs to execute one job, and nothing else."""

    job_id: str
    run_id: str
    repo_url: str
    git_ref: str
    command: str
    case_keys: list[str]
    lease_expires_at: datetime


class RunnerHeartbeat(BaseModel):
    worker_name: str = Field(min_length=1, max_length=200)


class RunnerFinish(BaseModel):
    worker_name: str = Field(min_length=1, max_length=200)
    status: Literal["succeeded", "failed"]
    exit_code: int | None = None
    output_tail: str | None = None
    error: str | None = None
    resolved_sha: str | None = Field(default=None, max_length=40)
    results: list[ResultIn] = Field(default_factory=list)


class RunJobOut(BaseModel):
    """The job as the dashboard shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    repo_url: str
    git_ref: str
    resolved_sha: str | None
    attempts: int
    exit_code: int | None
    output_tail: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None

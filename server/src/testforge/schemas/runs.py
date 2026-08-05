from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    name: str | None = None
    source: Literal["ci", "local", "manual", "runner"] = "local"
    ci_metadata: dict = Field(default_factory=dict)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    project_key: str
    plan_id: str | None
    external_id: str
    name: str | None
    source: str
    status: str
    created_by: str
    started_at: datetime
    completed_at: datetime | None


class PlanProgress(BaseModel):
    total_cases: int
    cases_with_result: int
    by_outcome: dict[str, int]
    cases_without_result: list[str]


class ManualExecute(BaseModel):
    outcome: Literal["passed", "failed", "skipped", "error", "blocked"]
    notes: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class RunSummaryOut(RunOut):
    total_results: int
    unresolved_count: int
    by_outcome: dict[str, int]
    plan_progress: PlanProgress | None = None


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    test_case_id: str | None
    case_key: str | None
    test_case_version_id: str | None
    test_identifier: str
    framework: str | None
    unresolved_case_key: str | None
    outcome: str
    duration_ms: int | None
    failure_type: str | None
    failure_message: str | None
    stack_trace: str | None
    executed_at: datetime


class AutomationLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    framework: str
    test_identifier: str
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool


class RunListItemOut(RunOut):
    total_results: int
    by_outcome: dict[str, int]

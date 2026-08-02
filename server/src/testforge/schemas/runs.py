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
    external_id: str
    name: str | None
    source: str
    status: str
    created_by: str
    started_at: datetime
    completed_at: datetime | None


class RunSummaryOut(RunOut):
    total_results: int
    unresolved_count: int
    by_outcome: dict[str, int]


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    test_case_id: str | None
    test_case_version_id: str | None
    test_identifier: str
    unresolved_case_key: str | None
    outcome: str
    duration_ms: int | None
    failure_type: str | None
    failure_message: str | None
    executed_at: datetime


class AutomationLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    framework: str
    test_identifier: str
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaseFilter(BaseModel):
    """Mirrors the case-list query parameters, so one mental model covers both."""

    suite_id: str | None = None
    tag: str | None = None
    status: str | None = None
    execution_type: Literal["manual", "automated"] | None = None
    q: str | None = None


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    milestone: str | None = Field(default=None, max_length=100)
    environment: str | None = Field(default=None, max_length=100)
    case_keys: list[str] = Field(default_factory=list)
    filter: CaseFilter | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str | None
    milestone: str | None
    environment: str | None
    status: str
    case_count: int
    created_by: str
    created_at: datetime
    archived_at: datetime | None


class PlanCaseOut(BaseModel):
    case_key: str
    title: str
    execution_type: str
    test_case_version_id: str | None
    position: int


class PlanRunCreate(BaseModel):
    name: str | None = None
    external_id: str | None = None

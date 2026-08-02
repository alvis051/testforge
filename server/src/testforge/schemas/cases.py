from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from testforge.schemas.common import Step

ExecutionType = Literal["manual", "automated"]
Priority = Literal["p0", "p1", "p2", "p3"]
Status = Literal["draft", "active", "deprecated"]


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    suite_id: str | None = None
    execution_type: ExecutionType = "automated"
    priority: Priority = "p2"
    preconditions: str | None = None
    steps: list[Step] = Field(default_factory=list)
    expected_result: str | None = None
    tags: list[str] = Field(default_factory=list)


class CaseUpdate(BaseModel):
    expected_version_no: int
    change_note: str | None = None
    title: str | None = None
    suite_id: str | None = None
    execution_type: ExecutionType | None = None
    priority: Priority | None = None
    status: Status | None = None
    owner: str | None = None
    preconditions: str | None = None
    steps: list[Step] | None = None
    expected_result: str | None = None


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_key: str
    project_id: str
    suite_id: str | None
    title: str
    execution_type: str
    priority: str
    status: str
    owner: str | None
    preconditions: str | None
    steps: list[Step] = Field(validation_alias="steps_json")
    expected_result: str | None
    current_version_no: int
    tags: list[str]
    created_by: str
    updated_by: str
    archived_at: datetime | None

    @field_validator("tags", mode="before")
    @classmethod
    def flatten_tags(cls, value: object) -> list[str]:
        if isinstance(value, list) and value and not isinstance(value[0], str):
            return [tag.name for tag in value]
        return value or []


class CaseVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_no: int
    title: str
    execution_type: str
    priority: str
    preconditions: str | None
    steps: list[Step] = Field(validation_alias="steps_json")
    expected_result: str | None
    change_note: str | None
    created_by: str
    created_at: datetime


class TagsSet(BaseModel):
    tags: list[str]

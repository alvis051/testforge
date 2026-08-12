from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    key: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z][A-Z0-9]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    repo_url: str | None = Field(default=None, max_length=500)
    default_ref: str = Field(default="main", max_length=200)
    test_command: str | None = Field(default=None, max_length=500)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    description: str | None
    case_seq: int
    created_by: str
    created_at: datetime
    repo_url: str | None
    default_ref: str
    test_command: str | None

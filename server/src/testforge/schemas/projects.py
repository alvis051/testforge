from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    key: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z][A-Z0-9]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    description: str | None
    case_seq: int
    created_by: str
    created_at: datetime

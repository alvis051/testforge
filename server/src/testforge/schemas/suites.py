from pydantic import BaseModel, ConfigDict, Field


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    description: str | None = None


class SuiteUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    # ``parent_id=None`` legitimately means "move to root", so a separate flag is needed to
    # distinguish that from "rename only". Defaults to False so a rename never moves a suite.
    move: bool = False


class SuiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    parent_id: str | None
    name: str
    path: str
    position: int

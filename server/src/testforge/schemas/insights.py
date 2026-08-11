from datetime import datetime

from pydantic import BaseModel


class FlakyCaseOut(BaseModel):
    case_key: str
    title: str
    score: float
    transitions: int
    sample_size: int
    #: The window's binary outcomes, oldest first. A list rather than a packed
    #: string, so adding a third class later is not a format change.
    sequence: list[str]


class RunTrendPointOut(BaseModel):
    run_id: str
    external_id: str
    name: str | None
    started_at: datetime
    #: passed / (passed + failed + error). ``None`` when the run had nothing
    #: countable, so the chart breaks the line instead of plotting a false zero.
    pass_rate: float | None
    passed: int
    failed: int
    total: int


class FailureCategoryOut(BaseModel):
    category: str
    count: int

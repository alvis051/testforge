"""The result contract.

Every producer of results — the pytest plugin, JUnit import, and the future runner —
emits exactly this shape, and every one of them lands in ``IngestionService.ingest``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["passed", "failed", "skipped", "error", "blocked"]


class ResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str | None = None
    test_identifier: str = Field(min_length=1, max_length=1000)
    framework: str = Field(default="pytest", max_length=50)
    outcome: Outcome
    duration_ms: int | None = Field(default=None, ge=0)
    failure_type: str | None = None
    failure_message: str | None = None
    stack_trace: str | None = None
    attachments: list[str] = Field(default_factory=list)
    executed_at: datetime


class ResultBatch(BaseModel):
    results: list[ResultIn]


class IngestSummary(BaseModel):
    run_id: str
    recorded: int
    resolved: int
    unresolved: int
    archived_case_results: int
    by_outcome: dict[str, int]

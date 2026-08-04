from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, TimestampMixin, UTCDateTime, utcnow
from testforge.ids import new_id


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("project_id", "external_id", name="uq_runs_project_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    plan_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_plans.id"), nullable=True, index=True
    )
    ci_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Result(Base):
    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=False, index=True
    )
    test_case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_cases.id"), nullable=True, index=True
    )
    test_case_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_case_versions.id"), nullable=True
    )
    automation_link_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("automation_links.id"), nullable=True
    )
    test_identifier: Mapped[str] = mapped_column(String(1000), nullable=False)
    unresolved_case_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

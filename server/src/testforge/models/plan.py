from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, TimestampMixin, UTCDateTime
from testforge.ids import new_id


class TestPlan(Base, TimestampMixin):
    __tablename__ = "test_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    milestone: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    environment: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class TestPlanCase(Base):
    """A frozen member of a plan's case selection. Immutable once written."""

    __tablename__ = "test_plan_cases"

    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_plans.id"), primary_key=True
    )
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id"), primary_key=True
    )
    test_case_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_case_versions.id"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

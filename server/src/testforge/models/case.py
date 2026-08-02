from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testforge.db.base import Base, TimestampMixin, UTCDateTime
from testforge.ids import new_id

test_case_tags = Table(
    "test_case_tags",
    Base.metadata,
    Column("case_id", String(36), ForeignKey("test_cases.id"), primary_key=True),
    Column("tag_id", String(36), ForeignKey("tags.id"), primary_key=True),
)


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_tags_project_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class TestCase(Base, TimestampMixin):
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    suite_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("suites.id"), nullable=True, index=True
    )
    case_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False, default="automated")
    priority: Mapped[str] = mapped_column(String(4), nullable=False, default="p2")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    tags: Mapped[list[Tag]] = relationship(secondary=test_case_tags, lazy="selectin")


class TestCaseVersion(Base):
    __tablename__ = "test_case_versions"
    __table_args__ = (UniqueConstraint("test_case_id", "version_no", name="uq_case_version_no"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    preconditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

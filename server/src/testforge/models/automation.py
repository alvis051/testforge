from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, UTCDateTime
from testforge.ids import new_id


class AutomationLink(Base):
    __tablename__ = "automation_links"
    __table_args__ = (
        UniqueConstraint("framework", "test_identifier", name="uq_automation_identifier"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    test_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("test_cases.id"), nullable=False, index=True
    )
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    test_identifier: Mapped[str] = mapped_column(String(1000), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

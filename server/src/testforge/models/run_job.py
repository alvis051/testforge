from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from testforge.db.base import Base, UTCDateTime, utcnow
from testforge.ids import new_id


class RunJob(Base):
    """One dispatched execution: the queue's row.

    The ``Run`` beside it is the *test* record; this is the *infrastructure* record.
    Keeping them apart is what lets a suite that ran and reported failures be a
    succeeded job with a completed run, while a repository that would not clone is a
    failed job with an errored run.
    """

    __tablename__ = "run_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    git_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    resolved_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    command: Mapped[str] = mapped_column(String(500), nullable=False)
    case_keys_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    claimed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

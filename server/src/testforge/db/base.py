from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """A timezone-aware DateTime that stays UTC-aware across a real round trip.

    SQLite has no native tz-aware storage: ``DateTime(timezone=True)`` writes the
    offset but silently reads back a naive ``datetime`` once SQLAlchemy's (weak)
    identity map no longer holds the original Python object and has to re-fetch
    the row. Every timestamp in this system is UTC (see global constraints), so on
    read we just re-attach ``UTC`` when the driver handed back a naive value. This
    is a no-op under Postgres, which already returns tz-aware values natively, and
    it renders identical DDL to a plain ``DateTime(timezone=True)`` column.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

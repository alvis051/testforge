from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)

    if is_sqlite:
        # SQLite ships with foreign keys off, so referential integrity would otherwise
        # go unenforced everywhere — dev and tests alike.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)

from collections.abc import Iterator

from fastapi import Header, Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_actor(x_actor: str | None = Header(default=None)) -> str:
    return x_actor or "local"

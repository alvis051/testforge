from collections.abc import Iterator

from fastapi import Header, Request
from sqlalchemy.orm import Session

from testforge.errors import AppError


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


def require_runner_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """Guard the worker-facing protocol.

    This is not authentication — S6 owns that. It keeps a stray client from claiming
    jobs meant for your workers or reporting fabricated results for one. With no token
    configured the protocol is off rather than open: there is no insecure default.
    """
    expected = request.app.state.settings.runner_token
    if not expected:
        raise AppError(
            "runner_not_configured",
            "TESTFORGE_RUNNER_TOKEN is not set, so the runner protocol is disabled",
            503,
        )
    if authorization != f"Bearer {expected}":
        raise AppError("invalid_runner_token", "runner token missing or incorrect", 401)

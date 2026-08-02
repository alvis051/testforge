from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class AppError(Exception):
    """A domain failure that maps onto the platform's stable error contract."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    def handle_app_error(_: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message, "details": error.details},
        )

    @app.exception_handler(IntegrityError)
    def handle_integrity_error(_: Request, error: IntegrityError) -> JSONResponse:
        """Map genuine unique-constraint violations to a clean 409 conflict.

        Only a unique-constraint violation is a client-facing "conflict" the client
        could plausibly retry past (e.g. with a different key). Any other
        IntegrityError -- a NOT NULL violation, a dangling foreign-key reference now
        that SQLite FK enforcement is on, etc. -- is a genuine server bug, not
        something the client can fix by retrying, so it must propagate as an
        unhandled exception (an honest 500 with a traceback) instead of being
        reported here as a clean conflict.

        SQLite's DBAPI error message for a unique-constraint violation contains the
        substring "UNIQUE constraint". If/when this project moves to Postgres, the
        equivalent check is structural rather than string-based:
        ``getattr(getattr(error, "orig", None), "sqlstate", None) == "23505"``
        (Postgres's unique-violation SQLSTATE). Not implemented here -- this project
        is SQLite-only in Slice 1 per the plan's stated scope.
        """
        if "UNIQUE constraint" not in str(getattr(error, "orig", error)):
            raise error
        return JSONResponse(
            status_code=409,
            content={
                "code": "conflict",
                "message": "the request conflicts with existing data",
                "details": {},
            },
        )

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_result_payload",
                "message": "request payload failed validation",
                "details": {"errors": jsonable(error.errors())},
            },
        )


def jsonable(errors: list[dict]) -> list[dict]:
    """Validation errors can carry exception objects in ``ctx``; make them serialisable."""
    cleaned = []
    for item in errors:
        entry = {k: v for k, v in item.items() if k != "ctx"}
        entry["loc"] = [str(part) for part in item.get("loc", ())]
        cleaned.append(entry)
    return cleaned

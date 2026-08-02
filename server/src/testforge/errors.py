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
    def handle_integrity_error(_: Request, __: IntegrityError) -> JSONResponse:
        """Catch-all for constraint violations that no service pre-empted with its own code."""
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

"""Consistent public API error handling."""

import logging
import sqlite3
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An expected API error safe to return to clients."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_body(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = []
        validation_errors = exc.errors()
        for item in validation_errors:
            location = ".".join(str(part) for part in item.get("loc", ()))
            details.append(
                {
                    "field": location or "request",
                    "issue": str(item.get("msg", "Invalid value")),
                }
            )
        error_types = {item.get("type") for item in validation_errors}
        if "language_not_supported" in error_types:
            code = "LANGUAGE_NOT_SUPPORTED"
            message = "The requested language is not supported."
        elif "invalid_language_tag" in error_types:
            code = "INVALID_LANGUAGE_TAG"
            message = "The language tag is invalid."
        else:
            code = "validation_error"
            message = "Request validation failed."
        return JSONResponse(
            status_code=422,
            content=error_body(
                code,
                message,
                {"fields": details},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "Resource not found." if exc.status_code == 404 else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
        )

    @app.exception_handler(sqlite3.Error)
    async def handle_database_error(
        _request: Request,
        exc: sqlite3.Error,
    ) -> JSONResponse:
        logger.exception("SQLite operation failed", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_body("database_error", "A database error occurred."),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        _request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception("Unexpected API error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An unexpected server error occurred."),
        )

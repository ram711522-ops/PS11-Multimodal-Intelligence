"""Health check route."""

import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.database import database_connection


router = APIRouter()


@router.get("/health", tags=["health"])
def health(request: Request) -> JSONResponse:
    """Report application and SQLite availability."""

    try:
        with database_connection(request.app.state.settings.database_path) as connection:
            connection.execute("SELECT 1").fetchone()
    except (OSError, sqlite3.Error):
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unavailable"},
        )

    return JSONResponse(
        status_code=200,
        content={"status": "ok", "database": "ok"},
    )

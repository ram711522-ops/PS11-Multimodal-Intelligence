"""Investigation lifecycle and pagination services."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID

from backend.app.database import database_connection
from backend.app.errors import ApiError
from backend.app.ids import generate_uuid
from backend.app.repositories.investigations import (
    count_investigations,
    get_investigation,
    insert_investigation,
    list_investigations,
)
from backend.app.repositories.media_assets import list_media_assets_for_investigation
from backend.app.schemas.investigations import InvestigationCreateRequest


def utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def create_investigation(
    database_path: Path | str,
    request: InvestigationCreateRequest,
) -> dict:
    investigation_id = generate_uuid()
    created_at = utc_now_iso8601()
    status = "received" if request.input_type == "text" else "awaiting_media"

    with database_connection(database_path) as connection:
        try:
            insert_investigation(
                connection,
                investigation_id=investigation_id,
                created_at=created_at,
                status=status,
                input_type=request.input_type,
                claim_text=request.claim_text,
                language=request.language,
                source_reference=request.source_reference,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

    return {
        "investigation_id": investigation_id,
        "input_type": request.input_type,
        "status": status,
        "created_at": created_at,
        "message": "Investigation created successfully.",
    }


def parse_investigation_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise ApiError(
            422,
            "invalid_uuid",
            "investigation_id must be a valid UUID.",
            {"field": "investigation_id", "issue": "Invalid UUID syntax."},
        ) from None


def retrieve_investigation(database_path: Path | str, investigation_id: str) -> dict:
    canonical_id = parse_investigation_uuid(investigation_id)
    with database_connection(database_path) as connection:
        investigation = get_investigation(connection, canonical_id)
        media = list_media_assets_for_investigation(connection, canonical_id)

    if investigation is None:
        raise ApiError(404, "not_found", "Investigation not found.")
    return {
        "investigation_id": investigation["id"],
        "input_type": investigation["input_type"],
        "status": investigation["status"],
        "claim": _claim_representation(investigation),
        "source_reference": investigation["source_reference"],
        "created_at": investigation["created_at"],
        "updated_at": investigation["updated_at"],
        "media": media,
        "latest_job": None,
        "latest_result": None,
        "failure": None,
    }


def retrieve_history(
    database_path: Path | str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    with database_connection(database_path) as connection:
        connection.execute("BEGIN")
        try:
            total = count_investigations(connection)
            rows = list_investigations(
                connection,
                limit=limit,
                offset=offset,
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise
    items = [
        {
            "investigation_id": row["id"],
            "input_type": row["input_type"],
            "status": row["status"],
            "claim": _claim_representation(row),
            "source_reference": row["source_reference"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _claim_representation(investigation: dict) -> dict | None:
    claim_text = investigation["claim_text"]
    if claim_text is None:
        return None
    return {
        "text": claim_text,
        "language": investigation["language"],
        "normalized_text": None,
    }

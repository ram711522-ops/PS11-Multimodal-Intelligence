"""Parameterized SQLite operations for persisted media assets."""

import json
import sqlite3
from typing import Any


def insert_media_asset(
    connection: sqlite3.Connection,
    *,
    media_id: str,
    investigation_id: str,
    file_reference: str,
    checksum: str,
    mime_type: str,
    size_bytes: int,
    original_filename: str,
    width: int,
    height: int,
    uploaded_at: str,
    processing_status: str = "received",
) -> None:
    """Insert media metadata; transaction ownership remains with the caller."""

    metadata_json = json.dumps(
        {
            "original_filename": original_filename,
            "width": width,
            "height": height,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    connection.execute(
        """
        INSERT INTO media_assets (
            id, investigation_id, media_type, file_reference, checksum,
            mime_type, size_bytes, metadata_json, processing_status, created_at
        ) VALUES (?, ?, 'image', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            media_id,
            investigation_id,
            file_reference,
            checksum,
            mime_type,
            size_bytes,
            metadata_json,
            processing_status,
            uploaded_at,
        ),
    )


def find_media_asset_by_investigation_and_checksum(
    connection: sqlite3.Connection,
    investigation_id: str,
    checksum: str,
) -> dict[str, Any] | None:
    """Find an existing asset with the checksum in the specified investigation."""

    row = connection.execute(
        """
        SELECT id, investigation_id, checksum
        FROM media_assets
        WHERE investigation_id = ? AND checksum = ?
        LIMIT 1
        """,
        (investigation_id, checksum),
    ).fetchone()
    return dict(row) if row is not None else None


def count_media_assets(
    connection: sqlite3.Connection,
    investigation_id: str,
) -> int:
    """Return the number of media assets attached to an investigation."""

    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM media_assets
        WHERE investigation_id = ?
        """,
        (investigation_id,),
    ).fetchone()
    return int(row["total"])


def list_media_assets_for_investigation(
    connection: sqlite3.Connection,
    investigation_id: str,
) -> list[dict[str, Any]]:
    """List public retrieval fields without returning internal paths or metadata."""

    rows = connection.execute(
        """
        SELECT id, media_type, mime_type, size_bytes, checksum,
               processing_status, created_at
        FROM media_assets
        WHERE investigation_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (investigation_id,),
    ).fetchall()
    return [dict(row) for row in rows]

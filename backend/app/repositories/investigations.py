"""Parameterized SQLite operations for investigations."""

import sqlite3
from typing import Any


def insert_investigation(
    connection: sqlite3.Connection,
    *,
    investigation_id: str,
    created_at: str,
    status: str,
    input_type: str,
    claim_text: str | None,
    language: str | None,
    source_reference: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO investigations (
            id, created_at, updated_at, status, input_type, claim_text,
            language, source_reference
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            investigation_id,
            created_at,
            created_at,
            status,
            input_type,
            claim_text,
            language,
            source_reference,
        ),
    )


def get_investigation(
    connection: sqlite3.Connection,
    investigation_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, created_at, updated_at, status, input_type, claim_text,
               language, source_reference
        FROM investigations
        WHERE id = ?
        """,
        (investigation_id,),
    ).fetchone()
    if row is None:
        return None

    return dict(row)


def list_investigations(
    connection: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT id, created_at, updated_at, status, input_type,
               claim_text, source_reference
        FROM investigations
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def count_investigations(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS total FROM investigations").fetchone()
    return int(row["total"])

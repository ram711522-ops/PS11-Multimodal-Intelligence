"""SQLite operations for the local, provenance-backed reference hash index."""

import sqlite3
from typing import Any


def insert_reference_media(
    connection: sqlite3.Connection,
    *,
    reference_id: str,
    source_reference: str,
    source_date: str,
    sha256: str,
    perceptual_hash: str,
    indexed_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO reference_media (
            reference_id, source_reference, source_date, sha256,
            perceptual_hash, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            reference_id,
            source_reference,
            source_date,
            sha256,
            perceptual_hash,
            indexed_at,
        ),
    )


def list_reference_media(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT reference_id, source_reference, source_date, sha256, perceptual_hash
        FROM reference_media
        ORDER BY source_date DESC, reference_id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def count_reference_media(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS total FROM reference_media").fetchone()
    return int(row["total"])

"""Parameterized SQLite operations for media processing runs and results."""

import sqlite3
from typing import Any


def get_media_asset_for_processing(
    connection: sqlite3.Connection,
    media_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, investigation_id, media_type, file_reference, checksum, mime_type,
               size_bytes, processing_status
        FROM media_assets
        WHERE id = ?
        """,
        (media_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_media_asset_ids_for_investigation(
    connection: sqlite3.Connection,
    investigation_id: str,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT id FROM media_assets
        WHERE investigation_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (investigation_id,),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def create_processing_run(
    connection: sqlite3.Connection,
    *,
    processing_run_id: str,
    media_id: str,
    investigation_id: str,
    media_type: str,
    processor_name: str,
    processor_version: str,
    status: str,
    created_at: str,
) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(attempt_number), 0) + 1 AS attempt_number
        FROM media_processing_runs
        WHERE media_id = ? AND processor_name = ?
        """,
        (media_id, processor_name),
    ).fetchone()
    attempt_number = int(row["attempt_number"])
    connection.execute(
        """
        INSERT INTO media_processing_runs (
            processing_run_id, media_id, investigation_id, media_type,
            processor_name, processor_version, status, attempt_number, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            processing_run_id,
            media_id,
            investigation_id,
            media_type,
            processor_name,
            processor_version,
            status,
            attempt_number,
            created_at,
        ),
    )
    return attempt_number


def mark_processing_started(
    connection: sqlite3.Connection,
    processing_run_id: str,
    started_at: str,
) -> None:
    connection.execute(
        """
        UPDATE media_processing_runs
        SET status = 'processing', started_at = ?
        WHERE processing_run_id = ?
        """,
        (started_at, processing_run_id),
    )


def skip_processing_run(
    connection: sqlite3.Connection,
    *,
    processing_run_id: str,
    completed_at: str,
    failure_code: str,
    failure_message: str,
) -> None:
    connection.execute(
        """
        UPDATE media_processing_runs
        SET status = 'skipped', completed_at = ?, failure_code = ?, failure_message = ?
        WHERE processing_run_id = ?
        """,
        (completed_at, failure_code, failure_message, processing_run_id),
    )


def complete_processing_run(
    connection: sqlite3.Connection,
    *,
    processing_run_id: str,
    processing_result_id: str,
    media_id: str,
    result_type: str,
    result_schema_version: str,
    result_json: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO media_processing_results (
            processing_result_id, processing_run_id, media_id, result_type,
            result_schema_version, result_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            processing_result_id,
            processing_run_id,
            media_id,
            result_type,
            result_schema_version,
            result_json,
            created_at,
        ),
    )
    connection.execute(
        """
        UPDATE media_processing_runs
        SET status = 'completed', completed_at = ?, failure_code = NULL,
            failure_message = NULL
        WHERE processing_run_id = ?
        """,
        (created_at, processing_run_id),
    )


def fail_processing_run(
    connection: sqlite3.Connection,
    *,
    processing_run_id: str,
    completed_at: str,
    failure_code: str,
    failure_message: str,
) -> None:
    connection.execute(
        """
        UPDATE media_processing_runs
        SET status = 'failed', completed_at = ?, failure_code = ?, failure_message = ?
        WHERE processing_run_id = ?
        """,
        (completed_at, failure_code, failure_message, processing_run_id),
    )


def get_processing_run(
    connection: sqlite3.Connection,
    processing_run_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM media_processing_runs WHERE processing_run_id = ?",
        (processing_run_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_processing_result(
    connection: sqlite3.Connection,
    processing_result_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM media_processing_results WHERE processing_result_id = ?",
        (processing_result_id,),
    ).fetchone()
    return dict(row) if row is not None else None

"""SQLite persistence for versioned Phase 4 processing jobs and results."""

from datetime import datetime, timezone
import json
import sqlite3
from uuid import uuid4

from ai_engine.media_processing import ProcessingResult

from .database import Database


def utc_now() -> str:
    """Return a UTC ISO 8601 timestamp with a stable sortable representation."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ProcessingRepository:
    """Persist job lifecycle and result records without a media-table FK."""

    def __init__(self, database: Database):
        self.database = database

    def investigation_exists(self, investigation_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        return row is not None

    def create_job(self, investigation_id: str, media_id: str) -> sqlite3.Row:
        job_id = str(uuid4())
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO processing_jobs
                   (job_id, investigation_id, media_id, processing_status, created_at)
                   VALUES (?, ?, ?, 'received', ?)""",
                (job_id, investigation_id, media_id, timestamp),
            )
            connection.execute(
                "UPDATE processing_jobs SET processing_status = 'queued' WHERE job_id = ?",
                (job_id,),
            )
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def claim_queued_job(self, job_id: str) -> sqlite3.Row | None:
        """Atomically claim queued work so concurrent callbacks do not duplicate it."""
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE processing_jobs
                   SET processing_status = 'processing', started_at = ?,
                       completed_at = NULL, attempt_count = attempt_count + 1
                   WHERE job_id = ? AND processing_status = 'queued'""",
                (timestamp, job_id),
            ).rowcount
            if updated != 1:
                return None
            return connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def mark_failed(self, job_id: str, code: str, message: str) -> sqlite3.Row | None:
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE processing_jobs SET processing_status = 'failed',
                       completed_at = ?, failure_code = ?, failure_message = ?,
                       failure_occurred_at = ?
                   WHERE job_id = ? AND processing_status = 'processing'""",
                (timestamp, code, message, timestamp, job_id),
            ).rowcount
            if updated != 1:
                return None
            return connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def complete_job(
        self, job_id: str, results: tuple[ProcessingResult, ...], attempt_number: int
    ) -> sqlite3.Row | None:
        """Store actual results and terminal success in one transaction."""
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT media_id, processing_status FROM processing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["processing_status"] != "processing":
                return None
            self._insert_results(connection, job_id, row["media_id"], results, attempt_number)
            connection.execute(
                """UPDATE processing_jobs SET processing_status = 'completed',
                       completed_at = ?, failure_code = NULL, failure_message = NULL,
                       failure_occurred_at = NULL
                   WHERE job_id = ?""",
                (timestamp, job_id),
            )
            return connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def store_partial_results(
        self, job_id: str, results: tuple[ProcessingResult, ...], attempt_number: int
    ) -> None:
        """Persist successful stages from a failed attempt without clearing prior output."""
        if not results:
            return
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT media_id, processing_status FROM processing_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["processing_status"] != "processing":
                return
            self._insert_results(connection, job_id, row["media_id"], results, attempt_number)

    @staticmethod
    def _insert_results(
        connection: sqlite3.Connection,
        job_id: str,
        media_id: str,
        results: tuple[ProcessingResult, ...],
        attempt_number: int,
    ) -> None:
        for result in results:
            if result.job_id != job_id or result.media_id != media_id:
                raise ValueError("processing result identifiers do not match the claimed job.")
            result_data_json = json.dumps(
                dict(result.result_data), ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            connection.execute(
                """INSERT INTO processing_results
                   (result_id, job_id, media_id, algorithm, algorithm_version,
                    result_type, result_data_json, created_at, attempt_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result.result_id, result.job_id, result.media_id, result.algorithm,
                 result.algorithm_version, result.result_type, result_data_json,
                 result.created_at, attempt_number),
            )

    def queue_retry(self, job_id: str) -> sqlite3.Row | None:
        """Queue only a failed job; existing successful result rows are retained."""
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE processing_jobs SET processing_status = 'queued',
                       started_at = NULL, completed_at = NULL, failure_code = NULL,
                       failure_message = NULL, failure_occurred_at = NULL
                   WHERE job_id = ? AND processing_status = 'failed'""",
                (job_id,),
            ).rowcount
            if updated != 1:
                return None
            return connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def mark_skipped(self, job_id: str) -> sqlite3.Row | None:
        """Mark unstarted work skipped; completed jobs remain terminal."""
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE processing_jobs SET processing_status = 'skipped',
                       completed_at = ?, failure_code = NULL, failure_message = NULL,
                       failure_occurred_at = NULL
                   WHERE job_id = ? AND processing_status IN ('received', 'queued')""",
                (timestamp, job_id),
            ).rowcount
            if updated != 1:
                return None
            return connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def list_results(self, job_id: str) -> list[sqlite3.Row]:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT * FROM processing_results WHERE job_id = ?
                   ORDER BY created_at ASC, result_id ASC""",
                (job_id,),
            ).fetchall()

    def list_perceptual_hash_results(self, *, exclude_media_id: str) -> list[sqlite3.Row]:
        """Read persisted unified pHash payloads as candidates for near-duplicate checks."""
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT media_id, result_data_json FROM processing_results
                   WHERE result_type = 'media_intelligence_pipeline' AND media_id != ?
                   ORDER BY created_at ASC, result_id ASC""",
                (exclude_media_id,),
            ).fetchall()

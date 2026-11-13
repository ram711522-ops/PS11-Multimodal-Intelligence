"""SQLite connection management and non-destructive schema initialization."""

import sqlite3
from pathlib import Path


class Database:
    """Persistent SQLite database used by the investigation repository."""

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create Phase 2 tables and additive Phase 4 processing tables.

        Existing investigation/media tables are never altered. Processing
        storage intentionally has no foreign key to ``media_assets`` because
        that Phase 3 table is absent from this provisional backend checkout.
        """
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id TEXT PRIMARY KEY,
                    input_type TEXT NOT NULL CHECK(input_type IN ('text', 'image', 'text_image')),
                    status TEXT NOT NULL CHECK(status IN ('received', 'awaiting_media')),
                    claim_text TEXT,
                    language TEXT,
                    source_reference TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_investigations_created_id "
                "ON investigations(created_at DESC, investigation_id DESC)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS processing_jobs (
                    job_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    media_id TEXT NOT NULL,
                    processing_status TEXT NOT NULL CHECK(processing_status IN (
                        'received', 'queued', 'processing', 'completed', 'failed', 'skipped'
                    )),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    failure_code TEXT,
                    failure_message TEXT,
                    failure_occurred_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    CHECK (
                        (failure_code IS NULL AND failure_message IS NULL AND failure_occurred_at IS NULL)
                        OR
                        (failure_code IS NOT NULL AND failure_message IS NOT NULL AND failure_occurred_at IS NOT NULL)
                    )
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_processing_jobs_investigation_created "
                "ON processing_jobs(investigation_id, created_at DESC, job_id DESC)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS processing_results (
                    result_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    media_id TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    result_type TEXT NOT NULL,
                    result_data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
                    FOREIGN KEY(job_id) REFERENCES processing_jobs(job_id) ON DELETE CASCADE
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_processing_results_job_created "
                "ON processing_results(job_id, created_at ASC, result_id ASC)"
            )

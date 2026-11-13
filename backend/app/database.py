"""SQLite connection and schema initialization helpers."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS investigations (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('awaiting_media', 'received', 'processing', 'completed', 'failed')
        ),
        input_type TEXT NOT NULL CHECK (input_type IN ('text', 'image', 'text_image')),
        claim_text TEXT,
        language TEXT,
        source_reference TEXT,
        CHECK (
            input_type = 'image'
            OR (claim_text IS NOT NULL AND length(trim(claim_text)) > 0)
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_assets (
        id TEXT PRIMARY KEY,
        investigation_id TEXT NOT NULL,
        media_type TEXT NOT NULL,
        file_reference TEXT NOT NULL,
        checksum TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        metadata_json TEXT,
        processing_status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (investigation_id)
            REFERENCES investigations (id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_processing_runs (
        processing_run_id TEXT PRIMARY KEY,
        media_id TEXT NOT NULL,
        investigation_id TEXT REFERENCES investigations (id) ON DELETE CASCADE,
        media_type TEXT,
        processor_name TEXT NOT NULL,
        processor_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('received', 'queued', 'processing', 'completed', 'failed', 'skipped')
        ),
        attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        failure_code TEXT,
        failure_message TEXT,
        FOREIGN KEY (media_id) REFERENCES media_assets (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS media_processing_results (
        processing_result_id TEXT PRIMARY KEY,
        processing_run_id TEXT NOT NULL,
        media_id TEXT NOT NULL,
        result_type TEXT NOT NULL,
        result_schema_version TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (processing_run_id)
            REFERENCES media_processing_runs (processing_run_id)
            ON DELETE CASCADE,
        FOREIGN KEY (media_id) REFERENCES media_assets (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_media (
        reference_id TEXT PRIMARY KEY,
        source_reference TEXT NOT NULL,
        source_date TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        perceptual_hash TEXT NOT NULL CHECK (length(perceptual_hash) = 16),
        indexed_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations (created_at DESC, id)",
    "CREATE INDEX IF NOT EXISTS idx_investigations_status ON investigations (status)",
    "CREATE INDEX IF NOT EXISTS idx_media_assets_investigation_id ON media_assets (investigation_id)",
    "CREATE INDEX IF NOT EXISTS idx_processing_runs_media_created ON media_processing_runs (media_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON media_processing_runs (status)",
    "CREATE INDEX IF NOT EXISTS idx_processing_results_media_created ON media_processing_results (media_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reference_media_source_date ON reference_media (source_date DESC)",
)


@contextmanager
def database_connection(database_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection and always close it on exit."""

    connection = sqlite3.connect(str(database_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
    finally:
        connection.close()


def initialize_database(database_path: Path | str) -> None:
    """Create the parent directory and initial tables/indexes idempotently."""

    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with database_connection(path) as connection:
        try:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            _ensure_processing_run_traceability_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_processing_runs_investigation_created "
                "ON media_processing_runs (investigation_id, created_at DESC)"
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise


def _ensure_processing_run_traceability_columns(
    connection: sqlite3.Connection,
) -> None:
    """Add traceability fields to pre-Stage-3 processing tables safely."""

    existing = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(media_processing_runs)").fetchall()
    }
    if "investigation_id" not in existing:
        connection.execute(
            "ALTER TABLE media_processing_runs ADD COLUMN investigation_id TEXT "
            "REFERENCES investigations (id) ON DELETE CASCADE"
        )
    if "media_type" not in existing:
        connection.execute(
            "ALTER TABLE media_processing_runs ADD COLUMN media_type TEXT"
        )
    connection.execute(
        """
        UPDATE media_processing_runs
        SET investigation_id = (
                SELECT investigation_id FROM media_assets
                WHERE media_assets.id = media_processing_runs.media_id
            ),
            media_type = (
                SELECT media_type FROM media_assets
                WHERE media_assets.id = media_processing_runs.media_id
            )
        WHERE investigation_id IS NULL OR media_type IS NULL
        """
    )

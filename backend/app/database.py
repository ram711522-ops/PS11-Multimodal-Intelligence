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
    "CREATE INDEX IF NOT EXISTS idx_investigations_created_at ON investigations (created_at DESC, id)",
    "CREATE INDEX IF NOT EXISTS idx_investigations_status ON investigations (status)",
    "CREATE INDEX IF NOT EXISTS idx_media_assets_investigation_id ON media_assets (investigation_id)",
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
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

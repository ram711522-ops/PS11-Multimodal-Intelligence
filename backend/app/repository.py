"""SQLite persistence operations for investigations."""

from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

from .database import Database
from .schemas import InvestigationCreate


def utc_now() -> str:
    """Return a lexically sortable UTC ISO 8601 timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class InvestigationRepository:
    """Database access for the Phase 2 investigation endpoints."""

    def __init__(self, database: Database):
        self.database = database

    def create(self, payload: InvestigationCreate) -> sqlite3.Row:
        investigation_id = str(uuid4())
        status = "received" if payload.input_type == "text" else "awaiting_media"
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO investigations
                   (investigation_id, input_type, status, claim_text, language,
                    source_reference, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (investigation_id, payload.input_type, status, payload.claim_text,
                 payload.language, payload.source_reference, timestamp, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM investigations WHERE investigation_id = ?", (investigation_id,)
            ).fetchone()
        return row

    def get(self, investigation_id: str) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM investigations WHERE investigation_id = ?", (investigation_id,)
            ).fetchone()

    def list(self, limit: int, offset: int) -> tuple[list[sqlite3.Row], int]:
        with self.database.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM investigations").fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM investigations
                   ORDER BY created_at DESC, investigation_id DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return rows, total

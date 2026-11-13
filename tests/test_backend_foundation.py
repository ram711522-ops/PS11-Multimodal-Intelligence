"""Automated tests for the Phase 1 backend foundation."""

from pathlib import Path
import asyncio
import sqlite3
import tempfile
import unittest
import uuid

import httpx

from backend.app.config import Settings
from backend.app.database import database_connection, initialize_database
from backend.app.ids import generate_uuid
from backend.app.main import create_app


class BackendFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        # Use a temporary directory inside the writable repository workspace.
        # The managed Python temp directory may be read-only in this environment.
        workspace_root = Path(__file__).resolve().parents[1]
        self.temp_directory = tempfile.TemporaryDirectory(dir=workspace_root)
        self.database_path = Path(self.temp_directory.name) / "phase1-test.sqlite3"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def run_asgi_request(self, app, method: str, path: str) -> httpx.Response:
        async def request_with_lifespan() -> httpx.Response:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.request(method, path)

        return asyncio.run(request_with_lifespan())

    def test_application_startup_initializes_database(self) -> None:
        app = create_app(Settings(database_path=self.database_path))

        async def start_and_stop() -> None:
            async with app.router.lifespan_context(app):
                self.assertTrue(self.database_path.exists())

        asyncio.run(start_and_stop())
        with database_connection(self.database_path) as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("investigations", tables)
        self.assertIn("media_assets", tables)

    def test_health_endpoint_reports_application_and_database(self) -> None:
        app = create_app(Settings(database_path=self.database_path))
        response = self.run_asgi_request(app, "GET", "/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_database_initialization_creates_both_tables_and_indexes(self) -> None:
        initialize_database(self.database_path)

        with database_connection(self.database_path) as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            indexes = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }

        self.assertTrue({"investigations", "media_assets"}.issubset(tables))
        self.assertIn("idx_investigations_created_at", indexes)
        self.assertIn("idx_investigations_status", indexes)
        self.assertIn("idx_media_assets_investigation_id", indexes)

    def test_foreign_key_enforcement_rejects_orphan_media(self) -> None:
        initialize_database(self.database_path)

        with database_connection(self.database_path) as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO media_assets (
                        id, investigation_id, media_type, file_reference, checksum,
                        mime_type, size_bytes, metadata_json, processing_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_uuid(),
                        generate_uuid(),
                        "image",
                        "uploads/image.jpg",
                        "sha256:example",
                        "image/jpeg",
                        10,
                        None,
                        "received",
                        "2026-11-13T00:00:00Z",
                    ),
                )

    def test_uuid_generator_returns_unique_uuid_v4_values(self) -> None:
        first = uuid.UUID(generate_uuid())
        second = uuid.UUID(generate_uuid())

        self.assertEqual(first.version, 4)
        self.assertEqual(second.version, 4)
        self.assertNotEqual(first, second)

    def test_database_connection_is_closed_after_context_exit(self) -> None:
        initialize_database(self.database_path)

        with database_connection(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_database_initialization_is_idempotent(self) -> None:
        initialize_database(self.database_path)
        initialize_database(self.database_path)

        with database_connection(self.database_path) as connection:
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('investigations', 'media_assets')"
            ).fetchone()[0]
        self.assertEqual(table_count, 2)


if __name__ == "__main__":
    unittest.main()

"""Focused tests for Phase 3 media configuration and repository foundation."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

from backend.app.config import (
    DEFAULT_MEDIA_STORAGE_PATH,
    PROJECT_ROOT,
    Settings,
)
from backend.app.database import database_connection, initialize_database
from backend.app.repositories.media_assets import (
    count_media_assets,
    find_media_asset_by_investigation_and_checksum,
    insert_media_asset,
    list_media_assets_for_investigation,
)


class MediaFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.temp_path = Path(self.temp_directory.name)
        self.database_path = self.temp_path / "media-foundation.sqlite3"
        self.storage_path = self.temp_path / "media-storage"
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_media_storage_default_and_environment_override(self) -> None:
        with patch.dict(os.environ, {"PS11_DATABASE_PATH": str(self.database_path)}, clear=True):
            default_settings = Settings.from_environment()
        self.assertEqual(default_settings.media_storage_path, DEFAULT_MEDIA_STORAGE_PATH)
        self.assertEqual(
            DEFAULT_MEDIA_STORAGE_PATH,
            PROJECT_ROOT / "data" / "media",
        )

        configured_path = self.storage_path / "custom"
        with patch.dict(
            os.environ,
            {
                "PS11_DATABASE_PATH": str(self.database_path),
                "PS11_MEDIA_STORAGE_PATH": str(configured_path),
            },
            clear=True,
        ):
            configured_settings = Settings.from_environment()
        self.assertEqual(configured_settings.media_storage_path, configured_path)

    def test_media_repository_crud_queries_and_metadata_do_not_change_schema(self) -> None:
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO investigations (
                    id, created_at, updated_at, status, input_type, claim_text,
                    language, source_reference
                ) VALUES (?, ?, ?, 'awaiting_media', 'image', NULL, NULL, NULL)
                """,
                ("investigation-a", "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z"),
            )
            connection.execute(
                """
                INSERT INTO investigations (
                    id, created_at, updated_at, status, input_type, claim_text,
                    language, source_reference
                ) VALUES (?, ?, ?, 'awaiting_media', 'image', NULL, NULL, NULL)
                """,
                ("investigation-b", "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z"),
            )
            connection.commit()
            schema_before = self._schema_snapshot(connection)

            first_media_id = str(uuid.uuid4())
            second_media_id = str(uuid.uuid4())
            self._insert_asset(
                connection,
                media_id=first_media_id,
                investigation_id="investigation-a",
                checksum="sha256:first",
                file_reference="investigation-a/first.jpg",
                original_filename="café.jpg",
                uploaded_at="2026-11-13T00:00:01Z",
            )
            self._insert_asset(
                connection,
                media_id=second_media_id,
                investigation_id="investigation-a",
                checksum="sha256:second",
                file_reference="investigation-a/second.png",
                original_filename="second.png",
                uploaded_at="2026-11-13T00:00:02Z",
            )
            connection.commit()

            found = find_media_asset_by_investigation_and_checksum(
                connection, "investigation-a", "sha256:first"
            )
            self.assertEqual(
                found,
                {
                    "id": first_media_id,
                    "investigation_id": "investigation-a",
                    "checksum": "sha256:first",
                },
            )
            self.assertIsNone(
                find_media_asset_by_investigation_and_checksum(
                    connection, "investigation-b", "sha256:first"
                )
            )
            self.assertEqual(count_media_assets(connection, "investigation-a"), 2)
            self.assertEqual(count_media_assets(connection, "investigation-b"), 0)

            listed = list_media_assets_for_investigation(connection, "investigation-a")
            self.assertEqual([asset["id"] for asset in listed], [first_media_id, second_media_id])
            self.assertEqual(
                set(listed[0]),
                {
                    "id",
                    "media_type",
                    "mime_type",
                    "size_bytes",
                    "checksum",
                    "processing_status",
                    "created_at",
                },
            )
            self.assertNotIn("file_reference", listed[0])
            self.assertNotIn("metadata_json", listed[0])
            self.assertEqual(listed[0]["processing_status"], "received")

            stored = connection.execute(
                "SELECT metadata_json FROM media_assets WHERE id = ?",
                (first_media_id,),
            ).fetchone()
            self.assertEqual(
                json.loads(stored["metadata_json"]),
                {"original_filename": "café.jpg", "width": 640, "height": 480},
            )
            self.assertEqual(self._schema_snapshot(connection), schema_before)

    def test_media_storage_path_is_ignored_by_git(self) -> None:
        candidate = "data/media/test-investigation/test-media.jpg"
        result = subprocess.run(
            ["git", "check-ignore", "-v", "--", candidate],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("/data/media/", result.stdout)

    def _insert_asset(
        self,
        connection,
        *,
        media_id: str,
        investigation_id: str,
        checksum: str,
        file_reference: str,
        original_filename: str,
        uploaded_at: str,
    ) -> None:
        insert_media_asset(
            connection,
            media_id=media_id,
            investigation_id=investigation_id,
            file_reference=file_reference,
            checksum=checksum,
            mime_type="image/jpeg",
            size_bytes=123,
            original_filename=original_filename,
            width=640,
            height=480,
            uploaded_at=uploaded_at,
        )

    @staticmethod
    def _schema_snapshot(connection) -> list[tuple]:
        return connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger', 'view')
            ORDER BY type, name
            """
        ).fetchall()


if __name__ == "__main__":
    unittest.main()

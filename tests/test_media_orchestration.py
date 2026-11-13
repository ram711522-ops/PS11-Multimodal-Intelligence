"""Integration tests for unified image/video processing orchestration."""

from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from PIL import Image

from backend.app.database import database_connection, initialize_database
from backend.app.services.media_pipeline import (
    MediaProcessingException,
    ProcessingFailure,
    process_existing_uploaded_media,
    process_investigation_media,
)
from backend.app.services.video_processor import ExtractedFrame, VideoProcessingResult


class MediaOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "orchestration.sqlite3"
        self.storage_path = self.root / "media"
        self.storage_path.mkdir()
        self.investigation_id = str(uuid.uuid4())
        initialize_database(self.database_path)
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO investigations (
                    id, created_at, updated_at, status, input_type,
                    claim_text, language, source_reference
                ) VALUES (?, ?, ?, 'received', 'image', NULL, NULL, NULL)
                """,
                (self.investigation_id, "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z"),
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_image_dispatch_persists_traceable_run_result_and_original(self) -> None:
        media_id, source, original = self._insert_image()
        outcome = process_existing_uploaded_media(
            self.database_path, self.storage_path, self.investigation_id, media_id
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.investigation_id, self.investigation_id)
        self.assertEqual(outcome.media_type, "image")
        self.assertEqual(outcome.processor_name, "ps11.image.normalizer")
        self.assertEqual(outcome.processor_version, "1.0.0")
        self.assertEqual(source.read_bytes(), original)
        artifact = self.storage_path / "processed" / outcome.result.normalized_artifact_reference
        self.assertTrue(artifact.is_file())
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT * FROM media_processing_runs WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
            persisted = connection.execute(
                "SELECT * FROM media_processing_results WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
        self.assertEqual(run["investigation_id"], self.investigation_id)
        self.assertEqual(run["media_id"], media_id)
        self.assertEqual(run["media_type"], "image")
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["started_at"])
        self.assertIsNotNone(run["completed_at"])
        result_json = json.loads(persisted["result_json"])
        self.assertEqual(result_json["investigation_id"], self.investigation_id)
        self.assertEqual(result_json["processing_run_id"], outcome.processing_run_id)
        self.assertEqual(result_json["media_type"], "image")
        self.assertEqual(result_json["normalized_artifact_reference"], artifact.name)
        self.assertNotIn(str(self.root), persisted["result_json"])

    def test_video_dispatch_persists_traceable_result_and_artifact_reference(self) -> None:
        media_id, source, original = self._insert_video()
        with patch(
            "backend.app.services.video_processor.VideoProcessor.process",
            side_effect=self._video_result,
        ) as process:
            outcome = process_existing_uploaded_media(
                self.database_path, self.storage_path, self.investigation_id, media_id,
                requested_timestamps_ms=(0,),
            )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.media_type, "video")
        self.assertEqual(outcome.processor_name, "ps11.video.frame_extractor")
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(process.call_args.args[0].processing_run_id, outcome.processing_run_id)
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT investigation_id, media_id, media_type, status FROM media_processing_runs "
                "WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
            stored = connection.execute(
                "SELECT result_json FROM media_processing_results WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
        self.assertEqual(tuple(run), (self.investigation_id, media_id, "video", "completed"))
        payload = json.loads(stored["result_json"])
        self.assertEqual(payload["frames"][0]["artifact_reference"], "frame-private.jpg")
        self.assertEqual(payload["frames"][0]["requested_timestamp_ms"], 0)
        self.assertEqual(payload["frames"][0]["actual_timestamp_ms"], 42)
        self.assertNotIn(str(self.root), stored["result_json"])

    def test_missing_media_missing_investigation_and_wrong_relationship(self) -> None:
        missing = process_existing_uploaded_media(
            self.database_path,
            self.storage_path,
            self.investigation_id,
            str(uuid.uuid4()),
        )
        self.assertEqual((missing.status, missing.failure.code), ("failed", "MEDIA_NOT_FOUND"))
        unknown_investigation = process_existing_uploaded_media(
            self.database_path,
            self.storage_path,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
        )
        self.assertEqual(
            (unknown_investigation.status, unknown_investigation.failure.code),
            ("failed", "INVESTIGATION_NOT_FOUND"),
        )
        media_id, _, _ = self._insert_image()
        wrong_parent = process_existing_uploaded_media(
            self.database_path, self.storage_path, str(uuid.uuid4()), media_id
        )
        self.assertEqual(
            (wrong_parent.status, wrong_parent.failure.code), ("failed", "INVESTIGATION_NOT_FOUND")
        )
        with database_connection(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM media_processing_runs").fetchone()[0], 0
            )

    def test_unsupported_mime_creates_a_skipped_run(self) -> None:
        media_id, _, _ = self._insert_asset(
            "image", "application/octet-stream", b"opaque", "unsupported.bin"
        )
        outcome = process_existing_uploaded_media(
            self.database_path, self.storage_path, self.investigation_id, media_id
        )
        self.assertEqual((outcome.status, outcome.failure.code), ("skipped", "UNSUPPORTED_MEDIA_TYPE"))
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT status, failure_code, investigation_id, media_type FROM media_processing_runs "
                "WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
        self.assertEqual(tuple(run), ("skipped", "UNSUPPORTED_MEDIA_TYPE", self.investigation_id, "image"))

    def test_processing_run_schema_adds_traceability_without_changing_media_assets(self) -> None:
        with database_connection(self.database_path) as connection:
            run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(media_processing_runs)")
            }
            processing_fore_keys = connection.execute(
                "PRAGMA foreign_key_list(media_processing_runs)"
            ).fetchall()
            media_schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='media_assets'"
            ).fetchone()["sql"]
        self.assertIn("investigation_id", run_columns)
        self.assertIn("media_type", run_columns)
        self.assertTrue(
            any(row["table"] == "investigations" for row in processing_fore_keys)
        )
        self.assertIn("FOREIGN KEY (investigation_id)", media_schema)
        self.assertNotIn("media_processing", media_schema)

    def test_existing_processing_runs_are_backfilled_by_additive_migration(self) -> None:
        legacy_path = self.root / "legacy-processing.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE investigations (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE media_assets (
                    id TEXT PRIMARY KEY, investigation_id TEXT NOT NULL,
                    media_type TEXT NOT NULL, file_reference TEXT NOT NULL,
                    checksum TEXT NOT NULL, mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, metadata_json TEXT,
                    processing_status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE media_processing_runs (
                    processing_run_id TEXT PRIMARY KEY, media_id TEXT NOT NULL,
                    processor_name TEXT NOT NULL, processor_version TEXT NOT NULL,
                    status TEXT NOT NULL, attempt_number INTEGER NOT NULL,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                    failure_code TEXT, failure_message TEXT
                );
                """
            )
            investigation_id = str(uuid.uuid4())
            media_id = str(uuid.uuid4())
            run_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO investigations VALUES (?, ?, ?)",
                (investigation_id, "2026-11-13T00:00:00Z", "received"),
            )
            connection.execute(
                "INSERT INTO media_assets VALUES (?, ?, 'image', 'image.png', ?, 'image/png', 1, NULL, 'received', ?)",
                (media_id, investigation_id, "a" * 64, "2026-11-13T00:00:01Z"),
            )
            connection.execute(
                "INSERT INTO media_processing_runs VALUES (?, ?, 'old.processor', '1', 'completed', 1, ?, ?, ?, NULL, NULL)",
                (run_id, media_id, "2026-11-13T00:00:02Z", "2026-11-13T00:00:02Z", "2026-11-13T00:00:03Z"),
            )
            connection.commit()

        initialize_database(legacy_path)
        with database_connection(legacy_path) as connection:
            row = connection.execute(
                "SELECT investigation_id, media_type FROM media_processing_runs WHERE processing_run_id = ?",
                (run_id,),
            ).fetchone()
        self.assertEqual(tuple(row), (investigation_id, "image"))

    def test_each_call_creates_an_independent_historical_run(self) -> None:
        media_id, _, _ = self._insert_image()
        first = process_existing_uploaded_media(
            self.database_path, self.storage_path, self.investigation_id, media_id
        )
        second = process_existing_uploaded_media(
            self.database_path, self.storage_path, self.investigation_id, media_id
        )
        self.assertNotEqual(first.processing_run_id, second.processing_run_id)
        self.assertNotEqual(
            first.result.normalized_artifact_reference,
            second.result.normalized_artifact_reference,
        )
        with database_connection(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM media_processing_runs WHERE media_id = ?", (media_id,)
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM media_processing_results WHERE media_id = ?", (media_id,)
                ).fetchone()[0],
                2,
            )

    def test_image_failure_does_not_prevent_later_video_processing(self) -> None:
        image_id, _, _ = self._insert_image(created_at="2026-11-13T00:00:01Z")
        video_id, _, _ = self._insert_video(created_at="2026-11-13T00:00:02Z")
        with (
            patch(
                "backend.app.services.image_processor.ImageProcessor.process",
                side_effect=MediaProcessingException(
                    ProcessingFailure("INVALID_IMAGE", "The image could not be decoded safely.")
                ),
            ),
            patch(
                "backend.app.services.video_processor.VideoProcessor.process",
                side_effect=self._video_result,
            ),
        ):
            outcomes = process_investigation_media(
                self.database_path, self.storage_path, self.investigation_id
            )
        self.assertEqual([item.media_id for item in outcomes], [image_id, video_id])
        self.assertEqual([item.status for item in outcomes], ["failed", "completed"])
        self.assertEqual(outcomes[0].failure.code, "INVALID_IMAGE")

    def test_video_failure_does_not_prevent_later_image_processing(self) -> None:
        self._insert_video(created_at="2026-11-13T00:00:01Z")
        image_id, _, original = self._insert_image(created_at="2026-11-13T00:00:02Z")
        with patch(
            "backend.app.services.video_processor.VideoProcessor.process",
            side_effect=MediaProcessingException(
                ProcessingFailure("VIDEO_DECODE_FAILED", "The video could not be decoded safely.")
            ),
        ):
            outcomes = process_investigation_media(
                self.database_path, self.storage_path, self.investigation_id
            )
        self.assertEqual([item.status for item in outcomes], ["failed", "completed"])
        self.assertEqual(outcomes[0].failure.code, "VIDEO_DECODE_FAILED")
        self.assertEqual(outcomes[1].media_id, image_id)
        self.assertEqual((self.storage_path / f"{image_id}.png").read_bytes(), original)

    def test_processing_persistence_failure_cleans_generated_image_artifact(self) -> None:
        media_id, _, _ = self._insert_image()
        with patch(
            "backend.app.services.media_pipeline.complete_processing_run",
            side_effect=sqlite3.OperationalError("private sqlite failure"),
        ):
            outcome = process_existing_uploaded_media(
                self.database_path, self.storage_path, self.investigation_id, media_id
            )
        self.assertEqual((outcome.status, outcome.failure.code), ("failed", "PROCESSING_PERSISTENCE_FAILED"))
        self.assertEqual(list((self.storage_path / "processed").glob("*.png")), [])
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT status, failure_code, failure_message FROM media_processing_runs "
                "WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["failure_code"], "PROCESSING_PERSISTENCE_FAILED")
        self.assertNotIn(str(self.root), run["failure_message"])

    def _insert_image(self, *, created_at: str = "2026-11-13T00:00:01Z"):
        stream = BytesIO()
        Image.new("RGB", (18, 14), color=(90, 40, 130)).save(stream, format="PNG")
        content = stream.getvalue()
        media_id = str(uuid.uuid4())
        source = self.storage_path / f"{media_id}.png"
        source.write_bytes(content)
        self._insert_row(media_id, "image", "image/png", source.name, content, created_at)
        return media_id, source, content

    def _insert_video(self, *, created_at: str = "2026-11-13T00:00:02Z"):
        content = b"unmodified video source fixture"
        media_id, source, _ = self._insert_asset(
            "video", "video/mp4", content, f"{uuid.uuid4()}.mp4", created_at=created_at
        )
        return media_id, source, content

    def _insert_asset(self, media_type: str, mime_type: str, content: bytes, filename: str,
                      *, created_at: str = "2026-11-13T00:00:01Z"):
        source = self.storage_path / filename
        source.write_bytes(content)
        media_id = str(uuid.uuid4())
        self._insert_row(media_id, media_type, mime_type, filename, content, created_at)
        return media_id, source, content

    def _insert_row(self, media_id: str, media_type: str, mime_type: str, filename: str,
                    content: bytes, created_at: str) -> None:
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, investigation_id, media_type, file_reference, checksum,
                    mime_type, size_bytes, metadata_json, processing_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'received', ?)
                """,
                (
                    media_id,
                    self.investigation_id,
                    media_type,
                    filename,
                    sha256(content).hexdigest(),
                    mime_type,
                    len(content),
                    created_at,
                ),
            )
            connection.commit()

    @staticmethod
    def _video_result(request):
        return VideoProcessingResult(
            media_id=request.media_id,
            processing_run_id=request.processing_run_id,
            container_format="mov,mp4,m4a,3gp,3g2,mj2",
            video_codec="h264",
            duration_ms=1000,
            fps=25.0,
            width=1280,
            height=720,
            source_sha256=sha256(request.storage_reference.read_bytes()).hexdigest(),
            requested_timestamps_ms=request.requested_timestamps_ms or (0,),
            frames=(
                ExtractedFrame(
                    requested_timestamp_ms=0,
                    actual_timestamp_ms=42,
                    frame_index=1,
                    width=1280,
                    height=720,
                    sha256="a" * 64,
                    artifact_reference="frame-private.jpg",
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()

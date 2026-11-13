"""Synchronous tests for the explicit image-processing foundation."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from PIL import Image

from backend.app.database import database_connection, initialize_database
from backend.app.services.media_pipeline import process_existing_uploaded_image


class ImageProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "processing.sqlite3"
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

    def test_jpeg_png_webp_normalize_rgb_and_preserve_original(self) -> None:
        for image_format, suffix in (("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp")):
            with self.subTest(image_format=image_format):
                media_id = self._insert_image(image_format, suffix)
                source_path = self.storage_path / f"{media_id}{suffix}"
                original_bytes = source_path.read_bytes()
                schema_before = self._media_assets_schema()

                outcome = process_existing_uploaded_image(
                    self.database_path, self.storage_path, media_id
                )

                self.assertEqual(outcome.status, "completed")
                self.assertIsNone(outcome.failure)
                self.assertEqual(outcome.result.source_format, image_format)
                self.assertEqual((outcome.result.width, outcome.result.height), (20, 12))
                self.assertEqual(outcome.result.normalized_color_mode, "RGB")
                self.assertEqual(outcome.result.sha256, sha256(original_bytes).hexdigest())
                self.assertEqual(source_path.read_bytes(), original_bytes)
                artifact = self.storage_path / "processed" / outcome.result.normalized_artifact_reference
                with Image.open(artifact) as normalized:
                    self.assertEqual(normalized.mode, "RGB")
                    self.assertEqual(normalized.format, "PNG")
                self.assertEqual(self._media_assets_schema(), schema_before)
                self._assert_persisted_result(outcome.processing_run_id, media_id)

    def test_exif_orientation_is_applied_without_copying_exif_personal_data(self) -> None:
        media_id = self._insert_image("JPEG", ".jpg", orientation=6)
        source_path = self.storage_path / f"{media_id}.jpg"
        original_bytes = source_path.read_bytes()
        outcome = process_existing_uploaded_image(
            self.database_path, self.storage_path, media_id
        )

        self.assertEqual(outcome.status, "completed")
        self.assertEqual((outcome.result.source_width, outcome.result.source_height), (20, 12))
        self.assertEqual((outcome.result.width, outcome.result.height), (12, 20))
        self.assertEqual(source_path.read_bytes(), original_bytes)
        artifact = self.storage_path / "processed" / outcome.result.normalized_artifact_reference
        with Image.open(artifact) as normalized:
            self.assertNotIn("exif", normalized.info)

    def test_corrupt_missing_and_checksum_mismatch_fail_without_path_disclosure(self) -> None:
        corrupt_id = self._insert_raw(b"corrupt image", "corrupt.jpg", "image/jpeg")
        corrupt = process_existing_uploaded_image(
            self.database_path, self.storage_path, corrupt_id
        )
        self.assertEqual((corrupt.status, corrupt.failure.code), ("failed", "INVALID_IMAGE"))

        missing_id = self._insert_raw(b"", "missing.jpg", "image/jpeg", create_file=False)
        missing = process_existing_uploaded_image(
            self.database_path, self.storage_path, missing_id
        )
        self.assertEqual((missing.status, missing.failure.code), ("failed", "MEDIA_FILE_NOT_FOUND"))

        mismatch_bytes = self._image_content("PNG")
        mismatch_id = self._insert_raw(
            mismatch_bytes,
            "mismatch.png",
            "image/png",
            checksum="0" * 64,
        )
        mismatch = process_existing_uploaded_image(
            self.database_path, self.storage_path, mismatch_id
        )
        self.assertEqual(
            (mismatch.status, mismatch.failure.code),
            ("failed", "SOURCE_CHECKSUM_MISMATCH"),
        )
        for outcome in (corrupt, missing, mismatch):
            self.assertNotIn(str(self.root), repr(outcome))
            with database_connection(self.database_path) as connection:
                run = connection.execute(
                    "SELECT status, failure_code, failure_message FROM media_processing_runs "
                    "WHERE processing_run_id = ?",
                    (outcome.processing_run_id,),
                ).fetchone()
            self.assertEqual(run["status"], "failed")
            self.assertNotIn(str(self.root), run["failure_message"])

    def test_unknown_media_returns_typed_failure_without_creating_run(self) -> None:
        outcome = process_existing_uploaded_image(
            self.database_path, self.storage_path, str(uuid.uuid4())
        )
        self.assertEqual((outcome.status, outcome.failure.code), ("failed", "MEDIA_NOT_FOUND"))
        self.assertIsNone(outcome.processing_run_id)

    def test_unsupported_decoded_format_is_recorded_as_failed(self) -> None:
        from io import BytesIO

        stream = BytesIO()
        Image.new("RGB", (8, 8), color=(1, 2, 3)).save(stream, format="GIF")
        media_id = self._insert_raw(stream.getvalue(), "unsupported.gif", "image/gif")
        outcome = process_existing_uploaded_image(
            self.database_path, self.storage_path, media_id
        )
        self.assertEqual(
            (outcome.status, outcome.failure.code),
            ("failed", "UNSUPPORTED_IMAGE_FORMAT"),
        )
        self.assertFalse((self.storage_path / "processed").exists())

    def test_non_image_asset_is_persisted_as_skipped(self) -> None:
        media_id = self._insert_image("PNG", ".png")
        with database_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE media_assets SET media_type = 'video' WHERE id = ?",
                (media_id,),
            )
            connection.commit()
        outcome = process_existing_uploaded_image(
            self.database_path, self.storage_path, media_id
        )
        self.assertEqual((outcome.status, outcome.failure.code), ("skipped", "UNSUPPORTED_MEDIA_TYPE"))
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT status, started_at FROM media_processing_runs "
                "WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
        self.assertEqual(run["status"], "skipped")
        self.assertIsNone(run["started_at"])

    def _insert_image(self, image_format: str, suffix: str, orientation: int | None = None) -> str:
        from io import BytesIO

        if orientation is not None:
            stream = BytesIO()
            image = Image.new("RGB", (20, 12), color=(90, 120, 150))
            exif = Image.Exif()
            exif[274] = orientation
            image.save(stream, format=image_format, exif=exif)
            image_content = stream.getvalue()
        else:
            image_content = self._image_content(image_format)
        media_id = str(uuid.uuid4())
        self._insert_raw(
            image_content,
            f"{media_id}{suffix}",
            f"image/{image_format.lower()}",
            media_id=media_id,
        )
        return media_id

    def _insert_raw(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
        *,
        create_file: bool = True,
        media_id: str | None = None,
        checksum: str | None = None,
    ) -> str:
        media_id = media_id or str(uuid.uuid4())
        if create_file:
            (self.storage_path / filename).write_bytes(content)
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, investigation_id, media_type, file_reference, checksum,
                    mime_type, size_bytes, metadata_json, processing_status, created_at
                ) VALUES (?, ?, 'image', ?, ?, ?, ?, NULL, 'received', ?)
                """,
                (
                    media_id,
                    self.investigation_id,
                    filename,
                    checksum or sha256(content).hexdigest(),
                    mime_type,
                    len(content),
                    "2026-11-13T00:00:01Z",
                ),
            )
            connection.commit()
        return media_id

    @staticmethod
    def _image_content(image_format: str) -> bytes:
        from io import BytesIO

        stream = BytesIO()
        Image.new("RGB", (20, 12), color=(90, 120, 150)).save(stream, format=image_format)
        return stream.getvalue()

    def _assert_persisted_result(self, run_id: str, media_id: str) -> None:
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT * FROM media_processing_runs WHERE processing_run_id = ?", (run_id,)
            ).fetchone()
            result = connection.execute(
                "SELECT * FROM media_processing_results WHERE processing_run_id = ?", (run_id,)
            ).fetchone()
        self.assertEqual(run["media_id"], media_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["attempt_number"], 1)
        self.assertEqual(run["processor_name"], "ps11.image.normalizer")
        self.assertEqual(run["processor_version"], "1.0.0")
        self.assertEqual(result["media_id"], media_id)
        self.assertEqual(result["result_schema_version"], "1.0")
        self.assertEqual(json.loads(result["result_json"])["normalized_color_mode"], "RGB")

    def _media_assets_schema(self) -> str:
        with database_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='media_assets'"
            ).fetchone()
        return row["sql"]


if __name__ == "__main__":
    unittest.main()

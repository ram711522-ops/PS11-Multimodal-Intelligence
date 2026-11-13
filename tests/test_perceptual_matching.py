"""Tests for genuine pHash calculation and source-attributed local matching."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from backend.app.database import database_connection, initialize_database
from backend.app.services.media_pipeline import process_existing_uploaded_media
from backend.app.services.perceptual_hash import hamming_distance, image_phash
from backend.app.services.reference_media import add_reference_media


class PerceptualMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "phash.sqlite3"
        self.storage_path = self.root / "media"
        self.storage_path.mkdir()
        initialize_database(self.database_path)
        self.investigation_id = "02f7a3b5-cd7b-44f5-a8fc-b0b6fa908a4a"
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

    def test_phash_is_deterministic_and_hamming_distance_is_real(self) -> None:
        original = self._image()
        resized = original.resize((96, 72), Image.Resampling.LANCZOS)
        first_hash = image_phash(original)
        self.assertEqual(first_hash, image_phash(original.copy()))
        self.assertLessEqual(hamming_distance(first_hash, image_phash(resized)), 4)
        self.assertEqual(hamming_distance("0000000000000000", "000000000000000f"), 4)

    def test_processing_stores_hash_and_source_attributed_reference_candidates(self) -> None:
        reference_path = self.root / "reference.png"
        self._image().save(reference_path, format="PNG")
        indexed = add_reference_media(
            self.database_path,
            reference_path,
            source_reference="https://example.org/archive/item-42",
            source_date="2024-04-12",
        )
        upload_path = self.storage_path / "source.png"
        self._image().save(upload_path, format="PNG")
        source_bytes = upload_path.read_bytes()
        media_id = "cd61dbe2-0d5d-4d1f-8c34-b8dafcbdf3c7"
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, investigation_id, media_type, file_reference, checksum,
                    mime_type, size_bytes, metadata_json, processing_status, created_at
                ) VALUES (?, ?, 'image', 'source.png', ?, 'image/png', ?, NULL, 'received', ?)
                """,
                (
                    media_id,
                    self.investigation_id,
                    sha256(source_bytes).hexdigest(),
                    len(source_bytes),
                    "2026-11-13T00:00:01Z",
                ),
            )
            connection.commit()

        outcome = process_existing_uploaded_media(
            self.database_path,
            self.storage_path,
            self.investigation_id,
            media_id,
        )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(len(outcome.result.perceptual_hash), 16)
        self.assertEqual(upload_path.read_bytes(), source_bytes)
        with database_connection(self.database_path) as connection:
            stored = connection.execute(
                "SELECT result_json FROM media_processing_results WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
            reference_rows = connection.execute(
                "SELECT COUNT(*) FROM reference_media"
            ).fetchone()[0]
        import json

        payload = json.loads(stored["result_json"])
        comparison = payload["reference_comparison"]
        self.assertEqual(comparison["status"], "candidates_found")
        self.assertEqual(comparison["matches"][0]["reference_id"], indexed["reference_id"])
        self.assertEqual(comparison["matches"][0]["source_reference"], "https://example.org/archive/item-42")
        self.assertEqual(comparison["matches"][0]["hamming_distance"], 0)
        self.assertEqual(reference_rows, 1)
        self.assertNotIn(str(self.root), stored["result_json"])

    def test_empty_reference_index_is_reported_as_empty_not_as_new_media(self) -> None:
        media_id = "e4c6bb92-7501-4e02-a008-cf86d1e54c28"
        path = self.storage_path / "empty-index.png"
        self._image().save(path, format="PNG")
        data = path.read_bytes()
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, investigation_id, media_type, file_reference, checksum,
                    mime_type, size_bytes, metadata_json, processing_status, created_at
                ) VALUES (?, ?, 'image', ?, ?, 'image/png', ?, NULL, 'received', ?)
                """,
                (
                    media_id,
                    self.investigation_id,
                    path.name,
                    sha256(data).hexdigest(),
                    len(data),
                    "2026-11-13T00:00:01Z",
                ),
            )
            connection.commit()
        outcome = process_existing_uploaded_media(
            self.database_path, self.storage_path, self.investigation_id, media_id
        )
        with database_connection(self.database_path) as connection:
            import json

            result = json.loads(
                connection.execute(
                    "SELECT result_json FROM media_processing_results WHERE processing_run_id = ?",
                    (outcome.processing_run_id,),
                ).fetchone()["result_json"]
            )
        self.assertEqual(result["reference_comparison"]["status"], "index_empty")
        self.assertIn("not evidence that the media is new", result["reference_comparison"]["interpretation"])

    def test_reference_requires_provenance_and_valid_image(self) -> None:
        path = self.root / "candidate.png"
        self._image().save(path, format="PNG")
        with self.assertRaises(ValueError):
            add_reference_media(
                self.database_path,
                path,
                source_reference="not a url",
                source_date="2024-01-01",
            )
        with self.assertRaises(ValueError):
            add_reference_media(
                self.database_path,
                path,
                source_reference="https://example.org/item",
                source_date="not-a-date",
            )

    @staticmethod
    def _image() -> Image.Image:
        image = Image.new("RGB", (128, 96), (232, 232, 232))
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 10, 54, 80), fill=(20, 30, 50))
        draw.ellipse((66, 20, 112, 67), fill=(210, 30, 25))
        draw.line((0, 90, 127, 90), fill=(20, 140, 75), width=4)
        return image


if __name__ == "__main__":
    unittest.main()

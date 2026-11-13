"""Validation tests for the backend-independent media processing contracts."""

from dataclasses import FrozenInstanceError
from hashlib import sha256
import unittest

from ai_engine.media_processing import (
    DerivedMediaReference,
    ImageMetadata,
    MediaInput,
    ProcessingFailure,
    ProcessingJob,
    ProcessingResult,
    ProcessingStatus,
)


MEDIA_ID = "7b28b64d-7a3f-4c88-a648-61e881ab15b2"
INVESTIGATION_ID = "255424f1-2e46-48b0-a457-53a5f0b26e60"
JOB_ID = "885424f1-2e46-48b0-a457-53a5f0b26e60"
RESULT_ID = "985424f1-2e46-48b0-a457-53a5f0b26e60"
DERIVED_ID = "a85424f1-2e46-48b0-a457-53a5f0b26e60"
UTC = "2026-11-13T08:15:30.123456Z"
PAYLOAD = b"image-payload-for-contract-validation"
DIGEST = sha256(PAYLOAD).hexdigest()


def metadata(**overrides) -> ImageMetadata:
    values = {
        "media_id": MEDIA_ID,
        "investigation_id": INVESTIGATION_ID,
        "mime_type": "image/jpeg",
        "width": 1920,
        "height": 1080,
        "size_bytes": len(PAYLOAD),
        "sha256": DIGEST,
        "original_filename": "photo.jpg",
        "uploaded_at": UTC,
    }
    values.update(overrides)
    return ImageMetadata(**values)


class ImageMetadataTests(unittest.TestCase):
    def test_accepts_metadata_for_each_supported_image_type(self):
        for mime_type in ("image/jpeg", "image/png", "image/webp"):
            with self.subTest(mime_type=mime_type):
                self.assertEqual(metadata(mime_type=mime_type).mime_type, mime_type)

    def test_rejects_invalid_identifiers(self):
        for field in ("media_id", "investigation_id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                metadata(**{field: "not-a-uuid"})

    def test_rejects_invalid_mime_type_and_dimensions(self):
        with self.assertRaises(ValueError):
            metadata(mime_type="image/gif")
        for field, value in (("width", 0), ("height", -1), ("size_bytes", 0), ("width", True)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                metadata(**{field: value})

    def test_rejects_invalid_checksum_and_canonicalizes_hex_case(self):
        with self.assertRaises(ValueError):
            metadata(sha256="abc")
        self.assertEqual(metadata(sha256=DIGEST.upper()).sha256, DIGEST)

    def test_rejects_blank_filename_and_invalid_or_non_utc_timestamps(self):
        with self.assertRaises(ValueError):
            metadata(original_filename="  ")
        for uploaded_at in ("not-a-time", "2026-11-13T08:15:30", "2026-11-13T08:15:30+05:30"):
            with self.subTest(uploaded_at=uploaded_at), self.assertRaises(ValueError):
                metadata(uploaded_at=uploaded_at)

    def test_contract_instances_are_immutable(self):
        item = metadata()
        with self.assertRaises(FrozenInstanceError):
            item.width = 42  # type: ignore[misc]


class MediaInputTests(unittest.TestCase):
    def test_accepts_matching_payload_and_metadata(self):
        self.assertEqual(MediaInput(metadata(), PAYLOAD).image_bytes, PAYLOAD)

    def test_rejects_empty_wrong_size_and_wrong_checksum_payloads(self):
        with self.assertRaises(ValueError):
            MediaInput(metadata(size_bytes=1), b"")
        with self.assertRaises(ValueError):
            MediaInput(metadata(size_bytes=len(PAYLOAD) + 1), PAYLOAD)
        with self.assertRaises(ValueError):
            MediaInput(metadata(sha256="0" * 64), PAYLOAD)


class ProcessingJobTests(unittest.TestCase):
    def test_accepts_all_statuses_and_string_enum_values(self):
        for status in ProcessingStatus:
            with self.subTest(status=status):
                job = ProcessingJob(JOB_ID, MEDIA_ID, INVESTIGATION_ID, status.value, UTC)
                self.assertIs(job.processing_status, status)

    def test_rejects_unknown_status_invalid_ids_and_timestamp_order(self):
        with self.assertRaises(ValueError):
            ProcessingJob(JOB_ID, MEDIA_ID, INVESTIGATION_ID, "pending", UTC)
        with self.assertRaises(ValueError):
            ProcessingJob("bad-id", MEDIA_ID, INVESTIGATION_ID, "received", UTC)
        with self.assertRaises(ValueError):
            ProcessingJob(JOB_ID, MEDIA_ID, INVESTIGATION_ID, "processing", UTC, started_at="2026-11-12T00:00:00Z")

    def test_accepts_optional_timestamps_and_structured_failure(self):
        failure = ProcessingFailure("DECODE_FAILED", "Image could not be decoded.", UTC)
        job = ProcessingJob(JOB_ID, MEDIA_ID, INVESTIGATION_ID, ProcessingStatus.FAILED,
                            UTC, UTC, UTC, failure)
        self.assertIs(job.failure, failure)


class ProcessingResultAndDerivedReferenceTests(unittest.TestCase):
    def test_processing_result_validates_ids_data_and_timestamp(self):
        result = ProcessingResult(RESULT_ID, JOB_ID, MEDIA_ID, "metadata-extractor", "1.0.0",
                                  "image_metadata", {"orientation": 1}, UTC)
        self.assertEqual(result.result_data["orientation"], 1)
        with self.assertRaises(ValueError):
            ProcessingResult("bad", JOB_ID, MEDIA_ID, "algo", "1", "type", {}, UTC)
        with self.assertRaises(TypeError):
            ProcessingResult(RESULT_ID, JOB_ID, MEDIA_ID, "algo", "1", "type", [], UTC)  # type: ignore[arg-type]

    def test_derived_media_reference_preserves_provenance_and_validates_fields(self):
        derived = DerivedMediaReference(DERIVED_ID, MEDIA_ID, "thumbnail", "1.0", "image/webp",
                                        320, 180, DIGEST.upper())
        self.assertEqual(derived.source_media_id, MEDIA_ID)
        self.assertEqual(derived.sha256, DIGEST)
        with self.assertRaises(ValueError):
            DerivedMediaReference(DERIVED_ID, "bad-id", "thumbnail", "1.0", "image/webp", 320, 180, DIGEST)
        with self.assertRaises(ValueError):
            DerivedMediaReference(DERIVED_ID, MEDIA_ID, "", "1.0", "image/webp", 320, 180, DIGEST)


if __name__ == "__main__":
    unittest.main()

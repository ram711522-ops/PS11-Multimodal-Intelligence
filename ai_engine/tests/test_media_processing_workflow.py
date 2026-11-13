"""End-to-end integration tests for the Phase 3 upload-to-AI workflow."""

from hashlib import sha256
from io import BytesIO
import unittest
from uuid import uuid4

from PIL import Image, ImageDraw

from ai_engine.media_processing import (
    MediaProcessingPipeline,
    ProcessingStatus,
    process_phase3_upload,
)


INVESTIGATION_ID = "255424f1-2e46-48b0-a457-53a5f0b26e60"
UPLOADED_AT = "2026-11-13T08:15:30.123456Z"


def upload_fixture(format_name: str, media_id: str | None = None) -> tuple[dict, bytes]:
    media_id = media_id or str(uuid4())
    image = Image.new("RGB", (96, 72), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 7, 42, 65), fill=(145, 40, 50))
    draw.ellipse((51, 18, 84, 55), fill=(20, 70, 160))
    stream = BytesIO()
    image.save(stream, format=format_name)
    content = stream.getvalue()
    mime_type, suffix = {
        "JPEG": ("image/jpeg", "jpg"),
        "PNG": ("image/png", "png"),
        "WEBP": ("image/webp", "webp"),
    }[format_name]
    return ({
        "investigation_id": INVESTIGATION_ID,
        "media_id": media_id,
        "filename": f"upload.{suffix}",
        "mime_type": mime_type,
        "size_bytes": len(content),
        "checksum": sha256(content).hexdigest(),
        "width": 96,
        "height": 72,
        "uploaded_at": UPLOADED_AT,
    }, content)


class MediaProcessingWorkflowTests(unittest.TestCase):
    def test_successful_jpeg_png_and_webp_workflows(self):
        for source_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(source_format=source_format):
                response, original = upload_fixture(source_format)
                snapshot = bytes(original)
                workflow = process_phase3_upload(response, original)
                self.assertEqual(workflow.processing_status, ProcessingStatus.COMPLETED)
                self.assertEqual(workflow.upload_metadata.media_id, response["media_id"])
                self.assertEqual(workflow.source_checksum, response["checksum"])
                self.assertIsNotNone(workflow.derived_image)
                self.assertEqual(workflow.derived_image.reference.mime_type, "image/jpeg")
                self.assertIsNotNone(workflow.perceptual_hash)
                self.assertEqual(workflow.perceptual_hash.source_media_id, response["media_id"])
                self.assertEqual(workflow.perceptual_hash.algorithm_version, "dct-64-v1")
                self.assertEqual(workflow.processing_result.job_id, workflow.job.job_id)
                self.assertEqual(workflow.processing_result.result_data["source_sha256"], response["checksum"])
                self.assertEqual(workflow.processing_result.result_data["processing_status"], "completed")
                self.assertIsNotNone(workflow.evidence_reference)
                self.assertEqual(workflow.evidence_reference.source_media_id, response["media_id"])
                self.assertEqual(original, snapshot)

    def test_identical_supplied_media_hash_is_reported_as_near_duplicate(self):
        source_response, source_bytes = upload_fixture("PNG")
        existing_response, existing_bytes = upload_fixture("PNG")
        existing = process_phase3_upload(existing_response, existing_bytes)
        result = process_phase3_upload(
            source_response,
            source_bytes,
            existing_media_hashes=[existing.perceptual_hash],
            minimum_similarity=1.0,
        )
        self.assertIsNotNone(result.near_duplicates)
        self.assertEqual(result.near_duplicates.matching_media_ids, (existing_response["media_id"],))
        match_data = result.processing_result.result_data["similarity_matches"]
        self.assertEqual(match_data[0]["similarity_score"], 1.0)

    def test_no_candidates_means_similarity_is_unavailable_not_fabricated(self):
        response, content = upload_fixture("JPEG")
        result = process_phase3_upload(response, content)
        self.assertIsNone(result.near_duplicates)
        self.assertIsNone(result.processing_result.result_data["similarity_matches"])

    def test_corrupt_image_returns_failed_job_with_safe_failure(self):
        bad = b"corrupt image payload"
        response = {
            "investigation_id": INVESTIGATION_ID,
            "media_id": str(uuid4()),
            "filename": "bad.png",
            "mime_type": "image/png",
            "size_bytes": len(bad),
            "checksum": sha256(bad).hexdigest(),
            "width": 1,
            "height": 1,
            "uploaded_at": UPLOADED_AT,
        }
        result = process_phase3_upload(response, bad)
        self.assertEqual(result.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(result.failures[0].code, "INVALID_IMAGE")
        self.assertNotIn("corrupt image payload", result.failures[0].message)
        self.assertIsNone(result.perceptual_hash)
        self.assertEqual(result.processing_result.result_data["failures"][0]["code"], "INVALID_IMAGE")

    def test_unsupported_content_is_reported_without_trusting_declared_mime(self):
        stream = BytesIO()
        Image.new("RGB", (8, 8), "red").save(stream, format="GIF")
        content = stream.getvalue()
        response = {
            "investigation_id": INVESTIGATION_ID,
            "media_id": str(uuid4()),
            "filename": "looks-like-jpeg.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": len(content),
            "checksum": sha256(content).hexdigest(),
            "width": 8,
            "height": 8,
            "uploaded_at": UPLOADED_AT,
        }
        result = process_phase3_upload(response, content)
        self.assertEqual(result.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(result.failures[0].code, "UNSUPPORTED_MEDIA_TYPE")

    def test_adapter_rejects_checksum_mismatch_before_processing(self):
        response, content = upload_fixture("PNG")
        changed = content + b"unexpected"
        with self.assertRaises(ValueError):
            process_phase3_upload(response, changed)

    def test_retries_are_explicit_and_preserve_attempt_history(self):
        response, content = upload_fixture("WEBP")
        attempts = 0

        def flaky_normalizer(value):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("private filesystem path")
            from ai_engine.media_processing import preprocess_image
            return preprocess_image(value)

        pipeline = MediaProcessingPipeline(normalizer=flaky_normalizer)
        job_id = str(uuid4())
        first = process_phase3_upload(response, content, job_id=job_id, pipeline=pipeline)
        self.assertEqual(first.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(first.failures[0].code, "PREPROCESSING_FAILED")
        second_without_retry = process_phase3_upload(response, content, job_id=job_id, pipeline=pipeline)
        self.assertEqual(second_without_retry.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(attempts, 1)
        retried = process_phase3_upload(
            response, content, job_id=job_id, retry_failed=True, pipeline=pipeline
        )
        self.assertEqual(retried.processing_status, ProcessingStatus.COMPLETED)
        self.assertEqual([attempt.attempt_number for attempt in retried.attempts], [1, 2])
        self.assertEqual(attempts, 2)

    def test_existing_hash_inputs_are_validated(self):
        response, content = upload_fixture("PNG")
        invalid = object()
        with self.assertRaises(TypeError):
            process_phase3_upload(response, content, existing_media_hashes=[invalid])


if __name__ == "__main__":
    unittest.main()

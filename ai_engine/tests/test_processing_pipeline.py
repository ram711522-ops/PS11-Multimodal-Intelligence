"""Integration tests for orchestration, provenance, retries and adapters."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import time
import unittest
from uuid import uuid4

from PIL import Image

from ai_engine.media_processing import (
    DerivedMediaReference,
    ExtractedVideoFrame,
    ImageMetadata,
    MediaEvidenceReference,
    MediaInput,
    MediaProcessingPipeline,
    MediaPerceptualHash,
    Phase3AdapterError,
    ProcessingStatus,
    VideoInput,
    VideoSamplingConfig,
    media_input_from_phase3_upload,
    validate_extracted_frames,
    verify_claim_with_media_references,
)
from ai_engine.verification import Claim, Evidence, EvidenceStance, Verdict


MEDIA_ID = "7b28b64d-7a3f-4c88-a648-61e881ab15b2"
INVESTIGATION_ID = "255424f1-2e46-48b0-a457-53a5f0b26e60"
UTC = "2026-11-13T08:15:30.123456Z"


def source_media(media_id=MEDIA_ID):
    buffer = BytesIO()
    Image.new("RGB", (40, 32), (80, 120, 160)).save(buffer, format="PNG")
    data = buffer.getvalue()
    metadata = ImageMetadata(
        media_id=media_id,
        investigation_id=INVESTIGATION_ID,
        mime_type="image/png",
        width=40,
        height=32,
        size_bytes=len(data),
        sha256=sha256(data).hexdigest(),
        original_filename="origin.png",
        uploaded_at=UTC,
    )
    return MediaInput(metadata, data)


def phase3_response(value):
    return {
        "investigation_id": value.metadata.investigation_id,
        "media_id": value.metadata.media_id,
        "filename": value.metadata.original_filename,
        "mime_type": value.metadata.mime_type,
        "size_bytes": value.metadata.size_bytes,
        "checksum": value.metadata.sha256,
        "width": value.metadata.width,
        "height": value.metadata.height,
        "uploaded_at": value.metadata.uploaded_at,
    }


class ProcessingPipelineTests(unittest.TestCase):
    def test_success_tracks_lifecycle_and_structured_results(self):
        value = source_media()
        job_id = str(uuid4())
        pipeline = MediaProcessingPipeline()
        execution = pipeline.process(value, job_id=job_id)
        self.assertEqual(execution.job.processing_status, ProcessingStatus.COMPLETED)
        self.assertEqual(execution.job.job_id, job_id)
        self.assertEqual(execution.job.media_id, MEDIA_ID)
        self.assertEqual(execution.job.investigation_id, INVESTIGATION_ID)
        self.assertEqual(len(execution.results), 2)
        self.assertTrue(all(result.job_id == job_id for result in execution.results))
        self.assertEqual(execution.results[0].result_data["source_sha256"], value.metadata.sha256)
        self.assertEqual(execution.results[1].result_type, "perceptual_hash")
        self.assertIsNotNone(execution.derived_media)
        self.assertIsNotNone(execution.perceptual_hash)
        self.assertEqual(pipeline.get_job(job_id), execution.job)

    def test_failed_normalization_can_be_retried_and_history_is_retained(self):
        calls = 0

        def flaky_normalizer(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("private path detail")
            from ai_engine.media_processing import preprocess_image
            return preprocess_image(value)

        pipeline = MediaProcessingPipeline(normalizer=flaky_normalizer)
        value = source_media()
        job_id = str(uuid4())
        first = pipeline.process(value, job_id=job_id)
        self.assertEqual(first.job.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(first.job.failure.code, "PREPROCESSING_FAILED")
        self.assertNotIn("private path detail", first.job.failure.message)
        self.assertIsNone(first.perceptual_hash)
        second = pipeline.process(value, job_id=job_id, retry_failed=True)
        self.assertEqual(second.job.processing_status, ProcessingStatus.COMPLETED)
        self.assertEqual(len(second.attempts), 2)
        self.assertEqual(second.attempts[0].status, ProcessingStatus.FAILED)
        self.assertEqual(second.attempts[1].status, ProcessingStatus.COMPLETED)
        self.assertEqual(len(pipeline.process(value, job_id=job_id).attempts), 2)

    def test_independent_phash_failure_preserves_normalized_artifact(self):
        def failed_hasher(_normalized):
            raise RuntimeError("private processing detail")

        result = MediaProcessingPipeline(hasher=failed_hasher).process(source_media())
        self.assertEqual(result.job.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(result.job.failure.code, "PHASH_FAILED")
        self.assertIsNotNone(result.derived_media)
        self.assertIsNone(result.perceptual_hash)
        self.assertEqual([entry.result_type for entry in result.results], ["normalized_image"])

    def test_duplicate_job_execution_returns_cached_result(self):
        calls = 0

        def counting_normalizer(value):
            nonlocal calls
            calls += 1
            from ai_engine.media_processing import preprocess_image
            return preprocess_image(value)

        pipeline = MediaProcessingPipeline(normalizer=counting_normalizer)
        value = source_media()
        job_id = str(uuid4())
        first = pipeline.process(value, job_id=job_id)
        duplicate = pipeline.process(value, job_id=job_id)
        self.assertIs(first, duplicate)
        self.assertEqual(calls, 1)

    def test_concurrent_duplicate_job_execution_is_serialized(self):
        calls = 0

        def counting_normalizer(value):
            nonlocal calls
            calls += 1
            time.sleep(0.02)
            from ai_engine.media_processing import preprocess_image
            return preprocess_image(value)

        pipeline = MediaProcessingPipeline(normalizer=counting_normalizer)
        value = source_media()
        job_id = str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as executor:
            executions = list(executor.map(lambda _: pipeline.process(value, job_id=job_id), range(4)))
        self.assertEqual(calls, 1)
        self.assertTrue(all(execution is executions[0] for execution in executions))

    def test_job_id_cannot_be_reused_for_different_media(self):
        pipeline = MediaProcessingPipeline()
        job_id = str(uuid4())
        pipeline.process(source_media(), job_id=job_id)
        with self.assertRaises(ValueError):
            pipeline.process(source_media(str(uuid4())), job_id=job_id)

    def test_skip_records_skipped_status_without_results(self):
        execution = MediaProcessingPipeline().skip(source_media())
        self.assertEqual(execution.job.processing_status, ProcessingStatus.SKIPPED)
        self.assertFalse(execution.results)


class EvidenceAndAdapterTests(unittest.TestCase):
    def test_evidence_reference_links_source_hash_p_hash_and_derived_artifact(self):
        value = source_media()
        execution = MediaProcessingPipeline().process(value)
        reference = MediaEvidenceReference.from_processed_media(
            value.metadata, execution.derived_media, execution.perceptual_hash
        )
        self.assertEqual(reference.source_media_id, MEDIA_ID)
        self.assertEqual(reference.source_sha256, value.metadata.sha256)
        self.assertEqual(reference.derived_media_id, execution.derived_media.derived_media_id)
        self.assertEqual(reference.perceptual_hash, execution.perceptual_hash.hash_value)
        # Provenance alone is not supplied as stance-bearing evidence.
        verification = verify_claim_with_media_references("A claim", [], (reference,))
        self.assertEqual(verification.verification.verdict, Verdict.UNVERIFIED)
        self.assertEqual(verification.media_references, (reference,))

    def test_verifier_uses_only_explicit_supplied_evidence(self):
        value = source_media()
        execution = MediaProcessingPipeline().process(value)
        reference = MediaEvidenceReference.from_processed_media(
            value.metadata, execution.derived_media, execution.perceptual_hash
        )
        assessed = Evidence("Publisher", "https://example.test/source", EvidenceStance.SUPPORT, 1, 1)
        result = verify_claim_with_media_references(Claim("Claim"), [assessed], (reference,))
        self.assertEqual(result.verification.verdict, Verdict.VERIFIED)
        self.assertEqual(result.verification.distinct_sources, 1)

    def test_phase3_upload_adapter_accepts_only_confirmed_shape(self):
        original = source_media()
        response = phase3_response(original)
        adapted = media_input_from_phase3_upload(response, original.image_bytes)
        self.assertEqual(adapted.metadata.media_id, MEDIA_ID)
        self.assertEqual(adapted.image_bytes, original.image_bytes)
        with self.assertRaises(Phase3AdapterError):
            media_input_from_phase3_upload({**response, "processing_status": "received"}, original.image_bytes)
        with self.assertRaises(Phase3AdapterError):
            media_input_from_phase3_upload(response, b"different bytes")


class VideoContractTests(unittest.TestCase):
    def test_video_sampling_configuration_and_frame_provenance_validation(self):
        video_bytes = b"video"
        video = VideoInput(
            MEDIA_ID, INVESTIGATION_ID, "video/mp4", len(video_bytes), sha256(video_bytes).hexdigest(), video_bytes
        )
        config = VideoSamplingConfig(interval_ms=1000, max_frames=2)
        frame_bytes = b"frame"
        frames = [
            ExtractedVideoFrame(
                MEDIA_ID, str(uuid4()), index, index * 1000, "image/jpeg", 20, 10,
                sha256(frame_bytes).hexdigest(), frame_bytes, {"source_time_base": "1/1000"},
            )
            for index in range(2)
        ]
        self.assertEqual(validate_extracted_frames(video, config, frames), tuple(frames))
        with self.assertRaises(ValueError):
            validate_extracted_frames(video, VideoSamplingConfig(max_frames=1), frames)
        off_interval = [frames[0], ExtractedVideoFrame(
            MEDIA_ID, str(uuid4()), 1, 500, "image/jpeg", 20, 10,
            sha256(frame_bytes).hexdigest(), frame_bytes, {},
        )]
        with self.assertRaises(ValueError):
            validate_extracted_frames(video, config, off_interval)


if __name__ == "__main__":
    unittest.main()

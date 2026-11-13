"""Tests for proposed processing API schemas and the unavailable adapter port."""

import unittest
from uuid import uuid4

from ai_engine.media_processing import (
    CreateProcessingJobRequest,
    ProcessingApiUnavailable,
    ProcessingFailure,
    ProcessingJob,
    ProcessingJobResponse,
    ProcessingResultsResponse,
    ProcessingResult,
    ProcessingStatus,
    RetryProcessingJobRequest,
    UnavailableProcessingApiAdapter,
)


NOW = "2026-11-13T09:30:00Z"


def make_job(*, status=ProcessingStatus.QUEUED, failure=None):
    return ProcessingJob(
        job_id=str(uuid4()),
        media_id=str(uuid4()),
        investigation_id=str(uuid4()),
        processing_status=status,
        created_at=NOW,
        started_at=NOW if status in {ProcessingStatus.PROCESSING, ProcessingStatus.COMPLETED, ProcessingStatus.FAILED} else None,
        completed_at=NOW if status in {ProcessingStatus.COMPLETED, ProcessingStatus.FAILED, ProcessingStatus.SKIPPED} else None,
        failure=failure,
    )


class ProcessingApiSchemaTests(unittest.TestCase):
    def test_create_request_validates_stable_uuid_identifiers(self):
        request = CreateProcessingJobRequest(str(uuid4()), str(uuid4()))
        self.assertTrue(request.investigation_id)
        with self.assertRaises(ValueError):
            CreateProcessingJobRequest("not-a-uuid", str(uuid4()))

    def test_job_response_wraps_valid_status_and_failure(self):
        failure = ProcessingFailure("INVALID_IMAGE", "Image could not be validated.", NOW)
        response = ProcessingJobResponse(
            make_job(status=ProcessingStatus.FAILED, failure=failure)
        )
        self.assertEqual(response.api_version, "1.0")
        self.assertEqual(response.job.processing_status, ProcessingStatus.FAILED)
        self.assertEqual(response.job.failure.code, "INVALID_IMAGE")

    def test_job_response_rejects_wrong_version_or_type(self):
        with self.assertRaises(ValueError):
            ProcessingJobResponse(make_job(), api_version="2.0")
        with self.assertRaises(TypeError):
            ProcessingJobResponse(job=object())

    def test_result_response_validates_job_linkage_and_total(self):
        job = make_job()
        result = ProcessingResult(
            result_id=str(uuid4()),
            job_id=job.job_id,
            media_id=job.media_id,
            algorithm="phash",
            algorithm_version="dct-64-v1",
            result_type="perceptual_hash",
            result_data={"hash": "0123456789abcdef"},
            created_at=NOW,
        )
        response = ProcessingResultsResponse(job.job_id, (result,), 1)
        self.assertEqual(response.items, (result,))
        self.assertEqual(response.total, 1)
        with self.assertRaises(ValueError):
            ProcessingResultsResponse(str(uuid4()), (result,), 1)
        with self.assertRaises(ValueError):
            ProcessingResultsResponse(job.job_id, (result,), 0)

    def test_retry_request_requires_explicit_true(self):
        self.assertTrue(RetryProcessingJobRequest(str(uuid4())).retry_failed)
        with self.assertRaises(ValueError):
            RetryProcessingJobRequest(str(uuid4()), retry_failed=False)
        with self.assertRaises(ValueError):
            RetryProcessingJobRequest("bad-id")


class UnavailableProcessingApiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = UnavailableProcessingApiAdapter()

    def test_adapter_fails_explicitly_instead_of_fabricating_work(self):
        create = CreateProcessingJobRequest(str(uuid4()), str(uuid4()))
        retry = RetryProcessingJobRequest(str(uuid4()))
        calls = (
            lambda: self.adapter.create_job(create),
            lambda: self.adapter.get_job(str(uuid4())),
            lambda: self.adapter.get_results(str(uuid4())),
            lambda: self.adapter.retry_job(retry),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(ProcessingApiUnavailable, "not implemented"):
                    call()


if __name__ == "__main__":
    unittest.main()

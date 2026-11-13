"""Coordinate persistent jobs with the backend-independent AI workflow."""

import json
import logging
from uuid import UUID

from ai_engine.media_processing import (
    MediaPerceptualHash,
    MediaProcessingPipeline,
    PHASH_ALGORITHM,
    PHASH_VERSION,
    ProcessingFailure,
    ProcessingJob,
    ProcessingResult,
    ProcessingStatus,
    process_phase3_upload,
)

from .media_storage import MediaSourceStorage, MediaSourceUnavailable
from .processing_repository import ProcessingRepository
from .service import ApiProblem


logger = logging.getLogger(__name__)


class ProcessingService:
    """Validate, persist, execute, and retrieve processing jobs/results."""

    def __init__(
        self,
        repository: ProcessingRepository,
        media_storage: MediaSourceStorage,
        pipeline: MediaProcessingPipeline | None = None,
    ):
        self.repository = repository
        self.media_storage = media_storage
        self.pipeline = pipeline or MediaProcessingPipeline()

    def create_job(self, investigation_id: str, media_id: str) -> ProcessingJob:
        investigation_id = _normalize_uuid("investigation_id", investigation_id)
        media_id = _normalize_uuid("media_id", media_id)
        if not self.repository.investigation_exists(investigation_id):
            raise ApiProblem(404, "INVESTIGATION_NOT_FOUND", "Investigation was not found.")
        if getattr(self.media_storage, "available", True) is False:
            raise ApiProblem(
                503,
                "PROCESSING_API_UNAVAILABLE",
                "Processing cannot accept work because media storage is not configured.",
            )
        row = self.repository.create_job(investigation_id, media_id)
        return _job_from_row(row)

    def get_job(self, job_id: str) -> ProcessingJob:
        job_id = _normalize_uuid("job_id", job_id)
        row = self.repository.get_job(job_id)
        if row is None:
            raise ApiProblem(404, "PROCESSING_JOB_NOT_FOUND", "Processing job was not found.")
        return _job_from_row(row)

    def get_results(self, job_id: str) -> tuple[list[ProcessingResult], int]:
        job_id = _normalize_uuid("job_id", job_id)
        if self.repository.get_job(job_id) is None:
            raise ApiProblem(404, "PROCESSING_JOB_NOT_FOUND", "Processing job was not found.")
        rows = self.repository.list_results(job_id)
        results = [_result_from_row(row) for row in rows]
        return results, len(results)

    def retry_job(self, job_id: str, *, retry_failed: bool) -> ProcessingJob:
        job_id = _normalize_uuid("job_id", job_id)
        if retry_failed is not True:
            raise ApiProblem(422, "VALIDATION_ERROR", "retry_failed must be true.")
        if self.repository.get_job(job_id) is None:
            raise ApiProblem(404, "PROCESSING_JOB_NOT_FOUND", "Processing job was not found.")
        row = self.repository.queue_retry(job_id)
        if row is None:
            raise ApiProblem(
                409,
                "PROCESSING_RETRY_NOT_ALLOWED",
                "Only failed processing jobs can be retried.",
            )
        return _job_from_row(row)

    def skip_job(self, job_id: str) -> ProcessingJob:
        """Persist an internal skip transition for work that has not started.

        v1.0 intentionally does not expose a skip HTTP endpoint; this method
        supports worker/policy integrations that need the shared lifecycle.
        """
        job_id = _normalize_uuid("job_id", job_id)
        if self.repository.get_job(job_id) is None:
            raise ApiProblem(404, "PROCESSING_JOB_NOT_FOUND", "Processing job was not found.")
        row = self.repository.mark_skipped(job_id)
        if row is None:
            raise ApiProblem(409, "PROCESSING_STATE_CONFLICT", "Started processing work cannot be skipped.")
        return _job_from_row(row)

    def execute(self, job_id: str) -> None:
        """Run one queued attempt; failures are stored without leaking internals."""
        try:
            job_id = _normalize_uuid("job_id", job_id)
            row = self.repository.claim_queued_job(job_id)
            if row is None:
                return
            attempt_number = row["attempt_count"]
            investigation_id = row["investigation_id"]
            media_id = row["media_id"]

            try:
                stored = self.media_storage.load_media(investigation_id, media_id)
            except MediaSourceUnavailable:
                self._fail(job_id, "MEDIA_SOURCE_UNAVAILABLE", "Original media storage is unavailable.")
                return
            except Exception:
                logger.exception("Unexpected media source adapter failure for job %s", job_id)
                self._fail(job_id, "MEDIA_SOURCE_UNAVAILABLE", "Original media could not be loaded.")
                return

            if (
                stored.upload_response.get("investigation_id") != investigation_id
                or stored.upload_response.get("media_id") != media_id
            ):
                self._fail(job_id, "MEDIA_SOURCE_UNAVAILABLE", "Stored media identifiers did not match the job.")
                return

            try:
                workflow_result = process_phase3_upload(
                    stored.upload_response,
                    stored.original_image_bytes,
                    existing_media_hashes=self._existing_hashes(exclude_media_id=media_id) or None,
                    job_id=job_id,
                    retry_failed=attempt_number > 1,
                    pipeline=self.pipeline,
                )
            except (TypeError, ValueError):
                self._fail(job_id, "PROCESSING_INPUT_INVALID", "Stored media metadata could not be validated.")
                return
            except Exception:
                logger.exception("Unexpected AI processing failure for job %s", job_id)
                self._fail(job_id, "PROCESSING_FAILED", "Media processing could not be completed.")
                return

            if workflow_result.processing_status is not ProcessingStatus.COMPLETED:
                failure = workflow_result.failures[0] if workflow_result.failures else ProcessingFailure(
                    "PROCESSING_FAILED",
                    "Media processing could not be completed.",
                    workflow_result.job.completed_at or workflow_result.job.created_at,
                )
                execution = self.pipeline.get_execution(job_id)
                if execution is not None and execution.results:
                    try:
                        self.repository.store_partial_results(
                            job_id, execution.results, attempt_number
                        )
                    except Exception:
                        logger.exception("Could not persist successful stages for failed job %s", job_id)
                        self._fail(
                            job_id,
                            "PROCESSING_PERSISTENCE_FAILED",
                            "Processing output could not be saved.",
                        )
                        return
                self._fail(job_id, failure.code, failure.message)
                return

            try:
                execution = self.pipeline.get_execution(job_id)
                successful_results = execution.results if execution is not None else ()
                all_results = (*successful_results, workflow_result.processing_result)
                completed = self.repository.complete_job(
                    job_id, all_results, attempt_number
                )
                if completed is None:
                    logger.warning("Processing job %s was no longer in processing state at completion", job_id)
            except Exception:
                logger.exception("Could not persist processing result for job %s", job_id)
                self._fail(job_id, "PROCESSING_PERSISTENCE_FAILED", "Processing output could not be saved.")
        except Exception:
            logger.exception("Unexpected processing worker error")
            try:
                self._fail(job_id, "PROCESSING_FAILED", "Media processing could not be completed.")
            except Exception:
                logger.exception("Could not persist processing failure state")

    def _fail(self, job_id: str, code: str, message: str) -> None:
        self.repository.mark_failed(job_id, code, message)

    def _existing_hashes(self, *, exclude_media_id: str) -> list[MediaPerceptualHash]:
        hashes: list[MediaPerceptualHash] = []
        for row in self.repository.list_perceptual_hash_results(exclude_media_id=exclude_media_id):
            try:
                data = json.loads(row["result_data_json"])
                phash = data.get("perceptual_hash")
                if not isinstance(phash, dict):
                    continue
                if (phash.get("algorithm"), phash.get("algorithm_version")) != (
                    PHASH_ALGORITHM, PHASH_VERSION
                ):
                    continue
                hashes.append(MediaPerceptualHash(
                    source_media_id=row["media_id"],
                    derived_media_id=phash["derived_media_id"],
                    hash_value=phash["value"],
                    algorithm=phash["algorithm"],
                    algorithm_version=phash["algorithm_version"],
                ))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                # Ignore malformed historical payloads; do not turn them into matches.
                continue
        return hashes


def _normalize_uuid(name: str, value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ApiProblem(400, "INVALID_PROCESSING_REQUEST", f"{name} must be a UUID.") from exc


def _job_from_row(row) -> ProcessingJob:
    failure = None
    if row["failure_code"] is not None:
        failure = ProcessingFailure(
            code=row["failure_code"],
            message=row["failure_message"],
            occurred_at=row["failure_occurred_at"],
        )
    return ProcessingJob(
        job_id=row["job_id"],
        investigation_id=row["investigation_id"],
        media_id=row["media_id"],
        processing_status=ProcessingStatus(row["processing_status"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        failure=failure,
    )


def _result_from_row(row) -> ProcessingResult:
    return ProcessingResult(
        result_id=row["result_id"],
        job_id=row["job_id"],
        media_id=row["media_id"],
        algorithm=row["algorithm"],
        algorithm_version=row["algorithm_version"],
        result_type=row["result_type"],
        result_data=json.loads(row["result_data_json"]),
        created_at=row["created_at"],
    )

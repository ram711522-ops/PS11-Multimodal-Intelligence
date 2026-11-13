"""In-process orchestration for the current image media intelligence stages."""

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable
from uuid import UUID, uuid4

from .models import (
    DerivedMediaReference,
    MediaInput,
    ProcessingFailure,
    ProcessingJob,
    ProcessingResult,
    ProcessingStatus,
)
from .perceptual_hash import MediaPerceptualHash, compute_phash
from .preprocessing import (
    AnimatedImageError,
    ImageDimensionsExceededError,
    InvalidImageError,
    PreprocessedImage,
    UnsupportedImageFormatError,
    preprocess_image,
)


@dataclass(frozen=True)
class ProcessingAttempt:
    """Outcome of one attempt, including independently recorded stage errors."""

    attempt_number: int
    status: ProcessingStatus
    started_at: str
    completed_at: str
    failures: tuple[ProcessingFailure, ...]
    result_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProcessingExecution:
    """Latest job state and outputs, with prior retry history preserved."""

    job: ProcessingJob
    attempts: tuple[ProcessingAttempt, ...]
    derived_media: DerivedMediaReference | None = None
    perceptual_hash: MediaPerceptualHash | None = None
    results: tuple[ProcessingResult, ...] = ()
    normalized_image: PreprocessedImage | None = None


class MediaProcessingPipeline:
    """Run normalization and pHash once per job ID within this process.

    This is an in-process execution coordinator, not a durable task queue. The
    injected functions make the independent stages testable and allow a later
    worker implementation to replace them without changing media contracts.
    """

    def __init__(
        self,
        *,
        normalizer: Callable[[MediaInput], PreprocessedImage] = preprocess_image,
        hasher: Callable[[PreprocessedImage], MediaPerceptualHash] = compute_phash,
    ) -> None:
        self._normalizer = normalizer
        self._hasher = hasher
        self._state_lock = Lock()
        self._job_locks: dict[str, Lock] = {}
        self._executions: dict[str, ProcessingExecution] = {}
        self._current_jobs: dict[str, ProcessingJob] = {}

    def get_execution(self, job_id: str) -> ProcessingExecution | None:
        """Return the last completed attempt for a known in-process job."""
        _validate_job_id(job_id)
        with self._state_lock:
            return self._executions.get(job_id)

    def get_job(self, job_id: str) -> ProcessingJob | None:
        """Return the latest lifecycle snapshot, including an active job."""
        _validate_job_id(job_id)
        with self._state_lock:
            return self._current_jobs.get(job_id)

    def process(
        self,
        media_input: MediaInput,
        *,
        job_id: str | None = None,
        retry_failed: bool = False,
    ) -> ProcessingExecution:
        """Run preprocessing and pHash, reusing completed job outputs.

        Repeating a completed job ID returns its cached result. A failed job is
        retried only when ``retry_failed=True``. Calls sharing a job ID serialize
        on a per-job lock, preventing concurrent duplicate execution in process.
        """
        if not isinstance(media_input, MediaInput):
            raise TypeError("media_input must be a MediaInput instance.")
        resolved_job_id = job_id or str(uuid4())
        _validate_job_id(resolved_job_id)

        with self._state_lock:
            lock = self._job_locks.setdefault(resolved_job_id, Lock())
        with lock:
            previous = self._executions.get(resolved_job_id)
            if previous is not None:
                if previous.job.media_id != media_input.metadata.media_id:
                    raise ValueError("A processing job ID cannot be reused for another media item.")
                if previous.job.processing_status is not ProcessingStatus.FAILED or not retry_failed:
                    return previous

            created_at = previous.job.created_at if previous else _utc_now()
            attempt_number = len(previous.attempts) + 1 if previous else 1
            started_at = _utc_now()
            job = ProcessingJob(
                job_id=resolved_job_id,
                media_id=media_input.metadata.media_id,
                investigation_id=media_input.metadata.investigation_id,
                processing_status=ProcessingStatus.RECEIVED,
                created_at=created_at,
            )
            self._publish_job(job)
            job = _job_with_status(job, ProcessingStatus.QUEUED)
            self._publish_job(job)
            job = _job_with_status(job, ProcessingStatus.PROCESSING, started_at=started_at)
            self._publish_job(job)

            failures: list[ProcessingFailure] = []
            results: list[ProcessingResult] = []
            normalized: PreprocessedImage | None = None
            perceptual: MediaPerceptualHash | None = None

            try:
                normalized = self._normalizer(media_input)
                results.append(_preprocessing_result(resolved_job_id, media_input, normalized))
            except Exception as exc:
                code, message = _preprocessing_failure(exc)
                failures.append(_stage_failure(code, message))

            if normalized is not None:
                try:
                    perceptual = self._hasher(normalized)
                    results.append(_phash_result(resolved_job_id, media_input, normalized, perceptual))
                except Exception:
                    failures.append(_stage_failure("PHASH_FAILED", "Perceptual hash generation failed."))

            completed_at = _utc_now()
            status = ProcessingStatus.FAILED if failures else ProcessingStatus.COMPLETED
            failure = failures[0] if failures else None
            job = ProcessingJob(
                job_id=resolved_job_id,
                media_id=media_input.metadata.media_id,
                investigation_id=media_input.metadata.investigation_id,
                processing_status=status,
                created_at=created_at,
                started_at=started_at,
                completed_at=completed_at,
                failure=failure,
            )
            attempt = ProcessingAttempt(
                attempt_number=attempt_number,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                failures=tuple(failures),
                result_ids=tuple(result.result_id for result in results),
            )
            attempts = (previous.attempts if previous else ()) + (attempt,)
            execution = ProcessingExecution(
                job=job,
                attempts=attempts,
                derived_media=normalized.reference if normalized else None,
                normalized_image=normalized,
                perceptual_hash=perceptual,
                results=tuple(results),
            )
            self._publish_execution(execution)
            return execution

    def skip(self, media_input: MediaInput, *, job_id: str | None = None) -> ProcessingExecution:
        """Record an intentional skip without creating processing outputs."""
        if not isinstance(media_input, MediaInput):
            raise TypeError("media_input must be a MediaInput instance.")
        resolved_job_id = job_id or str(uuid4())
        _validate_job_id(resolved_job_id)
        with self._state_lock:
            lock = self._job_locks.setdefault(resolved_job_id, Lock())
        with lock:
            current = self._executions.get(resolved_job_id)
            if current is not None:
                if current.job.media_id != media_input.metadata.media_id:
                    raise ValueError("A processing job ID cannot be reused for another media item.")
                return current
            now = _utc_now()
            job = ProcessingJob(
                resolved_job_id,
                media_input.metadata.media_id,
                media_input.metadata.investigation_id,
                ProcessingStatus.SKIPPED,
                now,
                completed_at=now,
            )
            attempt = ProcessingAttempt(1, ProcessingStatus.SKIPPED, now, now, (), ())
            execution = ProcessingExecution(job, (attempt,))
            self._publish_execution(execution)
            return execution

    def _publish_job(self, job: ProcessingJob) -> None:
        with self._state_lock:
            self._current_jobs[job.job_id] = job

    def _publish_execution(self, execution: ProcessingExecution) -> None:
        with self._state_lock:
            self._current_jobs[execution.job.job_id] = execution.job
            self._executions[execution.job.job_id] = execution


def _preprocessing_result(
    job_id: str, media_input: MediaInput, output: PreprocessedImage
) -> ProcessingResult:
    reference = output.reference
    return ProcessingResult(
        result_id=str(uuid4()),
        job_id=job_id,
        media_id=media_input.metadata.media_id,
        algorithm="image_preprocessing",
        algorithm_version=reference.transformation_version,
        result_type="normalized_image",
        result_data={
            "source_sha256": media_input.metadata.sha256,
            "derived_media_id": reference.derived_media_id,
            "derived_sha256": reference.sha256,
            "mime_type": reference.mime_type,
            "width": reference.width,
            "height": reference.height,
        },
        created_at=_utc_now(),
    )


def _phash_result(
    job_id: str,
    media_input: MediaInput,
    normalized: PreprocessedImage,
    perceptual: MediaPerceptualHash,
) -> ProcessingResult:
    return ProcessingResult(
        result_id=str(uuid4()),
        job_id=job_id,
        media_id=media_input.metadata.media_id,
        algorithm=perceptual.algorithm,
        algorithm_version=perceptual.algorithm_version,
        result_type="perceptual_hash",
        result_data={
            "source_sha256": media_input.metadata.sha256,
            "derived_media_id": normalized.reference.derived_media_id,
            "derived_sha256": normalized.reference.sha256,
            "phash": perceptual.hash_value,
        },
        created_at=_utc_now(),
    )


def _stage_failure(code: str, message: str) -> ProcessingFailure:
    return ProcessingFailure(code=code, message=message, occurred_at=_utc_now())


def _preprocessing_failure(error: Exception) -> tuple[str, str]:
    """Map known safe image errors to stable output codes without internals."""
    if isinstance(error, AnimatedImageError):
        return "ANIMATED_WEBP_NOT_SUPPORTED", "Animated WebP images are not supported."
    if isinstance(error, UnsupportedImageFormatError):
        return "UNSUPPORTED_MEDIA_TYPE", "The image format is not supported."
    if isinstance(error, ImageDimensionsExceededError):
        return "IMAGE_DIMENSIONS_EXCEEDED", "Image dimensions exceed supported limits."
    if isinstance(error, InvalidImageError):
        return "INVALID_IMAGE", "Image content could not be validated."
    return "PREPROCESSING_FAILED", "Image normalization failed."


def _job_with_status(
    job: ProcessingJob,
    status: ProcessingStatus,
    *,
    started_at: str | None = None,
) -> ProcessingJob:
    return ProcessingJob(
        job_id=job.job_id,
        media_id=job.media_id,
        investigation_id=job.investigation_id,
        processing_status=status,
        created_at=job.created_at,
        started_at=started_at or job.started_at,
        failure=job.failure,
    )


def _validate_job_id(value: str) -> None:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("job_id must be a valid UUID.") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

"""End-to-end AI-side image workflow from a documented Phase 3 upload payload."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable, Mapping
from uuid import uuid4

from .evidence import MediaEvidenceReference
from .models import (
    ImageMetadata,
    ProcessingFailure,
    ProcessingJob,
    ProcessingResult,
    ProcessingStatus,
)
from .perceptual_hash import MediaPerceptualHash, NearDuplicateResult, find_near_duplicates
from .phase3_adapter import media_input_from_phase3_upload
from .pipeline import MediaProcessingPipeline, ProcessingAttempt
from .preprocessing import PreprocessedImage


INTEGRATED_ALGORITHM = "ps11_media_processing"
INTEGRATED_ALGORITHM_VERSION = "1.0.0"


@dataclass(frozen=True)
class MediaProcessingWorkflowResult:
    """Unified job, artifact, similarity, provenance, and failure response."""

    job: ProcessingJob
    processing_result: ProcessingResult
    upload_metadata: ImageMetadata
    source_checksum: str
    derived_image: PreprocessedImage | None
    perceptual_hash: MediaPerceptualHash | None
    near_duplicates: NearDuplicateResult | None
    evidence_reference: MediaEvidenceReference | None
    failures: tuple[ProcessingFailure, ...]
    attempts: tuple[ProcessingAttempt, ...]

    @property
    def processing_status(self) -> ProcessingStatus:
        """Expose the terminal lifecycle status without a duplicate field."""
        return self.job.processing_status


def process_phase3_upload(
    upload_response: Mapping[str, object],
    original_image_bytes: bytes,
    *,
    existing_media_hashes: Iterable[MediaPerceptualHash] | None = None,
    minimum_similarity: float = 0.90,
    job_id: str | None = None,
    retry_failed: bool = False,
    pipeline: MediaProcessingPipeline | None = None,
) -> MediaProcessingWorkflowResult:
    """Validate the Phase 3 response, execute AI stages, and unify the outputs.

    The function performs no network or database access. It accepts exactly
    the documented upload response through ``phase3_adapter`` and the original
    bytes held by the caller. Retries are explicit via ``retry_failed``.
    """
    _validate_similarity(minimum_similarity)
    media_input = media_input_from_phase3_upload(upload_response, original_image_bytes)
    runner = pipeline or MediaProcessingPipeline()
    execution = runner.process(media_input, job_id=job_id, retry_failed=retry_failed)

    comparisons = None
    if existing_media_hashes is not None and execution.perceptual_hash is not None:
        comparisons = find_near_duplicates(
            execution.perceptual_hash,
            tuple(existing_media_hashes),
            minimum_similarity=minimum_similarity,
        )
    elif existing_media_hashes is not None:
        # Materialize and type-check candidates even when this run failed before pHash.
        candidates = tuple(existing_media_hashes)
        if any(not isinstance(candidate, MediaPerceptualHash) for candidate in candidates):
            raise TypeError("existing_media_hashes must contain MediaPerceptualHash values.")

    evidence_reference = None
    if execution.derived_media is not None and execution.perceptual_hash is not None:
        evidence_reference = MediaEvidenceReference.from_processed_media(
            media_input.metadata,
            execution.derived_media,
            execution.perceptual_hash,
        )

    unified = _unified_result(
        job=execution.job,
        metadata=media_input.metadata,
        source_checksum=media_input.metadata.sha256,
        derived_image=execution.normalized_image,
        perceptual_hash=execution.perceptual_hash,
        near_duplicates=comparisons,
        failures=execution.attempts[-1].failures if execution.attempts else (),
    )
    return MediaProcessingWorkflowResult(
        job=execution.job,
        processing_result=unified,
        upload_metadata=media_input.metadata,
        source_checksum=media_input.metadata.sha256,
        derived_image=execution.normalized_image,
        perceptual_hash=execution.perceptual_hash,
        near_duplicates=comparisons,
        evidence_reference=evidence_reference,
        failures=execution.attempts[-1].failures if execution.attempts else (),
        attempts=execution.attempts,
    )


def _unified_result(
    *,
    job: ProcessingJob,
    metadata: ImageMetadata,
    source_checksum: str,
    derived_image: PreprocessedImage | None,
    perceptual_hash: MediaPerceptualHash | None,
    near_duplicates: NearDuplicateResult | None,
    failures: tuple[ProcessingFailure, ...],
) -> ProcessingResult:
    derived_reference = derived_image.reference if derived_image is not None else None
    failure_values = [
        {"code": item.code, "message": item.message, "occurred_at": item.occurred_at}
        for item in failures
    ]
    match_values = None if near_duplicates is None else [
        {
            "matching_media_id": match.matching_media_id,
            "matching_derived_media_id": match.matching_derived_media_id,
            "hamming_distance": match.hamming_distance,
            "similarity_score": match.similarity_score,
        }
        for match in near_duplicates.matches
    ]
    derived_values = None if derived_reference is None else {
        "derived_media_id": derived_reference.derived_media_id,
        "source_media_id": derived_reference.source_media_id,
        "transformation": derived_reference.transformation,
        "transformation_version": derived_reference.transformation_version,
        "mime_type": derived_reference.mime_type,
        "width": derived_reference.width,
        "height": derived_reference.height,
        "sha256": derived_reference.sha256,
    }
    phash_values = None if perceptual_hash is None else {
        "value": perceptual_hash.hash_value,
        "algorithm": perceptual_hash.algorithm,
        "algorithm_version": perceptual_hash.algorithm_version,
        "derived_media_id": perceptual_hash.derived_media_id,
    }
    return ProcessingResult(
        result_id=str(uuid4()),
        job_id=job.job_id,
        media_id=metadata.media_id,
        algorithm=INTEGRATED_ALGORITHM,
        algorithm_version=INTEGRATED_ALGORITHM_VERSION,
        result_type="media_intelligence_pipeline",
        result_data={
            "processing_status": job.processing_status.value,
            "source_sha256": source_checksum,
            "upload": {
                "mime_type": metadata.mime_type,
                "width": metadata.width,
                "height": metadata.height,
                "size_bytes": metadata.size_bytes,
                "original_filename": metadata.original_filename,
                "uploaded_at": metadata.uploaded_at,
            },
            "derived_media": derived_values,
            "perceptual_hash": phash_values,
            "minimum_similarity": near_duplicates.minimum_similarity if near_duplicates else None,
            "similarity_matches": match_values,
            "failures": failure_values,
        },
        created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )


def _validate_similarity(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("minimum_similarity must be a finite number from 0 to 1.")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("minimum_similarity must be a finite number from 0 to 1.")

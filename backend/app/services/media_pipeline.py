"""Typed, route-independent orchestration for stored image and video media."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Literal, Protocol
from uuid import UUID

from backend.app.database import database_connection
from backend.app.ids import generate_uuid
from backend.app.repositories.investigations import get_investigation
from backend.app.repositories.media_processing import (
    complete_processing_run,
    create_processing_run,
    fail_processing_run,
    get_media_asset_for_processing,
    list_media_asset_ids_for_investigation,
    mark_processing_started,
    skip_processing_run,
)
from backend.app.services.reference_media import compare_local_references


logger = logging.getLogger(__name__)
ProcessingStatus = Literal[
    "received", "queued", "processing", "completed", "failed", "skipped"
]
UNSUPPORTED_PROCESSOR_NAME = "ps11.media.unsupported"
UNSUPPORTED_PROCESSOR_VERSION = "1.0.0"


def utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class MediaProcessingRequest:
    """Trusted internal references and checksum for one stored media asset."""

    media_id: str
    storage_reference: Path
    expected_checksum: str
    artifact_storage_path: Path
    processing_run_id: str | None = None
    requested_timestamps_ms: tuple[int, ...] | None = None


@dataclass(frozen=True)
class ImageProcessingResult:
    """Technical image metadata and private normalized artifact reference."""

    source_format: str
    source_width: int
    source_height: int
    width: int
    height: int
    source_color_mode: str
    normalized_color_mode: Literal["RGB"]
    bands: tuple[str, ...]
    has_alpha: bool
    icc_profile_present: bool
    sha256: str
    perceptual_hash: str
    normalized_artifact_reference: str


@dataclass(frozen=True)
class ProcessingFailure:
    """Safe processing failure data; never contains paths or decoder internals."""

    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ProcessingOutcome:
    processing_run_id: str | None
    media_id: str
    status: ProcessingStatus
    result: Any | None = None
    failure: ProcessingFailure | None = None
    investigation_id: str | None = None
    media_type: str | None = None
    processor_name: str | None = None
    processor_version: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    reference_comparison: dict | None = None
    deepfake_detections: tuple[dict[str, Any], ...] = ()


class MediaProcessingException(Exception):
    """Expected processing failure represented by a safe typed value."""

    def __init__(self, failure: ProcessingFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class MediaProcessor(Protocol):
    processor_name: str
    processor_version: str
    result_type: str
    result_schema_version: str

    def process(self, request: MediaProcessingRequest) -> Any: ...


def process_existing_uploaded_media(
    database_path: Path | str,
    media_storage_path: Path | str,
    investigation_id: str,
    media_id: str,
    *,
    requested_timestamps_ms: tuple[int, ...] | None = None,
) -> ProcessingOutcome:
    """Dispatch one stored asset to its processor and persist an independent run."""

    return _process_media(
        database_path,
        media_storage_path,
        investigation_id,
        media_id,
        requested_timestamps_ms=requested_timestamps_ms,
    )


def process_existing_uploaded_image(
    database_path: Path | str,
    media_storage_path: Path | str,
    media_id: str,
) -> ProcessingOutcome:
    """Backward-compatible explicit image entry point using unified orchestration."""

    return _process_by_stored_investigation(
        database_path, media_storage_path, media_id, expected_type="image"
    )


def process_existing_uploaded_video(
    database_path: Path | str,
    media_storage_path: Path | str,
    media_id: str,
    *,
    requested_timestamps_ms: tuple[int, ...] | None = None,
) -> ProcessingOutcome:
    """Backward-compatible explicit video entry point using unified orchestration."""

    return _process_by_stored_investigation(
        database_path,
        media_storage_path,
        media_id,
        expected_type="video",
        requested_timestamps_ms=requested_timestamps_ms,
    )


def process_investigation_media(
    database_path: Path | str,
    media_storage_path: Path | str,
    investigation_id: str,
) -> list[ProcessingOutcome]:
    """Process each asset independently in deterministic order.

    A typed failure for one asset is returned as that asset's outcome and does not
    stop later items from being attempted. This function is explicit and is not
    called by the upload route.
    """

    canonical_id = _canonical_uuid(investigation_id)
    if canonical_id is None:
        return [
            _failure_outcome(
                str(investigation_id),
                "INVALID_INVESTIGATION_ID",
                "The investigation identifier must be a valid UUID.",
            )
        ]
    try:
        with database_connection(database_path) as connection:
            if get_investigation(connection, canonical_id) is None:
                return [
                    _failure_outcome(
                        "",
                        "INVESTIGATION_NOT_FOUND",
                        "The investigation was not found.",
                        investigation_id=canonical_id,
                    )
                ]
            media_ids = list_media_asset_ids_for_investigation(connection, canonical_id)
    except sqlite3.Error:
        logger.exception("Unable to list investigation media for processing")
        return [
            _failure_outcome(
                "",
                "PROCESSING_PERSISTENCE_FAILED",
                "Unable to load investigation media for processing.",
                investigation_id=canonical_id,
            )
        ]

    outcomes: list[ProcessingOutcome] = []
    for media_id in media_ids:
        try:
            outcomes.append(
                process_existing_uploaded_media(
                    database_path,
                    media_storage_path,
                    canonical_id,
                    media_id,
                )
            )
        except Exception:
            logger.exception("Unexpected failure while processing one investigation asset")
            outcomes.append(
                _failure_outcome(
                    media_id,
                    "PROCESSING_FAILED",
                    "Media processing failed.",
                    investigation_id=canonical_id,
                )
            )
    return outcomes


def _process_by_stored_investigation(
    database_path: Path | str,
    media_storage_path: Path | str,
    media_id: str,
    *,
    expected_type: str,
    requested_timestamps_ms: tuple[int, ...] | None = None,
) -> ProcessingOutcome:
    canonical_media_id = _canonical_uuid(media_id)
    if canonical_media_id is None:
        return _failure_outcome(
            str(media_id), "INVALID_MEDIA_ID", "The media identifier must be a valid UUID."
        )
    try:
        with database_connection(database_path) as connection:
            asset = get_media_asset_for_processing(connection, canonical_media_id)
    except sqlite3.Error:
        logger.exception("Unable to load media metadata for processing")
        return _failure_outcome(
            canonical_media_id,
            "PROCESSING_PERSISTENCE_FAILED",
            "Unable to load media metadata for processing.",
        )
    if asset is None:
        return _failure_outcome(
            canonical_media_id, "MEDIA_NOT_FOUND", "The media asset was not found."
        )
    return _process_media(
        database_path,
        media_storage_path,
        asset["investigation_id"],
        canonical_media_id,
        requested_timestamps_ms=requested_timestamps_ms,
        expected_type=expected_type,
    )


def _process_media(
    database_path: Path | str,
    media_storage_path: Path | str,
    investigation_id: str,
    media_id: str,
    *,
    requested_timestamps_ms: tuple[int, ...] | None = None,
    expected_type: str | None = None,
) -> ProcessingOutcome:
    canonical_investigation_id = _canonical_uuid(investigation_id)
    canonical_media_id = _canonical_uuid(media_id)
    if canonical_investigation_id is None:
        return _failure_outcome(
            media_id,
            "INVALID_INVESTIGATION_ID",
            "The investigation identifier must be a valid UUID.",
        )
    if canonical_media_id is None:
        return _failure_outcome(
            media_id,
            "INVALID_MEDIA_ID",
            "The media identifier must be a valid UUID.",
            investigation_id=canonical_investigation_id,
        )

    try:
        with database_connection(database_path) as connection:
            investigation = get_investigation(connection, canonical_investigation_id)
            if investigation is None:
                return _failure_outcome(
                    canonical_media_id,
                    "INVESTIGATION_NOT_FOUND",
                    "The investigation was not found.",
                    investigation_id=canonical_investigation_id,
                )
            asset = get_media_asset_for_processing(connection, canonical_media_id)
    except sqlite3.Error:
        logger.exception("Unable to load investigation or media metadata for processing")
        return _failure_outcome(
            canonical_media_id,
            "PROCESSING_PERSISTENCE_FAILED",
            "Unable to load media metadata for processing.",
            investigation_id=canonical_investigation_id,
        )
    if asset is None or asset["investigation_id"] != canonical_investigation_id:
        return _failure_outcome(
            canonical_media_id,
            "MEDIA_NOT_FOUND",
            "The media asset was not found for this investigation.",
            investigation_id=canonical_investigation_id,
        )

    media_type = _validated_media_type(asset)
    processor = _create_processor(media_type) if media_type else None
    if expected_type and media_type != expected_type:
        processor = None
    if processor is None:
        processor_name = UNSUPPORTED_PROCESSOR_NAME
        processor_version = UNSUPPORTED_PROCESSOR_VERSION
        failure = ProcessingFailure(
            "UNSUPPORTED_MEDIA_TYPE",
            "The stored media type or MIME type is not supported for processing.",
        )
        return _create_skipped_run(
            database_path,
            canonical_investigation_id,
            canonical_media_id,
            str(asset["media_type"]),
            processor_name,
            processor_version,
            failure,
        )

    created_at = utc_now_iso8601()
    started_at = utc_now_iso8601()
    run_id = generate_uuid()
    try:
        with database_connection(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            create_processing_run(
                connection,
                processing_run_id=run_id,
                media_id=canonical_media_id,
                investigation_id=canonical_investigation_id,
                media_type=media_type,
                processor_name=processor.processor_name,
                processor_version=processor.processor_version,
                status="received",
                created_at=created_at,
            )
            connection.commit()
    except sqlite3.Error:
        logger.exception("Unable to create media processing run")
        return _failure_outcome(
            canonical_media_id,
            "PROCESSING_PERSISTENCE_FAILED",
            "Unable to create the media processing run.",
            investigation_id=canonical_investigation_id,
            media_type=media_type,
            processor_name=processor.processor_name,
            processor_version=processor.processor_version,
        )

    try:
        with database_connection(database_path) as connection:
            mark_processing_started(connection, run_id, started_at)
            connection.commit()
    except sqlite3.Error:
        logger.exception("Unable to start media processing run")
        failure = ProcessingFailure(
            "PROCESSING_PERSISTENCE_FAILED", "Unable to start the media processing run."
        )
        _record_failure(database_path, run_id, failure)
        return _failed_run_outcome(
            run_id,
            canonical_investigation_id,
            canonical_media_id,
            media_type,
            processor,
            created_at,
            started_at,
            failure,
        )

    result: Any | None = None
    try:
        source_path = _resolve_private_source(Path(media_storage_path), asset["file_reference"])
        request = MediaProcessingRequest(
            media_id=canonical_media_id,
            storage_reference=source_path,
            expected_checksum=asset["checksum"],
            artifact_storage_path=_artifact_directory(Path(media_storage_path), media_type),
            processing_run_id=run_id,
            requested_timestamps_ms=requested_timestamps_ms,
        )
        result = processor.process(request)
        completed_at = utc_now_iso8601()
        reference_comparison = None
        deepfake_detections = _run_deepfake_detectors(
            media_type, source_path, result, Path(media_storage_path)
        )
        if media_type == "image":
            with database_connection(database_path) as connection:
                reference_comparison = compare_local_references(
                    connection,
                    result.perceptual_hash,
                )
        result_payload = _result_payload(
            result,
            investigation_id=canonical_investigation_id,
            media_id=canonical_media_id,
            processing_run_id=run_id,
            media_type=media_type,
            processor_name=processor.processor_name,
            processor_version=processor.processor_version,
            started_at=started_at,
            completed_at=completed_at,
            reference_comparison=reference_comparison,
            deepfake_detections=deepfake_detections,
        )
        with database_connection(database_path) as connection:
            complete_processing_run(
                connection,
                processing_run_id=run_id,
                processing_result_id=generate_uuid(),
                media_id=canonical_media_id,
                result_type=processor.result_type,
                result_schema_version=processor.result_schema_version,
                result_json=json.dumps(
                    result_payload, ensure_ascii=False, separators=(",", ":")
                ),
                created_at=completed_at,
            )
            connection.commit()
        return ProcessingOutcome(
            processing_run_id=run_id,
            media_id=canonical_media_id,
            status="completed",
            result=result,
            investigation_id=canonical_investigation_id,
            media_type=media_type,
            processor_name=processor.processor_name,
            processor_version=processor.processor_version,
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            reference_comparison=reference_comparison,
            deepfake_detections=tuple(item.to_dict() for item in deepfake_detections),
        )
    except MediaProcessingException as exc:
        _record_failure(database_path, run_id, exc.failure)
        return _failed_run_outcome(
            run_id,
            canonical_investigation_id,
            canonical_media_id,
            media_type,
            processor,
            created_at,
            started_at,
            exc.failure,
        )
    except sqlite3.Error:
        logger.exception("Unable to persist media processing result")
        if result is not None:
            _cleanup_generated_artifacts(Path(media_storage_path), media_type, result)
        failure = ProcessingFailure(
            "PROCESSING_PERSISTENCE_FAILED", "Unable to persist media processing results."
        )
        _record_failure(database_path, run_id, failure)
        return _failed_run_outcome(
            run_id,
            canonical_investigation_id,
            canonical_media_id,
            media_type,
            processor,
            created_at,
            started_at,
            failure,
        )
    except Exception:
        logger.exception("Unexpected media processing failure")
        if result is not None:
            _cleanup_generated_artifacts(Path(media_storage_path), media_type, result)
        failure = ProcessingFailure("PROCESSING_FAILED", "Media processing failed.")
        _record_failure(database_path, run_id, failure)
        return _failed_run_outcome(
            run_id,
            canonical_investigation_id,
            canonical_media_id,
            media_type,
            processor,
            created_at,
            started_at,
            failure,
        )


def _validated_media_type(asset: dict[str, Any]) -> str | None:
    media_type = str(asset["media_type"] or "").lower()
    mime_type = str(asset["mime_type"] or "").split(";", 1)[0].strip().lower()
    if media_type == "image" and mime_type.startswith("image/") and len(mime_type) > 6:
        return "image"
    if media_type == "video" and mime_type.startswith("video/") and len(mime_type) > 6:
        return "video"
    if media_type == "audio" and mime_type.startswith("audio/") and len(mime_type) > 6:
        return "audio"
    return None


def _create_processor(media_type: str) -> MediaProcessor:
    if media_type == "image":
        from backend.app.services.image_processor import ImageProcessor

        return ImageProcessor()
    if media_type == "audio":
        from backend.app.services.audio_processor import AudioProcessor

        return AudioProcessor()
    from backend.app.services.video_processor import VideoProcessor

    return VideoProcessor()


def _create_skipped_run(
    database_path: Path | str,
    investigation_id: str,
    media_id: str,
    media_type: str,
    processor_name: str,
    processor_version: str,
    failure: ProcessingFailure,
) -> ProcessingOutcome:
    created_at = utc_now_iso8601()
    run_id = generate_uuid()
    try:
        with database_connection(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            create_processing_run(
                connection,
                processing_run_id=run_id,
                media_id=media_id,
                investigation_id=investigation_id,
                media_type=media_type if media_type in {"image", "video", "audio"} else None,
                processor_name=processor_name,
                processor_version=processor_version,
                status="received",
                created_at=created_at,
            )
            skip_processing_run(
                connection,
                processing_run_id=run_id,
                completed_at=created_at,
                failure_code=failure.code,
                failure_message=failure.message,
            )
            connection.commit()
        return ProcessingOutcome(
            processing_run_id=run_id,
            media_id=media_id,
            status="skipped",
            failure=failure,
            investigation_id=investigation_id,
            media_type=media_type,
            processor_name=processor_name,
            processor_version=processor_version,
            created_at=created_at,
            completed_at=created_at,
        )
    except sqlite3.Error:
        logger.exception("Unable to persist skipped media processing run")
        return _failure_outcome(
            media_id,
            "PROCESSING_PERSISTENCE_FAILED",
            "Unable to persist the skipped media processing run.",
            investigation_id=investigation_id,
            media_type=media_type,
            processor_name=processor_name,
            processor_version=processor_version,
        )


def _resolve_private_source(storage_root: Path, file_reference: str) -> Path:
    try:
        root = storage_root.resolve(strict=True)
        reference = Path(file_reference)
        if reference.is_absolute() or ".." in reference.parts:
            raise ValueError("invalid reference")
        source = (root / reference).resolve(strict=True)
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError("invalid reference")
        return source
    except FileNotFoundError:
        raise MediaProcessingException(
            ProcessingFailure("MEDIA_FILE_NOT_FOUND", "The stored media file was not found.")
        ) from None
    except (OSError, ValueError, RuntimeError):
        raise MediaProcessingException(
            ProcessingFailure(
                "INVALID_STORAGE_REFERENCE", "The stored media reference is invalid."
            )
        ) from None


def _artifact_directory(storage_root: Path, media_type: str) -> Path:
    if media_type == "video":
        return storage_root / "processed" / "frames"
    return storage_root / "processed"


def _run_deepfake_detectors(
    media_type: str,
    source_path: Path,
    processing_result: Any,
    storage_root: Path,
) -> tuple[Any, ...]:
    """Run optional detectors independently of preprocessing and pHash results."""

    from backend.app.services.deepfake_detection import failed_result

    if media_type == "audio":
        detection = getattr(processing_result, "detector_result", None)
        return (detection,) if detection is not None else ()

    detections = []
    if media_type == "image":
        from backend.app.services.image_deepfake_detector import detect_image

        try:
            detections.append(detect_image(source_path))
        except Exception:
            logger.exception("Image deepfake detector failed outside its error boundary")
            detections.append(failed_result(
                modality="image", detector_name="ps11.image.ai-generated-classifier",
                detector_version="1.0.0", model_name="Imager-1-Pro",
                model_version="unknown", model_source_url="https://huggingface.co/Imagers/imager-1-pro",
                model_license="Apache-2.0", code="DETECTOR_FAILED",
                message="The image detector failed safely.",
            ))
    elif media_type == "video":
        from backend.app.services.video_deepfake_detector import detect_video_frames
        from backend.app.services.audio_deepfake_detector import detect_video_audio

        try:
            detections.append(detect_video_frames(processing_result.frames, storage_root))
        except Exception:
            logger.exception("Video deepfake detector failed outside its error boundary")
            detections.append(failed_result(
                modality="video", detector_name="ps11.video.mtl-face-auth",
                detector_version="1.0.0", model_name="MTL Face Anti-Spoofing and Deepfake Detection",
                model_version="unknown", model_source_url="https://huggingface.co/shahriarhd/mtl-face-auth",
                model_license="MIT", code="DETECTOR_FAILED",
                message="The video detector failed safely.",
            ))
        try:
            detections.append(detect_video_audio(source_path))
        except Exception:
            logger.exception("Video audio analysis failed outside its error boundary")
            detections.append(failed_result(
                modality="audio", detector_name="ps11.audio.wav2vec2-synthetic-speech",
                detector_version="1.0.0", model_name="wav2vec2-deepfake-voice-detector",
                model_version="unknown",
                model_source_url="https://huggingface.co/garystafford/wav2vec2-deepfake-voice-detector",
                model_license="Apache-2.0", code="AUDIO_DETECTION_FAILED",
                message="The video audio detector failed safely.",
            ))
    return tuple(detections)


def _result_payload(
    result: Any,
    *,
    investigation_id: str,
    media_id: str,
    processing_run_id: str,
    media_type: str,
    processor_name: str,
    processor_version: str,
    started_at: str,
    completed_at: str,
    reference_comparison: dict | None = None,
    deepfake_detections: tuple[Any, ...] = (),
) -> dict[str, Any]:
    result_fields = asdict(result)
    result_fields.pop("detector_result", None)
    payload = {
        "investigation_id": investigation_id,
        "media_id": media_id,
        "processing_run_id": processing_run_id,
        "media_type": media_type,
        "processing_status": "completed",
        "processor_name": processor_name,
        "processor_version": processor_version,
        "started_at": started_at,
        "completed_at": completed_at,
        **result_fields,
    }
    if reference_comparison is not None:
        payload["reference_comparison"] = reference_comparison
    payload["deepfake_detections"] = [item.to_dict() for item in deepfake_detections]
    return payload


def _failed_run_outcome(
    run_id: str,
    investigation_id: str,
    media_id: str,
    media_type: str,
    processor: MediaProcessor,
    created_at: str,
    started_at: str,
    failure: ProcessingFailure,
) -> ProcessingOutcome:
    return ProcessingOutcome(
        processing_run_id=run_id,
        media_id=media_id,
        status="failed",
        failure=failure,
        investigation_id=investigation_id,
        media_type=media_type,
        processor_name=processor.processor_name,
        processor_version=processor.processor_version,
        created_at=created_at,
        started_at=started_at,
        completed_at=utc_now_iso8601(),
    )


def _failure_outcome(
    media_id: str,
    code: str,
    message: str,
    *,
    investigation_id: str | None = None,
    media_type: str | None = None,
    processor_name: str | None = None,
    processor_version: str | None = None,
) -> ProcessingOutcome:
    return ProcessingOutcome(
        processing_run_id=None,
        media_id=media_id,
        status="failed",
        failure=ProcessingFailure(code, message),
        investigation_id=investigation_id,
        media_type=media_type,
        processor_name=processor_name,
        processor_version=processor_version,
    )


def _record_failure(
    database_path: Path | str,
    run_id: str,
    failure: ProcessingFailure,
) -> None:
    try:
        with database_connection(database_path) as connection:
            fail_processing_run(
                connection,
                processing_run_id=run_id,
                completed_at=utc_now_iso8601(),
                failure_code=failure.code,
                failure_message=failure.message,
            )
            connection.commit()
    except sqlite3.Error:
        logger.exception("Unable to persist media processing failure state")


def _cleanup_generated_artifacts(storage_root: Path, media_type: str, result: Any) -> None:
    references: list[str] = []
    if media_type == "image":
        artifact = getattr(result, "normalized_artifact_reference", None)
        if artifact:
            references.append(str(artifact))
    elif media_type == "video":
        for frame in getattr(result, "frames", ()):
            artifact = getattr(frame, "artifact_reference", None)
            if artifact:
                references.append(str(artifact))
    for reference in references:
        if Path(reference).name != reference or "/" in reference or "\\" in reference:
            logger.error("Skipped cleanup for an invalid generated artifact reference")
            continue
        parent = "frames" if media_type == "video" else ""
        path = storage_root / "processed" / parent / reference
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Unable to clean up an unpersisted media artifact")


def _canonical_uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None

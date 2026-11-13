"""PyAV-based video metadata inspection and bounded frame extraction.

Frame extraction prepares media for later analysis. It does not detect
manipulation or establish authenticity.
"""

from dataclasses import dataclass
from hashlib import sha256
import hmac
import logging
import math
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from PIL import Image

from backend.app.config import VideoProcessingSettings
from backend.app.services.media_pipeline import (
    MediaProcessingException,
    MediaProcessingRequest,
    ProcessingFailure,
)


logger = logging.getLogger(__name__)
PROCESSOR_NAME = "ps11.video.frame_extractor"
PROCESSOR_VERSION = "1.0.0"
RESULT_TYPE = "video_frame_metadata"
RESULT_SCHEMA_VERSION = "1.0"
READ_CHUNK_BYTES = 64 * 1024

_SEMAPHORE_LOCK = threading.Lock()
_DECODER_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}


@dataclass(frozen=True)
class ExtractedFrame:
    """Metadata for one privately stored normalized RGB frame."""

    requested_timestamp_ms: int
    actual_timestamp_ms: int
    frame_index: int
    width: int
    height: int
    sha256: str
    artifact_reference: str


@dataclass(frozen=True)
class VideoProcessingResult:
    """Versioned technical metadata; artifact references are filenames only."""

    media_id: str
    processing_run_id: str
    container_format: str
    video_codec: str
    duration_ms: int
    fps: float
    width: int
    height: int
    source_sha256: str
    requested_timestamps_ms: tuple[int, ...]
    frames: tuple[ExtractedFrame, ...]


class VideoProcessor:
    processor_name = PROCESSOR_NAME
    processor_version = PROCESSOR_VERSION
    result_type = RESULT_TYPE
    result_schema_version = RESULT_SCHEMA_VERSION

    def __init__(self, settings: VideoProcessingSettings | None = None) -> None:
        self.settings = settings or VideoProcessingSettings.from_environment()

    def process(self, request: MediaProcessingRequest) -> VideoProcessingResult:
        """Verify a stored video and write selected frames as separate JPEGs."""

        if not request.processing_run_id:
            raise MediaProcessingException(
                ProcessingFailure(
                    "INVALID_PROCESSING_REQUEST",
                    "A processing run identifier is required for video processing.",
                )
            )

        started = time.monotonic()
        source_path = request.storage_reference
        try:
            file_size = source_path.stat().st_size
        except FileNotFoundError:
            raise MediaProcessingException(
                ProcessingFailure("MEDIA_FILE_NOT_FOUND", "The stored video file was not found.")
            ) from None
        except OSError:
            raise MediaProcessingException(
                ProcessingFailure("VIDEO_READ_FAILED", "The stored video could not be read.")
            ) from None
        if file_size <= 0:
            raise _failure("INVALID_VIDEO", "The stored video is empty.")
        if file_size > self.settings.max_size_bytes:
            raise _failure("VIDEO_SIZE_LIMIT_EXCEEDED", "The video exceeds the configured size limit.")

        source_digest = _sha256_file(source_path, self.settings.max_size_bytes)
        if not hmac.compare_digest(source_digest, request.expected_checksum.lower()):
            raise _failure(
                "SOURCE_CHECKSUM_MISMATCH",
                "The stored video checksum does not match its upload record.",
            )

        av = _load_av()
        semaphore = _decoder_semaphore(self.settings.max_concurrent_decoders)
        if not semaphore.acquire(timeout=self.settings.timeout_seconds):
            raise MediaProcessingException(
                ProcessingFailure(
                    "VIDEO_PROCESSING_BUSY",
                    "No video decoder is available within the configured wait time.",
                    retryable=True,
                )
            )

        artifacts: list[Path] = []
        container: Any | None = None
        try:
            _check_timeout(started, self.settings.timeout_seconds)
            try:
                container = av.open(
                    str(source_path),
                    mode="r",
                    timeout=(self.settings.timeout_seconds, self.settings.timeout_seconds),
                )
            except Exception:
                raise _failure(
                    "INVALID_VIDEO", "The stored file is not a decodable supported video."
                ) from None

            video_streams = [stream for stream in container.streams if stream.type == "video"]
            if not video_streams:
                raise _failure("INVALID_VIDEO", "The stored file contains no video stream.")
            stream = video_streams[0]
            width = int(stream.codec_context.width or 0)
            height = int(stream.codec_context.height or 0)
            duration_seconds = _duration_seconds(container, stream, av)
            fps = _positive_rate(stream)
            container_format = _safe_format_name(container)
            codec_name = str(stream.codec_context.name or "")
            if not container_format or not codec_name or fps <= 0:
                raise _failure(
                    "UNSUPPORTED_VIDEO_METADATA",
                    "The video container, codec, or frame rate is unsupported.",
                )
            _validate_metadata(width, height, duration_seconds, self.settings)
            requested = _requested_timestamps(
                duration_seconds,
                self.settings.sample_fps,
                self.settings.max_frames,
                request.requested_timestamps_ms,
            )
            if not requested:
                raise _failure("INVALID_VIDEO", "No frame timestamps fall within the video.")

            try:
                request.artifact_storage_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise _failure(
                    "FRAME_STORAGE_FAILED", "Unable to prepare private frame storage."
                ) from None
            frames: list[ExtractedFrame] = []
            target_index = 0
            frame_index = -1
            try:
                decoded_frames = container.decode(video=stream.index)
                for decoded in decoded_frames:
                    _check_timeout(started, self.settings.timeout_seconds)
                    frame_index += 1
                    actual_ms = _frame_timestamp_ms(decoded, stream)
                    if actual_ms is None:
                        continue
                    while target_index < len(requested) and requested[target_index] <= actual_ms:
                        frame = _save_frame(
                            decoded,
                            requested_timestamp_ms=requested[target_index],
                            actual_timestamp_ms=actual_ms,
                            frame_index=frame_index,
                            media_id=request.media_id,
                            processing_run_id=request.processing_run_id,
                            storage_directory=request.artifact_storage_path,
                            settings=self.settings,
                        )
                        artifacts.append(request.artifact_storage_path / frame.artifact_reference)
                        frames.append(frame)
                        target_index += 1
                    if target_index >= len(requested):
                        break
            except MediaProcessingException:
                raise
            except Exception:
                raise _failure(
                    "VIDEO_DECODE_FAILED", "The video could not be decoded safely."
                ) from None
            if target_index != len(requested):
                raise _failure(
                    "VIDEO_DECODE_FAILED",
                    "The decoder ended before all requested frames were available.",
                )

            _check_timeout(started, self.settings.timeout_seconds)
            return VideoProcessingResult(
                media_id=request.media_id,
                processing_run_id=request.processing_run_id,
                container_format=container_format,
                video_codec=codec_name,
                duration_ms=round(duration_seconds * 1000),
                fps=fps,
                width=width,
                height=height,
                source_sha256=source_digest,
                requested_timestamps_ms=requested,
                frames=tuple(frames),
            )
        except MediaProcessingException:
            _cleanup_artifacts(artifacts)
            raise
        except Exception:
            _cleanup_artifacts(artifacts)
            logger.exception("Unexpected video processing failure")
            raise MediaProcessingException(
                ProcessingFailure("VIDEO_PROCESSING_FAILED", "Video processing failed.")
            ) from None
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    # Decoder close after successful extraction must not turn a
                    # persisted processing result into a false processing failure.
                    logger.exception("Unable to close video decoder cleanly")
            semaphore.release()


def _load_av() -> Any:
    try:
        import av

        return av
    except ImportError:
        raise MediaProcessingException(
            ProcessingFailure(
                "VIDEO_DECODER_UNAVAILABLE",
                "Video processing requires the configured PyAV decoder dependency.",
                retryable=False,
            )
        ) from None


def _decoder_semaphore(capacity: int) -> threading.BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        semaphore = _DECODER_SEMAPHORES.get(capacity)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(capacity)
            _DECODER_SEMAPHORES[capacity] = semaphore
        return semaphore


def _sha256_file(path: Path, maximum_bytes: int) -> str:
    digest = sha256()
    byte_count = 0
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(READ_CHUNK_BYTES), b""):
                byte_count += len(chunk)
                if byte_count > maximum_bytes:
                    raise _failure(
                        "VIDEO_SIZE_LIMIT_EXCEEDED",
                        "The video exceeds the configured size limit.",
                    )
                digest.update(chunk)
    except MediaProcessingException:
        raise
    except OSError:
        raise MediaProcessingException(
            ProcessingFailure("VIDEO_READ_FAILED", "The stored video could not be read.")
        ) from None
    return digest.hexdigest()


def _duration_seconds(container: Any, stream: Any, av: Any) -> float:
    try:
        if stream.duration is not None and stream.time_base is not None:
            value = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            value = float(container.duration) / float(av.time_base)
        else:
            raise ValueError("duration missing")
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        raise _failure(
            "UNSUPPORTED_VIDEO_METADATA", "The video duration could not be determined."
        ) from None
    if not math.isfinite(value) or value <= 0:
        raise _failure("INVALID_VIDEO", "The video duration is invalid.")
    return value


def _positive_rate(stream: Any) -> float:
    try:
        rate = stream.average_rate or stream.base_rate or stream.guessed_rate
        result = float(rate) if rate else 0.0
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return 0.0
    return result if math.isfinite(result) and result > 0 else 0.0


def _safe_format_name(container: Any) -> str:
    try:
        formats = str(container.format.name or "")
    except Exception:
        return ""
    return formats[:128]


def _validate_metadata(
    width: int,
    height: int,
    duration_seconds: float,
    settings: VideoProcessingSettings,
) -> None:
    if width <= 0 or height <= 0:
        raise _failure("INVALID_VIDEO", "The decoded video dimensions are invalid.")
    if width > settings.max_width or height > settings.max_height:
        raise _failure(
            "VIDEO_RESOLUTION_LIMIT_EXCEEDED",
            "The video exceeds the configured resolution limit.",
        )
    if duration_seconds > settings.max_duration_seconds:
        raise _failure(
            "VIDEO_DURATION_LIMIT_EXCEEDED",
            "The video exceeds the configured duration limit.",
        )


def _requested_timestamps(
    duration_seconds: float,
    sample_fps: float,
    max_frames: int,
    requested_timestamps_ms: tuple[int, ...] | None,
) -> tuple[int, ...]:
    if requested_timestamps_ms is not None:
        if (
            not requested_timestamps_ms
            or len(requested_timestamps_ms) > max_frames
            or any(not isinstance(item, int) or item < 0 for item in requested_timestamps_ms)
            or tuple(sorted(set(requested_timestamps_ms))) != requested_timestamps_ms
            or any(item >= duration_seconds * 1000 for item in requested_timestamps_ms)
        ):
            raise _failure(
                "INVALID_FRAME_SAMPLING_REQUEST",
                "Requested frame timestamps must be unique, increasing, and within the video.",
            )
        return requested_timestamps_ms

    interval_ms = max(1, round(1000 / sample_fps))
    duration_ms = int(duration_seconds * 1000)
    candidate_count = (duration_ms + interval_ms - 1) // interval_ms
    if candidate_count <= max_frames:
        return tuple(index * interval_ms for index in range(candidate_count))
    if max_frames == 1:
        return (0,)
    last = candidate_count - 1
    indexes = [round(index * last / (max_frames - 1)) for index in range(max_frames)]
    return tuple(index * interval_ms for index in indexes)


def _frame_timestamp_ms(frame: Any, stream: Any) -> int | None:
    if frame.pts is None or frame.time_base is None:
        return None
    try:
        seconds = float(frame.pts * frame.time_base)
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return round(seconds * 1000)
    except (TypeError, ValueError, OverflowError):
        return None


def _save_frame(
    decoded: Any,
    *,
    requested_timestamp_ms: int,
    actual_timestamp_ms: int,
    frame_index: int,
    media_id: str,
    processing_run_id: str,
    storage_directory: Path,
    settings: VideoProcessingSettings,
) -> ExtractedFrame:
    output_path = storage_directory / f"{uuid.uuid4()}.jpg"
    try:
        decoded_width = int(decoded.width)
        decoded_height = int(decoded.height)
        if (
            decoded_width <= 0
            or decoded_height <= 0
            or decoded_width > settings.max_width
            or decoded_height > settings.max_height
        ):
            raise _failure(
                "VIDEO_RESOLUTION_LIMIT_EXCEEDED",
                "A decoded frame exceeds the configured resolution limit.",
            )
        try:
            rgb = decoded.to_ndarray(format="rgb24")
        except Exception:
            raise _failure("VIDEO_DECODE_FAILED", "The video frame could not be decoded safely.") from None
        image = Image.fromarray(rgb).convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0 or width > settings.max_width or height > settings.max_height:
            raise _failure(
                "VIDEO_RESOLUTION_LIMIT_EXCEEDED",
                "A decoded frame exceeds the configured resolution limit.",
            )
        descriptor = os.open(
            output_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            image.save(output, format="JPEG", quality=90, optimize=False)
            output.flush()
            os.fsync(output.fileno())
        checksum = _sha256_file(output_path, settings.max_size_bytes)
        return ExtractedFrame(
            requested_timestamp_ms=requested_timestamp_ms,
            actual_timestamp_ms=actual_timestamp_ms,
            frame_index=frame_index,
            width=width,
            height=height,
            sha256=checksum,
            artifact_reference=output_path.name,
        )
    except MediaProcessingException:
        _remove_artifact(output_path)
        raise
    except Exception:
        _remove_artifact(output_path)
        raise _failure("FRAME_STORAGE_FAILED", "Unable to store a decoded video frame.") from None


def _check_timeout(started: float, timeout_seconds: float) -> None:
    if time.monotonic() - started > timeout_seconds:
        raise MediaProcessingException(
            ProcessingFailure(
                "VIDEO_PROCESSING_TIMEOUT",
                "Video processing exceeded the configured time limit.",
                retryable=True,
            )
        )


def _failure(code: str, message: str) -> MediaProcessingException:
    return MediaProcessingException(ProcessingFailure(code, message))


def _cleanup_artifacts(paths: list[Path]) -> None:
    for path in paths:
        _remove_artifact(path)


def _remove_artifact(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to remove partial video frame artifact")

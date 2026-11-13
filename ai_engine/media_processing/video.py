"""Dependency-free contracts for future video frame extraction adapters.

No video decoder is installed in the current project environment, so this
module intentionally defines the input, sampling policy, frame provenance, and
extractor protocol without claiming to decode video itself.
"""

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Mapping, Protocol, Sequence
from uuid import UUID


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_FRAME_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


@dataclass(frozen=True)
class VideoInput:
    """Original video bytes and source identity for a future decoder adapter."""

    media_id: str
    investigation_id: str
    mime_type: str
    size_bytes: int
    sha256: str
    video_bytes: bytes

    def __post_init__(self) -> None:
        _require_uuid("media_id", self.media_id)
        _require_uuid("investigation_id", self.investigation_id)
        if not isinstance(self.mime_type, str) or not self.mime_type.startswith("video/"):
            raise ValueError("mime_type must identify video content.")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes <= 0:
            raise ValueError("size_bytes must be a positive integer.")
        if not isinstance(self.video_bytes, bytes) or len(self.video_bytes) != self.size_bytes:
            raise ValueError("video_bytes length must match size_bytes.")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("sha256 must be a hexadecimal SHA-256 digest.")
        if sha256(self.video_bytes).hexdigest() != self.sha256.lower():
            raise ValueError("video_bytes checksum does not match sha256.")


@dataclass(frozen=True)
class VideoSamplingConfig:
    """Bounded, deterministic frame sampling policy."""

    interval_ms: int = 1000
    max_frames: int = 100

    def __post_init__(self) -> None:
        if isinstance(self.interval_ms, bool) or not isinstance(self.interval_ms, int) or self.interval_ms <= 0:
            raise ValueError("interval_ms must be a positive integer.")
        if isinstance(self.max_frames, bool) or not isinstance(self.max_frames, int) or self.max_frames <= 0:
            raise ValueError("max_frames must be a positive integer.")


@dataclass(frozen=True)
class ExtractedVideoFrame:
    """Frame bytes and source-time provenance returned by a future extractor."""

    source_video_id: str
    derived_media_id: str
    frame_index: int
    source_timestamp_ms: int
    mime_type: str
    width: int
    height: int
    sha256: str
    image_bytes: bytes
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_uuid("source_video_id", self.source_video_id)
        _require_uuid("derived_media_id", self.derived_media_id)
        for name in ("frame_index", "source_timestamp_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        if self.mime_type not in _FRAME_MIME_TYPES:
            raise ValueError("frame mime_type must be JPEG, PNG, or WebP.")
        for name in ("width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if not isinstance(self.image_bytes, bytes) or not self.image_bytes:
            raise ValueError("image_bytes must be non-empty bytes.")
        if not isinstance(self.sha256, str) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("sha256 must be a hexadecimal SHA-256 digest.")
        if sha256(self.image_bytes).hexdigest() != self.sha256.lower():
            raise ValueError("image_bytes checksum does not match sha256.")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping.")


class VideoFrameExtractor(Protocol):
    """Interface implemented by a decoder-backed video package later."""

    def extract(
        self,
        video: VideoInput,
        config: VideoSamplingConfig,
    ) -> Sequence[ExtractedVideoFrame]:
        """Return frames preserving their original frame indexes and timestamps."""
        ...


def validate_extracted_frames(
    video: VideoInput,
    config: VideoSamplingConfig,
    frames: Sequence[ExtractedVideoFrame],
) -> tuple[ExtractedVideoFrame, ...]:
    """Validate bounds, source links, and deterministic timestamp ordering."""
    if len(frames) > config.max_frames:
        raise ValueError("extractor returned more frames than max_frames.")
    previous_timestamp: int | None = None
    seen_indexes: set[int] = set()
    for frame in frames:
        if not isinstance(frame, ExtractedVideoFrame):
            raise TypeError("frames must contain only ExtractedVideoFrame values.")
        if frame.source_video_id != video.media_id:
            raise ValueError("frame source_video_id does not match the input video.")
        if frame.frame_index in seen_indexes:
            raise ValueError("frame_index values must be unique.")
        if previous_timestamp is not None and frame.source_timestamp_ms < previous_timestamp:
            raise ValueError("source timestamps must be non-decreasing.")
        if previous_timestamp is not None and frame.source_timestamp_ms - previous_timestamp < config.interval_ms:
            raise ValueError("frames are closer together than the configured sampling interval.")
        previous_timestamp = frame.source_timestamp_ms
        seen_indexes.add(frame.frame_index)
    return tuple(frames)


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{name} must be a valid UUID.") from exc

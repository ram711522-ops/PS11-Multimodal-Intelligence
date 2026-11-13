"""Typed, framework-independent contracts for image processing stages.

These models describe data crossing the backend-to-AI boundary. They do not
perform image transformations, persistence, queueing, or API serialization.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Mapping
from uuid import UUID


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ProcessingStatus(str, Enum):
    """Lifecycle states shared by media processing producers and consumers."""

    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ImageMetadata:
    """Validated public metadata for an uploaded image."""

    media_id: str
    investigation_id: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    sha256: str
    original_filename: str
    uploaded_at: str

    def __post_init__(self) -> None:
        _validate_uuid("media_id", self.media_id)
        _validate_uuid("investigation_id", self.investigation_id)
        if self.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
            raise ValueError("mime_type must be JPEG, PNG, or WebP.")
        _validate_positive_integer("width", self.width)
        _validate_positive_integer("height", self.height)
        _validate_positive_integer("size_bytes", self.size_bytes)
        _validate_sha256(self.sha256)
        if not isinstance(self.original_filename, str) or not self.original_filename.strip():
            raise ValueError("original_filename must be a non-empty string.")
        _validate_utc_timestamp("uploaded_at", self.uploaded_at)
        object.__setattr__(self, "sha256", self.sha256.lower())


@dataclass(frozen=True)
class MediaInput:
    """Image bytes plus validated upload metadata for an in-process consumer.

    ``image_bytes`` is intentionally an in-process payload. Queue adapters
    should transport a private storage reference and construct this value at
    the worker boundary rather than serializing the bytes into job metadata.
    """

    metadata: ImageMetadata
    image_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, ImageMetadata):
            raise TypeError("metadata must be an ImageMetadata instance.")
        if not isinstance(self.image_bytes, bytes) or not self.image_bytes:
            raise ValueError("image_bytes must be non-empty bytes.")
        if len(self.image_bytes) != self.metadata.size_bytes:
            raise ValueError("image_bytes length must match metadata.size_bytes.")
        if hashlib.sha256(self.image_bytes).hexdigest() != self.metadata.sha256:
            raise ValueError("image_bytes checksum must match metadata.sha256.")


@dataclass(frozen=True)
class ProcessingFailure:
    """Safe, structured information about a failed processing attempt."""

    code: str
    message: str
    occurred_at: str

    def __post_init__(self) -> None:
        _validate_non_empty("code", self.code)
        _validate_non_empty("message", self.message)
        _validate_utc_timestamp("occurred_at", self.occurred_at)


@dataclass(frozen=True)
class ProcessingJob:
    """Lifecycle record for one media-processing attempt."""

    job_id: str
    media_id: str
    investigation_id: str
    processing_status: ProcessingStatus | str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    failure: ProcessingFailure | None = None

    def __post_init__(self) -> None:
        _validate_uuid("job_id", self.job_id)
        _validate_uuid("media_id", self.media_id)
        _validate_uuid("investigation_id", self.investigation_id)
        try:
            status = ProcessingStatus(self.processing_status)
        except (TypeError, ValueError) as exc:
            raise ValueError("processing_status is not a supported status.") from exc
        object.__setattr__(self, "processing_status", status)
        created = _validate_utc_timestamp("created_at", self.created_at)
        started = _validate_optional_timestamp("started_at", self.started_at)
        completed = _validate_optional_timestamp("completed_at", self.completed_at)
        if started is not None and started < created:
            raise ValueError("started_at cannot precede created_at.")
        if completed is not None and completed < (started or created):
            raise ValueError("completed_at cannot precede the job start or creation.")
        if self.failure is not None and not isinstance(self.failure, ProcessingFailure):
            raise TypeError("failure must be a ProcessingFailure or None.")


@dataclass(frozen=True)
class ProcessingResult:
    """Machine-produced result attached to a completed processing job."""

    result_id: str
    job_id: str
    media_id: str
    algorithm: str
    algorithm_version: str
    result_type: str
    result_data: Mapping[str, object]
    created_at: str

    def __post_init__(self) -> None:
        _validate_uuid("result_id", self.result_id)
        _validate_uuid("job_id", self.job_id)
        _validate_uuid("media_id", self.media_id)
        for field_name in ("algorithm", "algorithm_version", "result_type"):
            _validate_non_empty(field_name, getattr(self, field_name))
        if not isinstance(self.result_data, Mapping):
            raise TypeError("result_data must be a mapping.")
        _validate_utc_timestamp("created_at", self.created_at)


@dataclass(frozen=True)
class DerivedMediaReference:
    """Reference and provenance for an image derived from uploaded media."""

    derived_media_id: str
    source_media_id: str
    transformation: str
    transformation_version: str
    mime_type: str
    width: int
    height: int
    sha256: str

    def __post_init__(self) -> None:
        _validate_uuid("derived_media_id", self.derived_media_id)
        _validate_uuid("source_media_id", self.source_media_id)
        _validate_non_empty("transformation", self.transformation)
        _validate_non_empty("transformation_version", self.transformation_version)
        if self.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
            raise ValueError("mime_type must be JPEG, PNG, or WebP.")
        _validate_positive_integer("width", self.width)
        _validate_positive_integer("height", self.height)
        _validate_sha256(self.sha256)
        object.__setattr__(self, "sha256", self.sha256.lower())


def _validate_uuid(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string.")
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a valid UUID.") from exc


def _validate_positive_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")


def _validate_sha256(value: str) -> None:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ValueError("sha256 must be a 64-character hexadecimal SHA-256 digest.")


def _validate_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_optional_timestamp(name: str, value: str | None) -> datetime | None:
    return _validate_utc_timestamp(name, value) if value is not None else None


def _validate_utc_timestamp(name: str, value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UTC ISO 8601 timestamp string.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must include a UTC timezone.")
    return parsed.astimezone(timezone.utc)

"""Strict adapter from the approved Phase 3 upload response to AI media input."""

from collections.abc import Mapping

from .models import ImageMetadata, MediaInput


PHASE3_UPLOAD_FIELDS = frozenset({
    "investigation_id",
    "media_id",
    "filename",
    "mime_type",
    "size_bytes",
    "checksum",
    "width",
    "height",
    "uploaded_at",
})


class Phase3AdapterError(ValueError):
    """Raised when confirmed Phase 3 upload metadata is malformed or incomplete."""


def media_input_from_phase3_upload(
    response: Mapping[str, object], image_bytes: bytes
) -> MediaInput:
    """Map the exact documented upload payload and original bytes to Stage 1.

    This function does not make an HTTP request. It accepts only the nine
    documented success fields and verifies size/checksum through ``MediaInput``.
    """
    if not isinstance(response, Mapping) or set(response) != PHASE3_UPLOAD_FIELDS:
        raise Phase3AdapterError("Phase 3 upload response fields do not match the approved contract.")
    try:
        metadata = ImageMetadata(
            media_id=_require_str(response, "media_id"),
            investigation_id=_require_str(response, "investigation_id"),
            mime_type=_require_str(response, "mime_type"),
            width=_require_int(response, "width"),
            height=_require_int(response, "height"),
            size_bytes=_require_int(response, "size_bytes"),
            sha256=_require_str(response, "checksum"),
            original_filename=_require_str(response, "filename"),
            uploaded_at=_require_str(response, "uploaded_at"),
        )
        return MediaInput(metadata=metadata, image_bytes=image_bytes)
    except (TypeError, ValueError) as exc:
        raise Phase3AdapterError("Phase 3 upload metadata could not be validated.") from exc


def _require_str(response: Mapping[str, object], field: str) -> str:
    value = response[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _require_int(response: Mapping[str, object], field: str) -> int:
    value = response[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value

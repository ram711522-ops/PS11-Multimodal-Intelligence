"""Storage boundary for loading original Phase 3 media into processing.

The current checkout does not contain the approved ``media_assets`` table or
upload filesystem implementation. This module intentionally does not guess
their columns, filenames, or directory layout. A backend owner can inject a
loader that returns the documented Phase 3 upload metadata and original bytes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol


class MediaSourceUnavailable(RuntimeError):
    """Raised when the configured storage cannot load original media."""


@dataclass(frozen=True)
class StoredPhase3Media:
    """Documented Phase 3 upload response plus its original unmodified bytes."""

    upload_response: Mapping[str, object]
    original_image_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.upload_response, Mapping):
            raise TypeError("upload_response must be a mapping.")
        if not isinstance(self.original_image_bytes, bytes) or not self.original_image_bytes:
            raise ValueError("original_image_bytes must be non-empty bytes.")


class MediaSourceStorage(Protocol):
    """Port implemented by the approved media storage integration."""

    available: bool

    def load_media(self, investigation_id: str, media_id: str) -> StoredPhase3Media:
        """Load a media asset and return its upload metadata and original bytes."""


class UnavailableMediaSourceStorage:
    """Default adapter until Phase 3 storage is available in this backend."""

    available = False

    def load_media(self, investigation_id: str, media_id: str) -> StoredPhase3Media:
        raise MediaSourceUnavailable("Original media storage is not configured.")


class CallbackMediaSourceStorage:
    """Safe adapter around a future repository/filesystem loader callback.

    The callback must return the exact documented Phase 3 upload fields and
    the original bytes. The downstream AI adapter validates the field set,
    metadata, and checksum before processing.
    """

    def __init__(self, loader: Callable[[str, str], StoredPhase3Media]):
        self._loader = loader
        self.available = True

    def load_media(self, investigation_id: str, media_id: str) -> StoredPhase3Media:
        try:
            stored = self._loader(investigation_id, media_id)
        except MediaSourceUnavailable:
            raise
        except Exception as exc:
            raise MediaSourceUnavailable("Original media could not be loaded.") from exc
        if not isinstance(stored, StoredPhase3Media):
            raise MediaSourceUnavailable("Original media storage returned invalid data.")
        response = stored.upload_response
        if response.get("investigation_id") != investigation_id or response.get("media_id") != media_id:
            raise MediaSourceUnavailable("Stored media identifiers did not match the requested asset.")
        return stored

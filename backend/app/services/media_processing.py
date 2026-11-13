"""Stream and validate an image upload without parsing HTTP multipart data."""

from dataclasses import dataclass
import hashlib
import logging
import ntpath
from pathlib import Path
import tempfile
import unicodedata
import warnings
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from backend.app.errors import ApiError


logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_WIDTH = 8192
MAX_IMAGE_HEIGHT = 8192
MAX_IMAGE_PIXELS = 40_000_000
READ_CHUNK_SIZE = 64 * 1024

IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True)
class ProcessedImageUpload:
    """Validated temporary image file; its caller owns cleanup or finalization."""

    temporary_path: Path
    filename: str
    mime_type: str
    storage_extension: str
    size_bytes: int
    checksum: str
    width: int
    height: int

    def cleanup(self) -> None:
        """Remove the temporary image file without exposing filesystem details."""

        try:
            self.temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Unable to remove staged media file")
            raise ApiError(
                500,
                "MEDIA_STORAGE_FAILED",
                "Unable to remove the staged media file.",
            ) from None


def sanitize_display_filename(filename: str | None) -> str:
    """Return a safe display basename while preserving normalized Unicode."""

    if not isinstance(filename, str):
        return "image"

    normalized = unicodedata.normalize("NFC", filename)
    basename = ntpath.basename(normalized)
    sanitized = "".join(
        character
        for character in basename
        if unicodedata.category(character) != "Cc"
    )
    sanitized = unicodedata.normalize("NFC", sanitized)[:255]
    return sanitized or "image"


def process_image_upload(
    source: BinaryIO,
    *,
    original_filename: str | None,
    storage_path: Path | str,
) -> ProcessedImageUpload:
    """Stream, hash, and validate an image into a temporary storage file.

    The source is read in bounded chunks. The temporary file remains available on
    success for the caller to atomically move into UUID-based physical storage.
    Any validation or processing failure removes the temporary file.
    """

    storage_root = Path(storage_path)
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("Unable to prepare media storage")
        raise ApiError(
            500,
            "MEDIA_STORAGE_FAILED",
            "Unable to prepare media storage.",
        ) from None

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".ps11-upload-",
            suffix=".tmp",
            dir=storage_root,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            byte_count = 0
            checksum = hashlib.sha256()

            while True:
                bytes_remaining = MAX_UPLOAD_BYTES - byte_count
                read_size = min(READ_CHUNK_SIZE, bytes_remaining + 1)
                try:
                    chunk = source.read(read_size)
                except Exception:
                    logger.exception("Unable to read incoming media bytes")
                    raise ApiError(
                        400,
                        "INVALID_UPLOAD_REQUEST",
                        "Unable to read the uploaded image.",
                    ) from None

                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise ApiError(
                        400,
                        "INVALID_UPLOAD_REQUEST",
                        "The uploaded image stream is invalid.",
                    )

                next_byte_count = byte_count + len(chunk)
                if next_byte_count > MAX_UPLOAD_BYTES:
                    raise ApiError(
                        413,
                        "FILE_TOO_LARGE",
                        "The uploaded file exceeds 10 MiB.",
                    )

                written = temporary_file.write(chunk)
                if written != len(chunk):
                    raise OSError("Incomplete temporary media write")
                checksum.update(chunk)
                byte_count = next_byte_count

            temporary_file.flush()

        if byte_count == 0:
            raise ApiError(422, "INVALID_IMAGE", "The uploaded file is not a valid image.")

        image_format, mime_type, storage_extension, width, height = _validate_image_file(
            temporary_path
        )
        del image_format
        processed = ProcessedImageUpload(
            temporary_path=temporary_path,
            filename=sanitize_display_filename(original_filename),
            mime_type=mime_type,
            storage_extension=storage_extension,
            size_bytes=byte_count,
            checksum=checksum.hexdigest(),
            width=width,
            height=height,
        )
        return processed
    except ApiError:
        _cleanup_failed_temporary_file(temporary_path)
        raise
    except OSError:
        logger.exception("Media upload file processing failed")
        _cleanup_failed_temporary_file(temporary_path)
        raise ApiError(
            500,
            "MEDIA_STORAGE_FAILED",
            "Unable to stage the uploaded image.",
        ) from None
    except Exception:
        _cleanup_failed_temporary_file(temporary_path)
        raise


def _validate_image_file(path: Path) -> tuple[str, str, str, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = image.format or ""
                format_details = IMAGE_FORMATS.get(image_format)
                if format_details is None:
                    raise ApiError(
                        415,
                        "UNSUPPORTED_MEDIA_TYPE",
                        "The uploaded image format is not supported.",
                    )

                width, height = image.size
                _validate_dimensions(width, height)

                if image_format == "WEBP" and (
                    bool(getattr(image, "is_animated", False))
                    or int(getattr(image, "n_frames", 1)) > 1
                ):
                    raise ApiError(
                        415,
                        "ANIMATED_WEBP_NOT_SUPPORTED",
                        "Animated WebP images are not supported.",
                    )

                image.verify()

            # verify() checks structure but invalidates the decoder; reopen and load
            # to ensure the complete first/static image data can actually be decoded.
            with Image.open(path) as decoded_image:
                if decoded_image.format != image_format:
                    raise UnidentifiedImageError("Image format changed during validation")
                decoded_image.load()
    except ApiError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ApiError(
            422,
            "IMAGE_DIMENSIONS_EXCEEDED",
            "The image exceeds the allowed dimensions or pixel count.",
        ) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError, MemoryError):
        raise ApiError(
            422,
            "INVALID_IMAGE",
            "The uploaded file is not a valid supported image.",
        ) from None

    mime_type, extension = format_details
    return image_format, mime_type, extension, width, height


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_WIDTH
        or height > MAX_IMAGE_HEIGHT
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ApiError(
            422,
            "IMAGE_DIMENSIONS_EXCEEDED",
            "The image exceeds the allowed dimensions or pixel count.",
        )


def _cleanup_failed_temporary_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to remove temporary media file after processing failure")
        raise ApiError(
            500,
            "MEDIA_STORAGE_FAILED",
            "Unable to clean up the staged media file.",
        ) from None

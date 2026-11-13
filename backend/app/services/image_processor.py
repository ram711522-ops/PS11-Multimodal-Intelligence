"""Pillow-based, non-destructive image normalization processor."""

from hashlib import sha256
import hmac
import logging
from pathlib import Path
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.app.services.media_processing import (
    MAX_IMAGE_HEIGHT,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_WIDTH,
    MAX_UPLOAD_BYTES,
)
from backend.app.services.media_pipeline import (
    ImageProcessingResult,
    MediaProcessingException,
    MediaProcessingRequest,
    ProcessingFailure,
)
from backend.app.services.perceptual_hash import image_phash


logger = logging.getLogger(__name__)
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
PROCESSOR_NAME = "ps11.image.normalizer"
PROCESSOR_VERSION = "1.0.0"
RESULT_TYPE = "image_metadata"
RESULT_SCHEMA_VERSION = "1.0"


class ImageProcessor:
    processor_name = PROCESSOR_NAME
    processor_version = PROCESSOR_VERSION
    result_type = RESULT_TYPE
    result_schema_version = RESULT_SCHEMA_VERSION

    def process(self, request: MediaProcessingRequest) -> ImageProcessingResult:
        """Verify source bytes, apply orientation, and write a separate RGB PNG."""

        output_path: Path | None = None
        try:
            digest = _sha256_file(request.storage_reference)
            if not hmac.compare_digest(digest, request.expected_checksum.lower()):
                raise MediaProcessingException(
                    ProcessingFailure(
                        "SOURCE_CHECKSUM_MISMATCH",
                        "The stored image checksum does not match its upload record.",
                    )
                )

            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(request.storage_reference) as original:
                    source_format = (original.format or "").upper()
                    if source_format not in SUPPORTED_FORMATS:
                        raise MediaProcessingException(
                            ProcessingFailure(
                                "UNSUPPORTED_IMAGE_FORMAT",
                                "The stored image format is not supported for processing.",
                            )
                        )
                    source_width, source_height = original.size
                    if (
                        source_width <= 0
                        or source_height <= 0
                        or source_width > MAX_IMAGE_WIDTH
                        or source_height > MAX_IMAGE_HEIGHT
                        or source_width * source_height > MAX_IMAGE_PIXELS
                    ):
                        raise MediaProcessingException(
                            ProcessingFailure(
                                "IMAGE_LIMIT_EXCEEDED",
                                "The image exceeds safe decode limits.",
                            )
                        )
                    source_mode = original.mode
                    bands = tuple(original.getbands())
                    has_alpha = "A" in bands or "transparency" in original.info
                    icc_profile_present = bool(original.info.get("icc_profile"))
                    oriented = ImageOps.exif_transpose(original)
                    oriented.load()
                    rgb_image = oriented.convert("RGB")
                    perceptual_hash = image_phash(rgb_image)

            request.artifact_storage_path.mkdir(parents=True, exist_ok=True)
            output_path = request.artifact_storage_path / f"{uuid.uuid4()}.png"
            try:
                with output_path.open("xb") as output_file:
                    rgb_image.save(output_file, format="PNG")
                    output_file.flush()
            except OSError:
                _remove_output(output_path)
                raise MediaProcessingException(
                    ProcessingFailure(
                        "NORMALIZED_STORAGE_FAILED",
                        "Unable to store the normalized image artifact.",
                    )
                ) from None

            return ImageProcessingResult(
                source_format=source_format,
                source_width=source_width,
                source_height=source_height,
                width=rgb_image.width,
                height=rgb_image.height,
                source_color_mode=source_mode,
                normalized_color_mode="RGB",
                bands=bands,
                has_alpha=has_alpha,
                icc_profile_present=icc_profile_present,
                sha256=digest,
                perceptual_hash=perceptual_hash,
                normalized_artifact_reference=output_path.name,
            )
        except MediaProcessingException:
            _remove_output(output_path)
            raise
        except FileNotFoundError:
            raise MediaProcessingException(
                ProcessingFailure("MEDIA_FILE_NOT_FOUND", "The stored image file was not found.")
            ) from None
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            _remove_output(output_path)
            raise MediaProcessingException(
                ProcessingFailure("IMAGE_LIMIT_EXCEEDED", "The image exceeds safe decode limits.")
            ) from None
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError, MemoryError):
            _remove_output(output_path)
            logger.warning("Stored image could not be decoded or read safely")
            raise MediaProcessingException(
                ProcessingFailure("INVALID_IMAGE", "The stored image could not be decoded safely.")
            ) from None
        except Exception:
            _remove_output(output_path)
            logger.exception("Unexpected image normalization failure")
            raise MediaProcessingException(
                ProcessingFailure("IMAGE_PROCESSING_FAILED", "Image processing failed.")
            ) from None


def _sha256_file(path: Path) -> str:
    checksum = sha256()
    byte_count = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            byte_count += len(chunk)
            if byte_count > MAX_UPLOAD_BYTES:
                raise MediaProcessingException(
                    ProcessingFailure(
                        "IMAGE_LIMIT_EXCEEDED",
                        "The image exceeds safe processing limits.",
                    )
                )
            checksum.update(chunk)
    return checksum.hexdigest()


def _remove_output(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to clean up failed normalized image artifact")

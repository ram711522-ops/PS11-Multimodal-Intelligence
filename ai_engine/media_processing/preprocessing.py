"""Deterministic, backend-independent normalization of uploaded images."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

from .models import DerivedMediaReference, MediaInput


MAX_WIDTH = 8192
MAX_HEIGHT = 8192
MAX_PIXELS = 40_000_000
SUPPORTED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
TRANSFORMATION = "exif_orient_rgb_white_background_jpeg"
TRANSFORMATION_VERSION = "1.0.0"


class ImagePreprocessingError(ValueError):
    """Base class for safe, expected image preprocessing failures."""


class UnsupportedImageFormatError(ImagePreprocessingError):
    """Raised when decoded image content is not JPEG, PNG, or WebP."""


class InvalidImageError(ImagePreprocessingError):
    """Raised when image bytes are corrupt, truncated, or inconsistent."""


class ImageDimensionsExceededError(ImagePreprocessingError):
    """Raised before image decoding when configured dimension limits are exceeded."""


class AnimatedImageError(ImagePreprocessingError):
    """Raised for multi-frame WebP content, which Phase 3 upload rejects."""


@dataclass(frozen=True)
class PreprocessedImage:
    """Normalized JPEG bytes and their provenance metadata."""

    image_bytes: bytes
    reference: DerivedMediaReference


def preprocess_image(media_input: MediaInput, *, derived_media_id: str | None = None) -> PreprocessedImage:
    """Orient and normalize a validated media input to deterministic JPEG.

    The original ``MediaInput.image_bytes`` are read-only and never modified.
    The output keeps original pixel dimensions (after EXIF orientation), uses
    RGB color, composites transparency onto white, strips source metadata, and
    applies JPEG encoding only. No resizing or other image transform occurs.

    Raises typed ``ImagePreprocessingError`` subclasses for invalid, unsupported,
    animated, or over-limit content. Dimensions are checked before pixel decode.
    """
    if not isinstance(media_input, MediaInput):
        raise TypeError("media_input must be a MediaInput instance.")
    content = media_input.image_bytes

    try:
        with Image.open(BytesIO(content)) as probe:
            image_format = probe.format
            if image_format not in SUPPORTED_FORMATS:
                raise UnsupportedImageFormatError("Only JPEG, PNG, and WebP images are supported.")
            _validate_dimensions(probe.width, probe.height)
            if image_format == "WEBP" and getattr(probe, "is_animated", False):
                raise AnimatedImageError("Animated WebP images are not supported.")
            if getattr(probe, "n_frames", 1) > 1:
                raise AnimatedImageError("Animated images are not supported.")
            expected_mime = SUPPORTED_FORMATS[image_format]
            if media_input.metadata.mime_type != expected_mime:
                raise InvalidImageError("Image content does not match its validated MIME metadata.")
            probe.verify()

        with Image.open(BytesIO(content)) as source:
            # Confirm again on the decoding handle before loading pixel data.
            _validate_dimensions(source.width, source.height)
            source.load()
            oriented = ImageOps.exif_transpose(source)
            width, height = oriented.size
            _validate_dimensions(width, height)
            rgb = _to_rgb_on_white(oriented)
            output = BytesIO()
            rgb.save(
                output,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=False,
                progressive=False,
            )
            normalized_bytes = output.getvalue()
    except ImagePreprocessingError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageDimensionsExceededError("Image dimensions exceed the supported limits.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidImageError("Image bytes could not be safely decoded.") from exc

    reference = DerivedMediaReference(
        derived_media_id=derived_media_id or str(uuid4()),
        source_media_id=media_input.metadata.media_id,
        transformation=TRANSFORMATION,
        transformation_version=TRANSFORMATION_VERSION,
        mime_type="image/jpeg",
        width=width,
        height=height,
        sha256=sha256(normalized_bytes).hexdigest(),
    )
    return PreprocessedImage(image_bytes=normalized_bytes, reference=reference)


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise InvalidImageError("Image dimensions must be positive.")
    if width > MAX_WIDTH or height > MAX_HEIGHT or width * height > MAX_PIXELS:
        raise ImageDimensionsExceededError("Image dimensions exceed the supported limits.")


def _to_rgb_on_white(image: Image.Image) -> Image.Image:
    """Convert a decoded image to RGB, flattening alpha onto white."""
    has_alpha = "A" in image.getbands() or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        foreground = image.convert("RGBA")
        background = Image.new("RGBA", foreground.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, foreground).convert("RGB")
    return image.convert("RGB")

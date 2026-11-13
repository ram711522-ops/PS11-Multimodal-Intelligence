"""Tests for deterministic, bounded image preprocessing."""

from hashlib import sha256
from io import BytesIO
import unittest
from unittest.mock import patch
from uuid import UUID

from PIL import Image, ImageFile

from ai_engine.media_processing import (
    AnimatedImageError,
    ImageDimensionsExceededError,
    ImageMetadata,
    InvalidImageError,
    MediaInput,
    UnsupportedImageFormatError,
    preprocess_image,
)
from ai_engine.media_processing import preprocessing


MEDIA_ID = "7b28b64d-7a3f-4c88-a648-61e881ab15b2"
INVESTIGATION_ID = "255424f1-2e46-48b0-a457-53a5f0b26e60"
UPLOADED_AT = "2026-11-13T08:15:30.123456Z"


def encoded_image(format_name: str, *, mode="RGB", color=(40, 90, 150), size=(12, 8), **options) -> bytes:
    image = Image.new(mode, size, color)
    stream = BytesIO()
    image.save(stream, format=format_name, **options)
    return stream.getvalue()


def media_input(content: bytes, mime_type: str, filename: str = "source.bin") -> MediaInput:
    try:
        with Image.open(BytesIO(content)) as image:
            width, height, detected = image.width, image.height, image.format
        actual_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(detected, mime_type)
    except (OSError, ValueError):
        # The contract type validates metadata consistency, not image decoding.
        width, height, actual_mime = 1, 1, mime_type
    metadata = ImageMetadata(
        media_id=MEDIA_ID,
        investigation_id=INVESTIGATION_ID,
        mime_type=mime_type or actual_mime,
        width=width,
        height=height,
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
        original_filename=filename,
        uploaded_at=UPLOADED_AT,
    )
    return MediaInput(metadata, content)


class ImagePreprocessingTests(unittest.TestCase):
    def setUp(self):
        self.original_truncated_setting = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = False

    def tearDown(self):
        ImageFile.LOAD_TRUNCATED_IMAGES = self.original_truncated_setting

    def test_normalizes_jpeg_png_and_webp_to_rgb_jpeg(self):
        for source_format, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")):
            with self.subTest(source_format=source_format):
                original = encoded_image(source_format)
                result = preprocess_image(media_input(original, mime), derived_media_id=MEDIA_ID)
                with Image.open(BytesIO(result.image_bytes)) as normalized:
                    self.assertEqual(normalized.format, "JPEG")
                    self.assertEqual(normalized.mode, "RGB")
                    self.assertEqual(normalized.size, (12, 8))
                self.assertEqual(result.reference.source_media_id, MEDIA_ID)
                self.assertEqual(result.reference.derived_media_id, MEDIA_ID)
                self.assertEqual(result.reference.mime_type, "image/jpeg")
                self.assertEqual(result.reference.width, 12)
                self.assertEqual(result.reference.height, 8)
                self.assertEqual(result.reference.sha256, sha256(result.image_bytes).hexdigest())

    def test_exif_orientation_is_applied_and_dimensions_follow_rotation(self):
        exif = Image.Exif()
        exif[274] = 6  # Rotate 90 degrees clockwise.
        source = Image.new("RGB", (7, 3), (30, 80, 130))
        stream = BytesIO()
        source.save(stream, format="JPEG", exif=exif)
        original = stream.getvalue()

        result = preprocess_image(media_input(original, "image/jpeg"), derived_media_id=MEDIA_ID)
        self.assertEqual((result.reference.width, result.reference.height), (3, 7))
        with Image.open(BytesIO(result.image_bytes)) as normalized:
            self.assertEqual(normalized.size, (3, 7))

    def test_transparent_png_and_webp_are_composited_on_white(self):
        for fmt, mime, options in (
            ("PNG", "image/png", {}),
            ("WEBP", "image/webp", {"lossless": True}),
        ):
            with self.subTest(fmt=fmt):
                content = encoded_image(fmt, mode="RGBA", color=(255, 0, 0, 0), **options)
                result = preprocess_image(media_input(content, mime), derived_media_id=MEDIA_ID)
                with Image.open(BytesIO(result.image_bytes)) as normalized:
                    self.assertEqual(normalized.getpixel((0, 0)), (255, 255, 255))

    def test_corrupt_and_truncated_images_fail_safely(self):
        for content in (b"not an image", encoded_image("PNG")[:-12]):
            with self.subTest(size=len(content)):
                with self.assertRaises(InvalidImageError):
                    preprocess_image(media_input(content, "image/png"))

    def test_recognized_unsupported_format_is_rejected(self):
        gif = encoded_image("GIF")
        with self.assertRaises(UnsupportedImageFormatError):
            preprocess_image(media_input(gif, "image/png"))

    def test_content_mime_mismatch_is_invalid(self):
        png = encoded_image("PNG")
        with self.assertRaises(InvalidImageError):
            preprocess_image(media_input(png, "image/jpeg"))

    def test_animated_webp_is_rejected(self):
        first = Image.new("RGB", (8, 8), "red")
        second = Image.new("RGB", (8, 8), "blue")
        stream = BytesIO()
        first.save(stream, format="WEBP", save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaises(AnimatedImageError):
            preprocess_image(media_input(stream.getvalue(), "image/webp"))

    def test_dimension_limits_are_checked_before_pixel_load(self):
        for width, height in ((8193, 1), (1, 8193), (7000, 6000)):
            class HeaderOnlyImage:
                format = "PNG"
                n_frames = 1

                def __init__(self):
                    self.width = width
                    self.height = height

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def verify(self):
                    raise AssertionError("verified/decode path reached before bounds check")

            payload = encoded_image("PNG")
            value = media_input(payload, "image/png")
            with self.subTest(width=width, height=height), patch.object(
                preprocessing.Image, "open", return_value=HeaderOnlyImage()
            ):
                with self.assertRaises(ImageDimensionsExceededError):
                    preprocess_image(value)

    def test_output_is_deterministic_for_same_input_and_version(self):
        content = encoded_image("PNG", color=(20, 80, 140))
        value = media_input(content, "image/png")
        left = preprocess_image(value, derived_media_id=MEDIA_ID)
        right = preprocess_image(value, derived_media_id=MEDIA_ID)
        self.assertEqual(left.image_bytes, right.image_bytes)
        self.assertEqual(left.reference.sha256, right.reference.sha256)

    def test_original_media_bytes_are_not_modified(self):
        content = encoded_image("PNG", color=(100, 30, 220))
        original_snapshot = bytes(content)
        value = media_input(content, "image/png")
        preprocess_image(value, derived_media_id=MEDIA_ID)
        self.assertEqual(value.image_bytes, original_snapshot)
        self.assertEqual(content, original_snapshot)

    def test_generated_derived_id_is_a_uuid_and_transformation_is_versioned(self):
        result = preprocess_image(media_input(encoded_image("JPEG"), "image/jpeg"))
        UUID(result.reference.derived_media_id)
        self.assertEqual(result.reference.transformation, preprocessing.TRANSFORMATION)
        self.assertEqual(result.reference.transformation_version, preprocessing.TRANSFORMATION_VERSION)


if __name__ == "__main__":
    unittest.main()

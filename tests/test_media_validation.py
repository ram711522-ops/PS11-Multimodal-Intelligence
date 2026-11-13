"""Tests for Stage 2 image validation and temporary upload processing."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import unicodedata

from PIL import Image

from backend.app.errors import ApiError
from backend.app.services.media_processing import (
    MAX_IMAGE_HEIGHT,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_WIDTH,
    MAX_UPLOAD_BYTES,
    READ_CHUNK_SIZE,
    process_image_upload,
    sanitize_display_filename,
)


class RecordingStream(BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class MediaValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.storage_path = Path(self.temp_directory.name) / "media"

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def process(self, content: bytes, filename: str = "upload.bin"):
        return process_image_upload(
            BytesIO(content),
            original_filename=filename,
            storage_path=self.storage_path,
        )

    def test_valid_jpeg_png_and_webp_use_decoded_format(self) -> None:
        cases = (
            ("JPEG", "image/jpeg", ".jpg", "jpeg.bin"),
            ("PNG", "image/png", ".png", "png.jpg"),
            ("WEBP", "image/webp", ".webp", "webp.png"),
        )
        for image_format, mime_type, extension, supplied_name in cases:
            with self.subTest(image_format=image_format):
                content = self.image_bytes(image_format)
                staged = self.process(content, supplied_name)
                try:
                    self.assertEqual(staged.mime_type, mime_type)
                    self.assertEqual(staged.storage_extension, extension)
                    self.assertEqual(staged.size_bytes, len(content))
                    self.assertEqual(staged.width, 32)
                    self.assertEqual(staged.height, 24)
                    self.assertEqual(staged.checksum, sha256(content).hexdigest())
                    self.assertEqual(staged.filename, supplied_name)
                    self.assertTrue(staged.temporary_path.is_file())
                    self.assertNotIn(supplied_name, staged.temporary_path.name)
                finally:
                    staged.cleanup()

    def test_corrupt_image_is_rejected_and_temporary_file_removed(self) -> None:
        with self.assertRaises(ApiError) as raised:
            self.process(b"not an image", "corrupt.png")
        self.assertEqual((raised.exception.status_code, raised.exception.code), (422, "INVALID_IMAGE"))
        self.assertEqual(list(self.storage_path.iterdir()), [])

    def test_spoofed_extension_does_not_control_format_or_storage_extension(self) -> None:
        content = self.image_bytes("PNG")
        source = BytesIO(content)
        source.content_type = "image/jpeg"
        staged = process_image_upload(
            source,
            original_filename="../../private/fake.jpg",
            storage_path=self.storage_path,
        )
        try:
            self.assertEqual(staged.filename, "fake.jpg")
            self.assertEqual(staged.mime_type, "image/png")
            self.assertEqual(staged.storage_extension, ".png")
        finally:
            staged.cleanup()

    def test_actual_file_size_boundary_is_accepted_and_overflow_rejected(self) -> None:
        valid_image = self.image_bytes("PNG")
        at_limit = valid_image + b"\x00" * (MAX_UPLOAD_BYTES - len(valid_image))
        staged = self.process(at_limit, "boundary.png")
        try:
            self.assertEqual(staged.size_bytes, MAX_UPLOAD_BYTES)
            self.assertEqual(staged.checksum, sha256(at_limit).hexdigest())
        finally:
            staged.cleanup()

        over_limit = at_limit + b"x"
        with self.assertRaises(ApiError) as raised:
            self.process(over_limit, "overflow.png")
        self.assertEqual((raised.exception.status_code, raised.exception.code), (413, "FILE_TOO_LARGE"))
        self.assertEqual(list(self.storage_path.iterdir()), [])

    def test_file_is_read_in_bounded_chunks(self) -> None:
        content = self.image_bytes("PNG") + b"x" * (2 * 1024 * 1024)
        source = RecordingStream(content)
        staged = process_image_upload(
            source,
            original_filename="chunked.png",
            storage_path=self.storage_path,
        )
        try:
            self.assertTrue(source.read_sizes)
            self.assertTrue(all(0 < size <= READ_CHUNK_SIZE for size in source.read_sizes))
            self.assertEqual(staged.checksum, sha256(content).hexdigest())
        finally:
            staged.cleanup()

    def test_dimension_and_pixel_count_boundaries(self) -> None:
        cases = (
            ((MAX_IMAGE_WIDTH, 1), True),
            ((MAX_IMAGE_WIDTH + 1, 1), False),
            ((1, MAX_IMAGE_HEIGHT), True),
            ((1, MAX_IMAGE_HEIGHT + 1), False),
            ((8000, 5000), True),  # exactly 40 megapixels
            ((8000, 5001), False),  # above 40 megapixels
        )
        self.assertEqual(8000 * 5000, MAX_IMAGE_PIXELS)
        for dimensions, accepted in cases:
            with self.subTest(dimensions=dimensions):
                content = self.image_bytes("PNG", dimensions, mode="1")
                if accepted:
                    staged = self.process(content, "dimensions.png")
                    try:
                        self.assertEqual((staged.width, staged.height), dimensions)
                    finally:
                        staged.cleanup()
                else:
                    with self.assertRaises(ApiError) as raised:
                        self.process(content, "dimensions.png")
                    self.assertEqual(
                        (raised.exception.status_code, raised.exception.code),
                        (422, "IMAGE_DIMENSIONS_EXCEEDED"),
                    )
                    self.assertEqual(list(self.storage_path.iterdir()), [])

    def test_animated_webp_is_rejected_and_temporary_file_removed(self) -> None:
        content = self.animated_webp_bytes()
        with self.assertRaises(ApiError) as raised:
            self.process(content, "animation.webp")
        self.assertEqual(
            (raised.exception.status_code, raised.exception.code),
            (415, "ANIMATED_WEBP_NOT_SUPPORTED"),
        )
        self.assertEqual(list(self.storage_path.iterdir()), [])

    def test_filename_sanitization_handles_paths_controls_unicode_and_length(self) -> None:
        self.assertEqual(
            sanitize_display_filename(r"C:\private\folder/évidence.jpg"),
            "évidence.jpg",
        )
        self.assertEqual(
            sanitize_display_filename("bad\x00\x1fname\x7f.png"),
            "badname.png",
        )
        decomposed = unicodedata.normalize("NFD", "café") + ".png"
        normalized = sanitize_display_filename(decomposed)
        self.assertEqual(normalized, "café.png")
        self.assertEqual(unicodedata.normalize("NFC", normalized), normalized)
        long_name = sanitize_display_filename("é" * 300 + ".jpg")
        self.assertEqual(len(long_name), 255)
        self.assertEqual(sanitize_display_filename("../\x00"), "image")
        self.assertEqual(sanitize_display_filename(None), "image")

    def test_storage_failure_is_sanitized(self) -> None:
        blocking_file = self.storage_path
        blocking_file.write_text("not a directory", encoding="utf-8")
        with self.assertLogs("backend.app.services.media_processing", level="ERROR"):
            with self.assertRaises(ApiError) as raised:
                process_image_upload(
                    BytesIO(self.image_bytes("PNG")),
                    original_filename="image.png",
                    storage_path=blocking_file / "child",
                )
        self.assertEqual((raised.exception.status_code, raised.exception.code), (500, "MEDIA_STORAGE_FAILED"))
        self.assertNotIn(str(self.temp_directory.name), str(raised.exception))
        self.assertEqual(blocking_file.read_text(encoding="utf-8"), "not a directory")

    @staticmethod
    def image_bytes(
        image_format: str,
        size: tuple[int, int] = (32, 24),
        *,
        mode: str = "RGB",
    ) -> bytes:
        stream = BytesIO()
        Image.new(mode, size, color=0).save(stream, format=image_format)
        return stream.getvalue()

    @staticmethod
    def animated_webp_bytes() -> bytes:
        stream = BytesIO()
        first = Image.new("RGB", (16, 16), color="red")
        second = Image.new("RGB", (16, 16), color="blue")
        first.save(
            stream,
            format="WEBP",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
        return stream.getvalue()


if __name__ == "__main__":
    unittest.main()

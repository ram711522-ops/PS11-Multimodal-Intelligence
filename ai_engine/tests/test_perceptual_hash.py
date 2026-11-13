"""Tests for deterministic pHash generation and near-duplicate matching."""

from hashlib import sha256
from io import BytesIO
import unittest
from uuid import uuid4

from PIL import Image, ImageDraw

from ai_engine.media_processing import (
    DerivedMediaReference,
    ImageMetadata,
    InvalidPerceptualHashError,
    MediaInput,
    MediaPerceptualHash,
    PreprocessedImage,
    compare_phashes,
    compute_phash,
    find_near_duplicates,
    preprocess_image,
)


INVESTIGATION_ID = "255424f1-2e46-48b0-a457-53a5f0b26e60"
UTC = "2026-11-13T08:15:30.123456Z"


def make_normalized_hash(image: Image.Image) -> tuple[bytes, MediaPerceptualHash]:
    source = BytesIO()
    image.save(source, format="PNG")
    original = source.getvalue()
    with Image.open(BytesIO(original)) as decoded:
        width, height = decoded.size
    source_id = str(uuid4())
    metadata = ImageMetadata(
        media_id=source_id,
        investigation_id=INVESTIGATION_ID,
        mime_type="image/png",
        width=width,
        height=height,
        size_bytes=len(original),
        sha256=sha256(original).hexdigest(),
        original_filename="test.png",
        uploaded_at=UTC,
    )
    normalized = preprocess_image(MediaInput(metadata, original))
    return original, compute_phash(normalized)


def image_with_scene(*, shift=0, color=(190, 55, 40), reverse=False) -> Image.Image:
    image = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(image)
    if reverse:
        draw.rectangle((66 + shift, 12, 116, 116), fill=color)
    else:
        draw.rectangle((12 + shift, 12, 62 + shift, 116), fill=color)
    return image


def phash(source_id: str, derived_id: str, value: str) -> MediaPerceptualHash:
    return MediaPerceptualHash(source_id, derived_id, value)


class PerceptualHashGenerationTests(unittest.TestCase):
    def test_phash_is_deterministic_for_the_same_normalized_image(self):
        original, first = make_normalized_hash(image_with_scene())
        # A second preprocessing pass receives the same original bytes and IDs
        # are intentionally assigned the same derivative ID for reproducibility.
        source_id = first.source_media_id
        metadata = ImageMetadata(
            media_id=source_id,
            investigation_id=INVESTIGATION_ID,
            mime_type="image/png",
            width=128,
            height=128,
            size_bytes=len(original),
            sha256=sha256(original).hexdigest(),
            original_filename="test.png",
            uploaded_at=UTC,
        )
        normalized_again = preprocess_image(MediaInput(metadata, original), derived_media_id=first.derived_media_id)
        second = compute_phash(normalized_again)
        self.assertEqual(first.hash_value, second.hash_value)
        self.assertEqual(first.algorithm, "phash")
        self.assertEqual(first.algorithm_version, "dct-64-v1")

    def test_phash_is_separate_from_original_sha256(self):
        original, perceptual = make_normalized_hash(image_with_scene())
        self.assertNotEqual(perceptual.hash_value, sha256(original).hexdigest())
        self.assertEqual(len(perceptual.hash_value), 16)

    def test_visually_similar_images_have_small_hamming_distance(self):
        _, original = make_normalized_hash(image_with_scene())
        _, similar = make_normalized_hash(image_with_scene(shift=2, color=(180, 65, 45)))
        comparison = compare_phashes(original, similar)
        self.assertLessEqual(comparison.hamming_distance, 8)
        self.assertGreaterEqual(comparison.similarity_score, 0.875)

    def test_visually_different_images_have_lower_similarity(self):
        _, left = make_normalized_hash(image_with_scene())
        _, right = make_normalized_hash(image_with_scene(reverse=True))
        comparison = compare_phashes(left, right)
        self.assertGreater(comparison.hamming_distance, 8)
        self.assertLess(comparison.similarity_score, 0.875)

    def test_invalid_normalized_image_is_rejected_safely(self):
        bad_bytes = b"not a jpeg"
        reference = DerivedMediaReference(
            derived_media_id=str(uuid4()),
            source_media_id=str(uuid4()),
            transformation="normalization",
            transformation_version="1",
            mime_type="image/jpeg",
            width=1,
            height=1,
            sha256=sha256(bad_bytes).hexdigest(),
        )
        with self.assertRaises(InvalidPerceptualHashError):
            compute_phash(PreprocessedImage(bad_bytes, reference))


class HashComparisonTests(unittest.TestCase):
    def test_hamming_distance_and_similarity_for_known_hashes(self):
        result = compare_phashes("0000000000000000", "0000000000000001")
        self.assertEqual(result.hamming_distance, 1)
        self.assertEqual(result.similarity_score, 63 / 64)
        identical = compare_phashes("f" * 16, "F" * 16)
        self.assertEqual(identical.hamming_distance, 0)
        self.assertEqual(identical.similarity_score, 1.0)

    def test_invalid_hashes_and_thresholds_are_rejected(self):
        for value in ("", "0" * 15, "0" * 15 + "g", "+00000000000000", "0x00000000000000"):
            with self.subTest(value=value), self.assertRaises(InvalidPerceptualHashError):
                compare_phashes(value, "0" * 16)
        source = phash(str(uuid4()), str(uuid4()), "0" * 16)
        candidate = phash(str(uuid4()), str(uuid4()), "0" * 16)
        for threshold in (-0.1, 1.1, float("nan"), float("inf"), True):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                find_near_duplicates(source, [candidate], minimum_similarity=threshold)

    def test_hashes_from_different_versions_cannot_be_compared(self):
        first = phash(str(uuid4()), str(uuid4()), "0" * 16)
        second = MediaPerceptualHash(
            str(uuid4()), str(uuid4()), "0" * 16, algorithm_version="dct-64-v2"
        )
        with self.assertRaises(InvalidPerceptualHashError):
            compare_phashes(first, second)

    def test_threshold_is_inclusive_at_boundary(self):
        source = phash(str(uuid4()), str(uuid4()), "0" * 16)
        one_bit_apart = phash(str(uuid4()), str(uuid4()), "0" * 15 + "1")
        score = 63 / 64
        self.assertEqual(len(find_near_duplicates(source, [one_bit_apart], minimum_similarity=score).matches), 1)
        self.assertEqual(len(find_near_duplicates(source, [one_bit_apart], minimum_similarity=score + 0.001).matches), 0)
        self.assertEqual(len(find_near_duplicates(source, [source], minimum_similarity=1.0).matches), 0)

    def test_result_identifies_source_matching_media_and_derivatives(self):
        source = phash(str(uuid4()), str(uuid4()), "0" * 16)
        match_a = phash(str(uuid4()), str(uuid4()), "0" * 16)
        match_b = phash(str(uuid4()), str(uuid4()), "0" * 15 + "1")
        result = find_near_duplicates(source, [match_b, match_a], minimum_similarity=0.98)
        self.assertEqual(result.source_media_id, source.source_media_id)
        self.assertEqual(result.matching_media_ids, (match_a.source_media_id, match_b.source_media_id))
        self.assertEqual(result.matches[0].source_derived_media_id, source.derived_media_id)
        self.assertEqual(result.matches[0].matching_derived_media_id, match_a.derived_media_id)
        self.assertEqual(result.matches[1].similarity_score, 63 / 64)

    def test_result_order_is_deterministic_and_bad_candidates_are_rejected(self):
        source = phash(str(uuid4()), str(uuid4()), "0" * 16)
        candidates = [
            phash(str(uuid4()), str(uuid4()), "0" * 15 + "3"),
            phash(str(uuid4()), str(uuid4()), "0" * 15 + "1"),
        ]
        forward = find_near_duplicates(source, candidates, minimum_similarity=0.9)
        reverse = find_near_duplicates(source, list(reversed(candidates)), minimum_similarity=0.9)
        self.assertEqual(forward, reverse)
        with self.assertRaises(TypeError):
            find_near_duplicates(source, [object()])  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()

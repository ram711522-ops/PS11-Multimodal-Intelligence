"""Versioned perceptual hashing and near-duplicate comparison for images."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
import re
from typing import Iterable
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from .models import DerivedMediaReference
from .preprocessing import MAX_HEIGHT, MAX_PIXELS, MAX_WIDTH, PreprocessedImage


PHASH_ALGORITHM = "phash"
PHASH_VERSION = "dct-64-v1"
HASH_BITS = 64
HASH_HEX_LENGTH = HASH_BITS // 4
HASH_SIZE = 8
HIGH_FREQUENCY_FACTOR = 4
_DCT_SIZE = HASH_SIZE * HIGH_FREQUENCY_FACTOR
_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{16}$")


class InvalidPerceptualHashError(ValueError):
    """Raised when a normalized image or perceptual hash is invalid."""


@dataclass(frozen=True)
class MediaPerceptualHash:
    """A pHash linked to both its uploaded source and normalized derivative."""

    source_media_id: str
    derived_media_id: str
    hash_value: str
    algorithm: str = PHASH_ALGORITHM
    algorithm_version: str = PHASH_VERSION

    def __post_init__(self) -> None:
        _validate_uuid("source_media_id", self.source_media_id)
        _validate_uuid("derived_media_id", self.derived_media_id)
        if not isinstance(self.hash_value, str) or not _HASH_PATTERN.fullmatch(self.hash_value):
            raise InvalidPerceptualHashError("pHash must be a 64-bit hexadecimal value.")
        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise InvalidPerceptualHashError("algorithm must be a non-empty string.")
        if not isinstance(self.algorithm_version, str) or not self.algorithm_version.strip():
            raise InvalidPerceptualHashError("algorithm_version must be a non-empty string.")
        object.__setattr__(self, "hash_value", self.hash_value.lower())


@dataclass(frozen=True)
class HashComparison:
    """Hamming distance and normalized similarity for two 64-bit hashes."""

    hamming_distance: int
    similarity_score: float


@dataclass(frozen=True)
class NearDuplicateMatch:
    """One candidate match with source and derived-media traceability."""

    source_media_id: str
    matching_media_id: str
    source_derived_media_id: str
    matching_derived_media_id: str
    hamming_distance: int
    similarity_score: float


@dataclass(frozen=True)
class NearDuplicateResult:
    """Near-duplicate matches for one source media item."""

    source_media_id: str
    minimum_similarity: float
    algorithm: str
    algorithm_version: str
    matches: tuple[NearDuplicateMatch, ...]

    @property
    def matching_media_ids(self) -> tuple[str, ...]:
        """Return matched source media IDs in deterministic result order."""
        return tuple(match.matching_media_id for match in self.matches)


def compute_phash(normalized_image: PreprocessedImage) -> MediaPerceptualHash:
    """Compute deterministic 64-bit DCT pHash from Stage 2 normalized JPEG.

    The output hash is independent of and does not replace the original-media
    SHA-256 carried by ``ImageMetadata``. The derived-media reference is
    checked before decoding so the hash remains traceable to its exact bytes.
    """
    if not isinstance(normalized_image, PreprocessedImage):
        raise TypeError("normalized_image must be a PreprocessedImage instance.")
    reference = normalized_image.reference
    if not isinstance(reference, DerivedMediaReference):
        raise InvalidPerceptualHashError("normalized image must include a derived media reference.")
    content = normalized_image.image_bytes
    if not isinstance(content, bytes) or not content:
        raise InvalidPerceptualHashError("normalized image bytes must be non-empty.")
    if reference.mime_type != "image/jpeg":
        raise InvalidPerceptualHashError("pHash input must be normalized JPEG content.")
    if sha256(content).hexdigest() != reference.sha256:
        raise InvalidPerceptualHashError("normalized image checksum does not match its reference.")

    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != "JPEG":
                raise InvalidPerceptualHashError("normalized image bytes are not JPEG content.")
            if image.size != (reference.width, reference.height):
                raise InvalidPerceptualHashError("normalized image dimensions do not match its reference.")
            _validate_dimensions(image.width, image.height)
            image.verify()
        with Image.open(BytesIO(content)) as image:
            if image.format != "JPEG" or image.size != (reference.width, reference.height):
                raise InvalidPerceptualHashError("normalized image metadata is inconsistent.")
            _validate_dimensions(image.width, image.height)
            image.load()
            grayscale = image.convert("L").resize(
                (_DCT_SIZE, _DCT_SIZE), Image.Resampling.LANCZOS
            )
            coefficients = _low_frequency_dct(grayscale)
    except InvalidPerceptualHashError:
        raise
    except Image.DecompressionBombError as exc:
        raise InvalidPerceptualHashError("normalized image exceeds safe decode limits.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidPerceptualHashError("normalized image could not be safely decoded.") from exc

    median = sorted(coefficients)[len(coefficients) // 2]
    bits = 0
    for coefficient in coefficients:
        bits = (bits << 1) | int(coefficient > median)
    return MediaPerceptualHash(
        source_media_id=reference.source_media_id,
        derived_media_id=reference.derived_media_id,
        hash_value=f"{bits:0{HASH_HEX_LENGTH}x}",
    )


def compare_phashes(left: MediaPerceptualHash | str, right: MediaPerceptualHash | str) -> HashComparison:
    """Compare 64-bit pHash values using Hamming distance and similarity.

    Similarity is ``1 - hamming_distance / 64`` and is a heuristic score, not
    a probability. Strings are accepted for callers that store just hash data.
    """
    if isinstance(left, MediaPerceptualHash) and isinstance(right, MediaPerceptualHash):
        if (left.algorithm, left.algorithm_version) != (right.algorithm, right.algorithm_version):
            raise InvalidPerceptualHashError("pHashes must use the same algorithm and version.")
    left_value = _validated_hash_value(left.hash_value if isinstance(left, MediaPerceptualHash) else left)
    right_value = _validated_hash_value(right.hash_value if isinstance(right, MediaPerceptualHash) else right)
    distance = (int(left_value, 16) ^ int(right_value, 16)).bit_count()
    return HashComparison(distance, 1.0 - distance / HASH_BITS)


def find_near_duplicates(
    source: MediaPerceptualHash,
    candidates: Iterable[MediaPerceptualHash],
    *,
    minimum_similarity: float = 0.90,
) -> NearDuplicateResult:
    """Return candidates meeting the inclusive configurable similarity cutoff.

    Self-matches are excluded. Results sort by descending similarity then
    matching media UUID for stable ordering independent of input iteration.
    """
    if not isinstance(source, MediaPerceptualHash):
        raise TypeError("source must be a MediaPerceptualHash instance.")
    _validate_similarity_threshold(minimum_similarity)
    if isinstance(candidates, (str, bytes)):
        raise TypeError("candidates must be an iterable of MediaPerceptualHash instances.")

    matches: list[NearDuplicateMatch] = []
    for candidate in candidates:
        if not isinstance(candidate, MediaPerceptualHash):
            raise TypeError("candidates must contain only MediaPerceptualHash instances.")
        if candidate.source_media_id == source.source_media_id:
            continue
        comparison = compare_phashes(source, candidate)
        if comparison.similarity_score >= minimum_similarity:
            matches.append(NearDuplicateMatch(
                source_media_id=source.source_media_id,
                matching_media_id=candidate.source_media_id,
                source_derived_media_id=source.derived_media_id,
                matching_derived_media_id=candidate.derived_media_id,
                hamming_distance=comparison.hamming_distance,
                similarity_score=comparison.similarity_score,
            ))
    matches.sort(key=lambda match: (-match.similarity_score, match.matching_media_id))
    return NearDuplicateResult(
        source_media_id=source.source_media_id,
        minimum_similarity=minimum_similarity,
        algorithm=PHASH_ALGORITHM,
        algorithm_version=PHASH_VERSION,
        matches=tuple(matches),
    )


def _low_frequency_dct(image: Image.Image) -> list[float]:
    """Compute the top-left 8×8 orthonormal DCT coefficients separably."""
    pixels = image.tobytes()
    size = _DCT_SIZE
    matrix = [
        [
            (math.sqrt(1.0 / size) if frequency == 0 else math.sqrt(2.0 / size))
            * math.cos(math.pi * (2 * position + 1) * frequency / (2 * size))
            for position in range(size)
        ]
        for frequency in range(HASH_SIZE)
    ]

    horizontal: list[list[float]] = []
    for y in range(size):
        row = pixels[y * size:(y + 1) * size]
        horizontal.append([
            sum(matrix[frequency][x] * row[x] for x in range(size))
            for frequency in range(HASH_SIZE)
        ])

    coefficients = [
        sum(matrix[vertical][y] * horizontal[y][horizontal_frequency] for y in range(size))
        for vertical in range(HASH_SIZE)
        for horizontal_frequency in range(HASH_SIZE)
    ]
    return coefficients


def _validated_hash_value(value: str) -> str:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
        raise InvalidPerceptualHashError("pHash must be a 64-bit hexadecimal value.")
    return value.lower()


def _validate_uuid(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise InvalidPerceptualHashError(f"{name} must be a UUID string.")
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InvalidPerceptualHashError(f"{name} must be a valid UUID.") from exc


def _validate_similarity_threshold(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("minimum_similarity must be a finite number from 0 to 1.")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("minimum_similarity must be a finite number from 0 to 1.")


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise InvalidPerceptualHashError("normalized image dimensions must be positive.")
    if width > MAX_WIDTH or height > MAX_HEIGHT or width * height > MAX_PIXELS:
        raise InvalidPerceptualHashError("normalized image dimensions exceed supported limits.")

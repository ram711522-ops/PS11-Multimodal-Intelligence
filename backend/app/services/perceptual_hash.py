"""Deterministic pHash utilities for locally indexed image matching."""

import re

import numpy as np
from PIL import Image, ImageOps


HASH_SIZE = 8
HIGH_FREQUENCY_FACTOR = 4
_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{16}$")


def image_phash(image: Image.Image) -> str:
    """Return a 64-bit DCT perceptual hash for an image, after EXIF orientation."""

    size = HASH_SIZE * HIGH_FREQUENCY_FACTOR
    prepared = ImageOps.exif_transpose(image).convert("L").resize(
        (size, size), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(prepared, dtype=np.float64)
    transform = _dct_matrix(size)
    coefficients = transform @ pixels @ transform.T
    low_frequencies = coefficients[:HASH_SIZE, :HASH_SIZE].copy()
    median = float(np.median(low_frequencies.reshape(-1)[1:]))
    bits = (low_frequencies >= median).reshape(-1)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance(first: str, second: str) -> int:
    """Return the number of differing bits in two 64-bit hexadecimal hashes."""

    if not _HASH_PATTERN.fullmatch(first) or not _HASH_PATTERN.fullmatch(second):
        raise ValueError("Perceptual hashes must be 16 hexadecimal characters.")
    return (int(first, 16) ^ int(second, 16)).bit_count()


def _dct_matrix(size: int) -> np.ndarray:
    positions = np.arange(size, dtype=np.float64)
    frequencies = np.arange(size, dtype=np.float64)[:, None]
    matrix = np.cos(np.pi * (2 * positions + 1) * frequencies / (2 * size))
    matrix[0, :] *= np.sqrt(1 / size)
    matrix[1:, :] *= np.sqrt(2 / size)
    return matrix

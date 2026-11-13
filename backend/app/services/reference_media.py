"""Curate source-attributed local image references for pHash comparison."""

from datetime import date, datetime, timezone
from hashlib import sha256
import logging
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
import warnings

from PIL import Image, UnidentifiedImageError

from backend.app.config import Settings
from backend.app.database import database_connection, initialize_database
from backend.app.ids import generate_uuid
from backend.app.repositories.reference_media import insert_reference_media
from backend.app.repositories.reference_media import list_reference_media
from backend.app.services.media_processing import (
    MAX_IMAGE_HEIGHT,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_WIDTH,
    MAX_UPLOAD_BYTES,
    IMAGE_FORMATS,
)
from backend.app.services.perceptual_hash import image_phash
from backend.app.services.perceptual_hash import hamming_distance


logger = logging.getLogger(__name__)
READ_CHUNK_BYTES = 64 * 1024


def add_reference_media(
    database_path: Path | str,
    image_path: Path | str,
    *,
    source_reference: str,
    source_date: str,
) -> dict[str, str]:
    """Validate and hash a trusted local reference without copying its file.

    Callers must provide an actual source URL and source date. A pHash match is a
    candidate for human review, not evidence that two items have the same origin.
    """

    canonical_url = _validate_source_url(source_reference)
    canonical_date = _validate_source_date(source_date)
    path = Path(image_path)
    checksum = _sha256_file(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = (image.format or "").upper()
                if image_format not in IMAGE_FORMATS:
                    raise ValueError("Reference image must be JPEG, PNG, or WebP.")
                width, height = image.size
                _validate_dimensions(width, height)
                if image_format == "WEBP" and (
                    bool(getattr(image, "is_animated", False))
                    or int(getattr(image, "n_frames", 1)) > 1
                ):
                    raise ValueError("Animated WebP reference images are not supported.")
                image.verify()
            with Image.open(path) as decoded:
                decoded.load()
                perceptual_hash = image_phash(decoded)
    except ValueError:
        raise
    except (FileNotFoundError, OSError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, SyntaxError, EOFError, MemoryError):
        raise ValueError("Reference image could not be safely decoded.") from None

    reference_id = generate_uuid()
    indexed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    try:
        with database_connection(database_path) as connection:
            insert_reference_media(
                connection,
                reference_id=reference_id,
                source_reference=canonical_url,
                source_date=canonical_date,
                sha256=checksum,
                perceptual_hash=perceptual_hash,
                indexed_at=indexed_at,
            )
            connection.commit()
    except sqlite3.Error:
        logger.exception("Unable to save curated reference media metadata")
        raise RuntimeError("Unable to save reference media metadata.") from None
    return {
        "reference_id": reference_id,
        "source_reference": canonical_url,
        "source_date": canonical_date,
        "sha256": checksum,
        "perceptual_hash": perceptual_hash,
    }


def compare_local_references(
    connection: sqlite3.Connection,
    perceptual_hash: str,
    *,
    maximum_distance: int = 8,
    limit: int = 10,
) -> dict:
    """Return source-attributed pHash candidates, never an authenticity verdict."""

    if not 0 <= maximum_distance <= 64 or not 1 <= limit <= 100:
        raise ValueError("Reference matching limits are invalid.")
    references = list_reference_media(connection)
    if not references:
        return {
            "status": "index_empty",
            "method": "64-bit DCT perceptual hash",
            "maximum_hamming_distance": maximum_distance,
            "candidate_count": 0,
            "matches": [],
            "interpretation": "No local references are indexed; this is not evidence that the media is new.",
        }
    matches = []
    for reference in references:
        distance = hamming_distance(perceptual_hash, reference["perceptual_hash"])
        if distance <= maximum_distance:
            matches.append(
                {
                    "reference_id": reference["reference_id"],
                    "source_reference": reference["source_reference"],
                    "source_date": reference["source_date"],
                    "sha256": reference["sha256"],
                    "hamming_distance": distance,
                }
            )
    matches.sort(key=lambda item: (item["hamming_distance"], item["source_date"], item["reference_id"]))
    return {
        "status": "candidates_found" if matches else "no_candidate_within_threshold",
        "method": "64-bit DCT perceptual hash",
        "maximum_hamming_distance": maximum_distance,
        "candidate_count": len(references),
        "matches": matches[:limit],
        "interpretation": (
            "Candidates require human review; a pHash match does not prove common origin."
            if matches
            else "No local candidate is within the threshold; this does not prove the media is new."
        ),
    }


def _sha256_file(path: Path) -> str:
    digest = sha256()
    size = 0
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(READ_CHUNK_BYTES), b""):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError("Reference image exceeds the configured 10 MiB limit.")
                digest.update(chunk)
    except FileNotFoundError:
        raise ValueError("Reference image was not found.") from None
    except OSError:
        raise ValueError("Reference image could not be read.") from None
    if size == 0:
        raise ValueError("Reference image is empty.")
    return digest.hexdigest()


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_WIDTH
        or height > MAX_IMAGE_HEIGHT
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValueError("Reference image exceeds the configured dimension limits.")


def _validate_source_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("A source URL is required for every reference image.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Reference source must be a valid HTTP or HTTPS URL.")
    return value


def _validate_source_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("Reference source date must use YYYY-MM-DD format.") from None


def main() -> None:
    """Register a single curated reference using the project database."""

    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Local reference image to index")
    parser.add_argument("--source-url", required=True, help="Evidence source URL")
    parser.add_argument("--source-date", required=True, help="Source date (YYYY-MM-DD)")
    arguments = parser.parse_args()
    settings = Settings.from_environment()
    initialize_database(settings.database_path)
    print(
        json.dumps(
            add_reference_media(
                settings.database_path,
                arguments.image,
                source_reference=arguments.source_url,
                source_date=arguments.source_date,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

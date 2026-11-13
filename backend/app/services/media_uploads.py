"""Transactional media-upload lifecycle and filesystem compensation."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID

from backend.app.database import database_connection
from backend.app.errors import ApiError
from backend.app.ids import generate_uuid
from backend.app.repositories.investigations import (
    get_investigation,
    update_investigation_status,
)
from backend.app.repositories.media_assets import (
    count_media_assets,
    find_media_asset_by_investigation_and_checksum,
    insert_media_asset,
)
from backend.app.services.media_processing import ProcessedImageUpload


logger = logging.getLogger(__name__)
MAX_MEDIA_PER_INVESTIGATION = 5
UPLOADABLE_INPUT_TYPES = {"image", "text_image"}
UPLOADABLE_STATUSES = {"awaiting_media", "received"}


def canonical_upload_investigation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(
            400,
            "INVALID_UPLOAD_REQUEST",
            "The investigation identifier must be a valid UUID.",
        ) from None


def validate_upload_target(database_path: Path | str, investigation_id: str) -> str:
    """Validate the path UUID and preflight eligibility before receiving a body."""

    canonical_id = canonical_upload_investigation_id(investigation_id)
    try:
        with database_connection(database_path) as connection:
            investigation = get_investigation(connection, canonical_id)
    except sqlite3.Error:
        logger.exception("Unable to validate media upload target")
        raise ApiError(
            500,
            "MEDIA_PERSISTENCE_FAILED",
            "Unable to validate the investigation for media upload.",
        ) from None
    _require_upload_eligibility(investigation)
    return canonical_id


def persist_processed_upload(
    database_path: Path | str,
    storage_root: Path | str,
    investigation_id: str,
    processed: ProcessedImageUpload,
) -> dict:
    """Persist metadata and move the file under one serialized SQLite write txn."""

    root = Path(storage_root)
    media_id = generate_uuid()
    uploaded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    final_path = root / f"{media_id}{processed.storage_extension}"
    final_file_created = False
    transaction_committed = False

    try:
        with database_connection(database_path) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                investigation = get_investigation(connection, investigation_id)
                _require_upload_eligibility(investigation)

                if find_media_asset_by_investigation_and_checksum(
                    connection,
                    investigation_id,
                    processed.checksum,
                ) is not None:
                    raise ApiError(
                        409,
                        "DUPLICATE_MEDIA",
                        "This image has already been uploaded to the investigation.",
                    )

                if count_media_assets(connection, investigation_id) >= MAX_MEDIA_PER_INVESTIGATION:
                    raise ApiError(
                        409,
                        "MEDIA_LIMIT_REACHED",
                        "An investigation may contain at most five images.",
                    )

                try:
                    descriptor = os.open(
                        final_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                        0o600,
                    )
                    final_file_created = True
                    with os.fdopen(descriptor, "wb") as destination:
                        with processed.temporary_path.open("rb") as source:
                            shutil.copyfileobj(source, destination, length=64 * 1024)
                        destination.flush()
                        os.fsync(destination.fileno())
                    processed.temporary_path.unlink()
                except OSError:
                    logger.exception("Unable to move validated media into storage")
                    raise ApiError(
                        500,
                        "MEDIA_STORAGE_FAILED",
                        "Unable to store the uploaded image.",
                    ) from None

                insert_media_asset(
                    connection,
                    media_id=media_id,
                    investigation_id=investigation_id,
                    file_reference=final_path.name,
                    checksum=processed.checksum,
                    mime_type=processed.mime_type,
                    size_bytes=processed.size_bytes,
                    original_filename=processed.filename,
                    width=processed.width,
                    height=processed.height,
                    uploaded_at=uploaded_at,
                    processing_status="received",
                )
                if investigation["status"] == "awaiting_media":
                    update_investigation_status(
                        connection,
                        investigation_id,
                        status="received",
                        updated_at=uploaded_at,
                    )
                connection.commit()
                transaction_committed = True
            except ApiError as error:
                _rollback(connection)
                _cleanup_persisted_upload_files(
                    processed,
                    final_path,
                    final_file_created,
                    error,
                )
                raise
            except Exception as error:
                _rollback(connection)
                persistence_error = ApiError(
                    500,
                    "MEDIA_PERSISTENCE_FAILED",
                    "Unable to persist uploaded image metadata.",
                )
                _cleanup_persisted_upload_files(
                    processed,
                    final_path,
                    final_file_created,
                    persistence_error,
                )
                if isinstance(error, sqlite3.Error):
                    logger.exception("Unable to persist media upload")
                else:
                    logger.exception("Unexpected media persistence failure")
                raise persistence_error from None
    except sqlite3.Error:
        # A connection-close failure after commit must not turn a durable upload
        # into an API failure or delete its committed physical file.
        if transaction_committed:
            logger.exception("SQLite connection cleanup failed after media commit")
        else:
            persistence_error = ApiError(
                500,
                "MEDIA_PERSISTENCE_FAILED",
                "Unable to open the database for media persistence.",
            )
            _cleanup_persisted_upload_files(
                processed,
                final_path,
                final_file_created,
                persistence_error,
            )
            logger.exception("Unable to open database for media upload")
            raise persistence_error from None

    return {
        "investigation_id": investigation_id,
        "media_id": media_id,
        "filename": processed.filename,
        "mime_type": processed.mime_type,
        "size_bytes": processed.size_bytes,
        "checksum": processed.checksum,
        "width": processed.width,
        "height": processed.height,
        "uploaded_at": uploaded_at,
    }


def _require_upload_eligibility(investigation: dict | None) -> None:
    if investigation is None:
        raise ApiError(404, "INVESTIGATION_NOT_FOUND", "Investigation not found.")
    if investigation["input_type"] not in UPLOADABLE_INPUT_TYPES:
        raise ApiError(
            409,
            "MEDIA_UPLOAD_NOT_ALLOWED",
            "This investigation type cannot receive media.",
        )
    if investigation["status"] not in UPLOADABLE_STATUSES:
        raise ApiError(
            409,
            "MEDIA_UPLOAD_LOCKED",
            "Media uploads are locked for this investigation status.",
        )


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        logger.exception("Unable to roll back media upload transaction")


def _remove_file_preserving_error(path: Path, primary_error: ApiError) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Media upload cleanup failed; preserving primary API error")


def _cleanup_persisted_upload_files(
    processed: ProcessedImageUpload,
    final_path: Path,
    final_file_created: bool,
    primary_error: ApiError,
) -> None:
    if final_file_created:
        _remove_file_preserving_error(final_path, primary_error)
    _remove_file_preserving_error(processed.temporary_path, primary_error)

"""Investigation creation and retrieval routes."""

import logging
import sys

from fastapi import APIRouter, Query, Request, status
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.investigations import (
    InvestigationCreateRequest,
    InvestigationCreatedResponse,
    InvestigationHistoryResponse,
    InvestigationResponse,
)
from backend.app.services.investigations import (
    create_investigation,
    retrieve_history,
    retrieve_investigation,
)
from backend.app.errors import ApiError
from backend.app.services.media_processing import process_image_upload
from backend.app.services.media_uploads import (
    persist_processed_upload,
    validate_upload_target,
)
from backend.app.services.multipart_uploads import receive_single_image_part


router = APIRouter(prefix="/api/investigations", tags=["investigations"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=InvestigationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_investigation_route(
    payload: InvestigationCreateRequest,
    request: Request,
) -> dict:
    return create_investigation(request.app.state.settings.database_path, payload)


@router.get("", response_model=InvestigationHistoryResponse)
def list_investigations_route(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return retrieve_history(
        request.app.state.settings.database_path,
        limit=limit,
        offset=offset,
    )


@router.get("/{investigation_id}", response_model=InvestigationResponse)
def get_investigation_route(investigation_id: str, request: Request) -> dict:
    return retrieve_investigation(
        request.app.state.settings.database_path,
        investigation_id,
    )


@router.post(
    "/{investigation_id}/media",
    status_code=status.HTTP_201_CREATED,
)
async def upload_investigation_media_route(
    investigation_id: str,
    request: Request,
) -> dict:
    settings = request.app.state.settings
    canonical_id = await run_in_threadpool(
        validate_upload_target,
        settings.database_path,
        investigation_id,
    )
    part = await receive_single_image_part(request)
    try:
        processed = await run_in_threadpool(
            process_image_upload,
            part.stream,
            original_filename=part.filename,
            storage_path=settings.media_storage_path,
        )
    except BaseException:
        _close_multipart_part_without_masking_error(part)
        raise

    # Close the request spool before persistence. A close failure is still a
    # pre-commit failure, so the staged image can be removed without leaving a
    # committed upload that the client was told had failed.
    try:
        part.close()
    except ApiError:
        _cleanup_staged_file_without_masking_error(processed)
        raise

    try:
        return await run_in_threadpool(
            persist_processed_upload,
            settings.database_path,
            settings.media_storage_path,
            canonical_id,
            processed,
        )
    except BaseException:
        _cleanup_staged_file_without_masking_error(processed)
        raise


def _close_multipart_part_without_masking_error(part) -> None:
    """Close the parser spool and preserve an already active API error."""

    active_error = sys.exc_info()[0] is not None
    try:
        part.close()
    except ApiError:
        if not active_error:
            raise


def _cleanup_staged_file_without_masking_error(processed) -> None:
    """Clean the processor handoff file while preserving an active API failure."""

    active_error = sys.exc_info()[0] is not None
    try:
        processed.temporary_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Unable to clean up staged media file in route finalizer")
        if not active_error:
            raise ApiError(
                500,
                "MEDIA_STORAGE_FAILED",
                "Unable to clean up the staged media file.",
            ) from None

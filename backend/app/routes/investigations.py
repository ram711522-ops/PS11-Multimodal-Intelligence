"""Investigation creation and retrieval routes."""

from fastapi import APIRouter, Query, Request, status

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


router = APIRouter(prefix="/api/investigations", tags=["investigations"])


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

"""HTTP routes for the strict Phase 2 scope."""

from fastapi import APIRouter, Depends, Query, Request, Response

from .schemas import (
    ErrorResponse,
    InvestigationCreate,
    InvestigationHistory,
    InvestigationResponse,
    InvestigationSummary,
)
from .service import ApiProblem, InvestigationService

router = APIRouter()


def get_service(request: Request) -> InvestigationService:
    return request.app.state.investigation_service


def to_claim(row) -> dict | None:
    if row["input_type"] == "image":
        return None
    return {"text": row["claim_text"], "language": row["language"], "normalized_text": None}


def to_response(row) -> dict:
    return {
        "investigation_id": row["investigation_id"],
        "input_type": row["input_type"],
        "status": row["status"],
        "claim": to_claim(row),
        "source_reference": row["source_reference"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "media": [],
        "latest_job": None,
        "latest_result": None,
        "failure": None,
    }


@router.get("/health")
def health(request: Request, response: Response):
    """Report API/database health without implying AI features are online."""
    try:
        with request.app.state.database.connect() as connection:
            connection.execute("SELECT 1").fetchone()
    except Exception:
        response.status_code = 503
        return {
            "error": {
                "code": "HEALTH_CHECK_FAILED",
                "message": "The application health check could not verify the database.",
                "details": {},
            }
        }
    return {
        "status": "ok",
        "application": "ok",
        "database": "ok",
        "ai_modules": "not_initialized",
    }


@router.post(
    "/api/investigations", status_code=201, response_model=InvestigationResponse,
    responses={422: {"model": ErrorResponse}},
)
def create_investigation(payload: InvestigationCreate, service: InvestigationService = Depends(get_service)):
    return to_response(service.create(payload))


@router.get(
    "/api/investigations/{investigation_id}", response_model=InvestigationResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_investigation(investigation_id: str, service: InvestigationService = Depends(get_service)):
    return to_response(service.get(investigation_id))


@router.get("/api/investigations", response_model=InvestigationHistory)
def list_investigations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: InvestigationService = Depends(get_service),
):
    rows, total = service.list(limit, offset)
    items = [
        InvestigationSummary(
            investigation_id=row["investigation_id"], input_type=row["input_type"],
            status=row["status"], claim=to_claim(row), source_reference=row["source_reference"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}

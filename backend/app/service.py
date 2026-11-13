"""Business logic for investigation operations."""

from uuid import UUID

from .repository import InvestigationRepository
from .schemas import InvestigationCreate


class ApiProblem(Exception):
    """An expected API error with a stable public error code."""

    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class InvestigationService:
    """Coordinate validation and repository access."""

    def __init__(self, repository: InvestigationRepository):
        self.repository = repository

    def create(self, payload: InvestigationCreate):
        return self.repository.create(payload)

    def get(self, investigation_id: str):
        try:
            normalized_id = str(UUID(investigation_id))
        except (ValueError, AttributeError):
            raise ApiProblem(404, "INVESTIGATION_NOT_FOUND", "Investigation was not found.")
        row = self.repository.get(normalized_id)
        if row is None:
            raise ApiProblem(404, "INVESTIGATION_NOT_FOUND", "Investigation was not found.")
        return row

    def list(self, limit: int, offset: int):
        return self.repository.list(limit, offset)

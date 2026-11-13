"""Backend-independent port and schemas for the proposed processing API.

This module defines the AI side of the integration boundary only. It does not
perform HTTP requests or persist jobs. The Phase 2/3 backend has no processing
job endpoints, so the default adapter fails explicitly instead of simulating
jobs or results.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .models import ProcessingJob, ProcessingResult


PROCESSING_API_VERSION = "1.0"


@dataclass(frozen=True)
class CreateProcessingJobRequest:
    """Request to process an already uploaded media asset."""

    investigation_id: str
    media_id: str

    def __post_init__(self) -> None:
        _validate_uuid("investigation_id", self.investigation_id)
        _validate_uuid("media_id", self.media_id)


@dataclass(frozen=True)
class RetryProcessingJobRequest:
    """Explicit retry request; only failed jobs are eligible."""

    job_id: str
    retry_failed: bool = True

    def __post_init__(self) -> None:
        _validate_uuid("job_id", self.job_id)
        if self.retry_failed is not True:
            raise ValueError("retry_failed must be true for an explicit retry request.")


@dataclass(frozen=True)
class ProcessingJobResponse:
    """Versioned response wrapper around the shared processing job model."""

    job: ProcessingJob
    api_version: str = PROCESSING_API_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.job, ProcessingJob):
            raise TypeError("job must be a ProcessingJob instance.")
        if self.api_version != PROCESSING_API_VERSION:
            raise ValueError(f"api_version must be {PROCESSING_API_VERSION}.")


@dataclass(frozen=True)
class ProcessingResultsResponse:
    """Versioned list of actual result records currently available for a job."""

    job_id: str
    items: tuple[ProcessingResult, ...]
    total: int
    api_version: str = PROCESSING_API_VERSION

    def __post_init__(self) -> None:
        _validate_uuid("job_id", self.job_id)
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, ProcessingResult) for item in self.items
        ):
            raise TypeError("items must be a tuple of ProcessingResult values.")
        if any(item.job_id != self.job_id for item in self.items):
            raise ValueError("every result must belong to the response job_id.")
        if isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < len(self.items):
            raise ValueError("total must be an integer at least as large as items length.")
        if self.api_version != PROCESSING_API_VERSION:
            raise ValueError(f"api_version must be {PROCESSING_API_VERSION}.")


class ProcessingApiUnavailable(RuntimeError):
    """Raised while no backend processing API implementation is available."""


class ProcessingApiAdapter(Protocol):
    """Port a backend adapter must implement after API endpoints are approved."""

    def create_job(self, request: CreateProcessingJobRequest) -> ProcessingJobResponse:
        """Create work for an existing uploaded media asset."""

    def get_job(self, job_id: str) -> ProcessingJobResponse:
        """Retrieve the current lifecycle state for a processing job."""

    def get_results(self, job_id: str) -> ProcessingResultsResponse:
        """Retrieve actual result records produced for a processing job."""

    def retry_job(self, request: RetryProcessingJobRequest) -> ProcessingJobResponse:
        """Request another attempt for an eligible failed job."""


class UnavailableProcessingApiAdapter:
    """Explicit placeholder until the backend implements the proposed API."""

    @staticmethod
    def _unavailable() -> None:
        raise ProcessingApiUnavailable(
            "Processing API endpoints are not implemented by the current backend."
        )

    def create_job(self, request: CreateProcessingJobRequest) -> ProcessingJobResponse:
        self._unavailable()

    def get_job(self, job_id: str) -> ProcessingJobResponse:
        self._unavailable()

    def get_results(self, job_id: str) -> ProcessingResultsResponse:
        self._unavailable()

    def retry_job(self, request: RetryProcessingJobRequest) -> ProcessingJobResponse:
        self._unavailable()


def _validate_uuid(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID string.")
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{name} must be a valid UUID.") from exc

"""Pydantic schemas for the separately versioned Phase 4 processing API."""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_engine.media_processing import ProcessingStatus


PROCESSING_API_VERSION = "1.0"


class ProcessingApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProcessingJobCreateRequest(ProcessingApiModel):
    investigation_id: UUID
    media_id: UUID


class ProcessingJobRetryRequest(ProcessingApiModel):
    retry_failed: Literal[True]


class ProcessingFailureResponse(ProcessingApiModel):
    code: str
    message: str
    occurred_at: str

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: str) -> str:
        return _validate_utc_timestamp(value)


class ProcessingJobPayload(ProcessingApiModel):
    job_id: UUID
    investigation_id: UUID
    media_id: UUID
    processing_status: ProcessingStatus
    created_at: str
    started_at: str | None
    completed_at: str | None
    failure: ProcessingFailureResponse | None

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_optional_timestamps(cls, value: str | None) -> str | None:
        return _validate_utc_timestamp(value) if value is not None else None


class ProcessingJobResponse(ProcessingApiModel):
    api_version: Literal["1.0"] = PROCESSING_API_VERSION
    job: ProcessingJobPayload


class ProcessingResultPayload(ProcessingApiModel):
    result_id: UUID
    job_id: UUID
    media_id: UUID
    algorithm: str
    algorithm_version: str
    result_type: str
    result_data: dict[str, Any]
    created_at: str

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_utc_timestamp(value)


class ProcessingResultsResponse(ProcessingApiModel):
    api_version: Literal["1.0"] = PROCESSING_API_VERSION
    job_id: UUID
    items: list[ProcessingResultPayload]
    total: int = Field(ge=0)


class ProcessingErrorBody(ProcessingApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ProcessingErrorResponse(ProcessingApiModel):
    error: ProcessingErrorBody


def _validate_utc_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be a UTC ISO 8601 string ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be valid UTC ISO 8601") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be UTC")
    return value

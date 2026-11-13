"""Schemas for investigation creation and retrieval APIs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from backend.app.schemas.language_tags import canonicalize_language_tag


InputType = Literal["text", "image", "text_image"]
InvestigationStatus = Literal[
    "awaiting_media", "received", "processing", "completed", "failed"
]
SUPPORTED_LANGUAGE_TAGS = {
    "en",
    "en-US",
    "en-GB",
    "hi",
    "hi-IN",
    "te",
    "te-IN",
}


class InvestigationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_type: InputType
    claim_text: str | None = Field(default=None, max_length=10_000)
    language: str | None = None
    source_reference: str | None = Field(default=None, max_length=2_048)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            # None represents automatic-detection mode. No detector runs in this phase.
            return None
        try:
            normalized = canonicalize_language_tag(value)
        except ValueError as exc:
            raise PydanticCustomError(
                "invalid_language_tag",
                "Language must be a well-formed BCP-47 tag.",
            ) from exc

        if normalized not in SUPPORTED_LANGUAGE_TAGS:
            raise PydanticCustomError(
                "language_not_supported",
                "The language tag is well-formed but is not supported.",
            )
        return normalized

    @field_validator("source_reference")
    @classmethod
    def validate_source_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(character.isspace() for character in value):
            raise ValueError("source_reference must not contain whitespace")
        from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

        try:
            TypeAdapter(AnyHttpUrl).validate_python(value)
        except ValidationError:
            raise ValueError("source_reference must be a valid HTTP or HTTPS URL") from None
        return value

    @model_validator(mode="after")
    def validate_claim_text(self) -> "InvestigationCreateRequest":
        if self.input_type in {"text", "text_image"}:
            if self.claim_text is None or not self.claim_text.strip():
                raise ValueError("claim_text is required and must not be blank")
        elif self.claim_text is not None:
            raise ValueError("claim_text must be null for image input")
        return self


class MediaAssetResponse(BaseModel):
    id: str
    media_type: str
    mime_type: str
    size_bytes: int
    checksum: str
    processing_status: str
    created_at: str


class ClaimResponse(BaseModel):
    text: str
    language: str | None
    normalized_text: str | None


class InvestigationResponse(BaseModel):
    investigation_id: str
    input_type: InputType
    status: InvestigationStatus
    claim: ClaimResponse | None
    source_reference: str | None
    created_at: str
    updated_at: str
    media: list[MediaAssetResponse] = Field(default_factory=list)
    latest_job: dict | None = None
    latest_result: dict | None = None
    failure: dict | None = None


class InvestigationCreatedResponse(BaseModel):
    investigation_id: str
    input_type: InputType
    status: InvestigationStatus
    created_at: str
    message: str


class InvestigationHistoryItem(BaseModel):
    investigation_id: str
    input_type: InputType
    status: InvestigationStatus
    claim: ClaimResponse | None
    source_reference: str | None
    created_at: str
    updated_at: str


class InvestigationHistoryResponse(BaseModel):
    items: list[InvestigationHistoryItem]
    total: int
    limit: int
    offset: int

"""Pydantic request and response schemas for the Phase 2 API."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from language_tags import tags
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SUPPORTED_LANGUAGES = {
    tag.casefold(): tag for tag in ("en", "en-US", "en-GB", "hi", "hi-IN", "te", "te-IN")
}


def has_bcp47_structure(value: str) -> bool:
    """Check the RFC 5646 language-tag grammar before registry validation."""
    if not value or len(value) > 255 or value != value.strip():
        return False
    parts = value.split("-")
    if any(not part or len(part) > 8 or not part.isalnum() or not part.isascii() for part in parts):
        return False
    lower = [part.lower() for part in parts]
    if lower[0] == "x":
        return len(parts) > 1
    primary = parts[0]
    if not primary.isalpha() or not 2 <= len(primary) <= 8:
        return False
    index = 1
    if len(primary) <= 3:
        extlangs = 0
        while index < len(parts) and len(parts[index]) == 3 and parts[index].isalpha() and extlangs < 3:
            extlangs += 1
            index += 1
    if index < len(parts) and len(parts[index]) == 4 and parts[index].isalpha():
        index += 1
    if index < len(parts) and (
        (len(parts[index]) == 2 and parts[index].isalpha())
        or (len(parts[index]) == 3 and parts[index].isdigit())
    ):
        index += 1
    while index < len(parts):
        part = parts[index]
        if 5 <= len(part) <= 8 and part.isalnum():
            index += 1
        elif len(part) == 4 and part[0].isdigit() and part.isalnum():
            index += 1
        else:
            break
    seen_extensions: set[str] = set()
    while index < len(parts) and len(parts[index]) == 1 and lower[index] != "x":
        singleton = lower[index]
        if singleton in seen_extensions:
            return False
        seen_extensions.add(singleton)
        index += 1
        extension_start = index
        while index < len(parts) and 2 <= len(parts[index]) <= 8:
            index += 1
        if index == extension_start:
            return False
    if index < len(parts) and lower[index] == "x":
        index += 1
        private_start = index
        while index < len(parts) and 1 <= len(parts[index]) <= 8:
            index += 1
        if index == private_start:
            return False
    return index == len(parts)


def is_valid_bcp47(value: str) -> bool:
    """Use IANA registry validation, including valid grandfathered tags.

    The dependency treats deprecated grandfathered tags as invalid for modern
    use, but they remain valid BCP 47 tags. Its registry classification lets us
    accept those exact tags while the grammar check rejects malformed strings
    that its parser may otherwise normalize permissively.
    """
    parsed = tags.tag(value)
    if parsed.type == "grandfathered":
        return True
    return has_bcp47_structure(value) and parsed.valid


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigationCreate(ApiModel):
    input_type: Literal["text", "image", "text_image"]
    claim_text: str | None = Field(default=None, max_length=10_000)
    language: str | None = None
    source_reference: str | None = Field(default=None, max_length=2_048)

    @field_validator("claim_text", mode="before")
    @classmethod
    def trim_claim(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_valid_bcp47(value):
            raise ValueError("INVALID_LANGUAGE_TAG: language must be a valid BCP 47 tag")
        canonical_language = SUPPORTED_LANGUAGES.get(value.casefold())
        if canonical_language is None:
            raise ValueError("LANGUAGE_NOT_SUPPORTED: language is not supported yet")
        return canonical_language

    @field_validator("source_reference")
    @classmethod
    def validate_source_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
            # Accessing .port also rejects malformed port values.
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("source_reference must be a valid HTTP or HTTPS URL") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source_reference must be a valid HTTP or HTTPS URL")
        if any(character.isspace() for character in value):
            raise ValueError("source_reference must be a valid HTTP or HTTPS URL")
        return value

    @model_validator(mode="after")
    def validate_claim_requirement(self) -> "InvestigationCreate":
        if self.input_type in {"text", "text_image"} and not self.claim_text:
            raise ValueError("claim_text is required and cannot be empty for this input_type")
        if self.input_type == "image" and self.claim_text is not None:
            raise ValueError("claim_text must be null for image input_type")
        return self


class ClaimResponse(ApiModel):
    text: str | None
    language: str | None
    normalized_text: None = None


class InvestigationResponse(ApiModel):
    investigation_id: str
    input_type: str
    status: str
    claim: ClaimResponse | None
    source_reference: str | None
    created_at: datetime
    updated_at: datetime
    media: list[dict] = Field(default_factory=list)
    latest_job: None = None
    latest_result: None = None
    failure: None = None


class InvestigationSummary(ApiModel):
    investigation_id: str
    input_type: str
    status: str
    claim: ClaimResponse | None
    source_reference: str | None
    created_at: datetime
    updated_at: datetime


class InvestigationHistory(ApiModel):
    items: list[InvestigationSummary]
    total: int
    limit: int
    offset: int


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(ApiModel):
    error: ErrorBody

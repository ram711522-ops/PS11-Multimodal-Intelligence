"""Typed internal records for multilingual claims and source-attributed evidence."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from backend.app.claim_verification.exceptions import InvalidClaim, InvalidEvidence


LanguageStatus = Literal[
    "detected", "uncertain", "unavailable", "failed", "not_run_user_metadata_present"
]
EvidenceStance = Literal["supporting", "contradicting", "inconclusive"]
VerificationOutcome = Literal[
    "supported", "refuted", "mixed_evidence", "unverified", "insufficient_evidence"
]

SUPPORTED_LANGUAGES = {"en", "en-US", "en-GB", "hi", "hi-IN", "te", "te-IN"}


@dataclass(frozen=True)
class ClaimInput:
    investigation_id: str
    original_text: str
    user_provided_language: str | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.investigation_id)
        except (ValueError, TypeError, AttributeError):
            raise InvalidClaim("A valid investigation UUID is required.") from None
        if not isinstance(self.original_text, str) or not self.original_text.strip():
            raise InvalidClaim()
        if len(self.original_text) > 10_000:
            raise InvalidClaim("The claim exceeds the 10,000-character limit.")
        if self.user_provided_language is not None and self.user_provided_language not in SUPPORTED_LANGUAGES:
            raise InvalidClaim("The stored language is outside the supported language allowlist.")


@dataclass(frozen=True)
class LanguageDetection:
    status: LanguageStatus
    detected_language: str | None
    detector_name: str
    detector_version: str
    limitations: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    source_url: str
    provider_name: str
    provider_version: str
    retrieved_at: str
    evidence_id: str = field(default_factory=lambda: str(uuid4()))
    publisher: str | None = None
    publication_date: str | None = None
    review_date: str | None = None
    original_excerpt: str | None = None
    original_language: str | None = None
    evidence_category: str | None = None
    source_attribution: str | None = None
    processing_status: str = "retrieved"
    limitations: tuple[str, ...] = ()
    provider_rating: str | None = None
    assessment: EvidenceStance | None = None
    assessment_method: str | None = None
    assessment_version: str | None = None
    outdated: bool = False

    def __post_init__(self) -> None:
        try:
            UUID(self.evidence_id)
        except (ValueError, TypeError, AttributeError):
            raise InvalidEvidence("Evidence ID must be a UUID.") from None
        parsed = urlsplit(self.source_url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise InvalidEvidence("Evidence source URL must be a valid HTTP or HTTPS URL.")
        try:
            _ = parsed.port
        except ValueError:
            raise InvalidEvidence("Evidence source URL contains an invalid port.") from None
        if not self.provider_name.strip() or not self.provider_version.strip():
            raise InvalidEvidence("Evidence provider name and version are required.")
        _validate_timestamp(self.retrieved_at, "retrieved_at")
        if self.publication_date is not None:
            _validate_date(self.publication_date, "publication_date")
        if self.review_date is not None:
            _validate_date(self.review_date, "review_date")
        if self.assessment is not None and not (
            self.assessment_method and self.assessment_version
        ):
            raise InvalidEvidence("An evidence assessment must identify its method and version.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderStatus:
    provider_name: str
    provider_version: str
    status: Literal["ok", "empty", "disabled", "timeout", "failed"]
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class EvidenceSearchQuery:
    original_claim: str
    normalized_search_text: str
    language: str | None
    detected_language: str | None


@dataclass(frozen=True)
class EvidenceSearchResult:
    records: tuple[EvidenceRecord, ...]
    provider_statuses: tuple[ProviderStatus, ...]
    duplicate_count: int = 0


@dataclass(frozen=True)
class EvidenceConflict:
    supporting_evidence_id: str
    contradicting_evidence_id: str
    supporting_source: str | None
    contradicting_source: str | None


@dataclass(frozen=True)
class VerificationResult:
    investigation_id: str
    original_claim: str
    user_provided_language: str | None
    detected_language: str | None
    language_detection_status: LanguageStatus
    normalized_search_text: str
    translation: None
    outcome: VerificationOutcome
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    outdated_evidence_ids: tuple[str, ...]
    conflicts: tuple[EvidenceConflict, ...]
    evidence: tuple[EvidenceRecord, ...]
    providers: tuple[ProviderStatus, ...]
    assessment_method: str
    assessment_version: str
    processing_status: Literal["completed", "partial", "failed"]
    explanation: str
    limitations: tuple[str, ...]
    confidence: None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_timestamp(value: str, field_name: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise InvalidEvidence(f"{field_name} must be an ISO 8601 timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidEvidence(f"{field_name} must include a timezone.")


def _validate_date(value: str, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidEvidence(f"{field_name} must be an ISO 8601 date when present.") from None

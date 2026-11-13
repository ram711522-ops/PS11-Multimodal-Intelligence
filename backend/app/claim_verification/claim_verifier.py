"""Internal orchestration; reads an existing claim and does not persist results."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol
from uuid import UUID

from backend.app.claim_verification.evidence_analysis import (
    ASSESSMENT_METHOD,
    ASSESSMENT_VERSION,
    assess_evidence,
)
from backend.app.claim_verification.evidence_retrieval import EvidenceRetriever
from backend.app.claim_verification.exceptions import (
    InvestigationNotFound,
    InvalidClaim,
    InvalidInvestigationId,
)
from backend.app.claim_verification.language_detection import (
    LanguageDetector,
    UnavailableLanguageDetector,
)
from backend.app.claim_verification.models import (
    ClaimInput,
    EvidenceSearchQuery,
    LanguageDetection,
    SUPPORTED_LANGUAGES,
    VerificationResult,
)
from backend.app.claim_verification.normalization import (
    NORMALIZATION_METHOD,
    NORMALIZATION_VERSION,
    normalize_for_search,
)


class InvestigationClaimLoader(Protocol):
    def load_claim(self, investigation_id: str) -> ClaimInput: ...


@dataclass(frozen=True)
class SQLiteInvestigationClaimLoader:
    """Read-only adapter over the existing investigations table and repository."""

    database_path: Path | str

    def load_claim(self, investigation_id: str) -> ClaimInput:
        from backend.app.database import database_connection
        from backend.app.repositories.investigations import get_investigation

        try:
            canonical_id = str(UUID(investigation_id))
        except (ValueError, TypeError, AttributeError):
            raise InvalidInvestigationId() from None
        with database_connection(self.database_path) as connection:
            row = get_investigation(connection, canonical_id)
        if row is None:
            raise InvestigationNotFound()
        if row["claim_text"] is None:
            raise InvalidClaim("Image-only investigations do not contain a text claim.")
        return ClaimInput(
            investigation_id=row["id"],
            original_text=row["claim_text"],
            user_provided_language=row["language"],
        )


@dataclass(frozen=True)
class ClaimVerificationService:
    claim_loader: InvestigationClaimLoader
    evidence_retriever: EvidenceRetriever = EvidenceRetriever()
    language_detector: LanguageDetector = UnavailableLanguageDetector()

    def verify_investigation(self, investigation_id: str) -> VerificationResult:
        claim = self.claim_loader.load_claim(investigation_id)
        if claim.user_provided_language is not None:
            detection = LanguageDetection(
                status="not_run_user_metadata_present",
                detected_language=None,
                detector_name="ps11.language-detector.not-run",
                detector_version="1.0.0",
                limitations=("The stored language is user-provided metadata, not a detected language.",),
            )
        else:
            try:
                detection = self.language_detector.detect(claim.original_text)
            except Exception:
                detection = LanguageDetection(
                    status="failed", detected_language=None,
                    detector_name="ps11.language-detector.provider",
                    detector_version="unknown",
                    limitations=("Language detection failed; no language was inferred.",),
                    error_code="LANGUAGE_DETECTION_FAILED",
                )
            if not isinstance(detection, LanguageDetection):
                detection = LanguageDetection(
                    status="failed", detected_language=None,
                    detector_name="ps11.language-detector.provider",
                    detector_version="unknown",
                    limitations=("Language detection returned an invalid result; no language was inferred.",),
                    error_code="INVALID_LANGUAGE_DETECTION_RESULT",
                )
            elif detection.detected_language not in SUPPORTED_LANGUAGES and (
                detection.detected_language is not None or detection.status == "detected"
            ):
                detection = replace(
                    detection,
                    status="uncertain",
                    detected_language=None,
                    limitations=detection.limitations + (
                        "The detector returned a language outside the supported allowlist; it was not used.",
                    ),
                )

        normalized = normalize_for_search(claim.original_text)
        query = EvidenceSearchQuery(
            original_claim=claim.original_text,
            normalized_search_text=normalized,
            language=claim.user_provided_language or detection.detected_language,
            detected_language=detection.detected_language,
        )
        search = self.evidence_retriever.retrieve(query)
        outcome, supporting, contradicting, outdated, conflicts, explanation = assess_evidence(
            search.records
        )
        provider_failures = any(
            item.status in {"failed", "timeout"} for item in search.provider_statuses
        )
        limitations = [
            "The original claim is preserved exactly; normalized search text is separate and was not persisted.",
            f"Search normalization method/version: {NORMALIZATION_METHOD}/{NORMALIZATION_VERSION}.",
            "No translation was performed; similarity or publisher ratings are not truth evidence by themselves.",
            "Assessment only aggregates explicit evidence annotations; source text is not automatically interpreted.",
        ]
        limitations.extend(detection.limitations)
        if search.duplicate_count:
            limitations.append(f"Removed {search.duplicate_count} duplicate evidence record(s).")
        return VerificationResult(
            investigation_id=claim.investigation_id,
            original_claim=claim.original_text,
            user_provided_language=claim.user_provided_language,
            detected_language=detection.detected_language,
            language_detection_status=detection.status,
            normalized_search_text=normalized,
            translation=None,
            outcome=outcome,
            supporting_evidence_ids=supporting,
            contradicting_evidence_ids=contradicting,
            outdated_evidence_ids=outdated,
            conflicts=conflicts,
            evidence=search.records,
            providers=search.provider_statuses,
            assessment_method=ASSESSMENT_METHOD,
            assessment_version=ASSESSMENT_VERSION,
            processing_status="partial" if provider_failures else "completed",
            explanation=explanation,
            limitations=tuple(limitations),
        )

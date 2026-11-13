"""Deterministic aggregation of explicit, attributed evidence assessments."""

from collections import defaultdict
from urllib.parse import urlsplit

from backend.app.claim_verification.models import (
    EvidenceConflict,
    EvidenceRecord,
    VerificationOutcome,
)


ASSESSMENT_METHOD = "explicit_source_assessment_aggregation"
ASSESSMENT_VERSION = "1.0.0"
MIN_INDEPENDENT_SOURCES = 2


def assess_evidence(
    records: tuple[EvidenceRecord, ...],
) -> tuple[
    VerificationOutcome,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[EvidenceConflict, ...],
    str,
]:
    """Aggregate explicit annotations only; a publisher rating is never a stance."""

    outdated = tuple(record.evidence_id for record in records if record.outdated)
    eligible = tuple(
        record for record in records
        if not record.outdated and record.assessment in {"supporting", "contradicting"}
    )
    supporting = tuple(record for record in eligible if record.assessment == "supporting")
    contradicting = tuple(record for record in eligible if record.assessment == "contradicting")
    conflicts = tuple(
        EvidenceConflict(
            supporting_evidence_id=left.evidence_id,
            contradicting_evidence_id=right.evidence_id,
            supporting_source=left.source_attribution or left.publisher,
            contradicting_source=right.source_attribution or right.publisher,
        )
        for left in supporting
        for right in contradicting
    )

    if supporting and contradicting:
        return (
            "mixed_evidence", tuple(item.evidence_id for item in supporting),
            tuple(item.evidence_id for item in contradicting), outdated, conflicts,
            "Retrieved evidence contains explicit supporting and contradicting assessments.",
        )
    if supporting:
        sources = _independent_sources(supporting)
        if len(sources) >= MIN_INDEPENDENT_SOURCES:
            return (
                "supported", tuple(item.evidence_id for item in supporting), (), outdated, (),
                "At least two independent source domains have explicit supporting assessments.",
            )
        return (
            "insufficient_evidence", tuple(item.evidence_id for item in supporting), (), outdated, (),
            "Supporting assessments do not meet the independent-source minimum.",
        )
    if contradicting:
        sources = _independent_sources(contradicting)
        if len(sources) >= MIN_INDEPENDENT_SOURCES:
            return (
                "refuted", (), tuple(item.evidence_id for item in contradicting), outdated, (),
                "At least two independent source domains have explicit contradicting assessments.",
            )
        return (
            "insufficient_evidence", (), tuple(item.evidence_id for item in contradicting), outdated, (),
            "Contradicting assessments do not meet the independent-source minimum.",
        )
    if records:
        return (
            "unverified", (), (), outdated, (),
            "Evidence was retrieved, but no current explicit supporting or contradicting assessment is available.",
        )
    return (
        "insufficient_evidence", (), (), (), (),
        "No evidence records were retrieved.",
    )


def _independent_sources(records: tuple[EvidenceRecord, ...]) -> set[str]:
    sources: set[str] = set()
    for record in records:
        domain = urlsplit(record.source_url).hostname
        if domain:
            sources.add("domain:" + domain.casefold())
    return sources

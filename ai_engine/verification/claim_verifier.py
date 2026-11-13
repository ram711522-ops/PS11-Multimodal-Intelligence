"""Deterministic claim assessment from supplied evidence; no retrieval occurs here."""

from collections.abc import Iterable
from urllib.parse import urlsplit

from .models import Claim, Evidence, EvidenceStance, Verdict, VerificationResult


# These are explicit policy thresholds, not learned or calibrated values.
MIN_TOTAL_WEIGHT = 0.5
MIN_DOMINANCE_RATIO = 0.75
MIN_CONFLICT_RATIO = 0.25


def _source_key(item: Evidence) -> str:
    """Normalize a publisher identity, falling back to the URL host."""
    source = item.source.strip().casefold()
    if source:
        return source
    return (urlsplit(item.url).hostname or item.url.strip().casefold()).casefold()


def _independent_evidence(evidence: Iterable[Evidence]) -> list[Evidence]:
    """Keep the strongest item per source so repeats cannot inflate scores."""
    strongest: dict[str, Evidence] = {}
    for item in evidence:
        if not isinstance(item, Evidence):
            raise TypeError("evidence must contain only Evidence instances")
        key = _source_key(item)
        current = strongest.get(key)
        weight = item.reliability * item.relevance
        current_weight = current.reliability * current.relevance if current else -1.0
        if weight > current_weight:
            strongest[key] = item
    return list(strongest.values())


def verify_claim(claim: Claim | str, evidence: Iterable[Evidence]) -> VerificationResult:
    """Assess a claim from supplied evidence using transparent heuristics.

    A source contributes only its highest-weight item. Verdicts remain
    ``Unverified`` when usable evidence is too weak. ``Misleading`` is reserved
    for substantial, balanced evidence on both sides; a small minority view
    does not by itself create that verdict.
    """
    if isinstance(claim, str):
        claim = Claim(claim)
    if not isinstance(claim, Claim):
        raise TypeError("claim must be a Claim or non-empty string")

    independent = _independent_evidence(evidence)
    supporting = sum(
        item.reliability * item.relevance
        for item in independent if item.stance is EvidenceStance.SUPPORT
    )
    contradicting = sum(
        item.reliability * item.relevance
        for item in independent if item.stance is EvidenceStance.REFUTE
    )
    total = supporting + contradicting

    if total < MIN_TOTAL_WEIGHT:
        return VerificationResult(
            Verdict.UNVERIFIED, 0.0, round(supporting, 3), round(contradicting, 3),
            "Insufficient evidence strength after reliability and relevance weighting.",
            "No confidence assigned because total independent evidence weight is below "
            f"{MIN_TOTAL_WEIGHT:.2f}.", len(independent),
        )

    support_ratio = supporting / total
    refute_ratio = contradicting / total
    if support_ratio >= MIN_DOMINANCE_RATIO:
        verdict = Verdict.VERIFIED
        explanation = "Independent, weighted evidence predominantly supports the claim."
    elif refute_ratio >= MIN_DOMINANCE_RATIO:
        verdict = Verdict.CONTRADICTED
        explanation = "Independent, weighted evidence predominantly contradicts the claim."
    elif support_ratio >= MIN_CONFLICT_RATIO and refute_ratio >= MIN_CONFLICT_RATIO:
        verdict = Verdict.MISLEADING
        explanation = "Substantial independent evidence conflicts; neither side clearly dominates."
    else:
        verdict = Verdict.UNVERIFIED
        explanation = "Evidence is mixed or too inconclusive to support a reliable verdict."

    # Heuristic combines evidence volume (saturating at 3.0) and directional
    # agreement. It is deliberately described as an indicator, not probability.
    strength = min(total / 3.0, 1.0)
    agreement = abs(support_ratio - refute_ratio)
    confidence = round(strength * agreement, 3)
    basis = (
        f"Heuristic = min(total independent weight / 3, 1) × directional balance "
        f"({strength:.3f} × {agreement:.3f}); not a calibrated probability."
    )
    return VerificationResult(
        verdict, confidence, round(supporting, 3), round(contradicting, 3),
        explanation, basis, len(independent),
    )

"""Typed, backend-independent models used by claim verification."""

from dataclasses import dataclass
from enum import Enum
import math


class Verdict(str, Enum):
    """The qualitative outcome of assessing a claim against evidence."""

    VERIFIED = "Verified"
    CONTRADICTED = "Contradicted"
    MISLEADING = "Misleading"
    UNVERIFIED = "Unverified"


class EvidenceStance(str, Enum):
    """How an evidence item relates to the claim."""

    SUPPORT = "support"
    REFUTE = "refute"


@dataclass(frozen=True)
class Claim:
    """A textual claim to assess."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Claim text must be a non-empty string.")


@dataclass(frozen=True)
class Evidence:
    """A source's assessment of a claim, with bounded quality scores.

    ``source`` should identify the publisher or originating source. Items with
    the same normalized source are treated as one source during verification.
    """

    source: str
    url: str
    stance: EvidenceStance | str
    reliability: float
    relevance: float

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("Evidence source must be a non-empty string.")
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("Evidence URL must be a non-empty string.")
        try:
            stance = EvidenceStance(self.stance)
        except (TypeError, ValueError) as exc:
            raise ValueError("Evidence stance must be 'support' or 'refute'.") from exc
        object.__setattr__(self, "stance", stance)
        _validate_score("reliability", self.reliability)
        _validate_score("relevance", self.relevance)


@dataclass(frozen=True)
class VerificationResult:
    """Verdict and transparent evidence-weight summary.

    ``confidence`` is a bounded heuristic strength indicator, not a calibrated
    probability. ``confidence_basis`` states how that indicator was computed.
    """

    verdict: Verdict
    confidence: float
    supporting_score: float
    contradicting_score: float
    explanation: str
    confidence_basis: str
    distinct_sources: int


def _validate_score(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name.capitalize()} must be a number from 0 to 1.")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name.capitalize()} must be a finite number from 0 to 1.")

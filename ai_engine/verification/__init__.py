"""Public interface for the backend-independent verification core."""

from .claim_verifier import verify_claim
from .models import Claim, Evidence, EvidenceStance, Verdict, VerificationResult

__all__ = ["Claim", "Evidence", "EvidenceStance", "Verdict", "VerificationResult", "verify_claim"]

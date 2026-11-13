"""Internal, evidence-first claim verification components.

This package is not wired to public API routes or database persistence.
"""

from backend.app.claim_verification.claim_verifier import ClaimVerificationService

__all__ = ["ClaimVerificationService"]

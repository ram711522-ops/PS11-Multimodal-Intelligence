"""Typed internal failures for claim verification."""


class ClaimVerificationError(Exception):
    """Base class for safe, expected claim-verification failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InvalidClaim(ClaimVerificationError):
    def __init__(self, message: str = "A non-empty text claim is required.") -> None:
        super().__init__("INVALID_CLAIM", message)


class InvestigationNotFound(ClaimVerificationError):
    def __init__(self) -> None:
        super().__init__("INVESTIGATION_NOT_FOUND", "The investigation was not found.")


class InvalidInvestigationId(ClaimVerificationError):
    def __init__(self) -> None:
        super().__init__("INVALID_INVESTIGATION_ID", "The investigation identifier is invalid.")


class InvalidEvidence(ClaimVerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("INVALID_EVIDENCE", message)

"""Provider contracts and a disabled-by-default provider implementation."""

from typing import Protocol

from backend.app.claim_verification.models import EvidenceRecord, EvidenceSearchQuery


class EvidenceProvider(Protocol):
    name: str
    version: str

    def search(self, query: EvidenceSearchQuery) -> tuple[EvidenceRecord, ...]: ...


class DisabledEvidenceProvider:
    """Default provider; performs no network requests and returns no evidence."""

    name = "ps11.evidence-provider.disabled"
    version = "1.0.0"

    def search(self, _query: EvidenceSearchQuery) -> tuple[EvidenceRecord, ...]:
        return ()

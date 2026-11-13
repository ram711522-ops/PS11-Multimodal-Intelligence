"""Provider-isolated evidence retrieval, provenance validation, and deduplication."""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from backend.app.claim_verification.evidence_provider import (
    DisabledEvidenceProvider,
    EvidenceProvider,
)
from backend.app.claim_verification.models import (
    EvidenceRecord,
    EvidenceSearchQuery,
    EvidenceSearchResult,
    ProviderStatus,
)


@dataclass(frozen=True)
class EvidenceRetriever:
    providers: tuple[EvidenceProvider, ...] = (DisabledEvidenceProvider(),)
    allowed_source_domains: tuple[str, ...] | None = None

    def retrieve(self, query: EvidenceSearchQuery) -> EvidenceSearchResult:
        records: list[EvidenceRecord] = []
        statuses: list[ProviderStatus] = []
        for provider in self.providers:
            if isinstance(provider, DisabledEvidenceProvider):
                statuses.append(ProviderStatus(
                    provider_name=provider.name,
                    provider_version=provider.version,
                    status="disabled",
                    error_code="PROVIDER_DISABLED",
                    message="External evidence retrieval is disabled by default.",
                ))
                continue
            try:
                received = provider.search(query)
                provider_records = tuple(received)
                if any(not isinstance(record, EvidenceRecord) for record in provider_records):
                    raise TypeError("Provider returned an invalid evidence record.")
                if self.allowed_source_domains is not None:
                    allowed = {domain.casefold() for domain in self.allowed_source_domains}
                    provider_records = tuple(
                        record for record in provider_records
                        if (urlsplit(record.source_url).hostname or "").casefold() in allowed
                    )
                records.extend(provider_records)
                statuses.append(ProviderStatus(
                    provider_name=provider.name,
                    provider_version=provider.version,
                    status="ok" if provider_records else "empty",
                ))
            except TimeoutError:
                statuses.append(ProviderStatus(
                    provider_name=provider.name,
                    provider_version=provider.version,
                    status="timeout",
                    error_code="PROVIDER_TIMEOUT",
                    message="The evidence provider timed out.",
                ))
            except Exception:
                statuses.append(ProviderStatus(
                    provider_name=provider.name,
                    provider_version=provider.version,
                    status="failed",
                    error_code="PROVIDER_FAILED",
                    message="The evidence provider failed.",
                ))

        if not self.providers:
            statuses.append(ProviderStatus(
                provider_name="none", provider_version="none", status="disabled",
                error_code="NO_EVIDENCE_PROVIDERS", message="No evidence providers are enabled.",
            ))
        unique: dict[tuple[str, str | None, str | None], EvidenceRecord] = {}
        for record in records:
            key = (
                canonical_source_url(record.source_url),
                record.review_date,
                record.original_excerpt,
            )
            unique.setdefault(key, record)
        return EvidenceSearchResult(
            records=tuple(unique.values()),
            provider_statuses=tuple(statuses),
            duplicate_count=len(records) - len(unique),
        )


def canonical_source_url(value: str) -> str:
    parts = urlsplit(value)
    hostname = (parts.hostname or "").lower()
    port = parts.port
    netloc = hostname
    if port is not None and not (
        (parts.scheme.lower() == "http" and port == 80)
        or (parts.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))

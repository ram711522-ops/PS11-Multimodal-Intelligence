"""Traceable media references and explicit handoff to claim verification."""

from dataclasses import dataclass
import re
from uuid import UUID

from ai_engine.verification import Claim, Evidence, VerificationResult, verify_claim

from .models import DerivedMediaReference, ImageMetadata
from .perceptual_hash import MediaPerceptualHash


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MediaEvidenceReference:
    """Provenance for a derived artifact; it is not a truth assessment."""

    investigation_id: str
    source_media_id: str
    derived_media_id: str
    source_sha256: str
    derived_sha256: str
    perceptual_hash: str
    hash_algorithm: str
    hash_version: str
    transformation: str
    transformation_version: str

    def __post_init__(self) -> None:
        for name in ("investigation_id", "source_media_id", "derived_media_id"):
            try:
                UUID(getattr(self, name))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(f"{name} must be a valid UUID.") from exc
        if not _SHA256.fullmatch(self.source_sha256) or not _SHA256.fullmatch(self.derived_sha256):
            raise ValueError("source_sha256 and derived_sha256 must be lowercase SHA-256 digests.")
        if not re.fullmatch(r"[0-9a-f]{16}", self.perceptual_hash):
            raise ValueError("perceptual_hash must be a 64-bit hexadecimal pHash.")
        for name in ("hash_algorithm", "hash_version", "transformation", "transformation_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string.")

    @classmethod
    def from_processed_media(
        cls,
        metadata: ImageMetadata,
        derived: DerivedMediaReference,
        perceptual_hash: MediaPerceptualHash,
    ) -> "MediaEvidenceReference":
        """Build a provenance record after verifying all source/derivative links."""
        if metadata.media_id != derived.source_media_id:
            raise ValueError("derived reference does not point to the supplied source media.")
        if metadata.media_id != perceptual_hash.source_media_id:
            raise ValueError("pHash does not point to the supplied source media.")
        if derived.derived_media_id != perceptual_hash.derived_media_id:
            raise ValueError("pHash does not point to the supplied derived media.")
        return cls(
            investigation_id=metadata.investigation_id,
            source_media_id=metadata.media_id,
            derived_media_id=derived.derived_media_id,
            source_sha256=metadata.sha256,
            derived_sha256=derived.sha256,
            perceptual_hash=perceptual_hash.hash_value,
            hash_algorithm=perceptual_hash.algorithm,
            hash_version=perceptual_hash.algorithm_version,
            transformation=derived.transformation,
            transformation_version=derived.transformation_version,
        )


@dataclass(frozen=True)
class MediaVerification:
    """Claim-verifier output kept beside media provenance references."""

    verification: VerificationResult
    media_references: tuple[MediaEvidenceReference, ...]


def verify_claim_with_media_references(
    claim: Claim | str,
    evidence: list[Evidence] | tuple[Evidence, ...],
    media_references: tuple[MediaEvidenceReference, ...] = (),
) -> MediaVerification:
    """Delegate only supplied assessments to the existing claim verifier.

    Provenance references do not create evidence stances, reliability scores,
    or relevance scores. With no assessed evidence the existing verifier
    returns its normal ``Unverified`` result.
    """
    if not isinstance(media_references, tuple) or any(
        not isinstance(reference, MediaEvidenceReference) for reference in media_references
    ):
        raise TypeError("media_references must be a tuple of MediaEvidenceReference values.")
    return MediaVerification(verify_claim(claim, evidence), media_references)

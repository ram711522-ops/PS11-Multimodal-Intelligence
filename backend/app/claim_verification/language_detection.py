"""Language detector interface; no unsupported language guesses are made."""

from typing import Protocol

from backend.app.claim_verification.models import LanguageDetection


class LanguageDetector(Protocol):
    def detect(self, text: str) -> LanguageDetection: ...


class UnavailableLanguageDetector:
    """Safe default until an evaluated English/Hindi/Telugu detector is approved."""

    name = "ps11.language-detector.unavailable"
    version = "1.0.0"

    def detect(self, _text: str) -> LanguageDetection:
        return LanguageDetection(
            status="unavailable",
            detected_language=None,
            detector_name=self.name,
            detector_version=self.version,
            limitations=(
                "No language detector with validated English, Hindi, and Telugu coverage is configured.",
                "No language was inferred from script or user metadata.",
            ),
            error_code="LANGUAGE_DETECTOR_UNAVAILABLE",
        )

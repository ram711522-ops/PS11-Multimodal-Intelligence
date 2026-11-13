"""Internal audio media processor; no public audio-upload route is defined."""

from dataclasses import dataclass
from pathlib import Path

from backend.app.services.audio_deepfake_detector import detect_audio_file
from backend.app.services.deepfake_detection import DeepfakeDetectionResult
from backend.app.services.media_pipeline import MediaProcessingRequest


@dataclass(frozen=True)
class AudioProcessingResult:
    sample_rate: int | None
    channels: int | None
    duration_seconds: float | None
    detector_result: DeepfakeDetectionResult


class AudioProcessor:
    processor_name = "ps11.audio.deepfake_detector"
    processor_version = "1.0.0"
    result_type = "audio_authenticity_detection"
    result_schema_version = "1.0"

    def process(self, request: MediaProcessingRequest) -> AudioProcessingResult:
        result = detect_audio_file(
            request.storage_reference,
            expected_sha256=request.expected_checksum,
        )
        # No path or decoder-specific exception is persisted in this result.
        return AudioProcessingResult(
            sample_rate=None,
            channels=None,
            duration_seconds=None,
            detector_result=result,
        )

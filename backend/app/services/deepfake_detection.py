"""Shared, non-probabilistic result contract for optional pretrained detectors."""

from dataclasses import asdict, dataclass
from typing import Any, Literal


DetectionModality = Literal["image", "video", "audio"]
DetectionVerdict = Literal[
    "likely_manipulated", "likely_authentic", "inconclusive"
]
DetectionStatus = Literal["completed", "model_unavailable", "failed", "skipped"]


@dataclass(frozen=True)
class DetectionError:
    code: str
    message: str


@dataclass(frozen=True)
class DeepfakeDetectionResult:
    modality: DetectionModality
    verdict: DetectionVerdict
    raw_model_score: float | None
    calibrated_probability: float | None
    detector_name: str
    detector_version: str
    processing_status: DetectionStatus
    model_name: str
    model_version: str
    model_source_url: str
    model_license: str
    evidence: tuple[dict[str, Any], ...] = ()
    timestamps: tuple[dict[str, int], ...] = ()
    limitations: tuple[str, ...] = ()
    error: DetectionError | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def unavailable_result(
    *,
    modality: DetectionModality,
    detector_name: str,
    detector_version: str,
    model_name: str,
    model_version: str,
    model_source_url: str,
    model_license: str,
    reason: str = "Model weights or runtime dependencies are not configured locally.",
) -> DeepfakeDetectionResult:
    return DeepfakeDetectionResult(
        modality=modality,
        verdict="inconclusive",
        raw_model_score=None,
        calibrated_probability=None,
        detector_name=detector_name,
        detector_version=detector_version,
        processing_status="model_unavailable",
        model_name=model_name,
        model_version=model_version,
        model_source_url=model_source_url,
        model_license=model_license,
        limitations=("No model inference was performed.",),
        error=DetectionError("MODEL_UNAVAILABLE", reason),
    )


def failed_result(
    *,
    modality: DetectionModality,
    detector_name: str,
    detector_version: str,
    model_name: str,
    model_version: str,
    model_source_url: str,
    model_license: str,
    code: str,
    message: str,
    limitations: tuple[str, ...] = (),
) -> DeepfakeDetectionResult:
    return DeepfakeDetectionResult(
        modality=modality,
        verdict="inconclusive",
        raw_model_score=None,
        calibrated_probability=None,
        detector_name=detector_name,
        detector_version=detector_version,
        processing_status="failed",
        model_name=model_name,
        model_version=model_version,
        model_source_url=model_source_url,
        model_license=model_license,
        limitations=limitations,
        error=DetectionError(code, message),
    )

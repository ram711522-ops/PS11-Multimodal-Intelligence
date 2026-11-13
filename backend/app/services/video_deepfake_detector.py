"""Eight-frame face-video detector using the published MTL FaceAuth ONNX model."""

from functools import lru_cache
import logging
import math
from pathlib import Path
from typing import Any

from PIL import Image

from backend.app.config import Settings
from backend.app.services.deepfake_detection import (
    DeepfakeDetectionResult,
    failed_result,
    unavailable_result,
)


logger = logging.getLogger(__name__)
DETECTOR_NAME = "ps11.video.mtl-face-auth"
DETECTOR_VERSION = "1.0.0"
MODEL_NAME = "MTL Face Anti-Spoofing and Deepfake Detection"
MODEL_SOURCE_URL = "https://huggingface.co/shahriarhd/mtl-face-auth"
MODEL_LICENSE = "MIT"
FRAME_COUNT = 8
MODEL_LIMITATIONS = (
    "The checkpoint is a research detector trained/evaluated on FaceForensics++ and face video.",
    "It requires detectable faces in eight sampled frames and can fail under large motion or compression.",
    "The temporal head and deepfake score are uncalibrated model outputs, not proof.",
)


@lru_cache(maxsize=2)
def _load_session(model_path: str):
    import onnxruntime as ort

    return ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])


def detect_video_frames(
    frames: tuple[Any, ...] | list[Any],
    storage_root: Path | str,
) -> DeepfakeDetectionResult:
    settings = Settings.from_environment()
    model_path = settings.video_detector_model_path
    model_version = _version()
    if model_path is None or not model_path.is_file():
        return _unavailable(model_version)
    if len(frames) < FRAME_COUNT:
        return failed_result(
            modality="video", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="INSUFFICIENT_VIDEO_FRAMES",
            message="The video detector requires eight distinct sampled frames.",
            limitations=MODEL_LIMITATIONS,
        )

    try:
        import cv2
        import numpy as np

        session = _load_session(str(model_path))
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        face_detector = cv2.CascadeClassifier(str(cascade_path))
        if face_detector.empty():
            raise RuntimeError("Local face detector cascade is unavailable.")

        selected = _select_frames(frames)
        tensors: list[Any] = []
        timestamps: list[dict[str, int]] = []
        mean = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
        for frame in selected:
            reference = str(frame.artifact_reference)
            if Path(reference).name != reference or "/" in reference or "\\" in reference:
                raise ValueError("Invalid extracted frame reference.")
            frame_path = Path(storage_root) / "processed" / "frames" / reference
            with Image.open(frame_path) as opened:
                rgb = opened.convert("RGB")
                pixels = np.asarray(rgb)
            gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
            faces = face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
            if len(faces) == 0:
                raise LookupError("A sampled video frame has no detectable face.")
            x, y, width, height = max(faces, key=lambda box: int(box[2]) * int(box[3]))
            crop = pixels[y:y + height, x:x + width]
            prepared = Image.fromarray(crop).resize((224, 224), Image.Resampling.LANCZOS)
            array = np.asarray(prepared, dtype=np.float32) / 255.0
            array = (array - mean) / std
            tensors.append(np.transpose(array, (2, 0, 1)))
            timestamps.append({"timestamp_ms": int(frame.actual_timestamp_ms)})

        tensor = np.asarray([tensors], dtype=np.float32)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: tensor})
        values = np.concatenate([np.asarray(value).reshape(-1) for value in outputs])
        if values.size < 3 or not np.all(np.isfinite(values[:3])):
            raise ValueError("The video checkpoint returned an invalid output.")
        fake_logit, spoof_logit, temporal_logit = (float(item) for item in values[:3])
        fake_score = _sigmoid(fake_logit)
        # 0.6 is the model card's documented decision threshold; this is not
        # exposed as a calibrated probability.
        verdict = "likely_manipulated" if fake_score >= 0.6 else "likely_authentic"
        return DeepfakeDetectionResult(
            modality="video", verdict=verdict, raw_model_score=fake_logit,
            calibrated_probability=None, detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, processing_status="completed",
            model_name=MODEL_NAME, model_version=model_version,
            model_source_url=MODEL_SOURCE_URL, model_license=MODEL_LICENSE,
            evidence=({
                "type": "eight_frame_temporal_model",
                "frame_count": FRAME_COUNT,
                "spoof_head_raw_score": spoof_logit,
                "temporal_consistency_raw_score": temporal_logit,
                "decision_rule": "model-card sigmoid(deepfake head) threshold 0.6",
            },),
            timestamps=tuple(timestamps), limitations=MODEL_LIMITATIONS,
        )
    except LookupError:
        return failed_result(
            modality="video", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="FACE_NOT_DETECTED",
            message="A face could not be detected in every required sampled frame.",
            limitations=MODEL_LIMITATIONS,
        )
    except ImportError:
        return _unavailable(
            model_version,
            "ONNX Runtime/OpenCV CPU dependencies are unavailable; no inference was performed.",
        )
    except Exception:
        logger.exception("Video deepfake model loading or inference failed")
        return failed_result(
            modality="video", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="MODEL_INFERENCE_FAILED",
            message="The video detector could not complete inference.",
            limitations=MODEL_LIMITATIONS,
        )


def _select_frames(frames: tuple[Any, ...] | list[Any]) -> tuple[Any, ...]:
    ordered = sorted(frames, key=lambda frame: int(frame.actual_timestamp_ms))
    if len(ordered) == FRAME_COUNT:
        return tuple(ordered)
    indexes = [round(index * (len(ordered) - 1) / (FRAME_COUNT - 1))
               for index in range(FRAME_COUNT)]
    selected = tuple(ordered[index] for index in indexes)
    if len({int(frame.actual_timestamp_ms) for frame in selected}) != FRAME_COUNT:
        raise ValueError("Eight distinct video timestamps are required.")
    return selected


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _version() -> str:
    import os

    return os.getenv("PS11_VIDEO_DETECTOR_MODEL_VERSION", "local-unpinned")


def _unavailable(model_version: str, reason: str = "Model weights are not configured locally."):
    return unavailable_result(
        modality="video", detector_name=DETECTOR_NAME,
        detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
        model_version=model_version, model_source_url=MODEL_SOURCE_URL,
        model_license=MODEL_LICENSE, reason=reason,
    )

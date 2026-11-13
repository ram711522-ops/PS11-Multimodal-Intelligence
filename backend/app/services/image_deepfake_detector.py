"""Lazy CPU inference for an AI-generated image detector with explicit scope."""

from functools import lru_cache
import logging
from pathlib import Path
from typing import Any
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.app.config import Settings
from backend.app.services.deepfake_detection import (
    DeepfakeDetectionResult,
    failed_result,
    unavailable_result,
)
from backend.app.services.media_processing import IMAGE_FORMATS


logger = logging.getLogger(__name__)
DETECTOR_NAME = "ps11.image.ai-generated-classifier"
DETECTOR_VERSION = "1.0.0"
MODEL_NAME = "Imager-1-Pro"
MODEL_SOURCE_URL = "https://huggingface.co/Imagers/imager-1-pro"
MODEL_LICENSE = "Apache-2.0"
MODEL_LIMITATIONS = (
    "This checkpoint targets AI-generated images from its documented generator scope.",
    "It is not a validated face-swap or general image-manipulation detector.",
    "The raw class logit is not a calibrated probability.",
)


@lru_cache(maxsize=2)
def _load_model(model_path: str, revision: str):
    """Load only local configured weights; never download during inference."""

    del revision  # The local directory is the deployment's pinned model snapshot.
    import torch
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    processor = AutoImageProcessor.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    model = AutoModelForImageClassification.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    model.to("cpu")
    model.eval()
    return torch, processor, model


def detect_image(path: Path | str) -> DeepfakeDetectionResult:
    settings = Settings.from_environment()
    model_path = settings.image_detector_model_path
    model_version = _configured_version("PS11_IMAGE_DETECTOR_MODEL_VERSION")
    if not Path(path).is_file():
        return failed_result(
            modality="image", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="INVALID_IMAGE_INPUT",
            message="The image input could not be read.", limitations=MODEL_LIMITATIONS,
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format not in IMAGE_FORMATS:
                    return failed_result(
                        modality="image", detector_name=DETECTOR_NAME,
                        detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
                        model_version=model_version, model_source_url=MODEL_SOURCE_URL,
                        model_license=MODEL_LICENSE, code="UNSUPPORTED_IMAGE_FORMAT",
                        message="The image detector accepts JPEG, PNG, or WebP content.",
                        limitations=MODEL_LIMITATIONS,
                    )
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, EOFError):
        return failed_result(
            modality="image", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="INVALID_IMAGE_INPUT",
            message="The image could not be safely decoded for detection.",
            limitations=MODEL_LIMITATIONS,
        )

    if model_path is None or not model_path.is_dir():
        return unavailable_result(
            modality="image", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE,
        )
    try:
        torch, processor, model = _load_model(str(model_path), model_version)
        inputs = processor(images=image, return_tensors="pt")
        inputs = {key: value.to("cpu") for key, value in inputs.items()}
        with torch.inference_mode():
            logits = model(**inputs).logits.detach().cpu().reshape(-1)
        if logits.numel() != 2:
            raise ValueError("The configured image checkpoint must expose two classes.")
        scores = [float(logits[index].item()) for index in range(2)]
        label_map = getattr(model.config, "id2label", {}) or {}
        normalized_labels = {
            str(label_map.get(index, label_map.get(str(index), ""))).strip().lower()
            for index in range(2)
        }
        if normalized_labels and normalized_labels.issubset({"label_0", "label_1", ""}):
            fake_index = 1  # This selected checkpoint documents 0=Real, 1=AI.
        else:
            fake_index = next(
                (index for index in range(2)
                 if any(term in str(label_map.get(index, label_map.get(str(index), ""))).lower()
                        for term in ("ai", "fake", "synthetic", "generated"))),
                None,
            )
            if fake_index is None:
                raise ValueError("The checkpoint labels do not identify the AI-generated class.")
        prediction_index = max(range(2), key=lambda index: scores[index])
        verdict = "likely_manipulated" if prediction_index == fake_index else "likely_authentic"
        label = str(label_map.get(prediction_index, label_map.get(str(prediction_index), prediction_index)))
        return DeepfakeDetectionResult(
            modality="image", verdict=verdict, raw_model_score=scores[fake_index],
            calibrated_probability=None, detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, processing_status="completed",
            model_name=MODEL_NAME, model_version=model_version,
            model_source_url=MODEL_SOURCE_URL, model_license=MODEL_LICENSE,
            evidence=({"type": "image_classification", "predicted_label": label,
                       "class_logits": scores, "scope": "AI-generated image classification"},),
            limitations=MODEL_LIMITATIONS,
        )
    except ImportError:
        return unavailable_result(
            modality="image", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE,
            reason="PyTorch/Transformers runtime is unavailable; no inference was performed.",
        )
    except Exception:
        logger.exception("Image authenticity model loading or inference failed")
        return failed_result(
            modality="image", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="MODEL_INFERENCE_FAILED",
            message="The image detector could not complete inference.",
            limitations=MODEL_LIMITATIONS,
        )


def _configured_version(environment_name: str) -> str:
    import os

    return os.getenv(environment_name, "local-unpinned")

"""CPU-capable, lazy Wav2Vec2 detector for synthetic English speech."""

from functools import lru_cache
from hashlib import sha256
import hmac
import logging
import math
from pathlib import Path
import tempfile
from typing import Any

from backend.app.config import Settings
from backend.app.services.deepfake_detection import (
    DetectionError,
    DeepfakeDetectionResult,
    failed_result,
    unavailable_result,
)


logger = logging.getLogger(__name__)
DETECTOR_NAME = "ps11.audio.wav2vec2-synthetic-speech"
DETECTOR_VERSION = "1.0.0"
MODEL_NAME = "wav2vec2-deepfake-voice-detector"
MODEL_SOURCE_URL = "https://huggingface.co/garystafford/wav2vec2-deepfake-voice-detector"
MODEL_LICENSE = "Apache-2.0"
SAMPLE_RATE = 16_000
SEGMENT_SECONDS = 4.0
MAX_SEGMENTS = 16
MODEL_LIMITATIONS = (
    "Validated scope is English synthetic speech; it is not a general audio authenticity detector.",
    "Training data is a small set of speech clips and named TTS systems; domain shift is expected.",
    "The reported score is a raw logit margin, not a calibrated probability.",
)


@lru_cache(maxsize=2)
def _load_model(model_path: str, revision: str):
    del revision
    import torch
    from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

    extractor = AutoFeatureExtractor.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    model = AutoModelForAudioClassification.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    model.to("cpu")
    model.eval()
    return torch, extractor, model


def detect_audio_file(
    path: Path | str,
    expected_sha256: str | None = None,
) -> DeepfakeDetectionResult:
    settings = Settings.from_environment()
    model_version = _version()
    input_path = Path(path)
    try:
        import soundfile as sf

        if not input_path.is_file():
            raise ValueError("Audio file was not found.")
        size_bytes = input_path.stat().st_size
        if size_bytes <= 0 or size_bytes > settings.max_audio_size_bytes:
            raise ValueError("Audio file is empty or exceeds the configured size limit.")
        if expected_sha256 is not None:
            digest = sha256()
            with input_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
                return failed_result(
                    modality="audio", detector_name=DETECTOR_NAME,
                    detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
                    model_version=model_version, model_source_url=MODEL_SOURCE_URL,
                    model_license=MODEL_LICENSE, code="SOURCE_CHECKSUM_MISMATCH",
                    message="The audio checksum does not match its stored media record.",
                    limitations=MODEL_LIMITATIONS,
                )
        info = sf.info(str(input_path))
        duration = float(info.frames) / float(info.samplerate)
        if (
            info.frames <= 0 or info.samplerate <= 0 or info.channels <= 0
            or info.channels > 8 or not math.isfinite(duration)
            or duration > settings.max_audio_duration_seconds
        ):
            raise ValueError("Audio metadata is outside the supported limits.")
        if duration < 2.5:
            raise ValueError("Audio is shorter than the model's documented training clips.")
    except ImportError:
        return _unavailable("Audio decoding dependency is unavailable; no inference was performed.")
    except (OSError, RuntimeError, ValueError, OverflowError):
        return failed_result(
            modality="audio", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="INVALID_AUDIO",
            message="The audio input is invalid, unsupported, or exceeds safe limits.",
            limitations=MODEL_LIMITATIONS,
        )

    model_path = settings.audio_detector_model_path
    if model_path is None or not model_path.is_dir():
        return _unavailable()

    try:
        import librosa
        import numpy as np
        import soundfile as sf

        torch, extractor, model = _load_model(str(model_path), model_version)
        segment_frames = int(round(SEGMENT_SECONDS * info.samplerate))
        segment_count = max(1, math.ceil(info.frames / segment_frames))
        if segment_count > MAX_SEGMENTS:
            segment_count = MAX_SEGMENTS
        if info.frames <= segment_frames:
            starts = [0]
        elif segment_count == 1:
            starts = [max(0, info.frames - segment_frames)]
        else:
            last_start = max(0, info.frames - segment_frames)
            starts = sorted({
                round(index * last_start / (segment_count - 1))
                for index in range(segment_count)
            })

        segment_evidence: list[dict[str, Any]] = []
        margins: list[float] = []
        predictions: list[str] = []
        with sf.SoundFile(str(input_path), mode="r") as audio_file:
            for start_frame in starts:
                audio_file.seek(start_frame)
                samples = audio_file.read(
                    frames=min(segment_frames, info.frames - start_frame),
                    dtype="float32", always_2d=True,
                )
                mono = samples.mean(axis=1, dtype=np.float32)
                if info.samplerate != SAMPLE_RATE:
                    mono = librosa.resample(
                        mono, orig_sr=info.samplerate, target_sr=SAMPLE_RATE
                    )
                if mono.size < int(2.5 * SAMPLE_RATE):
                    continue
                inputs = extractor(
                    mono, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True
                )
                inputs = {key: value.to("cpu") for key, value in inputs.items()}
                with torch.inference_mode():
                    logits = model(**inputs).logits.detach().cpu().reshape(-1)
                if logits.numel() != 2:
                    raise ValueError("The configured audio checkpoint must expose real/fake logits.")
                real_score = float(logits[0].item())
                fake_score = float(logits[1].item())
                margin = fake_score - real_score
                label = "synthetic_speech" if margin > 0 else "human_speech"
                margins.append(margin)
                predictions.append(label)
                segment_evidence.append({
                    "type": "speech_segment_classification",
                    "start_ms": round(start_frame * 1000 / info.samplerate),
                    "end_ms": round((start_frame + min(segment_frames, info.frames - start_frame))
                                     * 1000 / info.samplerate),
                    "predicted_label": label,
                    "raw_fake_logit_margin": margin,
                })
        if not margins:
            raise ValueError("No audio segment was long enough for the selected detector.")
        verdict = (
            "likely_manipulated" if all(label == "synthetic_speech" for label in predictions)
            else "likely_authentic" if all(label == "human_speech" for label in predictions)
            else "inconclusive"
        )
        return DeepfakeDetectionResult(
            modality="audio", verdict=verdict,
            raw_model_score=sum(margins) / len(margins), calibrated_probability=None,
            detector_name=DETECTOR_NAME, detector_version=DETECTOR_VERSION,
            processing_status="completed", model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, evidence=tuple(segment_evidence),
            timestamps=tuple({"start_ms": item["start_ms"], "end_ms": item["end_ms"]}
                             for item in segment_evidence),
            limitations=MODEL_LIMITATIONS,
        )
    except ImportError:
        return _unavailable("PyTorch/Transformers/librosa runtime is unavailable; no inference was performed.")
    except Exception:
        logger.exception("Audio authenticity model loading or inference failed")
        return failed_result(
            modality="audio", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=model_version, model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="MODEL_INFERENCE_FAILED",
            message="The audio detector could not complete inference.",
            limitations=MODEL_LIMITATIONS,
        )


def detect_video_audio(video_path: Path | str) -> DeepfakeDetectionResult:
    """Safely extract a video's audio stream to temporary WAV storage, if present."""

    settings = Settings.from_environment()
    try:
        import av
        import numpy as np
        import soundfile as sf
    except ImportError:
        return _unavailable("PyAV or SoundFile is unavailable; the video audio stream was not decoded.")

    container = None
    try:
        decoder_timeout = settings.video_processing.timeout_seconds
        container = av.open(
            str(video_path),
            mode="r",
            timeout=(decoder_timeout, decoder_timeout),
        )
        audio_streams = [stream for stream in container.streams if stream.type == "audio"]
        if not audio_streams:
            return DeepfakeDetectionResult(
                modality="audio", verdict="inconclusive", raw_model_score=None,
                calibrated_probability=None, detector_name=DETECTOR_NAME,
                detector_version=DETECTOR_VERSION, processing_status="skipped",
                model_name=MODEL_NAME, model_version=_version(),
                model_source_url=MODEL_SOURCE_URL, model_license=MODEL_LICENSE,
                limitations=("The video has no audio stream.",),
                error=DetectionError("NO_AUDIO_STREAM", "No audio stream was present."),
            )
        with tempfile.TemporaryDirectory(prefix="ps11-audio-extract-") as directory:
            output_path = Path(directory) / "extracted-audio.wav"
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            sample_count = 0
            with sf.SoundFile(
                str(output_path), mode="w", samplerate=SAMPLE_RATE, channels=1,
                format="WAV", subtype="PCM_16",
            ) as output:
                for frame in container.decode(audio=audio_streams[0].index):
                    for resampled in resampler.resample(frame):
                        samples = resampled.to_ndarray().reshape(-1)
                        sample_count += int(samples.size)
                        if sample_count > settings.max_audio_duration_seconds * SAMPLE_RATE:
                            raise ValueError("Audio duration limit exceeded.")
                        if sample_count * 2 > settings.max_audio_size_bytes:
                            raise ValueError("Extracted audio size limit exceeded.")
                        output.write(np.asarray(samples, dtype=np.int16))
            return detect_audio_file(output_path)
    except Exception:
        logger.exception("Unable to extract or analyze the video audio stream")
        return failed_result(
            modality="audio", detector_name=DETECTOR_NAME,
            detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
            model_version=_version(), model_source_url=MODEL_SOURCE_URL,
            model_license=MODEL_LICENSE, code="AUDIO_EXTRACTION_FAILED",
            message="The video audio stream could not be safely extracted.",
            limitations=MODEL_LIMITATIONS,
        )
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:
                logger.warning("Video audio decoder did not close cleanly")


def _version() -> str:
    import os

    return os.getenv("PS11_AUDIO_DETECTOR_MODEL_VERSION", "local-unpinned")


def _unavailable(reason: str = "Model weights are not configured locally.") -> DeepfakeDetectionResult:
    return unavailable_result(
        modality="audio", detector_name=DETECTOR_NAME,
        detector_version=DETECTOR_VERSION, model_name=MODEL_NAME,
        model_version=_version(), model_source_url=MODEL_SOURCE_URL,
        model_license=MODEL_LICENSE, reason=reason,
    )

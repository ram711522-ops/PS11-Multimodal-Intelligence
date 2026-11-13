"""Mock-boundary tests for Phase 6 detector contracts and safe unavailable states."""

from contextlib import nullcontext
from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

import numpy as np
from PIL import Image
import soundfile as sf

from backend.app.database import database_connection, initialize_database
from backend.app.services.media_pipeline import process_existing_uploaded_media
from backend.app.services.video_processor import ExtractedFrame
from backend.app.services import audio_deepfake_detector as audio_detector
from backend.app.services import image_deepfake_detector as image_detector
from backend.app.services import video_deepfake_detector as video_detector


class _Tensor:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=np.float32)

    def to(self, _device):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def reshape(self, *_shape):
        return _Tensor(self.values.reshape(*_shape))

    def numel(self):
        return self.values.size

    def item(self):
        return float(self.values.item())

    def __getitem__(self, index):
        return _Tensor(self.values[index])


class _ImageModel:
    config = types.SimpleNamespace(id2label={0: "Real", 1: "AI"})

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self

    def __call__(self, **_inputs):
        return types.SimpleNamespace(logits=_Tensor([[-2.0, 3.0]]))


class _AudioModel:
    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self

    def __call__(self, **_inputs):
        return types.SimpleNamespace(logits=_Tensor([[-1.0, 2.0]]))


class DeepfakeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp_directory.name)

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_image_reports_model_unavailable_without_weights_and_invalid_input_safely(self):
        image_path = self.root / "image.png"
        Image.new("RGB", (24, 24), "white").save(image_path)
        with patch.dict(os.environ, {"PS11_IMAGE_DETECTOR_MODEL_PATH": ""}, clear=False):
            result = image_detector.detect_image(image_path)
        self.assertEqual(result.processing_status, "model_unavailable")
        self.assertEqual(result.verdict, "inconclusive")
        self.assertIsNone(result.raw_model_score)
        self.assertIsNone(result.calibrated_probability)

        invalid = self.root / "bad.png"
        invalid.write_bytes(b"not image data")
        self.assertEqual(image_detector.detect_image(invalid).error.code, "INVALID_IMAGE_INPUT")

    def test_image_cpu_inference_uses_raw_logit_and_standard_result(self):
        image_path = self.root / "image.png"
        Image.new("RGB", (24, 24), "white").save(image_path)
        processor = types.SimpleNamespace(
            __call__=lambda **_kwargs: {"pixel_values": _Tensor([0.0])}
        )

        class Processor:
            def __call__(self, **_kwargs):
                return {"pixel_values": _Tensor([0.0])}

        model = _ImageModel()
        with patch.dict(os.environ, {"PS11_IMAGE_DETECTOR_MODEL_PATH": str(self.root)}, clear=False), \
             patch.object(image_detector, "_load_model", return_value=(
                 types.SimpleNamespace(inference_mode=nullcontext), Processor(), model
             )):
            result = image_detector.detect_image(image_path)
        self.assertEqual(result.verdict, "likely_manipulated")
        self.assertEqual(result.raw_model_score, 3.0)
        self.assertIsNone(result.calibrated_probability)
        self.assertEqual(result.processing_status, "completed")
        self.assertIsNotNone(model)

    def test_unsupported_image_and_model_load_failure_are_not_guessed(self):
        gif_path = self.root / "unsupported.gif"
        Image.new("RGB", (12, 12), "blue").save(gif_path, format="GIF")
        unsupported = image_detector.detect_image(gif_path)
        self.assertEqual(unsupported.error.code, "UNSUPPORTED_IMAGE_FORMAT")
        self.assertIsNone(unsupported.raw_model_score)

        image_path = self.root / "image.png"
        Image.new("RGB", (24, 24), "white").save(image_path)
        with patch.dict(os.environ, {"PS11_IMAGE_DETECTOR_MODEL_PATH": str(self.root)}, clear=False), \
             patch.object(image_detector, "_load_model", side_effect=RuntimeError("private details")):
            result = image_detector.detect_image(image_path)
        self.assertEqual(result.error.code, "MODEL_INFERENCE_FAILED")
        self.assertNotIn(str(self.root), result.error.message)

    def test_video_requires_weights_and_never_scores_a_single_frame(self):
        with patch.dict(os.environ, {"PS11_VIDEO_DETECTOR_MODEL_PATH": ""}, clear=False):
            unavailable = video_detector.detect_video_frames((), self.root)
        self.assertEqual(unavailable.processing_status, "model_unavailable")
        self.assertIsNone(unavailable.raw_model_score)

        model_path = self.root / "video.onnx"
        model_path.write_bytes(b"mock model file")
        with patch.dict(os.environ, {"PS11_VIDEO_DETECTOR_MODEL_PATH": str(model_path)}, clear=False):
            insufficient = video_detector.detect_video_frames((), self.root)
        self.assertEqual(insufficient.processing_status, "failed")
        self.assertEqual(insufficient.error.code, "INSUFFICIENT_VIDEO_FRAMES")
        self.assertIsNone(insufficient.raw_model_score)

    def test_video_cpu_inference_uses_eight_frames_and_temporal_head(self):
        model_path = self.root / "video.onnx"
        model_path.write_bytes(b"mock model file")
        frame_directory = self.root / "processed" / "frames"
        frame_directory.mkdir(parents=True)
        frames = []
        for index in range(8):
            name = f"{index}.jpg"
            Image.new("RGB", (48, 48), (index * 10, 20, 30)).save(frame_directory / name)
            frames.append(ExtractedFrame(
                requested_timestamp_ms=index * 1000,
                actual_timestamp_ms=index * 1000,
                frame_index=index,
                width=48,
                height=48,
                sha256="a" * 64,
                artifact_reference=name,
            ))

        class _FaceDetector:
            def empty(self):
                return False

            def detectMultiScale(self, _gray, **_kwargs):
                return [(0, 0, 48, 48)]

        fake_cv2 = types.SimpleNamespace(
            data=types.SimpleNamespace(haarcascades=str(self.root)),
            CascadeClassifier=lambda _path: _FaceDetector(),
            COLOR_RGB2GRAY=1,
            cvtColor=lambda pixels, _code: pixels[:, :, 0],
        )

        class _Input:
            name = "frames"

        class _Session:
            def get_inputs(self):
                return [_Input()]

            def run(self, _outputs, feeds):
                self.received_shape = feeds["frames"].shape
                return [np.asarray([[1.2, -0.2, 0.8]], dtype=np.float32)]

        session = _Session()
        with patch.dict(os.environ, {"PS11_VIDEO_DETECTOR_MODEL_PATH": str(model_path)}, clear=False), \
             patch.dict(sys.modules, {"cv2": fake_cv2}), \
             patch.object(video_detector, "_load_session", return_value=session):
            result = video_detector.detect_video_frames(frames, self.root)
        self.assertEqual(session.received_shape, (1, 8, 3, 224, 224))
        self.assertEqual(result.processing_status, "completed")
        self.assertEqual(result.verdict, "likely_manipulated")
        self.assertAlmostEqual(result.raw_model_score, 1.2, places=6)
        self.assertIsNone(result.calibrated_probability)
        self.assertEqual(len(result.timestamps), 8)
        self.assertAlmostEqual(
            result.evidence[0]["temporal_consistency_raw_score"], 0.8, places=6
        )

    def test_audio_invalid_input_and_model_unavailable(self):
        invalid = self.root / "invalid.wav"
        invalid.write_bytes(b"broken audio")
        bad = audio_detector.detect_audio_file(invalid)
        self.assertEqual(bad.processing_status, "failed")
        self.assertEqual(bad.error.code, "INVALID_AUDIO")

        valid = self.root / "speech.wav"
        sf.write(valid, np.zeros(16_000 * 3, dtype=np.float32), 16_000)
        with patch.dict(os.environ, {"PS11_AUDIO_DETECTOR_MODEL_PATH": ""}, clear=False):
            unavailable = audio_detector.detect_audio_file(valid)
        self.assertEqual(unavailable.processing_status, "model_unavailable")
        self.assertIsNone(unavailable.raw_model_score)

    def test_video_without_audio_stream_is_skipped_safely(self):
        class Container:
            streams = []

            def close(self):
                self.closed = True

        captured = {}
        container = Container()

        def open_video(path, *, mode, timeout):
            captured.update(path=path, mode=mode, timeout=timeout)
            return container

        fake_av = types.SimpleNamespace(open=open_video)
        with patch.dict(sys.modules, {"av": fake_av}):
            result = audio_detector.detect_video_audio(self.root / "video.mp4")

        self.assertEqual(result.processing_status, "skipped")
        self.assertEqual(result.error.code, "NO_AUDIO_STREAM")
        self.assertEqual(captured["mode"], "r")
        self.assertGreater(captured["timeout"][0], 0)
        self.assertTrue(getattr(container, "closed", False))

    def test_audio_cpu_inference_has_segment_timestamps_and_raw_score(self):
        audio_path = self.root / "speech.wav"
        sf.write(audio_path, np.zeros(16_000 * 5, dtype=np.float32), 16_000)

        class Extractor:
            def __call__(self, *_args, **_kwargs):
                return {"input_values": _Tensor([0.0])}

        with patch.dict(os.environ, {"PS11_AUDIO_DETECTOR_MODEL_PATH": str(self.root)}, clear=False), \
             patch.object(audio_detector, "_load_model", return_value=(
                 types.SimpleNamespace(inference_mode=nullcontext), Extractor(), _AudioModel()
             )):
            result = audio_detector.detect_audio_file(audio_path)
        self.assertEqual(result.processing_status, "completed")
        self.assertEqual(result.verdict, "likely_manipulated")
        self.assertEqual(result.raw_model_score, 3.0)
        self.assertIsNone(result.calibrated_probability)
        self.assertTrue(result.timestamps)

    def test_audio_model_load_failure_is_safe_and_synthetic_or_genuine_labeling_is_model_driven(self):
        audio_path = self.root / "speech.wav"
        sf.write(audio_path, np.zeros(16_000 * 3, dtype=np.float32), 16_000)
        with patch.dict(os.environ, {"PS11_AUDIO_DETECTOR_MODEL_PATH": str(self.root)}, clear=False), \
             patch.object(audio_detector, "_load_model", side_effect=RuntimeError("private weights detail")):
            failed = audio_detector.detect_audio_file(audio_path)
        self.assertEqual(failed.error.code, "MODEL_INFERENCE_FAILED")
        self.assertNotIn("private weights detail", failed.error.message)

        class _GenuineAudioModel(_AudioModel):
            def __call__(self, **_inputs):
                return types.SimpleNamespace(logits=_Tensor([[2.0, -1.0]]))

        class Extractor:
            def __call__(self, *_args, **_kwargs):
                return {"input_values": _Tensor([0.0])}

        with patch.dict(os.environ, {"PS11_AUDIO_DETECTOR_MODEL_PATH": str(self.root)}, clear=False), \
             patch.object(audio_detector, "_load_model", return_value=(
                 types.SimpleNamespace(inference_mode=nullcontext), Extractor(), _GenuineAudioModel()
             )):
            result = audio_detector.detect_audio_file(audio_path)
        self.assertEqual(result.verdict, "likely_authentic")
        self.assertEqual(result.raw_model_score, -3.0)

    def test_pipeline_persists_detection_results_without_changing_api_or_schema(self):
        database_path = self.root / "phase6.sqlite3"
        storage_root = self.root / "storage"
        storage_root.mkdir()
        initialize_database(database_path)
        investigation_id = str(uuid.uuid4())
        media_id = str(uuid.uuid4())
        source = storage_root / f"{media_id}.png"
        Image.new("RGB", (32, 32), "red").save(source)
        source_bytes = source.read_bytes()
        with database_connection(database_path) as connection:
            connection.execute(
                "INSERT INTO investigations (id,created_at,updated_at,status,input_type,claim_text,language,source_reference) "
                "VALUES (?,?,?,'received','image',NULL,NULL,NULL)",
                (investigation_id, "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO media_assets (id,investigation_id,media_type,file_reference,checksum,mime_type,size_bytes,metadata_json,processing_status,created_at) "
                "VALUES (?,?, 'image', ?, ?, 'image/png', ?, NULL, 'received', ?)",
                (media_id, investigation_id, source.name, sha256(source_bytes).hexdigest(),
                 len(source_bytes), "2026-11-13T00:00:01Z"),
            )
            connection.commit()
        with patch.dict(os.environ, {"PS11_IMAGE_DETECTOR_MODEL_PATH": ""}, clear=False):
            outcome = process_existing_uploaded_media(
                database_path, storage_root, investigation_id, media_id
            )
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.deepfake_detections[0]["processing_status"], "model_unavailable")
        with database_connection(database_path) as connection:
            result = connection.execute(
                "SELECT result_json FROM media_processing_results WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
            media_columns = [row["name"] for row in connection.execute("PRAGMA table_info(media_assets)")]
        import json
        persisted = json.loads(result["result_json"])
        self.assertEqual(persisted["deepfake_detections"][0]["verdict"], "inconclusive")
        self.assertIn("perceptual_hash", persisted)
        self.assertNotIn("detector_result", persisted)
        self.assertEqual(media_columns, [
            "id", "investigation_id", "media_type", "file_reference", "checksum",
            "mime_type", "size_bytes", "metadata_json", "processing_status", "created_at",
        ])


if __name__ == "__main__":
    unittest.main()

"""Video processor tests using a small controlled PyAV-compatible decoder fixture."""

from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch

import numpy as np
from PIL import Image

from backend.app.config import VideoProcessingSettings
from backend.app.database import database_connection, initialize_database
from backend.app.services.media_pipeline import process_existing_uploaded_video
from backend.app.services.video_processor import (
    MediaProcessingException,
    MediaProcessingRequest,
    VideoProcessor,
    _requested_timestamps,
)


class _FakeFrame:
    def __init__(self, pts: int, *, fail: bool = False) -> None:
        self.pts = pts
        self.time_base = Fraction(1, 1)
        self.fail = fail
        self.width = 20
        self.height = 12

    def to_ndarray(self, *, format: str) -> np.ndarray:
        if self.fail:
            raise RuntimeError("decoder fixture failure")
        assert format == "rgb24"
        return np.full((12, 20, 3), (20 + self.pts, 80, 150), dtype=np.uint8)


class _FakeStream:
    type = "video"
    index = 0
    duration = 3
    time_base = Fraction(1, 1)
    average_rate = Fraction(1, 1)
    base_rate = None
    guessed_rate = None

    def __init__(self, *, width: int = 20, height: int = 12, fail_at: int | None = None):
        self.codec_context = types.SimpleNamespace(width=width, height=height, name="h264")
        self.fail_at = fail_at

    def decode(self):
        for index in range(3):
            yield _FakeFrame(index, fail=index == self.fail_at)


class _FakeContainer:
    duration = 3_000_000
    format = types.SimpleNamespace(name="mov,mp4,m4a,3gp,3g2,mj2")

    def __init__(self, stream: _FakeStream | None = None):
        self.streams = [stream or _FakeStream()]
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def decode(self, *, video: int):
        assert video == 0
        yield from self.streams[0].decode()


def _fake_av(*, container: _FakeContainer | None = None, open_error: Exception | None = None):
    def open_video(path: str, *, mode: str, timeout: tuple[float, float]):
        assert mode == "r"
        assert timeout[0] > 0 and timeout[1] > 0
        if open_error:
            raise open_error
        return container or _FakeContainer()

    return types.SimpleNamespace(open=open_video, time_base=1_000_000)


class VideoProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "video.sqlite3"
        self.storage_path = self.root / "media"
        self.storage_path.mkdir()
        self.investigation_id = str(uuid.uuid4())
        initialize_database(self.database_path)
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO investigations (
                    id, created_at, updated_at, status, input_type,
                    claim_text, language, source_reference
                ) VALUES (?, ?, ?, 'received', 'image', NULL, NULL, NULL)
                """,
                (self.investigation_id, "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z"),
            )
            connection.commit()
        self.settings = VideoProcessingSettings(
            max_size_bytes=1024,
            max_duration_seconds=5,
            max_width=100,
            max_height=100,
            max_frames=3,
            sample_fps=1,
            timeout_seconds=10,
            max_concurrent_decoders=1,
        )

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_video_metadata_frames_checksums_and_processing_persistence(self) -> None:
        media_id = self._insert_video()
        container = _FakeContainer()
        with patch.dict(sys.modules, {"av": _fake_av(container=container)}):
            outcome = process_existing_uploaded_video(
                self.database_path, self.storage_path, media_id
            )

        self.assertEqual(outcome.status, "completed")
        result = outcome.result
        self.assertEqual(result.media_id, media_id)
        self.assertEqual(result.processing_run_id, outcome.processing_run_id)
        self.assertEqual(result.container_format, "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertEqual(result.video_codec, "h264")
        self.assertEqual((result.width, result.height, result.duration_ms, result.fps), (20, 12, 3000, 1))
        self.assertEqual(result.requested_timestamps_ms, (0, 1000, 2000))
        self.assertEqual([frame.actual_timestamp_ms for frame in result.frames], [0, 1000, 2000])
        self.assertEqual([frame.frame_index for frame in result.frames], [0, 1, 2])
        self.assertTrue(container.closed)

        source = self.storage_path / "sample-video.bin"
        self.assertEqual(source.read_bytes(), b"fixture video bytes")
        for frame in result.frames:
            self.assertEqual(len(frame.sha256), 64)
            self.assertEqual(str(uuid.UUID(Path(frame.artifact_reference).stem)), Path(frame.artifact_reference).stem)
            artifact = self.storage_path / "processed" / "frames" / frame.artifact_reference
            self.assertEqual(sha256(artifact.read_bytes()).hexdigest(), frame.sha256)
            with Image.open(artifact) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, (20, 12))
        with database_connection(self.database_path) as connection:
            run = connection.execute(
                "SELECT status, processor_name, processor_version FROM media_processing_runs WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
            persisted = connection.execute(
                "SELECT result_json, result_schema_version FROM media_processing_results WHERE processing_run_id = ?",
                (outcome.processing_run_id,),
            ).fetchone()
        self.assertEqual(tuple(run), ("completed", "ps11.video.frame_extractor", "1.0.0"))
        self.assertEqual(persisted["result_schema_version"], "1.0")
        self.assertEqual(json.loads(persisted["result_json"])["media_id"], media_id)
        self.assertNotIn(str(self.root), persisted["result_json"])

    def test_explicit_timestamps_are_validated_and_sampling_is_capped(self) -> None:
        processor = VideoProcessor(self.settings)
        self.assertEqual(_requested_timestamps(10, 1, 3, None), (0, 4000, 9000))
        with self.assertRaises(MediaProcessingException) as raised:
            _requested_timestamps(3, 1, 3, (1000, 1000))
        self.assertEqual(raised.exception.failure.code, "INVALID_FRAME_SAMPLING_REQUEST")

    def test_size_duration_resolution_and_invalid_container_are_rejected(self) -> None:
        processor = VideoProcessor(self.settings)
        media_id = str(uuid.uuid4())
        path = self.storage_path / "source.mp4"
        path.write_bytes(b"video")
        request = MediaProcessingRequest(
            media_id,
            path,
            sha256(b"video").hexdigest(),
            self.storage_path / "out",
            processing_run_id=str(uuid.uuid4()),
        )

        too_big = VideoProcessor(
            VideoProcessingSettings(**{**self.settings.__dict__, "max_size_bytes": 4})
        )
        with self.assertRaises(MediaProcessingException) as raised:
            too_big.process(request)
        self.assertEqual(raised.exception.failure.code, "VIDEO_SIZE_LIMIT_EXCEEDED")

        with patch.dict(sys.modules, {"av": _fake_av(container=_FakeContainer(_FakeStream(width=101)))}):
            with self.assertRaises(MediaProcessingException) as raised:
                processor.process(request)
        self.assertEqual(raised.exception.failure.code, "VIDEO_RESOLUTION_LIMIT_EXCEEDED")

        class LongStream(_FakeStream):
            duration = 6

        long_container = _FakeContainer(LongStream())
        with patch.dict(sys.modules, {"av": _fake_av(container=long_container)}):
            with self.assertRaises(MediaProcessingException) as raised:
                processor.process(request)
        self.assertEqual(raised.exception.failure.code, "VIDEO_DURATION_LIMIT_EXCEEDED")

        with patch.dict(sys.modules, {"av": _fake_av(open_error=RuntimeError("not a video"))}):
            with self.assertRaises(MediaProcessingException) as raised:
                processor.process(request)
        self.assertEqual(raised.exception.failure.code, "INVALID_VIDEO")

    def test_partial_frames_are_removed_after_decode_failure(self) -> None:
        processor = VideoProcessor(self.settings)
        path = self.storage_path / "source.mp4"
        path.write_bytes(b"video")
        request = MediaProcessingRequest(
            str(uuid.uuid4()),
            path,
            sha256(b"video").hexdigest(),
            self.storage_path / "processed" / "frames",
            processing_run_id=str(uuid.uuid4()),
        )
        container = _FakeContainer(_FakeStream(fail_at=1))
        with patch.dict(sys.modules, {"av": _fake_av(container=container)}):
            with self.assertRaises(MediaProcessingException) as raised:
                processor.process(request)
        self.assertEqual(raised.exception.failure.code, "VIDEO_DECODE_FAILED")
        self.assertEqual(list(request.artifact_storage_path.glob("*.jpg")), [])
        self.assertTrue(container.closed)

    def _insert_video(self) -> str:
        content = b"fixture video bytes"
        path = self.storage_path / "sample-video.bin"
        path.write_bytes(content)
        media_id = str(uuid.uuid4())
        with database_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO media_assets (
                    id, investigation_id, media_type, file_reference, checksum,
                    mime_type, size_bytes, metadata_json, processing_status, created_at
                ) VALUES (?, ?, 'video', ?, ?, 'video/mp4', ?, NULL, 'received', ?)
                """,
                (
                    media_id,
                    self.investigation_id,
                    path.name,
                    sha256(content).hexdigest(),
                    len(content),
                    "2026-11-13T00:00:01Z",
                ),
            )
            connection.commit()
        return media_id


if __name__ == "__main__":
    unittest.main()

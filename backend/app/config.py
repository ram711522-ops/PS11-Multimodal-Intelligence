"""Environment-backed application settings."""

from dataclasses import dataclass
import math
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "ps11.sqlite3"
DEFAULT_MEDIA_STORAGE_PATH = PROJECT_ROOT / "data" / "media"


@dataclass(frozen=True)
class VideoProcessingSettings:
    """Resource limits and sampling policy for explicit video processing."""

    max_size_bytes: int = 100 * 1024 * 1024
    max_duration_seconds: float = 120.0
    max_width: int = 3840
    max_height: int = 2160
    max_frames: int = 16
    sample_fps: float = 1.0
    timeout_seconds: float = 60.0
    max_concurrent_decoders: int = 1

    @classmethod
    def from_environment(cls) -> "VideoProcessingSettings":
        defaults = cls()
        settings = cls(
            max_size_bytes=_read_int("PS11_VIDEO_MAX_SIZE_BYTES", defaults.max_size_bytes),
            max_duration_seconds=_read_float(
                "PS11_VIDEO_MAX_DURATION_SECONDS", defaults.max_duration_seconds
            ),
            max_width=_read_int("PS11_VIDEO_MAX_WIDTH", defaults.max_width),
            max_height=_read_int("PS11_VIDEO_MAX_HEIGHT", defaults.max_height),
            max_frames=_read_int("PS11_VIDEO_MAX_FRAMES", defaults.max_frames),
            sample_fps=_read_float("PS11_VIDEO_SAMPLE_FPS", defaults.sample_fps),
            timeout_seconds=_read_float(
                "PS11_VIDEO_TIMEOUT_SECONDS", defaults.timeout_seconds
            ),
            max_concurrent_decoders=_read_int(
                "PS11_VIDEO_MAX_CONCURRENT_DECODERS", defaults.max_concurrent_decoders
            ),
        )
        if (
            settings.max_size_bytes <= 0
            or settings.max_duration_seconds <= 0
            or not math.isfinite(settings.max_duration_seconds)
            or settings.max_width <= 0
            or settings.max_height <= 0
            or settings.max_frames <= 0
            or settings.sample_fps <= 0
            or not math.isfinite(settings.sample_fps)
            or settings.timeout_seconds <= 0
            or not math.isfinite(settings.timeout_seconds)
            or settings.max_concurrent_decoders <= 0
        ):
            raise ValueError("Video processing settings must be positive finite values.")
        return settings


@dataclass(frozen=True)
class Settings:
    """Runtime settings required by the backend foundation."""

    database_path: Path = DEFAULT_DATABASE_PATH
    media_storage_path: Path = DEFAULT_MEDIA_STORAGE_PATH
    video_processing: VideoProcessingSettings = VideoProcessingSettings()

    @classmethod
    def from_environment(cls) -> "Settings":
        """Read supported settings from the process environment."""

        configured_path = os.getenv("PS11_DATABASE_PATH")
        configured_media_path = os.getenv("PS11_MEDIA_STORAGE_PATH")
        return cls(
            database_path=Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH,
            media_storage_path=(
                Path(configured_media_path)
                if configured_media_path
                else DEFAULT_MEDIA_STORAGE_PATH
            ),
            video_processing=VideoProcessingSettings.from_environment(),
        )


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _read_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)

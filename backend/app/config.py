"""Configuration for the provisional backend."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings, overridable for local deployments and tests."""

    database_path: Path

    @classmethod
    def from_environment(cls) -> "Settings":
        configured = os.environ.get("PS11_DATABASE_PATH")
        default_path = Path(__file__).resolve().parents[1] / "data" / "ps11.sqlite3"
        return cls(database_path=Path(configured) if configured else default_path)

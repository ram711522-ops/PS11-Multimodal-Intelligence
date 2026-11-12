"""Environment-backed application settings."""

from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "ps11.sqlite3"


@dataclass(frozen=True)
class Settings:
    """Runtime settings required by the backend foundation."""

    database_path: Path = DEFAULT_DATABASE_PATH

    @classmethod
    def from_environment(cls) -> "Settings":
        """Read supported settings from the process environment."""

        configured_path = os.getenv("PS11_DATABASE_PATH")
        return cls(database_path=Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH)

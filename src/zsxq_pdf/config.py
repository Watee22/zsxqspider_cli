from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "app.sqlite3"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def markdown_dir(self) -> Path:
        return self.data_dir / "markdown"


def load_config(_path: Path | None) -> AppConfig:
    # Placeholder for future TOML config support.
    return AppConfig()

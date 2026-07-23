"""Cross-platform Core configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(frozen=True, slots=True)
class CoreConfig:
    data_dir: Path
    database_path: Path
    lock_path: Path
    host: str = "127.0.0.1"
    port: int = 7337
    max_import_bytes: int = 512 * 1024 * 1024
    max_dashboard_export_bytes: int = 2 * 1024 * 1024 * 1024
    require_auth: bool = True

    @classmethod
    def default(cls) -> CoreConfig:
        configured_dir = os.environ.get("ATC_CORE_DATA_DIR")
        data_dir = (
            Path(configured_dir).expanduser().resolve()
            if configured_dir
            else Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
        )
        host = os.environ.get("ATC_CORE_HOST", "127.0.0.1")
        port = int(os.environ.get("ATC_CORE_PORT", "7337"))
        if not 1 <= port <= 65_535:
            raise ValueError("ATC_CORE_PORT must be between 1 and 65535")
        max_import_bytes = int(os.environ.get("ATC_MAX_IMPORT_BYTES", str(512 * 1024 * 1024)))
        if not 1 <= max_import_bytes <= 900_000_000:
            raise ValueError("ATC_MAX_IMPORT_BYTES must be between 1 and 900000000")
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "core.sqlite3",
            lock_path=data_dir / "core.lock",
            host=host,
            port=port,
            max_import_bytes=max_import_bytes,
        )

    @classmethod
    def in_directory(cls, data_dir: Path, *, require_auth: bool = False) -> CoreConfig:
        resolved = data_dir.expanduser().resolve()
        return cls(
            data_dir=resolved,
            database_path=resolved / "core.sqlite3",
            lock_path=resolved / "core.lock",
            require_auth=require_auth,
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

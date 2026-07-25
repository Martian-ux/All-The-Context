from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.config import DEFAULT_MAX_IMPORT_BYTES, MAX_IMPORT_BYTES, CoreConfig


def test_default_import_limit_is_two_gigabytes(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path)

    assert config.max_import_bytes == DEFAULT_MAX_IMPORT_BYTES == 2_000_000_000


def test_environment_import_limit_accepts_the_ceiling_and_rejects_larger_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATC_MAX_IMPORT_BYTES", str(MAX_IMPORT_BYTES))
    assert CoreConfig.default().max_import_bytes == 2_000_000_000

    monkeypatch.setenv("ATC_MAX_IMPORT_BYTES", str(MAX_IMPORT_BYTES + 1))
    with pytest.raises(ValueError, match="between 1 and 2000000000"):
        CoreConfig.default()

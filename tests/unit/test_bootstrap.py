from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP: dict[str, Any] = runpy.run_path(str(REPOSITORY_ROOT / "scripts" / "bootstrap.py"))


def test_bootstrap_rebuilds_cross_version_or_unhealthy_environment(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("version = 3.12.10\n", encoding="utf-8")

    needs_rebuild = BOOTSTRAP["needs_rebuild"]
    assert needs_rebuild(venv_dir, runtime_version=(3, 14), probe=lambda _path: True)
    assert needs_rebuild(venv_dir, runtime_version=(3, 12), probe=lambda _path: False)
    assert not needs_rebuild(venv_dir, runtime_version=(3, 12), probe=lambda _path: True)


def test_bootstrap_uses_platform_specific_executable_paths(tmp_path: Path) -> None:
    python_path = BOOTSTRAP["venv_python_path"](tmp_path)
    atc_path = BOOTSTRAP["venv_atc_path"](tmp_path)

    assert python_path.name in {"python", "python.exe"}
    assert atc_path.name in {"atc", "atc.exe"}

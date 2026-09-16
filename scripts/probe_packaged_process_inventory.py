"""Run the packaged Windows process-inventory probe with bounded evidence."""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "allthecontext" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

# ruff: noqa: E402
from smoke_packaged_first_run import snapshot_packaged_processes


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--diagnostics-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    if platform.system() != "Windows":
        return 1
    arguments = _parse_arguments()
    diagnostics_dir = arguments.diagnostics_dir.expanduser()
    if not diagnostics_dir.is_absolute():
        return 1
    diagnostics_dir = diagnostics_dir.resolve()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    if not arguments.executable.is_file():
        return 1
    try:
        snapshot_packaged_processes(
            arguments.executable,
            diagnostics_root=diagnostics_dir,
        )
    except (OSError, RuntimeError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

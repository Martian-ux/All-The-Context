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
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--diagnostics-dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="write one content-free observation to this native-check output path",
    )
    arguments = parser.parse_args()
    explicit_arguments = arguments.executable is not None or arguments.diagnostics_dir is not None
    if arguments.output is not None and explicit_arguments:
        parser.error("--output cannot be combined with --executable or --diagnostics-dir")
    if arguments.output is None and (
        arguments.executable is None or arguments.diagnostics_dir is None
    ):
        parser.error("provide --output or both --executable and --diagnostics-dir")
    return arguments


def _publish_native_output(output: Path, *, before: set[Path]) -> bool:
    observations = sorted(
        path
        for path in output.parent.glob("packaged-process-inventory-*.json")
        if path not in before
    )
    if len(observations) != 1:
        return False
    try:
        observations[0].replace(output)
    except OSError:
        return False
    return True


def _run_native_output_probe(output: Path) -> int:
    if not output.is_absolute():
        return 1
    output = output.resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        before = set(output.parent.glob("packaged-process-inventory-*.json"))
    except OSError:
        return 1
    try:
        snapshot_packaged_processes(
            Path(sys.executable),
            diagnostics_root=output.parent,
        )
    except (OSError, RuntimeError):
        _publish_native_output(output, before=before)
        return 1
    return 0 if _publish_native_output(output, before=before) else 1


def main() -> int:
    if platform.system() != "Windows":
        return 1
    arguments = _parse_arguments()
    if arguments.output is not None:
        return _run_native_output_probe(arguments.output)
    assert arguments.executable is not None
    assert arguments.diagnostics_dir is not None
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

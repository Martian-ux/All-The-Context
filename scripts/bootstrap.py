"""Create or repair the repository virtual environment and install the application."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import venv
from collections.abc import Callable, Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MINIMUM_PYTHON = (3, 12)


def venv_python_path(venv_dir: Path) -> Path:
    """Return the virtual-environment interpreter without shell activation."""
    return venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python"


def venv_atc_path(venv_dir: Path) -> Path:
    """Return the installed CLI path for the current platform."""
    return venv_dir / "Scripts" / "atc.exe" if os.name == "nt" else venv_dir / "bin" / "atc"


def read_venv_version(venv_dir: Path) -> tuple[int, int] | None:
    """Read the major/minor version recorded by Python's venv module."""
    configuration = venv_dir / "pyvenv.cfg"
    if not configuration.is_file():
        return None
    try:
        lines = configuration.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip().casefold() == "version":
            parts = value.strip().split(".")
            try:
                return int(parts[0]), int(parts[1])
            except (IndexError, ValueError):
                return None
    return None


def environment_is_healthy(python: Path) -> bool:
    """Probe compiled dependencies that reveal a stale cross-version venv."""
    if not python.is_file():
        return False
    probe = "import _cffi_backend, allthecontext.cli, cryptography, mcp, pydantic_core; print('ok')"
    try:
        completed = subprocess.run(
            [str(python), "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def needs_rebuild(
    venv_dir: Path,
    *,
    runtime_version: tuple[int, int] | None = None,
    probe: Callable[[Path], bool] = environment_is_healthy,
) -> bool:
    """Return true for absent, mismatched, or internally inconsistent environments."""
    expected = runtime_version or (sys.version_info.major, sys.version_info.minor)
    return read_venv_version(venv_dir) != expected or not probe(venv_python_path(venv_dir))


def _run(command: Sequence[str]) -> None:
    subprocess.run(list(command), cwd=REPOSITORY_ROOT, check=True)


def _create_environment(venv_dir: Path) -> None:
    print(
        f"Creating a clean Python {sys.version_info.major}.{sys.version_info.minor} environment...",
        flush=True,
    )
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)


def _install(venv_dir: Path, *, development: bool) -> None:
    requirement = ".[dev]" if development else "."
    print(f"Installing All The Context ({requirement})...", flush=True)
    _run(
        [
            str(venv_python_path(venv_dir)),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            "-e",
            requirement,
        ]
    )


def _display_command(executable: Path, argument: str) -> str:
    resolved = executable.resolve()
    try:
        relative = resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        if os.name == "nt":
            return f'& "{resolved}" {argument}'
        return f"{shlex.quote(str(resolved))} {argument}"
    if os.name == "nt":
        return f".\\{relative} {argument}"
    return f"./{relative.as_posix()} {argument}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="also install test and lint tools")
    parser.add_argument("--reset", action="store_true", help="rebuild even if the venv is healthy")
    parser.add_argument(
        "--venv",
        type=Path,
        default=REPOSITORY_ROOT / ".venv",
        help="virtual-environment directory (default: .venv)",
    )
    args = parser.parse_args()

    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        raise SystemExit(f"Python {required}+ is required; found {sys.version.split()[0]}")

    venv_dir = args.venv.expanduser().resolve()
    if Path(sys.prefix).resolve() == venv_dir:
        raise SystemExit(
            "Run bootstrap with the system Python, not the .venv Python it may need to repair."
        )

    rebuild = args.reset or needs_rebuild(venv_dir)
    if rebuild:
        _create_environment(venv_dir)
    else:
        print(f"Reusing healthy environment at {venv_dir}")

    _install(venv_dir, development=args.dev)
    if not environment_is_healthy(venv_python_path(venv_dir)):
        if rebuild:
            raise SystemExit("Installation completed, but the runtime health check failed")
        print("The existing environment failed after installation; rebuilding once...")
        _create_environment(venv_dir)
        _install(venv_dir, development=args.dev)
        if not environment_is_healthy(venv_python_path(venv_dir)):
            raise SystemExit("Installation completed, but the runtime health check failed")

    atc = venv_atc_path(venv_dir)
    print("\nAll The Context is ready.")
    print("Initialize once:")
    print(f"  {_display_command(atc, 'init')}")
    print("Start Core:")
    print(f"  {_display_command(atc, 'serve-core')}")
    print("Then open http://127.0.0.1:7337")


if __name__ == "__main__":
    main()

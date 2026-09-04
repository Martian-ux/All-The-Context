"""Build native Windows components twice from clean roots and compare bytes.

This gate never launches a produced executable.  It stages the second verified
build into the normal build/dist layout only after all four components match,
then writes a canonical metadata document and checksum with no workspace
paths or timestamps.
"""

from __future__ import annotations

import argparse
import importlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scripts import build_desktop, native_build_provenance
elif __package__:
    build_desktop = importlib.import_module("scripts.build_desktop")
    native_build_provenance = importlib.import_module("scripts.native_build_provenance")
else:  # Direct ``python scripts/...`` execution.
    build_desktop = importlib.import_module("build_desktop")
    native_build_provenance = importlib.import_module("native_build_provenance")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD_ROOT = ROOT / "build" / "desktop"
DEFAULT_DIST_ROOT = ROOT / "dist" / "desktop"
DEFAULT_OUTPUT_DIR = ROOT / "dist" / "native-build-provenance"
VERSION_PATTERN = native_build_provenance.VERSION_PATTERN
COMMIT_PATTERN = native_build_provenance.COMMIT_PATTERN


class ReproducibleBuildError(ValueError):
    """Raised when a pinned clean native build cannot be proven reproducible."""


def _fresh_directory(path: Path, *, label: str) -> Path:
    """Require a new or empty ordinary directory; never delete operator data."""

    path = path.expanduser().resolve()
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ReproducibleBuildError(f"{label} must be absent or empty")
    else:
        path.mkdir(parents=True)
    return path


def reproducible_environment(*, config_root: Path) -> dict[str, str]:
    """Return process settings that remove host-time and hash-order variance."""

    environment = dict(os.environ)
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYINSTALLER_CONFIG_DIR": str(config_root / "pyinstaller-config"),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "SOURCE_DATE_EPOCH": "0",
            "TZ": "UTC",
        }
    )
    return environment


def _run_clean_build(
    repository_root: Path,
    build_root: Path,
    dist_root: Path,
    *,
    version: str,
    source_commit: str,
) -> native_build_provenance.BuildSnapshot:
    _fresh_directory(build_root, label="clean build root")
    _fresh_directory(dist_root, label="clean dist root")
    command = [
        sys.executable,
        str(repository_root / "scripts" / "build_desktop.py"),
        "--system",
        "Windows",
        "--source-root",
        str(repository_root / "packages" / "allthecontext" / "src"),
        "--build-root",
        str(build_root),
        "--dist-root",
        str(dist_root),
        "--version",
        version,
        "--source-commit",
        source_commit,
        "--architecture",
        "x86_64",
    ]
    completed = subprocess.run(
        command,
        cwd=repository_root,
        env=reproducible_environment(config_root=build_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ReproducibleBuildError(
            f"clean native build failed with exit code {completed.returncode}"
        )
    return native_build_provenance.collect_snapshot(
        build_desktop.component_paths(build_root=build_root, dist_root=dist_root),
        label="clean-build",
    )


def _current_uv_version() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise ReproducibleBuildError("pinned uv is unavailable")
    completed = subprocess.run(
        [executable, "--version"], check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise ReproducibleBuildError("pinned uv version could not be inspected")
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", completed.stdout or completed.stderr)
    if match is None:
        raise ReproducibleBuildError("pinned uv version could not be inspected")
    return match.group(1)


def current_toolchain() -> dict[str, str]:
    if platform.python_version() != native_build_provenance.PINNED_PYTHON_VERSION:
        raise ReproducibleBuildError(
            "native reproducibility requires Python "
            f"{native_build_provenance.PINNED_PYTHON_VERSION}"
        )
    try:
        import PyInstaller
    except ImportError as exc:
        raise ReproducibleBuildError("pinned PyInstaller is unavailable") from exc
    observed = {
        "python": platform.python_version(),
        "pyinstaller": str(getattr(PyInstaller, "__version__", "")),
        "uv": _current_uv_version(),
    }
    native_build_provenance._validate_toolchain(observed)
    return observed


def _stable_lock_digest(path: Path, *, label: str) -> str:
    regular = native_build_provenance._regular_file(path, label=label)
    first = native_build_provenance._stable_hash(regular, label=label)[0]
    second = native_build_provenance._stable_hash(regular, label=label)[0]
    if first != second:
        raise ReproducibleBuildError(f"{label} changed while lock identity was collected")
    return first


def lock_digests(repository_root: Path) -> dict[str, dict[str, str]]:
    return {
        name: {
            "sha256": _stable_lock_digest(
                repository_root / name,
                label=f"reviewed lock {name}",
            )
        }
        for name in native_build_provenance.LOCK_FILE_NAMES
    }


def project_version(repository_root: Path) -> str:
    try:
        project = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
        value = project["project"]["version"]
    except (KeyError, OSError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ReproducibleBuildError("project version metadata is unavailable") from exc
    if not isinstance(value, str):
        raise ReproducibleBuildError("project version metadata is malformed")
    runtime_path = (
        repository_root / "packages" / "allthecontext" / "src" / "allthecontext" / "__init__.py"
    )
    try:
        runtime = runtime_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReproducibleBuildError("runtime version metadata is unavailable") from exc
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']$', runtime, re.MULTILINE)
    if match is None or match.group(1) != value:
        raise ReproducibleBuildError("project and runtime version metadata do not match")
    return value


def checked_out_commit(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or COMMIT_PATTERN.fullmatch(value) is None:
        raise ReproducibleBuildError("checked-out source commit is unavailable")
    return value


def _stage_verified_components(
    snapshot: native_build_provenance.BuildSnapshot,
    *,
    source_build_root: Path,
    source_dist_root: Path,
    final_build_root: Path,
    final_dist_root: Path,
) -> dict[str, Path]:
    _fresh_directory(final_build_root, label="final build root")
    _fresh_directory(final_dist_root, label="final dist root")
    source_paths = build_desktop.component_paths(
        build_root=source_build_root,
        dist_root=source_dist_root,
    )
    final_paths = build_desktop.component_paths(
        build_root=final_build_root,
        dist_root=final_dist_root,
    )
    if snapshot.label != "clean-build-2":
        raise ReproducibleBuildError("only the second verified clean build may be staged")
    for role, _filename, _build_filename in native_build_provenance.COMPONENTS:
        source = source_paths[role]
        destination = final_paths[role]
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source, destination)
        except OSError as exc:
            raise ReproducibleBuildError("verified native component could not be staged") from exc
    return cast(dict[str, Path], final_paths)


def generate_provenance(
    *,
    repository_root: Path,
    version: str,
    source_commit: str,
    final_build_root: Path,
    final_dist_root: Path,
    output_dir: Path,
    toolchain: Mapping[str, str] | None = None,
    locks: Mapping[str, Mapping[str, str]] | None = None,
    build_runner: Callable[[Path, Path, Path], native_build_provenance.BuildSnapshot] | None = None,
) -> tuple[dict[str, object], dict[str, Path]]:
    """Run the two clean builds, stage matching bytes, and emit metadata."""

    if COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ReproducibleBuildError("source commit must be a full lowercase SHA-1")
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReproducibleBuildError("version is malformed")
    active_toolchain = dict(toolchain) if toolchain is not None else current_toolchain()
    active_locks = (
        {name: dict(value) for name, value in locks.items()}
        if locks is not None
        else lock_digests(repository_root)
    )
    runner = build_runner
    if runner is None:

        def runner(_source: Path, build: Path, dist: Path) -> native_build_provenance.BuildSnapshot:
            return _run_clean_build(
                repository_root,
                build,
                dist,
                version=version,
                source_commit=source_commit,
            )

    with tempfile.TemporaryDirectory(prefix="atc-native-repro-") as temporary_name:
        temporary = Path(temporary_name)
        first_build_root = temporary / "clean-build-1" / "build"
        first_dist_root = temporary / "clean-build-1" / "dist"
        second_build_root = temporary / "clean-build-2" / "build"
        second_dist_root = temporary / "clean-build-2" / "dist"
        first = runner(repository_root, first_build_root, first_dist_root)
        second = runner(repository_root, second_build_root, second_dist_root)
        first = native_build_provenance.BuildSnapshot("clean-build-1", first.components)
        second = native_build_provenance.BuildSnapshot("clean-build-2", second.components)
        payload = native_build_provenance.build_payload(
            version=version,
            source_commit=source_commit,
            toolchain=active_toolchain,
            locks=active_locks,
            first=first,
            second=second,
        )
        final_paths = _stage_verified_components(
            second,
            source_build_root=second_build_root,
            source_dist_root=second_dist_root,
            final_build_root=final_build_root,
            final_dist_root=final_dist_root,
        )

    output = output_dir.expanduser().resolve()
    _fresh_directory(output, label="provenance output directory")
    native_build_provenance.write_provenance(
        output / native_build_provenance.PROVENANCE_FILE_NAME, payload
    )
    native_build_provenance.verify_provenance(
        output / native_build_provenance.PROVENANCE_FILE_NAME,
        final_paths,
        version=version,
        source_commit=source_commit,
    )
    return payload, final_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--version")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--build-root", type=Path, default=DEFAULT_BUILD_ROOT)
    parser.add_argument("--dist-root", type=Path, default=DEFAULT_DIST_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    arguments = parser.parse_args()
    try:
        repository_root = arguments.repository_root.expanduser().resolve(strict=True)
        if platform.system() != "Windows":
            raise ReproducibleBuildError("native Windows reproducibility requires a Windows runner")
        project_version_value = project_version(repository_root)
        version = arguments.version or project_version_value
        if VERSION_PATTERN.fullmatch(version) is None or version != project_version_value:
            raise ReproducibleBuildError("version does not match project metadata")
        if checked_out_commit(repository_root) != arguments.source_commit:
            raise ReproducibleBuildError("checked-out commit does not match source commit input")
        payload, _paths = generate_provenance(
            repository_root=repository_root,
            version=version,
            source_commit=arguments.source_commit,
            final_build_root=arguments.build_root,
            final_dist_root=arguments.dist_root,
            output_dir=arguments.output_dir,
        )
        output = arguments.output_dir.expanduser().resolve()
        print(output / native_build_provenance.PROVENANCE_FILE_NAME)
        print(output / native_build_provenance.CHECKSUM_FILE_NAME)
        print(
            "reproducible native build verified "
            f"components={len(cast(list[object], payload['components']))} "
            f"contract={native_build_provenance.CONTRACT_ID}"
        )
        return 0
    except (
        native_build_provenance.NativeBuildProvenanceError,
        ReproducibleBuildError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"reproducible build error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

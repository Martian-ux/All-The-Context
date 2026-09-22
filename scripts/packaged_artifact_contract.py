"""Resolve and verify the exact native outputs used by packaged smokes."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts.native_build_provenance import (
        CHECKSUM_FILE_NAME,
        COMMIT_PATTERN,
        PROVENANCE_FILE_NAME,
        NativeBuildProvenanceError,
        verify_provenance,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from native_build_provenance import (  # type: ignore[no-redef]
        CHECKSUM_FILE_NAME,
        COMMIT_PATTERN,
        PROVENANCE_FILE_NAME,
        NativeBuildProvenanceError,
        verify_provenance,
    )


class PackagedArtifactContractError(ValueError):
    """The packaged smoke inputs are not one verified artifact set."""


def resolve_artifact_root(path: Path) -> Path:
    """Resolve one existing artifact root without discovering another root."""

    candidate = path.expanduser()
    if candidate.is_symlink():
        raise PackagedArtifactContractError("artifact root must be a plain directory")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PackagedArtifactContractError("artifact root is unavailable") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise PackagedArtifactContractError("artifact root must be a plain directory")
    return resolved


def artifact_executable(artifact_root: Path, system: str) -> Path:
    """Return the one platform entry point below the explicit artifact root."""

    desktop = artifact_root / "dist" / "desktop"
    if system == "Windows":
        return desktop / "AllTheContextSetup.exe"
    if system == "Darwin":
        return desktop / "AllTheContext.app" / "Contents" / "MacOS" / "AllTheContext"
    return desktop / "all-the-context"


def recovery_executable(artifact_root: Path, system: str) -> tuple[Path, str]:
    """Return the exact console recovery surface below the artifact root."""

    desktop = artifact_root / "dist" / "desktop"
    if system == "Windows":
        return desktop / "AllTheContextRecovery.exe", "frozen-console-recovery-helper"
    if system == "Darwin":
        return (
            desktop / "AllTheContext.app" / "Contents" / "MacOS" / "all-the-context-recovery",
            "frozen-console-recovery-helper",
        )
    return desktop / "all-the-context", "frozen-linux-console-desktop"


def native_component_paths(artifact_root: Path) -> dict[str, Path]:
    """Map provenance roles to fixed paths in the explicit build layout."""

    build = artifact_root / "build" / "desktop"
    desktop = artifact_root / "dist" / "desktop"
    return {
        "main": desktop / "AllTheContextSetup.exe",
        "mcp": build / "helper-dist" / "AllTheContextMCP.exe",
        "recovery": desktop / "AllTheContextRecovery.exe",
        "updater": build / "update-helper-dist" / "AllTheContextUpdater.exe",
    }


def checked_out_source_commit(source_root: Path) -> str:
    """Read the exact commit of the source checkout bound to the smoke."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackagedArtifactContractError("source checkout commit is unavailable") from exc
    source_commit = completed.stdout.strip()
    if completed.returncode != 0 or COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise PackagedArtifactContractError("source checkout commit is unavailable")
    return source_commit


def _within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def validate_windows_artifact_contract(
    *,
    artifact_root: Path,
    provenance_manifest: Path | None,
    provenance_checksum: Path | None,
    source_root: Path,
    version: str,
) -> dict[str, Any]:
    """Verify source identity and every exact executable before any launch."""

    if provenance_manifest is None or provenance_checksum is None:
        raise PackagedArtifactContractError(
            "Windows packaged smokes require provenance manifest and checksum"
        )
    root = resolve_artifact_root(artifact_root)
    try:
        manifest = provenance_manifest.expanduser().resolve(strict=True)
        checksum = provenance_checksum.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PackagedArtifactContractError("provenance input is unavailable") from exc
    expected_checksum = manifest.with_name(CHECKSUM_FILE_NAME)
    if (
        manifest.name != PROVENANCE_FILE_NAME
        or checksum != expected_checksum
        or not _within(manifest, root)
        or not _within(checksum, root)
    ):
        raise PackagedArtifactContractError(
            "provenance paths are not the explicit artifact contract"
        )
    source_commit = checked_out_source_commit(source_root)
    try:
        return verify_provenance(
            manifest,
            native_component_paths(root),
            version=version,
            source_commit=source_commit,
        )
    except NativeBuildProvenanceError as exc:
        raise PackagedArtifactContractError(str(exc)) from exc


__all__ = [
    "PackagedArtifactContractError",
    "artifact_executable",
    "checked_out_source_commit",
    "native_component_paths",
    "recovery_executable",
    "resolve_artifact_root",
    "validate_windows_artifact_contract",
]

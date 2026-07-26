"""Verify committed dashboard assets match a production build byte-for-byte."""

from __future__ import annotations

import filecmp
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .release_manifest import ManifestError, sha256_file

PACKAGE_WEB_RELATIVE = Path("packages/allthecontext/src/allthecontext/web")
DASHBOARD_RELATIVE = Path("apps/dashboard")


@dataclass(frozen=True)
class DashboardParityReport:
    ok: bool
    source_commit: str | None
    committed_files: int
    built_files: int
    mismatches: tuple[str, ...]
    missing_in_committed: tuple[str, ...]
    missing_in_built: tuple[str, ...]
    committed_digest: str
    built_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ok": self.ok,
            "source_commit": self.source_commit,
            "committed_files": self.committed_files,
            "built_files": self.built_files,
            "mismatch_count": len(self.mismatches),
            "mismatches": list(self.mismatches),
            "missing_in_committed": list(self.missing_in_committed),
            "missing_in_built": list(self.missing_in_built),
            "committed_tree_sha256": self.committed_digest,
            "built_tree_sha256": self.built_digest,
        }


def _file_map(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        raise ManifestError(f"dashboard asset root is missing: {root}")
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in result:
            raise ManifestError(f"duplicate dashboard asset path: {relative}")
        result[relative] = path
    if not result:
        raise ManifestError(f"dashboard asset root contains no files: {root}")
    return result


def _tree_digest(files: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        file_digest, size = sha256_file(files[name])
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_dashboard_dist(dashboard_root: Path, output_dir: Path) -> Path:
    npm = shutil.which("npm")
    if npm is None:
        raise ManifestError("npm is required to verify dashboard production parity")
    if output_dir.exists():
        raise ManifestError("dashboard build output directory must not already exist")
    subprocess.run([npm, "ci"], cwd=dashboard_root, check=True)
    subprocess.run([npm, "run", "build"], cwd=dashboard_root, check=True)
    dist = dashboard_root / "dist"
    if not dist.is_dir():
        raise ManifestError("dashboard production build did not create dist/")
    shutil.copytree(dist, output_dir)
    return output_dir


def compare_asset_trees(committed_root: Path, built_root: Path) -> DashboardParityReport:
    committed = _file_map(committed_root)
    built = _file_map(built_root)
    committed_names = set(committed)
    built_names = set(built)
    missing_in_committed = tuple(sorted(built_names - committed_names))
    missing_in_built = tuple(sorted(committed_names - built_names))
    mismatches: list[str] = []
    for name in sorted(committed_names & built_names):
        if not filecmp.cmp(committed[name], built[name], shallow=False):
            mismatches.append(name)
    ok = not mismatches and not missing_in_committed and not missing_in_built
    return DashboardParityReport(
        ok=ok,
        source_commit=None,
        committed_files=len(committed),
        built_files=len(built),
        mismatches=tuple(mismatches),
        missing_in_committed=missing_in_committed,
        missing_in_built=missing_in_built,
        committed_digest=_tree_digest(committed),
        built_digest=_tree_digest(built),
    )


def verify_dashboard_parity(
    repository_root: Path,
    *,
    source_commit: str | None = None,
    build: bool = True,
    built_dist: Path | None = None,
) -> DashboardParityReport:
    repository_root = repository_root.resolve()
    committed_root = repository_root / PACKAGE_WEB_RELATIVE
    dashboard_root = repository_root / DASHBOARD_RELATIVE
    if built_dist is not None:
        report = compare_asset_trees(committed_root, built_dist)
    elif build:
        # Build in-place under apps/dashboard/dist for byte comparison.
        npm = shutil.which("npm")
        if npm is None:
            raise ManifestError("npm is required to verify dashboard production parity")
        subprocess.run([npm, "ci"], cwd=dashboard_root, check=True)
        subprocess.run([npm, "run", "build"], cwd=dashboard_root, check=True)
        dist = dashboard_root / "dist"
        if not dist.is_dir():
            raise ManifestError("dashboard production build did not create dist/")
        report = compare_asset_trees(committed_root, dist)
    else:
        raise ManifestError("dashboard parity requires build=True or an explicit built_dist")
    if source_commit is not None:
        report = DashboardParityReport(
            ok=report.ok,
            source_commit=source_commit,
            committed_files=report.committed_files,
            built_files=report.built_files,
            mismatches=report.mismatches,
            missing_in_committed=report.missing_in_committed,
            missing_in_built=report.missing_in_built,
            committed_digest=report.committed_digest,
            built_digest=report.built_digest,
        )
    if not report.ok:
        raise ManifestError(
            "committed dashboard assets do not match the production build "
            f"(mismatches={len(report.mismatches)}, "
            f"missing_in_committed={len(report.missing_in_committed)}, "
            f"missing_in_built={len(report.missing_in_built)})"
        )
    return report


def write_parity_report(path: Path, report: DashboardParityReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace dashboard parity report: {path.name}")
    path.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

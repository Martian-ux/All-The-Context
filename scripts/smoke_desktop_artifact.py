"""Exercise the frozen desktop artifact without changing user configuration."""

from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "desktop"
sys.path.insert(0, str(ROOT / "packages" / "allthecontext" / "src"))

from allthecontext import __version__

try:
    from scripts.packaged_artifact_contract import (
        PackagedArtifactContractError,
        validate_windows_artifact_contract,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from packaged_artifact_contract import (  # type: ignore[no-redef]
        PackagedArtifactContractError,
        validate_windows_artifact_contract,
    )


def artifact_executable(system: str, artifact_root: Path = ROOT) -> Path:
    desktop = artifact_root / "dist" / "desktop"
    if system == "Windows":
        return desktop / "AllTheContextSetup.exe"
    if system == "Darwin":
        return desktop / "AllTheContext.app" / "Contents" / "MacOS" / "AllTheContext"
    return desktop / "all-the-context"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=ROOT)
    parser.add_argument("--provenance-manifest", type=Path)
    parser.add_argument("--provenance-checksum", type=Path)
    arguments = parser.parse_args()
    system = platform.system()
    artifact_root = arguments.artifact_root.expanduser().resolve(strict=True)
    executable = artifact_executable(system, artifact_root)
    if not executable.is_file():
        raise SystemExit(f"desktop artifact is missing: {executable}")
    if system == "Windows":
        try:
            validate_windows_artifact_contract(
                artifact_root=artifact_root,
                provenance_manifest=arguments.provenance_manifest,
                provenance_checksum=arguments.provenance_checksum,
                source_root=ROOT,
                version=__version__,
            )
        except PackagedArtifactContractError as exc:
            raise SystemExit(f"packaged artifact contract failed: {exc}") from None
    report = artifact_root / "dist" / "desktop" / "diagnostics.json"
    subprocess.run([str(executable), "--diagnostics", str(report)], check=True, timeout=60)
    payload = json.loads(report.read_text(encoding="utf-8"))
    expected = {
        "frozen": True,
        "distribution_trust": "unsigned-community",
        "dashboard_bundled": True,
        "update_keyring_bundled": True,
        "mcp_stdio_available": True,
        "platform": system,
    }
    observed = {key: payload.get(key) for key in expected}
    if observed != expected:
        raise SystemExit(f"unexpected frozen diagnostics: {payload}")
    if system in {"Windows", "Darwin"} and not payload.get("mcp_helper_bundled"):
        raise SystemExit(f"the GUI build is missing its console MCP helper: {payload}")
    if system in {"Windows", "Darwin"} and not payload.get("recovery_helper_bundled"):
        raise SystemExit(f"the GUI build is missing its console recovery helper: {payload}")
    if system == "Windows" and not payload.get("update_helper_bundled"):
        raise SystemExit(f"the Windows build is missing its update helper: {payload}")
    if system == "Linux" and not payload.get("recovery_helper_bundled"):
        raise SystemExit(f"Linux console recovery surface missing: {payload}")
    if payload.get("core_migrations", 0) < 9 or payload.get("relay_migrations", 0) < 1:
        raise SystemExit(f"migrations were not bundled: {payload}")
    migration_names = payload.get("core_migration_names") or []
    if "009_import_operations.sql" not in migration_names and not payload.get(
        "import_operations_migration"
    ):
        raise SystemExit(f"import-operations migration missing from frozen package: {payload}")
    if not payload.get("dashboard_import_operations"):
        raise SystemExit(
            f"combined browser+import dashboard assets missing import-operations surface: {payload}"
        )
    if system in {"Windows", "Darwin"}:
        with tempfile.TemporaryDirectory(prefix="atc-packaged-credential-") as temporary:
            credential_report = Path(temporary) / "credential.json"
            environment = os.environ.copy()
            environment["ATC_PACKAGED_SMOKE"] = "1"
            subprocess.run(
                [
                    str(executable),
                    "--packaged-credential-acceptance",
                    str(credential_report),
                ],
                check=True,
                timeout=60,
                env=environment,
            )
            credential_payload = json.loads(credential_report.read_text(encoding="utf-8"))
            if credential_payload != {
                "platform": system,
                "os_credential": "round-trip-and-delete-passed",
            }:
                raise SystemExit(f"unexpected packaged credential acceptance: {credential_payload}")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

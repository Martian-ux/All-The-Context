"""Assemble or verify immutable native release-candidate inventories."""

from __future__ import annotations

import argparse
import json
import platform
import re
import struct
import subprocess
import tomllib
from pathlib import Path

from allthecontext.release_candidate import (
    CANDIDATE_FILE_NAME,
    SOURCE_EVIDENCE_FILE_NAMES,
    ReleaseTarget,
    assemble_candidate,
    attach_attestation_bundles,
    normalize_github_release_state,
    select_github_release_from_api_listing,
    validate_github_release_state,
    verify_candidate,
    verify_release_asset_set,
)
from allthecontext.release_manifest import ManifestError, ReleaseVersion, sha256_file

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_READINESS_DOCUMENTS = {
    Path("SUPPORT.md"): (
        "github.com/martian-ux/all-the-context/issues/new",
        "security/advisories/new",
        "triage",
    ),
    Path("docs/KNOWN_ISSUES.md"): (
        "severity",
        "impact",
        "workaround",
        "owner",
        "post-v1",
    ),
    Path("SECURITY.md"): (
        "private vulnerability reporting",
        "http 404",
        "does not silently",
    ),
    Path("docs/operations/RUNBOOK.md"): ("restore", "backup"),
}
PUBLIC_READINESS_LINKS = (
    "[support](SUPPORT.md)",
    "[known issues](docs/KNOWN_ISSUES.md)",
    "[security](SECURITY.md)",
    "[recovery runbook](docs/operations/RUNBOOK.md)",
)


def _stage_into_release(release_dir: Path, source_evidence_dir: Path) -> None:
    """Copy reviewed source-evidence files into the release inventory directory."""

    if not source_evidence_dir.is_dir():
        raise ManifestError(f"source evidence directory is missing: {source_evidence_dir}")
    for file_name in SOURCE_EVIDENCE_FILE_NAMES.values():
        source = source_evidence_dir / file_name
        destination = release_dir / file_name
        if not source.is_file() or source.is_symlink():
            raise ManifestError(f"source evidence file is missing: {file_name}")
        if destination.exists():
            raise ManifestError(f"refusing to replace release file: {file_name}")
        destination.write_bytes(source.read_bytes())


def canonical_python_version(version: str) -> str:
    """Return the accepted PEP 440 spelling for the supported SemVer subset."""

    ReleaseVersion.parse(version)
    return re.sub(r"-beta\.([1-9][0-9]*)$", r"b\1", version)


def validate_public_readiness_docs(project_root: Path) -> None:
    """Require stable source-level support, safety, and recovery guidance."""

    for relative, required_markers in PUBLIC_READINESS_DOCUMENTS.items():
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"release readiness document is missing: {relative.as_posix()}")
        text = path.read_text(encoding="utf-8").casefold()
        missing = [marker for marker in required_markers if marker.casefold() not in text]
        if missing:
            raise ManifestError(
                "release readiness document is incomplete: "
                f"{relative.as_posix()} missing {', '.join(missing)}"
            )
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    missing_links = [link for link in PUBLIC_READINESS_LINKS if link not in readme]
    if missing_links:
        raise ManifestError(
            "README release-readiness links are incomplete: " + ", ".join(missing_links)
        )


def validate_runner_target(expected_platform: str, expected_architecture: str) -> None:
    systems = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
    architectures = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    actual_platform = systems.get(platform.system())
    actual_architecture = architectures.get(platform.machine().casefold())
    if struct.calcsize("P") != 8 or actual_platform is None or actual_architecture is None:
        raise ManifestError("release runner must be a recognized 64-bit native platform")
    if (actual_platform, actual_architecture) != (expected_platform, expected_architecture):
        raise ManifestError(
            "release runner target is mislabeled "
            f"(expected={expected_platform}/{expected_architecture}, "
            f"actual={actual_platform}/{actual_architecture})"
        )


def validate_source_metadata(
    project_root: Path,
    *,
    version: str,
    channel: str,
    source_commit: str,
    checked_out_commit: str,
) -> None:
    parsed = ReleaseVersion.parse(version)
    if channel not in {"stable", "beta"} or (channel == "beta") != (parsed.stability == 0):
        raise ManifestError("release version and channel do not match")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or checked_out_commit != source_commit:
        raise ManifestError("checked-out commit is not the exact immutable source input")
    accepted_versions = {version, canonical_python_version(version)}
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = project.get("project", {}).get("version")
    if project_version not in accepted_versions:
        raise ManifestError("release version does not match Python project metadata")
    runtime = (project_root / "packages/allthecontext/src/allthecontext/__init__.py").read_text(
        encoding="utf-8"
    )
    if not any(f'__version__ = "{candidate}"' in runtime for candidate in accepted_versions):
        raise ManifestError("release version does not match runtime metadata")
    dashboard = json.loads(
        (project_root / "apps/dashboard/package.json").read_text(encoding="utf-8")
    )
    dashboard_lock = json.loads(
        (project_root / "apps/dashboard/package-lock.json").read_text(encoding="utf-8")
    )
    if dashboard.get("version") != version:
        raise ManifestError("release version does not match dashboard package metadata")
    lock_root = dashboard_lock.get("packages", {}).get("")
    if dashboard_lock.get("version") != version or not isinstance(lock_root, dict):
        raise ManifestError("release version does not match dashboard lock metadata")
    if lock_root.get("version") != version:
        raise ManifestError("dashboard lock root has a stale release version")
    uv_lock = tomllib.loads((project_root / "uv.lock").read_text(encoding="utf-8"))
    locked_project = [
        package
        for package in uv_lock.get("package", [])
        if isinstance(package, dict) and package.get("name") == "all-the-context"
    ]
    if len(locked_project) != 1 or locked_project[0].get("version") not in accepted_versions:
        raise ManifestError("release version does not match the Python dependency lock")
    validate_public_readiness_docs(project_root)


def _targets(values: list[str]) -> list[ReleaseTarget]:
    return [ReleaseTarget.parse(value) for value in values]


def _release_asset_descriptors(paths: list[Path]) -> dict[str, tuple[str, int]]:
    return {path.name: sha256_file(path) for path in paths}


def _write_release_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError("refusing to replace existing GitHub release state")
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-source")
    validate.add_argument("--project-root", type=Path, default=REPOSITORY_ROOT)
    validate.add_argument("--version", required=True)
    validate.add_argument("--channel", choices=("beta", "stable"), required=True)
    validate.add_argument("--source-commit", required=True)
    validate.add_argument("--checked-out-commit")
    runner = commands.add_parser("validate-runner")
    runner.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    runner.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    attach = commands.add_parser("attach-attestations")
    attach.add_argument("--release-dir", type=Path, required=True)
    attach.add_argument("--subject", type=Path)
    attach.add_argument("--provenance-bundle", type=Path, required=True)
    attach.add_argument("--sbom-bundle", type=Path, required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--release-dir", type=Path, required=True)
    assemble.add_argument("--version", required=True)
    assemble.add_argument("--channel", choices=("beta", "stable"), required=True)
    assemble.add_argument("--source-commit", required=True)
    assemble.add_argument("--target", action="append", required=True)
    assemble.add_argument("--ota-target", action="append", required=True)
    assemble.add_argument(
        "--source-evidence-dir",
        type=Path,
        help="Directory containing matrix evidence, component inventory, and notices",
    )
    assemble.add_argument("--output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--release-dir", type=Path, required=True)
    verify.add_argument("--candidate", type=Path)
    verify.add_argument("--expected-sha256")
    verify.add_argument("--target", action="append")
    verify.add_argument("--ota-target", action="append")
    verify.add_argument("--asset-stage", choices=("draft", "signed", "promotion"))
    assets = commands.add_parser("list-assets")
    assets.add_argument("--release-dir", type=Path, required=True)
    assets.add_argument("--candidate", type=Path)
    assets.add_argument("--stage", choices=("draft", "signed", "promotion"), required=True)
    state = commands.add_parser("verify-release-state")
    state.add_argument("--input", type=Path, required=True)
    state.add_argument("--tag", required=True)
    state.add_argument("--source-commit", required=True)
    state.add_argument("--draft", choices=("true", "false"), required=True)
    state.add_argument("--immutable", choices=("true", "false"), required=True)
    state.add_argument("--release-dir", type=Path)
    state.add_argument("--candidate", type=Path)
    state.add_argument("--asset-stage", choices=("draft", "signed", "promotion"))
    resolve = commands.add_parser("resolve-release")
    resolve.add_argument("--input", type=Path, required=True)
    resolve.add_argument("--tag", required=True)
    resolve.add_argument("--source-commit", required=True)
    resolve.add_argument("--draft", choices=("true", "false"), required=True)
    resolve.add_argument("--immutable", choices=("true", "false"), required=True)
    resolve.add_argument("--output", type=Path, required=True)
    resolve.add_argument("--release-dir", type=Path)
    resolve.add_argument("--candidate", type=Path)
    resolve.add_argument("--asset-stage", choices=("draft", "signed", "promotion"))
    list_release_assets = commands.add_parser("list-release-assets")
    list_release_assets.add_argument("--input", type=Path, required=True)
    notes = commands.add_parser("write-notes")
    notes.add_argument("--release-dir", type=Path, required=True)
    notes.add_argument("--candidate", type=Path)
    notes.add_argument("--output", type=Path, required=True)
    return root


def _write_notes(candidate_path: Path, release_dir: Path, output: Path) -> None:
    candidate = verify_candidate(candidate_path, release_dir)
    lines = [
        f"# All The Context v{candidate['version']}",
        "",
        f"> **Unsigned community {candidate['channel']} build.** Windows may display an ",
        "> unknown-publisher or SmartScreen warning. This release has no paid ",
        "> platform signing.",
        "",
        "> Supported release targets: Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64. ",
        "> Windows x86-64 is the only OTA target; Linux is direct install only. ",
        "> macOS is not supported and no macOS package is included.",
        "",
        f"Source commit: `{candidate['source_commit']}`",
        "",
        "Every native package has an exact SHA-256 sidecar, an SPDX 2.3 subject document, ",
        "and GitHub/Sigstore provenance and SBOM attestations. The candidate inventory is ",
        "itself checksummed and attested. Verify those materials before installation.",
        "",
        "The release remains a draft until offline Ed25519 OTA manifests are independently ",
        "signed, verified, attached, and approved. No signing private key is stored by GitHub.",
        "",
        "## Native packages and OTA payloads",
        "",
    ]
    package_labels = {
        "windows": "one-click setup",
        "linux": "portable archive",
    }
    for artifact in candidate["artifacts"]:
        platform_name = artifact["platform"]
        if platform_name not in package_labels:
            raise ManifestError(
                f"public release notes do not support {platform_name} packages or DMG assets"
            )
        package_label = package_labels[platform_name]
        lines.append(
            f"- Direct native package ({package_label}): "
            f"`{artifact['direct_package']['name']}` — "
            f"`{artifact['direct_package']['sha256']}`"
        )
        eligibility = "eligible" if artifact["ota_manifest_eligible"] else "withheld"
        lines.append(
            f"- OTA payload ({eligibility}): `{artifact['ota_archive']['name']}` — "
            f"`{artifact['ota_archive']['sha256']}`"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ManifestError("refusing to replace existing release notes")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "validate-source":
            checked_out_commit = arguments.checked_out_commit
            if checked_out_commit is None:
                checked_out_commit = subprocess.run(
                    ["git", "-C", str(arguments.project_root), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            validate_source_metadata(
                arguments.project_root,
                version=arguments.version,
                channel=arguments.channel,
                source_commit=arguments.source_commit,
                checked_out_commit=checked_out_commit,
            )
            print(f"validated release source {arguments.source_commit}")
        elif arguments.command == "validate-runner":
            validate_runner_target(arguments.platform, arguments.architecture)
            print(f"validated native runner {arguments.platform}/{arguments.architecture}")
        elif arguments.command == "attach-attestations":
            outputs = attach_attestation_bundles(
                arguments.release_dir,
                provenance_bundle=arguments.provenance_bundle,
                sbom_bundle=arguments.sbom_bundle,
                subject=arguments.subject,
            )
            print("\n".join(str(path) for path in outputs))
        elif arguments.command == "assemble":
            if arguments.source_evidence_dir is not None:
                _stage_into_release(arguments.release_dir, arguments.source_evidence_dir)
            candidate = assemble_candidate(
                arguments.release_dir,
                version=arguments.version,
                channel=arguments.channel,
                source_commit=arguments.source_commit,
                targets=_targets(arguments.target),
                ota_targets=_targets(arguments.ota_target),
                output=arguments.output,
            )
            print(candidate)
            print(candidate.with_name(f"{candidate.name}.sha256"))
        elif arguments.command == "verify":
            candidate = arguments.candidate or arguments.release_dir / CANDIDATE_FILE_NAME
            expected_targets = _targets(arguments.target) if arguments.target else None
            expected_ota_targets = _targets(arguments.ota_target) if arguments.ota_target else None
            if arguments.asset_stage:
                verify_release_asset_set(
                    candidate,
                    arguments.release_dir,
                    stage=arguments.asset_stage,
                    expected_sha256=arguments.expected_sha256,
                    expected_targets=expected_targets,
                    expected_ota_targets=expected_ota_targets,
                )
            else:
                verify_candidate(
                    candidate,
                    arguments.release_dir,
                    expected_sha256=arguments.expected_sha256,
                    expected_targets=expected_targets,
                    expected_ota_targets=expected_ota_targets,
                )
            digest, _ = sha256_file(candidate)
            print(f"verified release candidate {digest}")
        elif arguments.command == "list-assets":
            candidate = arguments.candidate or arguments.release_dir / CANDIDATE_FILE_NAME
            paths = verify_release_asset_set(
                candidate,
                arguments.release_dir,
                stage=arguments.stage,
            )
            print("\n".join(path.as_posix() for path in paths))
        elif arguments.command == "verify-release-state":
            value = json.loads(arguments.input.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ManifestError("GitHub release state must be a JSON object")
            if (arguments.release_dir is None) != (arguments.asset_stage is None):
                raise ManifestError(
                    "release state asset verification requires both release-dir and asset-stage"
                )
            expected_asset_names = None
            expected_asset_descriptors = None
            if arguments.release_dir is not None:
                candidate = arguments.candidate or arguments.release_dir / CANDIDATE_FILE_NAME
                paths = verify_release_asset_set(
                    candidate,
                    arguments.release_dir,
                    stage=arguments.asset_stage,
                )
                expected_asset_names = {path.name for path in paths}
                expected_asset_descriptors = _release_asset_descriptors(paths)
            validate_github_release_state(
                value,
                tag=arguments.tag,
                source_commit=arguments.source_commit,
                draft=arguments.draft == "true",
                immutable=arguments.immutable == "true",
                expected_asset_names=expected_asset_names,
                expected_asset_descriptors=expected_asset_descriptors,
            )
            print(f"validated GitHub release state {arguments.tag}")
        elif arguments.command == "resolve-release":
            value = json.loads(arguments.input.read_text(encoding="utf-8"))
            normalized = select_github_release_from_api_listing(value, tag=arguments.tag)
            if (arguments.release_dir is None) != (arguments.asset_stage is None):
                raise ManifestError(
                    "release resolution asset verification requires both release-dir "
                    "and asset-stage"
                )
            expected_asset_names = None
            expected_asset_descriptors = None
            if arguments.release_dir is not None:
                candidate = arguments.candidate or arguments.release_dir / CANDIDATE_FILE_NAME
                paths = verify_release_asset_set(
                    candidate,
                    arguments.release_dir,
                    stage=arguments.asset_stage,
                )
                expected_asset_names = {path.name for path in paths}
                expected_asset_descriptors = _release_asset_descriptors(paths)
            validate_github_release_state(
                normalized,
                tag=arguments.tag,
                source_commit=arguments.source_commit,
                draft=arguments.draft == "true",
                immutable=arguments.immutable == "true",
                expected_asset_names=expected_asset_names,
                expected_asset_descriptors=expected_asset_descriptors,
            )
            _write_release_state(arguments.output, normalized)
            print(normalized["releaseId"])
        elif arguments.command == "list-release-assets":
            value = json.loads(arguments.input.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ManifestError("GitHub release state must be a JSON object")
            normalized = normalize_github_release_state(
                value,
                require_release_id=True,
                require_asset_api_metadata=True,
            )
            assets = sorted(normalized["assets"], key=lambda asset: asset["name"])
            print("\n".join(f"{asset['id']}\t{asset['name']}" for asset in assets))
        else:
            candidate = arguments.candidate or arguments.release_dir / CANDIDATE_FILE_NAME
            _write_notes(candidate, arguments.release_dir, arguments.output)
            print(arguments.output)
        return 0
    except (
        ManifestError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        tomllib.TOMLDecodeError,
    ) as exc:
        raise SystemExit(f"release candidate error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())

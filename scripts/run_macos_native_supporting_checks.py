"""Run candidate-bound macOS preparation checks without claiming acceptance.

This runner is intentionally narrower than the Beta 1 native acceptance plan.
It verifies an exact candidate inventory, stages the architecture-specific app
from its DMG, and executes the existing isolated package smokes.  It never
emits acceptance receipts and cannot replace supervised Gatekeeper, real-client,
login/reboot, destructive recovery, or two-gigabyte boundary journeys.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import allthecontext
from allthecontext.release_candidate import verify_candidate
from allthecontext.release_manifest import sha256_file

try:
    from scripts.macos_acceptance_preflight import (
        NATIVE_ACCEPTANCE_PROFILE,
        collect_host_facts,
        evaluate_host_facts,
        write_report,
    )
    from scripts.smoke_platform_package import verify_macos_app, verify_package
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from macos_acceptance_preflight import (  # type: ignore[no-redef]
        NATIVE_ACCEPTANCE_PROFILE,
        collect_host_facts,
        evaluate_host_facts,
        write_report,
    )
    from smoke_platform_package import (  # type: ignore[no-redef]
        verify_macos_app,
        verify_package,
    )

MACOS_ARCHITECTURES = ("arm64", "x86_64")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MACOS_VERSION_PATTERN = re.compile(r"^[1-9][0-9]*\.[0-9]+(?:\.[0-9]+)?$")
SUPPORTING_SCRIPTS = (
    ("native_adapter_isolation", "smoke_platform_acceptance.py", ("--require-os-credential",)),
    ("frozen_artifact_resources", "smoke_desktop_artifact.py", ()),
    ("packaged_recovery_smoke", "smoke_packaged_recovery.py", ()),
    ("isolated_packaged_first_run", "smoke_packaged_first_run.py", ()),
)
REMAINING_NATIVE_ACCEPTANCE = (
    "gatekeeper_first_launch_and_unsigned_disclosure",
    "real_codex_stable_first_run_restart_retrieval_and_conflict",
    "real_claude_stable_first_run_restart_retrieval_and_conflict",
    "keychain_failure_rollback_and_residual_audit",
    "launchagent_login_and_reboot_persistence",
    "security_origin_isolation_and_authorization_journeys",
    "deletion_retention_export_restore_and_stopped_core_recovery",
    "allocated_two_gigabyte_boundary_interrupt_retry_and_resource_budget",
)
TOOLING_FILE_NAMES = (
    "check_runner_architecture.py",
    "macos_acceptance_preflight.py",
    "run_macos_native_supporting_checks.py",
    "smoke_platform_package.py",
)


class SupportingCheckError(RuntimeError):
    """Closed-code failure that is safe to record in a content-free report."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class PhaseObservation:
    phase: str
    status: str
    return_code: int | None
    duration_ms: int
    stdout_bytes: int
    stderr_bytes: int


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _path_is_within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def create_results_directory(path: Path, *, project_root: Path) -> Path:
    """Create one new results directory outside the source checkout."""

    destination = path.expanduser().resolve()
    source = project_root.expanduser().resolve(strict=True)
    if _path_is_within(destination, source):
        raise SupportingCheckError("results_directory_inside_source")
    if destination.exists() or destination.is_symlink():
        raise SupportingCheckError("results_directory_already_exists")
    destination.mkdir(parents=True)
    return destination


def _git_text(project_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise SupportingCheckError("source_git_state_unreadable")
    return completed.stdout.strip()


def validate_source_checkout(project_root: Path, *, expected_commit: str) -> None:
    """Require a clean checkout at the exact candidate source commit."""

    if _git_text(project_root, "rev-parse", "HEAD") != expected_commit:
        raise SupportingCheckError("source_commit_mismatch")
    if _git_text(project_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SupportingCheckError("source_checkout_not_clean")
    staged_desktop = project_root / "dist" / "desktop"
    if (project_root / "dist").is_symlink():
        raise SupportingCheckError("desktop_staging_parent_is_symlink")
    if staged_desktop.exists() or staged_desktop.is_symlink():
        raise SupportingCheckError("desktop_staging_path_not_empty")


def validate_source_python_environment(project_root: Path) -> None:
    """Require imports and child processes to use the exact checkout package."""

    package_file = getattr(allthecontext, "__file__", None)
    if not isinstance(package_file, str):
        raise SupportingCheckError("source_python_environment_unreadable")
    expected = (
        project_root / "packages" / "allthecontext" / "src" / "allthecontext" / "__init__.py"
    ).resolve(strict=True)
    if Path(package_file).resolve(strict=True) != expected:
        raise SupportingCheckError("source_python_environment_mismatch")


def tooling_digests() -> dict[str, dict[str, Any]]:
    """Bind the external preparation harness without treating it as product bytes."""

    directory = Path(__file__).resolve().parent
    result: dict[str, dict[str, Any]] = {}
    for name in TOOLING_FILE_NAMES:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise SupportingCheckError("supporting_tooling_incomplete")
        digest, size = sha256_file(path)
        result[name] = {"sha256": digest, "size": size}
    return result


def select_macos_artifact(candidate: dict[str, Any], *, architecture: str) -> dict[str, Any]:
    """Select exactly one already-validated candidate entry for this Mac."""

    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, list):
        raise SupportingCheckError("candidate_artifact_inventory_missing")
    matches = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and item.get("platform") == "macos"
        and item.get("architecture") == architecture
    ]
    if len(matches) != 1:
        raise SupportingCheckError("macos_candidate_target_not_unique")
    return matches[0]


def _descriptor(artifact: dict[str, Any], field: str) -> dict[str, Any]:
    value = artifact.get(field)
    if not isinstance(value, dict) or set(value) != {"name", "sha256", "size"}:
        raise SupportingCheckError("candidate_artifact_descriptor_invalid")
    name = value.get("name")
    digest = value.get("sha256")
    size = value.get("size")
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not isinstance(digest, str)
        or SHA256_PATTERN.fullmatch(digest) is None
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        raise SupportingCheckError("candidate_artifact_descriptor_invalid")
    return {"name": name, "sha256": digest, "size": size}


def _mount_points(attach_payload: bytes) -> list[Path]:
    try:
        parsed = plistlib.loads(attach_payload)
    except (ValueError, plistlib.InvalidFileException) as exc:
        raise SupportingCheckError("dmg_attach_report_invalid") from exc
    if not isinstance(parsed, dict):
        raise SupportingCheckError("dmg_attach_report_invalid")
    return [
        Path(str(entity["mount-point"]))
        for entity in parsed.get("system-entities", [])
        if isinstance(entity, dict) and entity.get("mount-point")
    ]


def stage_macos_app_from_dmg(
    package: Path,
    *,
    destination: Path,
    architecture: str,
    version: str,
) -> None:
    """Copy one verified app from a read-only DMG into fresh smoke staging."""

    if destination.exists() or destination.is_symlink():
        raise SupportingCheckError("desktop_staging_path_not_empty")
    mount_point = Path(tempfile.mkdtemp(prefix="atc-macos-dmg-"))
    attached_ok = False
    detached = False
    try:
        attached = subprocess.run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                str(mount_point),
                "-plist",
                str(package),
            ],
            check=False,
            capture_output=True,
            timeout=120,
        )
        if attached.returncode != 0:
            raise SupportingCheckError("dmg_attach_failed")
        attached_ok = True
        mount_points = _mount_points(attached.stdout)
        if len(mount_points) != 1 or mount_points[0].resolve() != mount_point.resolve():
            raise SupportingCheckError("dmg_mount_count_invalid")
        source = mount_point / "All The Context.app"
        if not source.is_dir() or source.is_symlink():
            raise SupportingCheckError("dmg_application_missing")
        destination.parent.mkdir(parents=True, exist_ok=False)
        copied = subprocess.run(
            ["ditto", str(source), str(destination)],
            check=False,
            capture_output=True,
            timeout=300,
        )
        if copied.returncode != 0:
            raise SupportingCheckError("dmg_application_copy_failed")
    finally:
        detach = subprocess.run(
            ["hdiutil", "detach", str(mount_point)],
            check=False,
            capture_output=True,
            timeout=120,
        )
        detached = detach.returncode == 0
        if attached_ok and not detached:
            forced_detach = subprocess.run(
                ["hdiutil", "detach", "-force", str(mount_point)],
                check=False,
                capture_output=True,
                timeout=120,
            )
            detached = forced_detach.returncode == 0
        with contextlib.suppress(OSError):
            mount_point.rmdir()
        if attached_ok and not detached:
            raise SupportingCheckError("dmg_detach_failed")
    if not attached_ok:
        raise SupportingCheckError("dmg_attach_failed")
    verify_macos_app(
        destination,
        expected_architecture=architecture,
        expected_version=version,
    )


def run_subprocess_phase(
    phase: str,
    command: list[str],
    *,
    project_root: Path,
    environment: dict[str, str],
    timeout_seconds: int = 900,
) -> PhaseObservation:
    """Run a phase while retaining only content-free sizes and status."""

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            env=environment,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        return PhaseObservation(
            phase=phase,
            status="failed_timeout",
            return_code=None,
            duration_ms=_elapsed_ms(started),
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
        )
    return PhaseObservation(
        phase=phase,
        status="pass" if completed.returncode == 0 else "failed",
        return_code=completed.returncode,
        duration_ms=_elapsed_ms(started),
        stdout_bytes=len(completed.stdout),
        stderr_bytes=len(completed.stderr),
    )


def _record_in_process_phase(
    phase: str,
    action: Callable[[], object],
) -> PhaseObservation:
    started = time.monotonic()
    try:
        action()
    except Exception:
        return PhaseObservation(phase, "failed", 1, _elapsed_ms(started), 0, 0)
    return PhaseObservation(phase, "pass", 0, _elapsed_ms(started), 0, 0)


def _remove_exact_tree(path: Path, *, expected_parent: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return True
    if path.parent.resolve() != expected_parent.resolve():
        return False
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError:
        return False
    return not path.exists() and not path.is_symlink()


def _write_json_once(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_supporting_checks(
    *,
    project_root: Path,
    release_dir: Path,
    candidate_path: Path,
    expected_candidate_sha256: str,
    architecture: str,
    expected_os_version: str,
    results_dir: Path,
    dedicated_clean_user_attested: bool,
) -> dict[str, Any]:
    """Run all bounded supporting checks and write a content-free report."""

    if architecture not in MACOS_ARCHITECTURES:
        raise SupportingCheckError("macos_architecture_invalid")
    if SHA256_PATTERN.fullmatch(expected_candidate_sha256) is None:
        raise SupportingCheckError("candidate_sha256_invalid")
    if MACOS_VERSION_PATTERN.fullmatch(expected_os_version) is None:
        raise SupportingCheckError("expected_macos_version_invalid")

    source = project_root.expanduser().resolve(strict=True)
    release = release_dir.expanduser().resolve(strict=True)
    candidate_input = candidate_path.expanduser()
    if candidate_input.is_symlink():
        raise SupportingCheckError("candidate_must_be_direct_release_file")
    candidate_file = candidate_input.resolve(strict=True)
    if candidate_file.parent != release:
        raise SupportingCheckError("candidate_must_be_direct_release_file")
    output = create_results_directory(results_dir, project_root=source)
    phases: list[PhaseObservation] = []
    candidate: dict[str, Any] | None = None
    artifact: dict[str, Any] | None = None
    failure_reason: str | None = None
    preflight_status = "not_run"
    native_eligible = False
    source_commit: str | None = None
    version: str | None = None
    harness_digests: dict[str, dict[str, Any]] = {}
    staging = source / "dist" / "desktop"
    run_root: Path | None = None
    dist_existed = (source / "dist").exists()

    try:
        candidate_result: dict[str, Any] | None = None

        def verify_inventory() -> None:
            nonlocal candidate_result
            candidate_result = verify_candidate(
                candidate_file,
                release,
                expected_sha256=expected_candidate_sha256,
            )

        candidate_observation = _record_in_process_phase(
            "exact_candidate_inventory_verification",
            verify_inventory,
        )
        phases.append(candidate_observation)
        if candidate_observation.status != "pass" or candidate_result is None:
            raise SupportingCheckError("candidate_inventory_verification_failed")
        candidate = candidate_result
        source_commit_value = candidate.get("source_commit")
        version_value = candidate.get("version")
        if not isinstance(source_commit_value, str) or not isinstance(version_value, str):
            raise SupportingCheckError("candidate_identity_invalid")
        source_commit = source_commit_value
        version = version_value
        validate_source_checkout(source, expected_commit=source_commit)
        validate_source_python_environment(source)
        harness_digests = tooling_digests()
        artifact = select_macos_artifact(candidate, architecture=architecture)

        host_facts = collect_host_facts()
        preflight = evaluate_host_facts(
            host_facts,
            profile=NATIVE_ACCEPTANCE_PROFILE,
            expected_architecture=architecture,
            expected_major=int(expected_os_version.partition(".")[0]),
            expected_os_version=expected_os_version,
            dedicated_clean_user_attested=dedicated_clean_user_attested,
        )
        write_report(output / "macos-native-preflight.json", preflight)
        preflight_status = str(preflight["status"])
        native_eligible = bool(preflight["native_acceptance_eligible"])
        if preflight_status != "pass" or not native_eligible:
            raise SupportingCheckError("native_preflight_failed")

        package_observation = _record_in_process_phase(
            "exact_direct_package_verification",
            lambda: verify_package(
                release,
                platform_name="macos",
                architecture=architecture,
            ),
        )
        phases.append(package_observation)
        if package_observation.status != "pass":
            raise SupportingCheckError("direct_package_verification_failed")

        direct_package = _descriptor(artifact, "direct_package")
        package_path = release / direct_package["name"]
        staging_observation = _record_in_process_phase(
            "dmg_application_staging",
            lambda: stage_macos_app_from_dmg(
                package_path,
                destination=staging / "AllTheContext.app",
                architecture=architecture,
                version=version_value,
            ),
        )
        phases.append(staging_observation)
        if staging_observation.status != "pass":
            raise SupportingCheckError("dmg_application_staging_failed")

        run_root = Path(tempfile.mkdtemp(prefix="atc-macos-supporting-"))
        environment = dict(os.environ)
        environment["ATC_PACKAGED_SMOKE_PARENT"] = str(run_root / "packaged-first-run")
        for phase, script_name, extra_arguments in SUPPORTING_SCRIPTS:
            observation = run_subprocess_phase(
                phase,
                [sys.executable, str(source / "scripts" / script_name), *extra_arguments],
                project_root=source,
                environment=environment,
            )
            phases.append(observation)
            if observation.status != "pass":
                raise SupportingCheckError(f"{phase}_failed")
    except SupportingCheckError as exc:
        failure_reason = exc.reason_code
    except Exception:
        failure_reason = "unexpected_supporting_check_failure"
    finally:
        staging_removed = _remove_exact_tree(staging, expected_parent=source / "dist")
        if not dist_existed and (source / "dist").is_dir():
            with contextlib.suppress(OSError):
                (source / "dist").rmdir()
        run_root_removed = True
        if run_root is not None:
            run_root_removed = _remove_exact_tree(
                run_root,
                expected_parent=run_root.parent,
            )
        try:
            source_clean = not _git_text(
                source,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
        except SupportingCheckError:
            source_clean = False
        cleanup = {
            "desktop_staging_removed": staging_removed,
            "ephemeral_run_root_removed": run_root_removed,
            "source_checkout_clean": source_clean,
        }
        if not all(cleanup.values()) and failure_reason is None:
            failure_reason = "cleanup_verification_failed"

    artifact_digests: dict[str, dict[str, Any]] = {}
    if artifact is not None:
        for field in ("direct_package", "ota_archive"):
            try:
                descriptor = _descriptor(artifact, field)
            except SupportingCheckError:
                failure_reason = failure_reason or "candidate_artifact_descriptor_invalid"
                continue
            artifact_digests[field] = descriptor

    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "macos_native_supporting_checks",
        "content_free": True,
        "preparation_only": True,
        "acceptance_claimed": False,
        "canonical_receipts_emitted": False,
        "status": "pass" if failure_reason is None else "failed",
        "failure_reason": failure_reason,
        "platform": platform.system(),
        "architecture": architecture,
        "expected_macos_version": expected_os_version,
        "source_commit": source_commit,
        "candidate_sha256": expected_candidate_sha256,
        "version": version,
        "supporting_tooling_digests": harness_digests,
        "preflight": {
            "status": preflight_status,
            "native_acceptance_eligible": native_eligible,
        },
        "artifact_digests": artifact_digests,
        "phases": [asdict(phase) for phase in phases],
        "cleanup": cleanup,
        "remaining_native_acceptance": list(REMAINING_NATIVE_ACCEPTANCE),
    }
    _write_json_once(output / "macos-native-supporting-checks.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--architecture", choices=MACOS_ARCHITECTURES, required=True)
    parser.add_argument("--expected-os-version", required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--dedicated-clean-user-attested", action="store_true")
    arguments = parser.parse_args()

    try:
        report = run_supporting_checks(
            project_root=arguments.project_root,
            release_dir=arguments.release_dir,
            candidate_path=arguments.candidate,
            expected_candidate_sha256=arguments.expected_candidate_sha256,
            architecture=arguments.architecture,
            expected_os_version=arguments.expected_os_version,
            results_dir=arguments.results_dir,
            dedicated_clean_user_attested=arguments.dedicated_clean_user_attested,
        )
    except SupportingCheckError as exc:
        print(
            json.dumps(
                {
                    "kind": "macos_native_supporting_checks",
                    "status": "refused",
                    "reason_code": exc.reason_code,
                    "acceptance_claimed": False,
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "kind": report["kind"],
                "status": report["status"],
                "acceptance_claimed": False,
                "canonical_receipts_emitted": False,
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Record a content-free macOS host preflight without claiming acceptance.

The hosted-CI profile proves that the build is running natively on the declared
macOS architecture with the native packaging tools available.  The stricter
native-acceptance profile additionally enforces the frozen operator hardware,
storage, exact-OS, non-root, and dedicated-clean-user prerequisites.

Neither profile executes a product journey or closes an acceptance gate.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import plistlib
import secrets
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.check_runner_architecture import normalized_architecture
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from check_runner_architecture import normalized_architecture

HOSTED_CI_PROFILE = "hosted-ci"
NATIVE_ACCEPTANCE_PROFILE = "native-acceptance"
PROFILES = (HOSTED_CI_PROFILE, NATIVE_ACCEPTANCE_PROFILE)
MACOS_ARCHITECTURES = ("arm64", "x86_64")
MIN_LOGICAL_CPUS = 4
MIN_MEMORY_BYTES = 8 * 1024 * 1024 * 1024
MIN_ROOT_FREE_BYTES_EXCLUSIVE = 16 * 1024 * 1024 * 1024
REQUIRED_NATIVE_TOOLS = (
    "codesign",
    "diskutil",
    "ditto",
    "hdiutil",
    "launchctl",
    "lipo",
    "plutil",
    "security",
    "sw_vers",
    "sysctl",
    "xattr",
)


@dataclass(frozen=True, slots=True)
class MacOSHostFacts:
    system: str
    architecture: str | None
    rosetta_translated: bool | None
    os_version: str | None
    os_build: str | None
    logical_cpus: int | None
    memory_bytes: int | None
    root_free_bytes: int | None
    root_internal: bool | None
    root_solid_state: bool | None
    filesystem: str | None
    executing_as_root: bool
    missing_tools: tuple[str, ...]


def _run_text(command: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout.strip()


def _command_text(command: Sequence[str]) -> str | None:
    returncode, stdout = _run_text(command)
    if returncode != 0 or not stdout:
        return None
    return stdout


def _command_positive_int(command: Sequence[str]) -> int | None:
    value = _command_text(command)
    if value is None or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def parse_diskutil_plist(value: str | None) -> tuple[bool | None, bool | None, str | None]:
    """Return only allowlisted storage facts from ``diskutil info -plist``."""

    if value is None:
        return None, None, None
    try:
        payload = plistlib.loads(value.encode("utf-8"))
    except (ValueError, plistlib.InvalidFileException):
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    internal_value = payload.get("Internal")
    solid_state_value = payload.get("SolidState")
    filesystem_value = payload.get("FilesystemType") or payload.get("FileSystemPersonality")
    internal = internal_value if isinstance(internal_value, bool) else None
    solid_state = solid_state_value if isinstance(solid_state_value, bool) else None
    filesystem = filesystem_value if isinstance(filesystem_value, str) else None
    return internal, solid_state, filesystem


def collect_host_facts() -> MacOSHostFacts:
    """Collect only content-free facts needed by the frozen macOS preflight."""

    system = platform.system()
    architecture: str | None = None
    with contextlib.suppress(RuntimeError):
        architecture = normalized_architecture(platform.machine())

    missing_tools = tuple(sorted(name for name in REQUIRED_NATIVE_TOOLS if not shutil.which(name)))
    if system != "Darwin":
        return MacOSHostFacts(
            system=system,
            architecture=architecture,
            rosetta_translated=None,
            os_version=None,
            os_build=None,
            logical_cpus=None,
            memory_bytes=None,
            root_free_bytes=None,
            root_internal=None,
            root_solid_state=None,
            filesystem=None,
            executing_as_root=False,
            missing_tools=missing_tools,
        )

    translated_returncode, translated_value = _run_text(("sysctl", "-in", "sysctl.proc_translated"))
    if translated_returncode != 0:
        rosetta_translated: bool | None = False
    elif translated_value == "1":
        rosetta_translated = True
    elif translated_value in {"", "0"}:
        rosetta_translated = False
    else:
        rosetta_translated = None

    root_free_bytes: int | None
    try:
        root_free_bytes = shutil.disk_usage(Path("/")).free
    except OSError:
        root_free_bytes = None
    internal, solid_state, filesystem = parse_diskutil_plist(
        _command_text(("diskutil", "info", "-plist", "/"))
    )
    getuid = getattr(os, "geteuid", None)
    executing_as_root = bool(getuid is not None and getuid() == 0)
    return MacOSHostFacts(
        system=system,
        architecture=architecture,
        rosetta_translated=rosetta_translated,
        os_version=_command_text(("sw_vers", "-productVersion")),
        os_build=_command_text(("sw_vers", "-buildVersion")),
        logical_cpus=_command_positive_int(("sysctl", "-n", "hw.logicalcpu")),
        memory_bytes=_command_positive_int(("sysctl", "-n", "hw.memsize")),
        root_free_bytes=root_free_bytes,
        root_internal=internal,
        root_solid_state=solid_state,
        filesystem=filesystem,
        executing_as_root=executing_as_root,
        missing_tools=missing_tools,
    )


def _major_version(value: str | None) -> int | None:
    if value is None:
        return None
    major = value.partition(".")[0]
    return int(major) if major.isdecimal() else None


def evaluate_host_facts(
    facts: MacOSHostFacts,
    *,
    profile: str,
    expected_architecture: str,
    expected_major: int,
    expected_os_version: str | None,
    dedicated_clean_user_attested: bool,
) -> dict[str, Any]:
    """Evaluate facts against preparation and native-acceptance prerequisites."""

    if profile not in PROFILES:
        raise ValueError(f"unsupported macOS preflight profile: {profile}")
    if expected_architecture not in MACOS_ARCHITECTURES:
        raise ValueError(f"unsupported macOS architecture: {expected_architecture}")
    if expected_major <= 0:
        raise ValueError("expected macOS major version must be positive")

    common_reasons: list[str] = []
    if facts.system != "Darwin":
        common_reasons.append("macos_required")
    if facts.architecture != expected_architecture:
        common_reasons.append("native_architecture_mismatch")
    if facts.rosetta_translated is not False:
        common_reasons.append("rosetta_translation_rejected")
    if _major_version(facts.os_version) != expected_major:
        common_reasons.append("macos_major_version_mismatch")
    if facts.os_build is None:
        common_reasons.append("macos_build_unreadable")
    if facts.missing_tools:
        common_reasons.append("native_tools_missing")

    native_reasons = list(common_reasons)
    if expected_os_version is None:
        native_reasons.append("exact_macos_version_not_frozen")
    elif facts.os_version != expected_os_version:
        native_reasons.append("exact_macos_version_mismatch")
    if facts.logical_cpus is None or facts.logical_cpus < MIN_LOGICAL_CPUS:
        native_reasons.append("four_logical_cpus_required")
    if facts.memory_bytes is None or facts.memory_bytes < MIN_MEMORY_BYTES:
        native_reasons.append("eight_gib_memory_required")
    if facts.root_free_bytes is None or facts.root_free_bytes <= MIN_ROOT_FREE_BYTES_EXCLUSIVE:
        native_reasons.append("more_than_sixteen_gib_root_free_required")
    if facts.root_internal is not True:
        native_reasons.append("internal_root_storage_required")
    if facts.root_solid_state is not True:
        native_reasons.append("solid_state_root_storage_required")
    if facts.executing_as_root:
        native_reasons.append("root_execution_rejected")
    if not dedicated_clean_user_attested:
        native_reasons.append("dedicated_clean_user_attestation_required")

    active_reasons = native_reasons if profile == NATIVE_ACCEPTANCE_PROFILE else common_reasons
    report = {
        "schema_version": 1,
        "kind": "macos_acceptance_preflight",
        "content_free": True,
        "acceptance_claimed": False,
        "preparation_only": True,
        "profile": profile,
        "status": "pass" if not active_reasons else "unavailable",
        "reason_codes": sorted(set(active_reasons)),
        "expected_architecture": expected_architecture,
        "expected_macos_major": expected_major,
        "expected_macos_version": expected_os_version,
        "dedicated_clean_user_attested": dedicated_clean_user_attested,
        "native_acceptance_eligible": not native_reasons,
        "native_acceptance_reason_codes": sorted(set(native_reasons)),
        "minimums": {
            "logical_cpus": MIN_LOGICAL_CPUS,
            "memory_bytes": MIN_MEMORY_BYTES,
            "root_free_bytes_exclusive": MIN_ROOT_FREE_BYTES_EXCLUSIVE,
            "internal_root_storage": True,
            "solid_state_root_storage": True,
        },
        "observed": asdict(facts),
    }
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Atomically create one preflight report without replacing existing evidence."""

    destination = path.expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to replace existing preflight report: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.atc-new")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--expected-architecture", choices=MACOS_ARCHITECTURES, required=True)
    parser.add_argument("--expected-major", type=int, default=26)
    parser.add_argument("--expected-os-version")
    parser.add_argument("--dedicated-clean-user-attested", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.profile == NATIVE_ACCEPTANCE_PROFILE and arguments.expected_os_version is None:
        parser.error("--expected-os-version is required for native-acceptance")

    report = evaluate_host_facts(
        collect_host_facts(),
        profile=arguments.profile,
        expected_architecture=arguments.expected_architecture,
        expected_major=arguments.expected_major,
        expected_os_version=arguments.expected_os_version,
        dedicated_clean_user_attested=arguments.dedicated_clean_user_attested,
    )
    write_report(arguments.output, report)
    print(
        json.dumps(
            {
                "kind": report["kind"],
                "profile": report["profile"],
                "status": report["status"],
                "acceptance_claimed": False,
                "native_acceptance_eligible": report["native_acceptance_eligible"],
                "reason_codes": report["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

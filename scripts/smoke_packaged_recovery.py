"""Smoke packaged recovery/admin modes from built bytes only.

Fail closed when the platform-required console recovery surface is missing.
Windows/macOS require the version-matched console helper; Linux uses the
console-capable main desktop binary. Do not fall back to source checkout or a
windowed-only desktop binary: those soft-passes cannot prove a candidate.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "desktop"
BUILD = ROOT / "build" / "desktop"


def recovery_command(system: str) -> tuple[list[str], str]:
    """Return (command_prefix, mode) for the operator-reachable recovery surface.

    Raises SystemExit when the frozen console surface is absent.
    """

    if system == "Windows":
        candidates = (
            (DIST / "AllTheContextRecovery.exe", "frozen-console-recovery-helper"),
            (
                BUILD / "recovery-helper-dist" / "AllTheContextRecovery.exe",
                "frozen-staged-console-recovery-helper",
            ),
        )
        for path, mode in candidates:
            if path.is_file():
                return [str(path)], mode
        raise SystemExit(
            "Windows console recovery helper missing; expected dist/desktop/"
            "AllTheContextRecovery.exe or build/desktop/recovery-helper-dist/"
            "AllTheContextRecovery.exe (embedded in AllTheContextSetup.exe for install)"
        )

    if system == "Darwin":
        app = DIST / "AllTheContext.app"
        candidates = (
            (
                app / "Contents" / "MacOS" / "all-the-context-recovery",
                "frozen-console-recovery-helper",
            ),
            (
                app / "Contents" / "Frameworks" / "all-the-context-recovery",
                "frozen-console-recovery-helper",
            ),
            (
                BUILD / "recovery-helper-dist" / "all-the-context-recovery",
                "frozen-staged-console-recovery-helper",
            ),
        )
        for path, mode in candidates:
            if path.is_file():
                return [str(path)], mode
        raise SystemExit(
            "macOS console recovery helper missing; expected all-the-context-recovery "
            "inside AllTheContext.app (Contents/MacOS or Contents/Frameworks) or "
            "build/desktop/recovery-helper-dist/"
        )

    linux = DIST / "all-the-context"
    if linux.is_file():
        return [str(linux)], "frozen-linux-console-desktop"
    raise SystemExit(
        "Linux console recovery surface missing; expected dist/desktop/all-the-context"
    )


def _require_help_output(command_prefix: list[str], mode: str) -> None:
    help_proc = subprocess.run(
        [*command_prefix, "--recovery-help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(ROOT),
    )
    combined = f"{help_proc.stdout}\n{help_proc.stderr}"
    if help_proc.returncode != 0 or "recovery" not in combined.casefold():
        raise SystemExit(
            f"recovery help failed ({mode}): rc={help_proc.returncode} "
            f"stdout={help_proc.stdout!r} stderr={help_proc.stderr!r}"
        )
    if not help_proc.stdout.strip():
        # Windowed PE may still inherit captured pipes in CI; require real stdout text
        # so operator-reachable console helpers cannot silent-pass.
        raise SystemExit(
            f"recovery help produced empty stdout ({mode}); console recovery helper required"
        )


def _require_doctor(command_prefix: list[str], mode: str, data_dir: Path) -> None:
    doctor_proc = subprocess.run(
        [
            *command_prefix,
            "--recovery-doctor",
            "--recovery-data-dir",
            str(data_dir),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(ROOT),
    )
    if doctor_proc.returncode != 0:
        raise SystemExit(
            f"recovery doctor failed ({mode}): rc={doctor_proc.returncode} "
            f"stdout={doctor_proc.stdout!r} stderr={doctor_proc.stderr!r}"
        )


def main() -> int:
    system = platform.system()
    command_prefix, mode = recovery_command(system)
    if "windowed" in mode or mode == "source-desktop-mode":
        raise SystemExit(f"refusing non-console recovery surface: {mode}")

    _require_help_output(command_prefix, mode)

    with tempfile.TemporaryDirectory(prefix="atc-recovery-smoke-") as temporary:
        data_dir = Path(temporary) / "data"
        data_dir.mkdir()
        from allthecontext.models import CandidateInput
        from allthecontext.storage import CoreStore

        store = CoreStore(data_dir / "core.sqlite3")
        store.initialize_vault("Fiction smoke vault")
        observation = store.add_candidate(
            CandidateInput(
                kind="fact",
                content="Fiction recovery smoke observation.",
                explicit_user_statement=True,
            )
        )
        assert observation.record_id is not None
        _require_doctor(command_prefix, mode, data_dir)

        passphrase = "fiction-smoke-recovery-passphrase"
        env = os.environ.copy()
        env["ATC_EXPORT_PASSPHRASE"] = passphrase
        export_path = Path(temporary) / "export.atcexp"
        export_proc = subprocess.run(
            [
                *command_prefix,
                "--recovery-export",
                str(export_path),
                "--recovery-data-dir",
                str(data_dir),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
        if export_proc.returncode != 0 or not export_path.is_file():
            raise SystemExit(
                f"recovery export failed: {export_proc.returncode} {export_proc.stderr}"
            )
        isolated = Path(temporary) / "isolated"
        restore_proc = subprocess.run(
            [
                *command_prefix,
                "--recovery-restore",
                str(export_path),
                "--recovery-data-dir",
                str(data_dir),
                "--recovery-destination",
                str(isolated),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
        if restore_proc.returncode != 0 or not (isolated / "core.sqlite3").is_file():
            raise SystemExit(
                f"recovery restore failed: {restore_proc.returncode} {restore_proc.stderr}"
            )
        restore_payload = json.loads(restore_proc.stdout)
        if restore_payload.get("integrity") != "verified":
            raise SystemExit(f"restore integrity not verified: {restore_payload}")
        purge_proc = subprocess.run(
            [
                *command_prefix,
                "--recovery-purge",
                "record",
                observation.record_id,
                "--recovery-confirmation",
                f"PURGE RECORD {observation.record_id}",
                "--recovery-data-dir",
                str(data_dir),
                "--recovery-no-compact",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
        if purge_proc.returncode != 0:
            raise SystemExit(f"recovery purge failed: {purge_proc.returncode} {purge_proc.stderr}")

    print(
        json.dumps(
            {
                "recovery_smoke": "passed",
                "mode": mode,
                "help": "passed",
                "doctor": "passed",
                "export_restore_purge": "passed",
                "integrity": "verified",
                "python_checkout_required": False,
                "console_helper_required": True,
                "beta_d03_acceptance": "not_claimed",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

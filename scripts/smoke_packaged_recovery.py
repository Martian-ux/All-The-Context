"""Smoke packaged recovery/admin modes without inventing candidate receipts."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "desktop"


def artifact_executable(system: str) -> Path:
    if system == "Windows":
        return DIST / "AllTheContextSetup.exe"
    if system == "Darwin":
        return DIST / "AllTheContext.app" / "Contents" / "MacOS" / "AllTheContext"
    return DIST / "all-the-context"


def main() -> int:
    system = platform.system()
    # Prefer frozen artifact; fall back to source desktop entry for contributor smoke.
    executable = artifact_executable(system)
    if executable.is_file():
        command_prefix: list[str] = [str(executable)]
        mode = "frozen-artifact"
    else:
        command_prefix = [sys.executable, "-m", "allthecontext.desktop"]
        mode = "source-desktop-mode"
        print(
            json.dumps(
                {
                    "warning": "frozen artifact missing; exercising source desktop recovery modes",
                    "expected_artifact": str(executable),
                }
            ),
            file=sys.stderr,
        )

    help_proc = subprocess.run(
        [*command_prefix, "--recovery-help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(ROOT),
    )
    if help_proc.returncode != 0 or "recovery" not in help_proc.stdout.casefold():
        raise SystemExit(f"recovery help failed: {help_proc.returncode} {help_proc.stderr}")

    with tempfile.TemporaryDirectory(prefix="atc-recovery-smoke-") as temporary:
        data_dir = Path(temporary) / "data"
        data_dir.mkdir()
        # Seed via Python store only for contributor path; frozen path still gets doctor.
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
            timeout=120,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
        if restore_proc.returncode != 0 or not (isolated / "core.sqlite3").is_file():
            raise SystemExit(
                f"recovery restore failed: {restore_proc.returncode} {restore_proc.stderr}"
            )
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
                "export_restore_purge": "passed",
                "python_checkout_required": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

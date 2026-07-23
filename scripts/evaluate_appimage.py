"""Record the evidence-based AppImage decision for the Linux beta package."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def evaluate_appimage(tool: Path | None = None) -> dict[str, Any]:
    candidate = tool
    if candidate is None:
        discovered = shutil.which("appimagetool")
        candidate = Path(discovered) if discovered else None

    tool_status = "not-installed"
    tool_version: str | None = None
    if candidate is not None and candidate.is_file():
        completed = subprocess.run(
            [str(candidate), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if completed.returncode == 0:
            tool_status = "available-unpinned-native-tool"
            first_line = (completed.stdout or completed.stderr).strip().splitlines()
            tool_version = first_line[0][:160] if first_line else "version-not-reported"
        else:
            tool_status = "present-but-not-runnable"

    blockers = [
        "appimagetool is not a Python build dependency and would add an architecture-specific "
        "native supply-chain input",
        "the native builder is not pinned, checksummed, or covered by this repository's "
        "provenance yet",
        "AppImage desktop integration still requires separate acceptance across current Linux "
        "desktop environments",
    ]
    if tool_status == "not-installed":
        blockers.insert(0, "appimagetool is absent from the clean beta CI toolchain")
    elif tool_status == "present-but-not-runnable":
        blockers.insert(0, "the discovered appimagetool cannot run in the clean build environment")

    return {
        "schema_version": 1,
        "decision": "portable-tar-gzip-fallback",
        "appimage_status": tool_status,
        "appimagetool_version": tool_version,
        "blockers": blockers,
        "fallback_properties": {
            "builder": "python-standard-library",
            "native_build_dependency": False,
            "shell_launcher_required": False,
            "core_security_depends_on_posix_modes": False,
            "archive_executable_mode_is_packaging_metadata_only": True,
        },
        "revisit_when": [
            "a pinned appimagetool digest is reviewed and provenance-covered",
            "AppRun can remain the frozen executable rather than a shell script",
            "install, MCP, startup, update, and cleanup pass on supported Linux desktops",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appimagetool", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = evaluate_appimage(arguments.appimagetool)
    target = arguments.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Exercise the frozen desktop artifact without changing user configuration."""

from __future__ import annotations

import json
import platform
import subprocess
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
    executable = artifact_executable(system)
    if not executable.is_file():
        raise SystemExit(f"desktop artifact is missing: {executable}")
    report = DIST / "diagnostics.json"
    subprocess.run([str(executable), "--diagnostics", str(report)], check=True, timeout=60)
    payload = json.loads(report.read_text(encoding="utf-8"))
    expected = {
        "frozen": True,
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
    if system == "Windows" and not payload.get("update_helper_bundled"):
        raise SystemExit(f"the Windows build is missing its recovery helper: {payload}")
    if payload.get("core_migrations", 0) < 1 or payload.get("relay_migrations", 0) < 1:
        raise SystemExit(f"migrations were not bundled: {payload}")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

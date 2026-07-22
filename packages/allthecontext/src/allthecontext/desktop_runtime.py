"""Runtime command discovery shared by the desktop app, MCP, and startup setup."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


def mcp_helper_name() -> str:
    return "AllTheContextMCP.exe" if sys.platform == "win32" else "all-the-context-mcp"


def _packaged_mcp_helper(executable: Path) -> Path | None:
    configured = os.environ.get("ATC_MCP_EXECUTABLE")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if candidate.is_file() else None

    # Linux desktop builds retain a console-capable executable, so the app can
    # serve STDIO directly without pointing clients at a one-file extraction path.
    if sys.platform.startswith("linux"):
        return None

    sibling = executable.with_name(mcp_helper_name())
    versioned_pattern = f"{sibling.stem}-*{sibling.suffix}"
    sibling_candidates = [
        candidate
        for candidate in (sibling, *executable.parent.glob(versioned_pattern))
        if candidate.is_file()
    ]
    if sibling_candidates:
        return max(sibling_candidates, key=lambda candidate: candidate.stat().st_mtime_ns)

    bundle_root_value = getattr(sys, "_MEIPASS", None)
    if bundle_root_value:
        candidate = Path(bundle_root_value).resolve() / mcp_helper_name()
        if candidate.is_file():
            return candidate

    data_helper = (
        Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
        / "bin"
        / mcp_helper_name()
    )
    return data_helper if data_helper.is_file() else None


@dataclass(frozen=True, slots=True)
class RuntimeCommand:
    executable: Path
    base_args: tuple[str, ...] = ()
    mcp_executable: Path | None = None

    @classmethod
    def current(cls) -> RuntimeCommand:
        executable = Path(sys.executable).resolve()
        if getattr(sys, "frozen", False):
            return cls(executable, mcp_executable=_packaged_mcp_helper(executable))
        return cls(executable, ("-m", "allthecontext.desktop"))

    def mode(self, argument: str) -> tuple[str, ...]:
        return (str(self.executable), *self.base_args, argument)

    def mcp(self) -> tuple[str, ...]:
        if self.mcp_executable is not None:
            return (str(self.mcp_executable),)
        return self.mode("--mcp-stdio")

    def core(self) -> tuple[str, ...]:
        return self.mode("--core")

    def setup(self) -> tuple[str, ...]:
        return self.mode("--setup")

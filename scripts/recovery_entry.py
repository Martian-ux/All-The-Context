"""PyInstaller entry point for the console-subsystem recovery/admin helper."""

from __future__ import annotations

import sys

from allthecontext.desktop import main


def _argv_with_default_help(argv: list[str]) -> list[str]:
    """Default to recovery help so a bare helper launch is operator-reachable."""

    if any(
        argument == "--recovery-help" or argument.startswith("--recovery-") for argument in argv
    ):
        return argv
    return ["--recovery-help", *argv]


if __name__ == "__main__":
    raise SystemExit(main(_argv_with_default_help(sys.argv[1:])))

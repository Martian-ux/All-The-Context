"""Install Python dependencies from the reviewed uv.lock rather than broad ranges."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="optional project extras to install (for example dev or packaging)",
    )
    parser.add_argument(
        "--skip-project",
        action="store_true",
        help="install only locked third-party constraints without the local project",
    )
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    lock = root / "uv.lock"
    if not lock.is_file():
        print("install_locked_python error: uv.lock is missing", file=sys.stderr)
        return 1
    uv = shutil.which("uv")
    python = sys.executable
    try:
        if uv is None:
            subprocess.run(
                [python, "-m", "pip", "install", "--upgrade", "pip", "uv"],
                check=True,
            )
            uv = shutil.which("uv") or str(Path(python).with_name("uv"))
        with tempfile.TemporaryDirectory(prefix="atc-locked-") as temporary:
            constraints = Path(temporary) / "constraints.txt"
            # Export frozen versions without hashes so editable project installs work.
            export_command = [
                uv,
                "export",
                "--frozen",
                "--no-emit-project",
                "--no-hashes",
                "--output-file",
                str(constraints),
            ]
            if not arguments.extra:
                export_command.append("--no-dev")
            for extra in arguments.extra:
                export_command.extend(["--extra", extra])
            subprocess.run(
                export_command,
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            if arguments.skip_project:
                subprocess.run(
                    [python, "-m", "pip", "install", "-r", str(constraints)],
                    cwd=root,
                    check=True,
                )
            else:
                extras = ""
                if arguments.extra:
                    extras = "[" + ",".join(arguments.extra) + "]"
                subprocess.run(
                    [
                        python,
                        "-m",
                        "pip",
                        "install",
                        "-e",
                        f".{extras}",
                        "-c",
                        str(constraints),
                    ],
                    cwd=root,
                    check=True,
                )
        print("installed Python dependencies from reviewed uv.lock constraints")
        return 0
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"install_locked_python error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

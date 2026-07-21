"""Build the dashboard and optionally copy its artifacts into the Python package."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = REPOSITORY_ROOT / "apps" / "dashboard"
PACKAGE_WEB_ROOT = REPOSITORY_ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "web"


def build(*, copy_to_package: bool) -> None:
    """Run the platform-native npm executable and copy only on request."""
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is required to build the dashboard")
    subprocess.run([npm, "ci"], cwd=DASHBOARD_ROOT, check=True)
    subprocess.run([npm, "run", "build"], cwd=DASHBOARD_ROOT, check=True)
    if copy_to_package:
        output = DASHBOARD_ROOT / "dist"
        if not output.is_dir():
            raise SystemExit("dashboard build did not create dist")
        if PACKAGE_WEB_ROOT.exists():
            shutil.rmtree(PACKAGE_WEB_ROOT)
        shutil.copytree(output, PACKAGE_WEB_ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--copy-to-package",
        action="store_true",
        help="replace generated Python package web assets after a successful build",
    )
    arguments = parser.parse_args()
    build(copy_to_package=arguments.copy_to_package)


if __name__ == "__main__":
    main()

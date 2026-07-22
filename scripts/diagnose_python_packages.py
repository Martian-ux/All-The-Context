"""Verify built Python archives contain runtime resources and no private keys."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

REQUIRED_SUFFIXES = {
    "allthecontext/migrations/core/001_initial.sql",
    "allthecontext/migrations/relay/0001_initial.sql",
    "allthecontext/web/index.html",
}
PRIVATE_KEY_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


def _names(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {name.replace("\\", "/") for name in archive.namelist()}
    with tarfile.open(path, "r:gz") as archive:
        return {name.replace("\\", "/") for name in archive.getnames()}


def diagnose(path: Path) -> None:
    names = _names(path)
    missing = [
        suffix for suffix in REQUIRED_SUFFIXES if not any(name.endswith(suffix) for name in names)
    ]
    if missing:
        raise ValueError(f"{path.name} is missing runtime resources: {sorted(missing)}")
    private = [name for name in names if Path(name).suffix.casefold() in PRIVATE_KEY_SUFFIXES]
    if private:
        raise ValueError(f"{path.name} contains private-key-like files: {sorted(private)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--directory", type=Path)
    arguments = parser.parse_args()
    paths = list(arguments.paths)
    if arguments.directory is not None:
        paths.extend(sorted(arguments.directory.glob("*.whl")))
        paths.extend(sorted(arguments.directory.glob("*.tar.gz")))
    if not paths:
        parser.error("provide package paths or --directory")
    for path in paths:
        diagnose(path)
        print(f"verified package resources: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

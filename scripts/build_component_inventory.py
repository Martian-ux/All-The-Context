"""Produce component/license inventory and notices from reviewed locks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from allthecontext.component_inventory import (
    INVENTORY_FILE_NAME,
    build_component_inventory,
    write_component_inventory,
    write_notices,
)
from allthecontext.release_manifest import ManifestError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        source_commit = arguments.source_commit
        if source_commit is None:
            source_commit = subprocess.run(
                ["git", "-C", str(arguments.repository_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        inventory = build_component_inventory(
            arguments.repository_root,
            source_commit=source_commit,
            version=arguments.version,
        )
        output_dir = arguments.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        inventory_path = write_component_inventory(output_dir / INVENTORY_FILE_NAME, inventory)
        notices_path = write_notices(output_dir / "NOTICES.txt", inventory)
        print(inventory_path)
        print(inventory_path.with_name(f"{inventory_path.name}.sha256"))
        print(notices_path)
        print(f"components={inventory['component_count']}")
        return 0
    except (ManifestError, OSError, subprocess.SubprocessError) as exc:
        print(f"component inventory error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

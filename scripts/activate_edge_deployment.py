"""Activate reviewed Edge defaults after a pinned Blueprint branch is public."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "packages" / "allthecontext" / "src"
DEFAULTS_PATH = SOURCE_ROOT / "allthecontext" / "edge_deployment_defaults.py"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from allthecontext.edge_activation import activate_edge_deployment  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--blueprint-commit", required=True)
    parser.add_argument(
        "--repository-url",
        default="https://github.com/Martian-ux/All-The-Context",
    )
    parser.add_argument("--defaults-output", type=Path, default=DEFAULTS_PATH)
    args = parser.parse_args()
    result = activate_edge_deployment(
        repository=args.repository,
        metadata_path=args.metadata,
        blueprint_commit=args.blueprint_commit,
        repository_url=args.repository_url,
        defaults_output=args.defaults_output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

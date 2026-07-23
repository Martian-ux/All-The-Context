"""Verify that an exact hosted Edge image is anonymously retrievable from GHCR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "packages" / "allthecontext" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from allthecontext.edge_registry import verify_anonymous_ghcr_pull  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()
    result = verify_anonymous_ghcr_pull(
        args.image_reference,
        timeout_seconds=max(1.0, min(args.timeout_seconds, 60.0)),
    )
    print(json.dumps(result.mapping(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

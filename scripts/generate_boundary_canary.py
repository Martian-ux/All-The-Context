#!/usr/bin/env python3
"""Generate or verify the deterministic raw-import boundary canary.

Exact ``2_000_000_000``-byte candidate acceptance remains operator-controlled
(B-204). This script materializes the stable generator contract for local
acceptance work without reading personal exports.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from allthecontext.boundary_canary import (
    BOUNDARY_CANARY_SIZE_BYTES,
    boundary_canary_spec,
    verify_boundary_canary_file,
    write_boundary_canary,
)
from allthecontext.config import MAX_IMPORT_BYTES
from allthecontext.import_boundary import BOUNDARY_PLUS_ONE_BYTES, scale_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        help="destination path for the canary file",
    )
    parser.add_argument(
        "--size-bytes",
        type=int,
        default=BOUNDARY_CANARY_SIZE_BYTES,
        help=f"logical size (default {BOUNDARY_CANARY_SIZE_BYTES}; max {MAX_IMPORT_BYTES + 1})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing canary path",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify an existing canary without rewriting it",
    )
    parser.add_argument(
        "--spec-only",
        action="store_true",
        help="print the deterministic spec (streams the size once for SHA-256)",
    )
    args = parser.parse_args(argv)

    if args.size_bytes < 0 or args.size_bytes > MAX_IMPORT_BYTES + 1:
        print(
            f"size-bytes must be between 0 and {MAX_IMPORT_BYTES + 1}",
            file=sys.stderr,
        )
        return 2

    if args.spec_only:
        spec = boundary_canary_spec(args.size_bytes)
        print(
            json.dumps(
                {
                    "spec": spec.as_dict(),
                    "scale_profile": scale_profile(),
                    "boundary_plus_one_bytes": BOUNDARY_PLUS_ONE_BYTES,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.verify_only:
        spec = verify_boundary_canary_file(args.path, size_bytes=args.size_bytes)
    else:
        spec = write_boundary_canary(
            args.path,
            size_bytes=args.size_bytes,
            overwrite=args.overwrite,
        )
    print(json.dumps(spec.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

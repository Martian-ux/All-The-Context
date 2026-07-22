"""Prepare and verify an isolated real hosted Edge acceptance deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "packages" / "allthecontext" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from allthecontext.edge_acceptance import (  # noqa: E402
    prepare_hosted_edge_acceptance,
    verify_hosted_edge_acceptance,
)


def _write_secret_file(path: Path, value: str) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        with resolved.open("x", encoding="utf-8", newline="\n") as destination:
            destination.write(value)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite existing secret file: {resolved}") from exc


def _prepare(workspace: Path, output_directory: Path) -> dict[str, object]:
    prepared = prepare_hosted_edge_acceptance(workspace)
    output = output_directory.expanduser().resolve()
    setup_path = output / "setup.env"
    recovery_path = output / "recovery-code.txt"
    _write_secret_file(setup_path, f"ATC_EDGE_BUNDLE={prepared.claim_bundle}\n")
    _write_secret_file(recovery_path, f"{prepared.recovery_code}\n")
    return {
        "claim_file": str(setup_path),
        "next": "Upload setup.env as the Render Blueprint environment handoff, then verify.",
        "personal_context_used": False,
        "recovery_file": str(recovery_path),
        "result": "prepared",
        "secrets_printed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--workspace", type=Path, required=True)
    prepare.add_argument("--output-directory", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--workspace", type=Path, required=True)
    verify.add_argument("--edge-url", required=True)
    verify.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()
    if args.command == "prepare":
        result = _prepare(args.workspace, args.output_directory)
    else:
        result = verify_hosted_edge_acceptance(
            args.workspace,
            args.edge_url,
            timeout_seconds=max(1.0, min(args.timeout_seconds, 60.0)),
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

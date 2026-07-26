"""Validate content-free acceptance receipts and bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from allthecontext.acceptance_receipt import (
    load_receipt,
    load_receipt_bundle,
    missing_required_gates,
    write_template_receipt,
)
from allthecontext.release_manifest import ManifestError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--receipt", type=Path, required=True)
    bundle = commands.add_parser("validate-bundle")
    bundle.add_argument("--bundle", type=Path, required=True)
    bundle.add_argument(
        "--require-publication-gates",
        action="store_true",
        help="require the protected-publication gate set to be pass",
    )
    template = commands.add_parser("write-template")
    template.add_argument("--output", type=Path, required=True)
    template.add_argument("--source-commit", required=True)
    template.add_argument("--gate-id", required=True)
    return root


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "validate":
            receipt = load_receipt(arguments.receipt)
            print(
                json.dumps(
                    {
                        "receipt_id": receipt["receipt_id"],
                        "gate_id": receipt["gate_id"],
                        "status": receipt["status"],
                        "ok": True,
                    },
                    sort_keys=True,
                )
            )
        elif arguments.command == "validate-bundle":
            bundle = load_receipt_bundle(arguments.bundle)
            missing = (
                missing_required_gates(bundle["receipts"])
                if arguments.require_publication_gates
                else []
            )
            if missing:
                raise ManifestError(
                    "required publication gates are not pass: " + ", ".join(missing)
                )
            print(
                json.dumps(
                    {
                        "receipt_count": len(bundle["receipts"]),
                        "source_commit": bundle["source_commit"],
                        "candidate_sha256": bundle["candidate_sha256"],
                        "decision": bundle["maintainer_decision"].get("decision"),
                        "ok": True,
                    },
                    sort_keys=True,
                )
            )
        else:
            write_template_receipt(
                arguments.output,
                source_commit=arguments.source_commit,
                gate_id=arguments.gate_id,
            )
            # Re-load to prove the template validates as not_run.
            receipt = load_receipt(arguments.output)
            if receipt["status"] != "not_run":
                raise ManifestError("template receipt must remain not_run")
            print(arguments.output)
        return 0
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"acceptance receipt error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

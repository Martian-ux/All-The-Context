"""Fail-closed protected publication preflight CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from allthecontext.publication_gate import evaluate_publication_gate, write_publication_record
from allthecontext.release_candidate import PUBLICATION_GATE_RECORD_FILE_NAME
from allthecontext.release_manifest import ManifestError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYRING = REPOSITORY_ROOT / "release" / "keys.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--receipt-bundle", type=Path, required=True)
    parser.add_argument("--keyring", type=Path, default=DEFAULT_KEYRING)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--expected-public-key-sha256", required=True)
    parser.add_argument(
        "--asset-stage",
        choices=("draft", "signed", "promotion"),
        default="promotion",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the publication record (defaults to release-dir for promotion)",
    )
    arguments = parser.parse_args()
    try:
        record = evaluate_publication_gate(
            release_dir=arguments.release_dir,
            candidate_sha256=arguments.candidate_sha256,
            source_commit=arguments.source_commit,
            receipt_bundle_path=arguments.receipt_bundle,
            keyring_path=arguments.keyring,
            key_id=arguments.key_id,
            expected_public_key_sha256=arguments.expected_public_key_sha256,
            asset_stage=arguments.asset_stage,
            persist_decision_artifacts=arguments.asset_stage == "promotion",
        )
        canonical = arguments.release_dir / PUBLICATION_GATE_RECORD_FILE_NAME
        if arguments.output is not None:
            output = arguments.output
            if output.resolve() != canonical.resolve():
                write_publication_record(output, record)
            print(output if output.exists() else canonical)
        elif canonical.is_file():
            print(canonical)
        print(json.dumps({"ok": True, "asset_count": record["asset_count"]}, sort_keys=True))
        return 0
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"publication gate error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

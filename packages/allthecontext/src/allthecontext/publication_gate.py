"""Fail-closed protected publication preflight.

Requires the reviewed candidate descriptor, exact immutable asset inventory,
required acceptance receipt set, explicit maintainer decision, and public-key
identity to agree. Key custody and live repository protection changes remain
operator work outside this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .acceptance_receipt import (
    REQUIRED_PUBLICATION_GATES,
    load_receipt_bundle,
    missing_required_gates,
)
from .release_candidate import (
    CANDIDATE_FILE_NAME,
    verify_candidate,
    verify_release_asset_set,
)
from .release_manifest import ManifestError, load_keyring, sha256_file

COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _require_active_public_key(
    keyring: Mapping[str, Any], *, key_id: str, channel: str
) -> dict[str, Any]:
    keys = keyring.get("keys")
    if not isinstance(keys, list):
        raise ManifestError("release keyring is missing keys")
    matches = [
        key
        for key in keys
        if isinstance(key, dict)
        and key.get("key_id") == key_id
        and key.get("status") == "active"
        and isinstance(key.get("channels"), list)
        and channel in key["channels"]
    ]
    if len(matches) != 1:
        raise ManifestError(
            f"publication requires exactly one active public key for {key_id!r} on {channel}"
        )
    fingerprint = matches[0].get("public_key_sha256")
    if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
        raise ManifestError("publication public-key fingerprint is malformed")
    return matches[0]


def evaluate_publication_gate(
    *,
    release_dir: Path,
    candidate_sha256: str,
    source_commit: str,
    receipt_bundle_path: Path,
    keyring_path: Path,
    key_id: str,
    expected_public_key_sha256: str,
    asset_stage: str = "promotion",
) -> dict[str, Any]:
    """Return a content-free publication decision record or raise ManifestError."""

    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("publication source_commit must be a full lowercase SHA")
    if SHA256.fullmatch(candidate_sha256) is None:
        raise ManifestError("publication candidate_sha256 must be a lowercase SHA-256 digest")
    if KEY_ID.fullmatch(key_id) is None:
        raise ManifestError("publication key_id is invalid")
    if not expected_public_key_sha256.startswith("sha256:"):
        raise ManifestError("expected public-key fingerprint must use sha256:<hex> form")

    release_dir = release_dir.resolve()
    candidate_path = release_dir / CANDIDATE_FILE_NAME
    if not candidate_path.is_file():
        raise ManifestError(
            "publication requires release-candidate-v1.json in the release directory"
        )

    candidate = verify_candidate(
        candidate_path,
        release_dir,
        expected_sha256=candidate_sha256,
    )
    if candidate.get("source_commit") != source_commit:
        raise ManifestError("candidate source_commit does not match publication input")
    channel = candidate.get("channel")
    if not isinstance(channel, str):
        raise ManifestError("candidate channel is missing")

    # Exact immutable asset inventory for the controlled stage.
    assets = verify_release_asset_set(
        candidate_path,
        release_dir,
        stage=asset_stage,
        expected_sha256=candidate_sha256,
    )
    asset_names = sorted(path.name for path in assets)

    bundle = load_receipt_bundle(receipt_bundle_path)
    if bundle["source_commit"] != source_commit:
        raise ManifestError("receipt bundle source_commit does not match publication input")
    if bundle["candidate_sha256"] != candidate_sha256:
        raise ManifestError("receipt bundle candidate_sha256 does not match publication input")

    decision = bundle.get("maintainer_decision")
    if not isinstance(decision, dict):
        raise ManifestError("receipt bundle maintainer_decision is required")
    if decision.get("decision") != "approve":
        raise ManifestError(
            "publication fails closed without an explicit maintainer approve decision"
        )
    if decision.get("independent_human_review_claimed") is True:
        raise ManifestError("publication rejects false independent-review claims")

    missing = missing_required_gates(bundle["receipts"], required_gates=REQUIRED_PUBLICATION_GATES)
    if missing:
        raise ManifestError(
            "publication fails closed; required receipt gates are not pass: " + ", ".join(missing)
        )
    # Fail closed on any not_run or fail receipt in the required set.
    for receipt in bundle["receipts"]:
        gate_id = receipt.get("gate_id")
        status = receipt.get("status")
        if gate_id in REQUIRED_PUBLICATION_GATES and status != "pass":
            raise ManifestError(f"required gate {gate_id} is not pass (status={status})")
        if status == "not_run":
            raise ManifestError(
                f"publication rejects not_run receipt {receipt.get('receipt_id')}; "
                "do not claim evidence that has not executed"
            )

    keyring = load_keyring(keyring_path)
    public_key = _require_active_public_key(keyring, key_id=key_id, channel=channel)
    if public_key.get("public_key_sha256") != expected_public_key_sha256:
        raise ManifestError(
            "publication public-key identity does not match the reviewed fingerprint"
        )

    actual_digest, _ = sha256_file(candidate_path)
    if actual_digest != candidate_sha256:
        raise ManifestError("candidate digest changed during publication gate evaluation")

    return {
        "schema_version": 1,
        "ok": True,
        "source_commit": source_commit,
        "candidate_sha256": candidate_sha256,
        "channel": channel,
        "version": candidate.get("version"),
        "asset_stage": asset_stage,
        "asset_count": len(asset_names),
        "assets": asset_names,
        "key_id": key_id,
        "public_key_sha256": expected_public_key_sha256,
        "maintainer_approver": decision.get("approver"),
        "required_gates": sorted(REQUIRED_PUBLICATION_GATES),
        "receipt_count": len(bundle["receipts"]),
    }


def write_publication_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace publication record: {path.name}")
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

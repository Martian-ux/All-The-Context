"""Fail-closed protected publication preflight.

Requires the reviewed candidate descriptor, exact immutable asset inventory,
required acceptance receipt set, explicit maintainer decision, and public-key
identity to agree. Key custody and live repository protection changes remain
operator work outside this module.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .acceptance_receipt import (
    CERTIFICATION_PUBLICATION_POLICY,
    LEAN_PUBLIC_BETA_POLICY,
    POST_PUBLICATION_GATES,
    RECEIPT_BUNDLE_FILE_NAME,
    candidate_inventory_digests,
    load_receipt_bundle,
    missing_required_gates,
    publication_gates_for_policy,
    recompute_receipt_artifact_bindings,
    validate_receipt_bundle,
)
from .exact_source_gate import load_matrix_evidence
from .release_candidate import (
    CANDIDATE_FILE_NAME,
    MATRIX_EVIDENCE_FILE_NAME,
    PUBLICATION_GATE_RECORD_FILE_NAME,
    expected_release_asset_names,
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
    persist_decision_artifacts: bool = True,
) -> dict[str, Any]:
    """Return a content-free publication decision record or raise ManifestError.

    When ``persist_decision_artifacts`` is true and ``asset_stage`` is
    ``promotion``, the validated receipt bundle and publication record are
    written into ``release_dir`` under their canonical names so they become
    part of the immutable promotion asset set. The record binds the exact
    candidate digest and receipt-bundle digest; it never embeds private keys
    or sensitive evidence.
    """

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

    # Pre-decision inventory must already match the signed promotion set minus
    # the two decision artifacts we are about to persist.
    if asset_stage == "promotion" and persist_decision_artifacts:
        pre_stage = "signed"
        verify_release_asset_set(
            candidate_path,
            release_dir,
            stage=pre_stage,
            expected_sha256=candidate_sha256,
        )
    else:
        verify_release_asset_set(
            candidate_path,
            release_dir,
            stage=asset_stage,
            expected_sha256=candidate_sha256,
        )

    bundle = load_receipt_bundle(receipt_bundle_path)
    if bundle["source_commit"] != source_commit:
        raise ManifestError("receipt bundle source_commit does not match publication input")
    if bundle["candidate_sha256"] != candidate_sha256:
        raise ManifestError("receipt bundle candidate_sha256 does not match publication input")

    publication_policy = str(bundle.get("publication_policy", CERTIFICATION_PUBLICATION_POLICY))
    required_gate_ids = set(publication_gates_for_policy(publication_policy))
    gate_ids = [str(receipt["gate_id"]) for receipt in bundle["receipts"]]
    # Preserve the more useful sequencing diagnostic before the generic exact-set
    # check. These gates can exist only after immutable publication.
    for gate_id in gate_ids:
        if gate_id in POST_PUBLICATION_GATES:
            raise ManifestError(
                f"publication rejects post-publication gate {gate_id} before release"
            )
    unexpected = sorted(set(gate_ids) - required_gate_ids)
    missing_gate_ids = sorted(required_gate_ids - set(gate_ids))
    if unexpected or missing_gate_ids or len(gate_ids) != len(required_gate_ids):
        details: list[str] = []
        if missing_gate_ids:
            details.append("missing=" + ",".join(missing_gate_ids))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        if len(gate_ids) != len(required_gate_ids):
            details.append(f"receipt_count={len(gate_ids)}")
        raise ManifestError(
            "publication receipt gate IDs must equal the exact required set: " + "; ".join(details)
        )

    # Candidate inventory already verified; recompute matrix evidence identity.
    load_matrix_evidence(release_dir / MATRIX_EVIDENCE_FILE_NAME, source_commit=source_commit)
    inventory_digests = candidate_inventory_digests(candidate)
    recompute_receipt_artifact_bindings(
        bundle["receipts"],
        inventory_digests=inventory_digests,
        candidate_sha256=candidate_sha256,
    )

    decision = bundle.get("maintainer_decision")
    if not isinstance(decision, dict):
        raise ManifestError("receipt bundle maintainer_decision is required")
    if decision.get("decision") != "approve":
        raise ManifestError(
            "publication fails closed without an explicit maintainer approve decision"
        )
    if decision.get("independent_human_review_claimed") is not False:
        raise ManifestError("publication rejects false independent-review claims")
    if publication_policy == LEAN_PUBLIC_BETA_POLICY:
        acknowledgements = bundle.get("lean_beta_acknowledgements")
        if not isinstance(acknowledgements, dict) or not all(
            value is True for value in acknowledgements.values()
        ):
            raise ManifestError(
                "publication requires every lean public-beta acknowledgement to be true"
            )

    missing = missing_required_gates(
        bundle["receipts"],
        required_gates=required_gate_ids,
        inventory_digests=inventory_digests,
    )
    if missing:
        raise ManifestError(
            "publication fails closed; required receipt gates are not pass: " + ", ".join(missing)
        )
    for receipt in bundle["receipts"]:
        gate_id = receipt.get("gate_id")
        status = receipt.get("status")
        if gate_id in required_gate_ids and status != "pass":
            raise ManifestError(f"required gate {gate_id} is not pass (status={status})")
        if status in {"not_run", "skipped", "unavailable", "fail"}:
            raise ManifestError(
                f"publication rejects non-pass receipt {receipt.get('receipt_id')} "
                f"(status={status}); do not claim incomplete evidence"
            )
        if status == "pass" and receipt.get("candidate_sha256") != candidate_sha256:
            raise ManifestError(
                f"pass receipt {receipt.get('receipt_id')} is not bound to the candidate digest"
            )
        if status == "pass" and receipt.get("source_commit") != source_commit:
            raise ManifestError(
                f"pass receipt {receipt.get('receipt_id')} is not bound to the source commit"
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

    receipt_ids = sorted(
        str(receipt["receipt_id"])
        for receipt in bundle["receipts"]
        if isinstance(receipt.get("receipt_id"), str)
    )
    expected_assets = sorted(expected_release_asset_names(candidate, stage=asset_stage))
    record: dict[str, Any] = {
        "schema_version": 1,
        "ok": True,
        "source_commit": source_commit,
        "candidate_sha256": candidate_sha256,
        "channel": channel,
        "version": candidate.get("version"),
        "asset_stage": asset_stage,
        "asset_count": len(expected_assets),
        "assets": expected_assets,
        "key_id": key_id,
        "public_key_sha256": expected_public_key_sha256,
        "maintainer_approver": decision.get("approver"),
        "independent_human_review_claimed": False,
        "publication_policy": publication_policy,
        "required_gates": sorted(required_gate_ids),
        "receipt_count": len(bundle["receipts"]),
        "reviewed_receipt_ids": receipt_ids,
        "receipt_bundle_name": RECEIPT_BUNDLE_FILE_NAME,
        "publication_record_name": PUBLICATION_GATE_RECORD_FILE_NAME,
    }
    if publication_policy == LEAN_PUBLIC_BETA_POLICY:
        record["lean_beta_acknowledgements"] = bundle["lean_beta_acknowledgements"]

    if persist_decision_artifacts and asset_stage == "promotion":
        bundle_destination = release_dir / RECEIPT_BUNDLE_FILE_NAME
        record_destination = release_dir / PUBLICATION_GATE_RECORD_FILE_NAME
        if receipt_bundle_path.resolve() != bundle_destination.resolve():
            if bundle_destination.exists():
                raise ManifestError(f"refusing to replace {bundle_destination.name}")
            shutil.copyfile(receipt_bundle_path, bundle_destination)
        validate_receipt_bundle(json.loads(bundle_destination.read_text(encoding="utf-8")))
        bundle_digest, bundle_size = sha256_file(bundle_destination)
        record["receipt_bundle_sha256"] = bundle_digest
        record["receipt_bundle_size"] = bundle_size
        write_publication_record(record_destination, record)
        record_digest, record_size = sha256_file(record_destination)
        # Bind the record digest in the return value only; the on-disk record is
        # content-free and digest-stable without embedding its own hash.
        record = dict(record)
        record["publication_record_sha256"] = record_digest
        record["publication_record_size"] = record_size

    assets = verify_release_asset_set(
        candidate_path,
        release_dir,
        stage=asset_stage,
        expected_sha256=candidate_sha256,
    )
    asset_names = sorted(path.name for path in assets)
    if asset_names != expected_assets:
        raise ManifestError("publication asset inventory drifted during gate evaluation")
    return record


def write_publication_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace publication record: {path.name}")
    # Never persist self-referential digests that would force a rewrite loop.
    serializable = {
        key: value
        for key, value in record.items()
        if key not in {"publication_record_sha256", "publication_record_size"}
    }
    path.write_text(
        json.dumps(serializable, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

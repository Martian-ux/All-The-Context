"""Machine-checkable, content-free acceptance receipt validation.

Receipts never embed conversation text, credentials, full local paths, or raw
exports. They distinguish source evidence, exact downloaded-artifact evidence,
skipped/unavailable evidence, severity, limitations, and maintainer decisions.
A receipt with status ``not_run`` is never treated as pass.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from .release_manifest import ManifestError, sha256_file

RECEIPT_SCHEMA_VERSION = 1
RECEIPT_BUNDLE_FILE_NAME = "acceptance-receipt-bundle-v1.json"
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
GATE_ID = re.compile(r"^BETA-[A-Z][0-9]{2}$")
RECEIPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

EvidenceKind = Literal["source", "exact_downloaded_artifact", "skipped", "unavailable"]
ReceiptStatus = Literal["pass", "fail", "skipped", "unavailable", "not_run"]
Severity = Literal["P0", "P1", "P2", "P3"]
MaintainerDecision = Literal["approve", "reject"]

ALLOWED_EVIDENCE_KINDS = frozenset(
    {"source", "exact_downloaded_artifact", "skipped", "unavailable"}
)
ALLOWED_STATUSES = frozenset({"pass", "fail", "skipped", "unavailable", "not_run"})
ALLOWED_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
ALLOWED_DECISIONS = frozenset({"approve", "reject"})

# Gates that publication requires before an approve decision can pass.
REQUIRED_PUBLICATION_GATES = frozenset(
    {
        "BETA-R01",
        "BETA-R02",
        "BETA-R03",
        "BETA-S06",
        "BETA-O01",
    }
)

FORBIDDEN_RECEIPT_KEYS = frozenset(
    {
        "conversation",
        "conversations",
        "raw_text",
        "payload",
        "password",
        "secret",
        "token",
        "credential",
        "private_key",
        "export_body",
        "observation_text",
        "query_text",
        "full_path",
        "absolute_path",
    }
)


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON object")
    return value


def _assert_content_free(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ManifestError(f"{path} contains a non-string key")
            folded = key.casefold()
            if folded in FORBIDDEN_RECEIPT_KEYS or any(
                token in folded for token in ("password", "private_key", "raw_context")
            ):
                raise ManifestError(f"receipt field is not content-free: {path}.{key}")
            _assert_content_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_content_free(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > 8 * 1024:
            raise ManifestError(f"receipt string is unreasonably large: {path}")
        # Reject obvious absolute developer paths in free text.
        if re.search(r"(?i)(?:C:\\Users\\|/Users/[^/\s]+/|/home/[^/\s]+/)", value):
            raise ManifestError(f"receipt must not contain absolute developer paths: {path}")


def validate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(receipt)
    _assert_content_free(value)
    required = {
        "schema_version",
        "receipt_id",
        "gate_id",
        "evidence_kind",
        "status",
        "source_commit",
        "content_free",
    }
    if not required.issubset(value):
        missing = sorted(required - set(value))
        raise ManifestError(f"acceptance receipt is missing fields: {', '.join(missing)}")
    if value.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ManifestError("acceptance receipt schema_version must be 1")
    if value.get("content_free") is not True:
        raise ManifestError("acceptance receipt must declare content_free=true")
    receipt_id = value.get("receipt_id")
    gate_id = value.get("gate_id")
    evidence_kind = value.get("evidence_kind")
    status = value.get("status")
    source_commit = value.get("source_commit")
    if not isinstance(receipt_id, str) or RECEIPT_ID.fullmatch(receipt_id) is None:
        raise ManifestError("acceptance receipt_id is invalid")
    if not isinstance(gate_id, str) or GATE_ID.fullmatch(gate_id) is None:
        raise ManifestError("acceptance gate_id is invalid")
    if evidence_kind not in ALLOWED_EVIDENCE_KINDS:
        raise ManifestError("acceptance evidence_kind is invalid")
    if status not in ALLOWED_STATUSES:
        raise ManifestError("acceptance status is invalid")
    if not isinstance(source_commit, str) or COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("acceptance source_commit must be a full lowercase SHA")
    if status == "pass" and evidence_kind in {"skipped", "unavailable"}:
        raise ManifestError("skipped/unavailable evidence cannot claim pass")
    if status == "not_run":
        # Explicit non-claim; never treat as success downstream.
        pass
    severity = value.get("severity")
    if severity is not None and severity not in ALLOWED_SEVERITIES:
        raise ManifestError("acceptance severity is invalid")
    limitations = value.get("limitations", [])
    if not isinstance(limitations, list):
        raise ManifestError("acceptance limitations must be a list")
    for item in limitations:
        if not isinstance(item, dict):
            raise ManifestError("acceptance limitation entries must be objects")
        if not isinstance(item.get("id"), str) or not isinstance(item.get("summary"), str):
            raise ManifestError("acceptance limitation requires id and summary")
        if severity is None and item.get("severity") in {"P0", "P1"}:
            raise ManifestError("P0/P1 limitations require a receipt severity")
    candidate_sha256 = value.get("candidate_sha256")
    if candidate_sha256 is not None and (
        not isinstance(candidate_sha256, str) or SHA256.fullmatch(candidate_sha256) is None
    ):
        raise ManifestError("acceptance candidate_sha256 must be a lowercase SHA-256 digests")
    if (
        evidence_kind == "exact_downloaded_artifact"
        and status == "pass"
        and not isinstance(candidate_sha256, str)
    ):
        raise ManifestError("exact downloaded-artifact pass receipts require candidate_sha256")
    attempts = value.get("attempts", [])
    if not isinstance(attempts, list):
        raise ManifestError("acceptance attempts must be a list")
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ManifestError("acceptance attempt entries must be objects")
        if attempt.get("status") not in ALLOWED_STATUSES:
            raise ManifestError("acceptance attempt status is invalid")
    return value


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"acceptance receipt is not valid UTF-8 JSON: {path.name}") from exc
    return validate_receipt(_require_dict(value, "acceptance receipt"))


def validate_receipt_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(bundle)
    _assert_content_free(value)
    required = {
        "schema_version",
        "source_commit",
        "candidate_sha256",
        "receipts",
        "maintainer_decision",
    }
    if not required.issubset(value):
        missing = sorted(required - set(value))
        raise ManifestError(f"acceptance receipt bundle is missing fields: {', '.join(missing)}")
    if value.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ManifestError("acceptance receipt bundle schema_version must be 1")
    source_commit = value.get("source_commit")
    candidate_sha256 = value.get("candidate_sha256")
    if not isinstance(source_commit, str) or COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("bundle source_commit must be a full lowercase SHA")
    if not isinstance(candidate_sha256, str) or SHA256.fullmatch(candidate_sha256) is None:
        raise ManifestError("bundle candidate_sha256 must be a lowercase SHA-256 digest")
    receipts_value = value.get("receipts")
    if not isinstance(receipts_value, list) or not receipts_value:
        raise ManifestError("acceptance receipt bundle must contain receipts")
    receipts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in receipts_value:
        receipt = validate_receipt(_require_dict(item, "bundle receipt"))
        if receipt["receipt_id"] in seen_ids:
            raise ManifestError(f"duplicate receipt_id: {receipt['receipt_id']}")
        seen_ids.add(receipt["receipt_id"])
        if receipt["source_commit"] != source_commit:
            raise ManifestError("receipt source_commit does not match the bundle")
        if (
            receipt.get("candidate_sha256") is not None
            and receipt["candidate_sha256"] != candidate_sha256
        ):
            raise ManifestError("receipt candidate_sha256 does not match the bundle")
        receipts.append(receipt)
    decision = value.get("maintainer_decision")
    decision_obj = _require_dict(decision, "maintainer_decision")
    decision_value = decision_obj.get("decision")
    if decision_value not in ALLOWED_DECISIONS and decision_value is not None:
        raise ManifestError("maintainer_decision.decision must be approve, reject, or null")
    if decision_obj.get("independent_human_review_claimed") is True:
        raise ManifestError(
            "receipts must not claim independent human review under sole-maintainer governance"
        )
    approver = decision_obj.get("approver")
    if decision_value == "approve" and (not isinstance(approver, str) or not approver.strip()):
        raise ManifestError("approve decisions require a named human approver")
    value["receipts"] = receipts
    return value


def load_receipt_bundle(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"receipt bundle is not valid UTF-8 JSON: {path.name}") from exc
    return validate_receipt_bundle(_require_dict(value, "acceptance receipt bundle"))


def receipt_claims_pass(receipt: Mapping[str, Any]) -> bool:
    return receipt.get("status") == "pass"


def missing_required_gates(
    receipts: Sequence[Mapping[str, Any]],
    *,
    required_gates: Iterable[str] = REQUIRED_PUBLICATION_GATES,
) -> list[str]:
    required = set(required_gates)
    satisfied: set[str] = set()
    for receipt in receipts:
        gate_id = receipt.get("gate_id")
        if not isinstance(gate_id, str):
            continue
        if receipt_claims_pass(receipt) and receipt.get("status") != "not_run":
            satisfied.add(gate_id)
    return sorted(required - satisfied)


def write_template_receipt(path: Path, *, source_commit: str, gate_id: str) -> None:
    """Write a not_run template that cannot be mistaken for executed evidence."""

    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("template source commit must be a full lowercase SHA")
    if GATE_ID.fullmatch(gate_id) is None:
        raise ManifestError("template gate_id is invalid")
    template = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": f"template-{gate_id.casefold()}",
        "gate_id": gate_id,
        "evidence_kind": "source",
        "status": "not_run",
        "source_commit": source_commit,
        "content_free": True,
        "severity": None,
        "limitations": [],
        "attempts": [],
        "notes": "Template only. This receipt has not run and claims no evidence.",
    }
    validate_receipt(template)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace receipt template: {path.name}")
    path.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest_file(path: Path) -> str:
    digest, _ = sha256_file(path)
    return digest

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
SAFE_ARTIFACT_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
COUNT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
LIMITATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Short operational notes only; not a free-form personal-context dump.
NOTES_MAX_LENGTH = 280
SUMMARY_MAX_LENGTH = 500
WORKAROUND_MAX_LENGTH = 500
FOLLOW_UP_MAX_LENGTH = 200
CLOSED_REASON_MAX_LENGTH = 200
APPROVER_MAX_LENGTH = 200
DECISION_NOTES_MAX_LENGTH = 500

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

# Pre-publication V1 gates. Public-release smoke and launch-watch closure happen
# only after the immutable release exists.
REQUIRED_PUBLICATION_GATES = frozenset(
    {
        "BETA-P01",
        "BETA-P02",
        "BETA-P03",
        "BETA-P04",
        "BETA-P05",
        "BETA-P06",
        "BETA-S01",
        "BETA-S02",
        "BETA-S03",
        "BETA-S04",
        "BETA-S05",
        "BETA-S06",
        "BETA-D01",
        "BETA-D02",
        "BETA-D03",
        "BETA-R01",
        "BETA-R02",
        "BETA-R03",
        "BETA-R04",
        "BETA-X01",
    }
)
POST_PUBLICATION_GATES = frozenset({"BETA-R05", "BETA-O01"})

# Gates whose pass claims require candidate-bound downloaded-artifact or
# operational evidence, including postpublication public smoke/watch closure.
EXACT_ARTIFACT_PUBLICATION_GATES = frozenset(
    {
        "BETA-P01",
        "BETA-P02",
        "BETA-P03",
        "BETA-P04",
        "BETA-P05",
        "BETA-P06",
        "BETA-S01",
        "BETA-S02",
        "BETA-S03",
        "BETA-S04",
        "BETA-S05",
        "BETA-S06",
        "BETA-D01",
        "BETA-D02",
        "BETA-D03",
        "BETA-R03",
        "BETA-R04",
        "BETA-R05",
        "BETA-X01",
        "BETA-O01",
    }
)
# Source-level publication scaffolding only (never label these as exact artifact).
SOURCE_ALLOWED_PUBLICATION_GATES = frozenset({"BETA-R01", "BETA-R02"})

RECEIPT_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "gate_id",
        "evidence_kind",
        "status",
        "source_commit",
        "candidate_sha256",
        "content_free",
        "severity",
        "limitations",
        "attempts",
        "artifact_digests",
        "counts",
        "notes",
    }
)
LIMITATION_ALLOWED_KEYS = frozenset({"id", "summary", "severity", "workaround", "follow_up"})
ATTEMPT_ALLOWED_KEYS = frozenset({"attempt", "status", "closed_reason"})
BUNDLE_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "source_commit",
        "candidate_sha256",
        "version",
        "receipts",
        "maintainer_decision",
    }
)
DECISION_ALLOWED_KEYS = frozenset(
    {
        "decision",
        "approver",
        "decided_at",
        "independent_human_review_claimed",
        "ai_assisted",
        "notes",
        "reviewed_receipt_ids",
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

SECRET_VALUE_MARKERS = re.compile(
    r"(?i)("
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|"
    r"\bsk-[A-Za-z0-9]{20,}\b|"
    r"\bghp_[A-Za-z0-9]{36}\b|"
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"password\s*[:=]\s*\S+|"
    r"api[_-]?key\s*[:=]\s*\S+|"
    r"client_secret\s*[:=]\s*\S+|"
    r"private[_-]?key\s*[:=]\s*\S+"
    r")"
)


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON object")
    return value


def _reject_unknown_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestError(f"{label} has unknown fields: {', '.join(unknown)}")


def _assert_content_free(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ManifestError(f"{path} contains a non-string key")
            folded = key.casefold()
            if folded in FORBIDDEN_RECEIPT_KEYS or any(
                token in folded
                for token in ("password", "private_key", "raw_context", "credential")
            ):
                raise ManifestError(f"receipt field is not content-free: {path}.{key}")
            if SECRET_VALUE_MARKERS.search(key):
                raise ManifestError(f"receipt field is not content-free: {path}.{key}")
            _assert_content_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_content_free(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > 8 * 1024:
            raise ManifestError(f"receipt string is unreasonably large: {path}")
        if re.search(r"(?i)(?:C:\\Users\\|/Users/[^/\s]+/|/home/[^/\s]+/)", value):
            raise ManifestError(f"receipt must not contain absolute developer paths: {path}")
        if SECRET_VALUE_MARKERS.search(value):
            raise ManifestError(f"receipt value is not content-free: {path}")


def _validate_limitations(limitations: object) -> list[dict[str, Any]]:
    if not isinstance(limitations, list):
        raise ManifestError("acceptance limitations must be a list")
    validated: list[dict[str, Any]] = []
    for item in limitations:
        entry = _require_dict(item, "acceptance limitation")
        _reject_unknown_keys(entry, LIMITATION_ALLOWED_KEYS, "acceptance limitation")
        limitation_id = entry.get("id")
        summary = entry.get("summary")
        if not isinstance(limitation_id, str) or LIMITATION_ID.fullmatch(limitation_id) is None:
            raise ManifestError("acceptance limitation id is invalid")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX_LENGTH:
            raise ManifestError("acceptance limitation summary is invalid")
        severity = entry.get("severity")
        if severity is not None and severity not in ALLOWED_SEVERITIES:
            raise ManifestError("acceptance limitation severity is invalid")
        workaround = entry.get("workaround")
        if workaround is not None and (
            not isinstance(workaround, str) or len(workaround) > WORKAROUND_MAX_LENGTH
        ):
            raise ManifestError("acceptance limitation workaround is invalid")
        follow_up = entry.get("follow_up")
        if follow_up is not None and (
            not isinstance(follow_up, str) or len(follow_up) > FOLLOW_UP_MAX_LENGTH
        ):
            raise ManifestError("acceptance limitation follow_up is invalid")
        validated.append(entry)
    return validated


def _validate_attempts(attempts: object) -> list[dict[str, Any]]:
    if not isinstance(attempts, list):
        raise ManifestError("acceptance attempts must be a list")
    validated: list[dict[str, Any]] = []
    for item in attempts:
        attempt = _require_dict(item, "acceptance attempt")
        _reject_unknown_keys(attempt, ATTEMPT_ALLOWED_KEYS, "acceptance attempt")
        number = attempt.get("attempt")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ManifestError("acceptance attempt number must be a positive integer")
        if attempt.get("status") not in ALLOWED_STATUSES:
            raise ManifestError("acceptance attempt status is invalid")
        closed_reason = attempt.get("closed_reason")
        if closed_reason is not None and (
            not isinstance(closed_reason, str) or len(closed_reason) > CLOSED_REASON_MAX_LENGTH
        ):
            raise ManifestError("acceptance attempt closed_reason is invalid")
        validated.append(attempt)
    return validated


def _validate_artifact_digests(digests: object) -> dict[str, str]:
    if digests is None:
        return {}
    if not isinstance(digests, dict):
        raise ManifestError("acceptance artifact_digests must be an object")
    validated: dict[str, str] = {}
    for key, value in digests.items():
        if not isinstance(key, str) or SAFE_ARTIFACT_KEY.fullmatch(key) is None:
            raise ManifestError(
                "acceptance artifact_digests keys must be safe basenames or target IDs"
            )
        if "/" in key or "\\" in key or key in {".", ".."} or ":" in key:
            raise ManifestError(
                "acceptance artifact_digests keys must not be absolute paths or path segments"
            )
        if not isinstance(value, str) or SHA256.fullmatch(value) is None:
            raise ManifestError(
                "acceptance artifact_digests values must be lowercase SHA-256 digests"
            )
        validated[key] = value
    return validated


def _validate_counts(counts: object) -> dict[str, int]:
    if counts is None:
        return {}
    if not isinstance(counts, dict):
        raise ManifestError("acceptance counts must be an object")
    validated: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(key, str) or COUNT_KEY.fullmatch(key) is None:
            raise ManifestError("acceptance counts keys must be short snake_case identifiers")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ManifestError("acceptance counts values must be integers")
        validated[key] = value
    return validated


def _has_executed_attempt(attempts: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        isinstance(item, Mapping) and item.get("status") in {"pass", "fail"} for item in attempts
    )


def validate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(receipt)
    _assert_content_free(value)
    _reject_unknown_keys(value, RECEIPT_ALLOWED_KEYS, "acceptance receipt")
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
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != RECEIPT_SCHEMA_VERSION:
        raise ManifestError("acceptance receipt schema_version must be integer 1")
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
    # Pass claims for package/platform gates must be exact downloaded artifacts.
    if (
        status == "pass"
        and isinstance(gate_id, str)
        and gate_id in EXACT_ARTIFACT_PUBLICATION_GATES
        and evidence_kind != "exact_downloaded_artifact"
    ):
        raise ManifestError(
            f"gate {gate_id} pass requires exact_downloaded_artifact evidence; "
            "source-only evidence cannot satisfy it"
        )
    # Do not launder source scaffolding as exact artifact evidence.
    if (
        status == "pass"
        and isinstance(gate_id, str)
        and gate_id in SOURCE_ALLOWED_PUBLICATION_GATES
        and evidence_kind == "exact_downloaded_artifact"
    ):
        raise ManifestError(
            f"gate {gate_id} is source-level publication scaffolding and cannot "
            "be labeled exact_downloaded_artifact"
        )
    severity = value.get("severity")
    if severity is not None and severity not in ALLOWED_SEVERITIES:
        raise ManifestError("acceptance severity is invalid")
    limitations = _validate_limitations(value.get("limitations", []))
    for item in limitations:
        item_severity = item.get("severity")
        if severity is None and item_severity in {"P0", "P1"}:
            raise ManifestError("P0/P1 limitations require a receipt severity")
    value["limitations"] = limitations
    candidate_sha256 = value.get("candidate_sha256")
    if candidate_sha256 is not None and (
        not isinstance(candidate_sha256, str) or SHA256.fullmatch(candidate_sha256) is None
    ):
        raise ManifestError("acceptance candidate_sha256 must be a lowercase SHA-256 digest")
    if status == "pass" and not isinstance(candidate_sha256, str):
        raise ManifestError("pass receipts require candidate_sha256 bound to the exact candidate")
    if (
        evidence_kind == "exact_downloaded_artifact"
        and status == "pass"
        and not isinstance(candidate_sha256, str)
    ):
        raise ManifestError("exact downloaded-artifact pass receipts require candidate_sha256")
    attempts = _validate_attempts(value.get("attempts", []))
    if status == "pass" and not _has_executed_attempt(attempts):
        raise ManifestError("pass receipts require at least one executed attempt")
    value["attempts"] = attempts
    if "artifact_digests" in value:
        value["artifact_digests"] = _validate_artifact_digests(value.get("artifact_digests"))
    if status == "pass" and evidence_kind == "exact_downloaded_artifact":
        digests = value.get("artifact_digests")
        if not isinstance(digests, dict) or not digests:
            raise ManifestError("exact downloaded-artifact pass receipts require artifact_digests")
    if "counts" in value:
        value["counts"] = _validate_counts(value.get("counts"))
    notes = value.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > NOTES_MAX_LENGTH:
            raise ManifestError(
                f"acceptance notes must be a short operational string "
                f"(max {NOTES_MAX_LENGTH} characters)"
            )
        if status == "pass" and notes.strip() and len(notes) > 120:
            raise ManifestError("pass receipt notes must stay short and operational")
    return value


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"acceptance receipt is not valid UTF-8 JSON: {path.name}") from exc
    return validate_receipt(_require_dict(value, "acceptance receipt"))


def _open_p0_p1_limitations(receipts: Sequence[Mapping[str, Any]]) -> list[str]:
    open_ids: list[str] = []
    for receipt in receipts:
        receipt_severity = receipt.get("severity")
        limitations = receipt.get("limitations", [])
        if not isinstance(limitations, list):
            continue
        for item in limitations:
            if not isinstance(item, Mapping):
                continue
            severity = item.get("severity") or receipt_severity
            if severity in {"P0", "P1"}:
                open_ids.append(str(item.get("id") or receipt.get("receipt_id")))
    return open_ids


def validate_receipt_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(bundle)
    _assert_content_free(value)
    _reject_unknown_keys(value, BUNDLE_ALLOWED_KEYS, "acceptance receipt bundle")
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
    bundle_schema = value.get("schema_version")
    if isinstance(bundle_schema, bool) or bundle_schema != RECEIPT_SCHEMA_VERSION:
        raise ManifestError("acceptance receipt bundle schema_version must be integer 1")
    source_commit = value.get("source_commit")
    candidate_sha256 = value.get("candidate_sha256")
    if not isinstance(source_commit, str) or COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("bundle source_commit must be a full lowercase SHA")
    if not isinstance(candidate_sha256, str) or SHA256.fullmatch(candidate_sha256) is None:
        raise ManifestError("bundle candidate_sha256 must be a lowercase SHA-256 digest")
    version = value.get("version")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ManifestError("bundle version must be a non-empty string when present")
    receipts_value = value.get("receipts")
    if not isinstance(receipts_value, list) or not receipts_value:
        raise ManifestError("acceptance receipt bundle must contain receipts")
    receipts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_gates: set[str] = set()
    shared_artifact_digests: dict[str, str] = {}
    for item in receipts_value:
        receipt = validate_receipt(_require_dict(item, "bundle receipt"))
        if receipt["receipt_id"] in seen_ids:
            raise ManifestError(f"duplicate receipt_id: {receipt['receipt_id']}")
        seen_ids.add(receipt["receipt_id"])
        gate_id = receipt["gate_id"]
        if gate_id in seen_gates:
            raise ManifestError(
                f"duplicate gate_id in receipt bundle: {gate_id}; "
                "duplicate-shadowed checks are refused"
            )
        seen_gates.add(gate_id)
        if receipt["source_commit"] != source_commit:
            raise ManifestError("receipt source_commit does not match the bundle")
        receipt_digest = receipt.get("candidate_sha256")
        if receipt_digest is not None and receipt_digest != candidate_sha256:
            raise ManifestError("receipt candidate_sha256 does not match the bundle")
        if receipt.get("status") == "pass" and receipt_digest != candidate_sha256:
            raise ManifestError("pass receipt must bind the exact candidate digest")
        digests = receipt.get("artifact_digests")
        if isinstance(digests, dict):
            for key, digest in digests.items():
                prior = shared_artifact_digests.get(key)
                if prior is not None and prior != digest:
                    raise ManifestError(f"conflicting artifact_digests for {key} across receipts")
                shared_artifact_digests[key] = digest
        receipts.append(receipt)
    decision = value.get("maintainer_decision")
    decision_obj = _require_dict(decision, "maintainer_decision")
    _reject_unknown_keys(decision_obj, DECISION_ALLOWED_KEYS, "maintainer_decision")
    decision_value = decision_obj.get("decision")
    if decision_value not in ALLOWED_DECISIONS and decision_value is not None:
        raise ManifestError("maintainer_decision.decision must be approve, reject, or null")
    if decision_obj.get("independent_human_review_claimed") is not False:
        raise ManifestError(
            "receipts must not claim independent human review under sole-maintainer governance"
        )
    approver = decision_obj.get("approver")
    if decision_value == "approve" and (not isinstance(approver, str) or not approver.strip()):
        raise ManifestError("approve decisions require a named human approver")
    if approver is not None and (
        not isinstance(approver, str) or len(approver) > APPROVER_MAX_LENGTH
    ):
        raise ManifestError("maintainer_decision.approver is invalid")
    notes = decision_obj.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > DECISION_NOTES_MAX_LENGTH):
        raise ManifestError("maintainer_decision.notes must be a short operational string")
    reviewed = decision_obj.get("reviewed_receipt_ids")
    if decision_value == "approve":
        if not isinstance(reviewed, list) or not reviewed:
            raise ManifestError(
                "approve decisions require reviewed_receipt_ids enumerating every receipt"
            )
        if any(not isinstance(item, str) for item in reviewed):
            raise ManifestError("reviewed_receipt_ids must be strings")
        reviewed_set = set(reviewed)
        if reviewed_set != seen_ids or len(reviewed) != len(seen_ids):
            raise ManifestError(
                "maintainer decision must enumerate every receipt_id exactly once; "
                "failed and repeated attempts cannot be silently omitted"
            )
        open_blockers = _open_p0_p1_limitations(receipts)
        if open_blockers:
            raise ManifestError(
                "publication rejects open P0/P1 limitations: " + ", ".join(sorted(open_blockers))
            )
    elif reviewed is not None:
        if not isinstance(reviewed, list) or any(not isinstance(item, str) for item in reviewed):
            raise ManifestError("reviewed_receipt_ids must be a list of strings when present")
    value["receipts"] = receipts
    value["maintainer_decision"] = decision_obj
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
    inventory_digests: Mapping[str, str] | None = None,
) -> list[str]:
    required = set(required_gates)
    satisfied: set[str] = set()
    for receipt in receipts:
        gate_id = receipt.get("gate_id")
        if not isinstance(gate_id, str):
            continue
        if receipt_claims_pass(receipt) and receipt.get("status") != "not_run":
            if not _has_executed_attempt(receipt.get("attempts", [])):
                continue
            if not isinstance(receipt.get("candidate_sha256"), str):
                continue
            evidence_kind = receipt.get("evidence_kind")
            if gate_id in EXACT_ARTIFACT_PUBLICATION_GATES:
                if evidence_kind != "exact_downloaded_artifact":
                    continue
                digests = receipt.get("artifact_digests")
                if not isinstance(digests, dict) or not digests:
                    continue
                if inventory_digests is not None:
                    try:
                        _require_exact_artifact_digest_bindings(
                            digests, inventory_digests=inventory_digests
                        )
                    except ManifestError:
                        continue
            elif gate_id in SOURCE_ALLOWED_PUBLICATION_GATES:
                if evidence_kind != "source":
                    continue
            satisfied.add(gate_id)
    return sorted(required - satisfied)


def candidate_inventory_digests(candidate: Mapping[str, Any]) -> dict[str, str]:
    """Collect unique name→sha256 pairs declared by a verified candidate inventory."""

    digests: dict[str, str] = {}

    def _add(name: object, digest: object) -> None:
        if not isinstance(name, str) or not isinstance(digest, str):
            return
        if name in digests:
            if digests[name] != digest:
                raise ManifestError(f"candidate inventory declares conflicting digests for {name}")
            raise ManifestError(f"candidate inventory declares duplicate file name: {name}")
        digests[name] = digest

    source_evidence = candidate.get("source_evidence")
    if isinstance(source_evidence, dict):
        for descriptor in source_evidence.values():
            if not isinstance(descriptor, Mapping):
                continue
            _add(descriptor.get("name"), descriptor.get("sha256"))
    artifacts = candidate.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                continue
            for key, descriptor in artifact.items():
                if key in {"platform", "architecture", "ota_manifest_eligible"}:
                    continue
                if not isinstance(descriptor, Mapping):
                    continue
                _add(descriptor.get("name"), descriptor.get("sha256"))
    if not digests:
        raise ManifestError("candidate inventory declares no artifact digests")
    return digests


def _require_exact_artifact_digest_bindings(
    digests: Mapping[str, str],
    *,
    inventory_digests: Mapping[str, str],
) -> None:
    """Every exact-artifact binding key must be declared by the candidate inventory."""

    if not digests:
        raise ManifestError("exact downloaded-artifact pass receipts require artifact_digests")
    if not inventory_digests:
        raise ManifestError("candidate inventory digests are required for exact artifact binding")
    for name, digest in digests.items():
        if not isinstance(name, str) or not name.strip():
            raise ManifestError("artifact_digests keys must be non-empty declared filenames")
        expected = inventory_digests.get(name)
        if expected is None:
            raise ManifestError(
                f"receipt artifact_digests key is not declared by the candidate inventory: {name}"
            )
        if expected != digest:
            raise ManifestError(
                f"receipt artifact digest does not match candidate inventory: {name}"
            )


def recompute_receipt_artifact_bindings(
    receipts: Sequence[Mapping[str, Any]],
    *,
    inventory_digests: Mapping[str, str],
    candidate_sha256: str,
) -> None:
    """Refuse mixed inventory/artifact digests that do not recompute from inventory.

    For every exact_downloaded_artifact pass receipt, every artifact_digests key
    must be declared by the verified candidate inventory and match its digest.
    Arbitrary safe basenames that are not inventory members never satisfy a
    gate. ``candidate_sha256`` binding remains a separate exact-candidate check.
    """

    if SHA256.fullmatch(candidate_sha256) is None:
        raise ManifestError("candidate_sha256 must be a lowercase SHA-256 digest")
    for receipt in receipts:
        if receipt.get("status") != "pass":
            continue
        if receipt.get("candidate_sha256") != candidate_sha256:
            raise ManifestError("pass receipt candidate digest does not recompute")
        digests = receipt.get("artifact_digests")
        evidence_kind = receipt.get("evidence_kind")
        gate_id = receipt.get("gate_id")
        if evidence_kind == "exact_downloaded_artifact" or (
            isinstance(gate_id, str) and gate_id in EXACT_ARTIFACT_PUBLICATION_GATES
        ):
            if not isinstance(digests, dict):
                raise ManifestError(
                    "exact downloaded-artifact pass receipts require artifact_digests"
                )
            _require_exact_artifact_digest_bindings(digests, inventory_digests=inventory_digests)
        elif isinstance(digests, dict) and digests:
            for name, digest in digests.items():
                expected = inventory_digests.get(name)
                if expected is None:
                    raise ManifestError(
                        "receipt artifact_digests key is not declared by the "
                        f"candidate inventory: {name}"
                    )
                if expected != digest:
                    raise ManifestError(
                        f"receipt artifact digest does not match candidate inventory: {name}"
                    )


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
        "notes": "Template only. Not executed evidence.",
    }
    validate_receipt(template)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace receipt template: {path.name}")
    path.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest_file(path: Path) -> str:
    digest, _ = sha256_file(path)
    return digest

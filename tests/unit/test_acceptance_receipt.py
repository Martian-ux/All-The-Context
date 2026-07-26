from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.acceptance_receipt import (
    load_receipt,
    missing_required_gates,
    validate_receipt,
    validate_receipt_bundle,
)
from allthecontext.release_manifest import ManifestError

SOURCE = "a" * 40
DIGEST = "b" * 64


def _receipt(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "receipt_id": "source-beta-r01",
        "gate_id": "BETA-R01",
        "evidence_kind": "source",
        "status": "pass",
        "source_commit": SOURCE,
        "content_free": True,
        "severity": None,
        "limitations": [],
        "attempts": [{"attempt": 1, "status": "pass"}],
    }
    base.update(overrides)
    return base


def test_template_receipt_is_not_run_and_not_pass() -> None:
    root = Path(__file__).resolve().parents[2]
    receipt = load_receipt(root / "release" / "acceptance-receipt.template.json")
    assert receipt["status"] == "not_run"
    assert receipt["status"] != "pass"


def test_receipt_rejects_secret_fields_and_absolute_paths() -> None:
    with pytest.raises(ManifestError, match="content-free"):
        validate_receipt(_receipt(password="nope"))
    with pytest.raises(ManifestError, match="absolute developer paths"):
        bad_path = "/Users/" + "someone/secret.db"
        validate_receipt(_receipt(notes=f"see {bad_path}"))


def test_skipped_evidence_cannot_claim_pass() -> None:
    with pytest.raises(ManifestError, match="cannot claim pass"):
        validate_receipt(_receipt(evidence_kind="skipped", status="pass"))


def test_bundle_missing_required_gates() -> None:
    bundle = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "candidate_sha256": DIGEST,
        "receipts": [_receipt()],
        "maintainer_decision": {
            "decision": "approve",
            "approver": "maintainer",
            "independent_human_review_claimed": False,
        },
    }
    validated = validate_receipt_bundle(bundle)
    missing = missing_required_gates(validated["receipts"])
    assert "BETA-R02" in missing
    assert "BETA-R01" not in missing


def test_bundle_rejects_independent_review_claim() -> None:
    bundle = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "candidate_sha256": DIGEST,
        "receipts": [_receipt()],
        "maintainer_decision": {
            "decision": "approve",
            "approver": "maintainer",
            "independent_human_review_claimed": True,
        },
    }
    with pytest.raises(ManifestError, match="independent human review"):
        validate_receipt_bundle(bundle)


def test_exact_artifact_pass_requires_candidate_digest() -> None:
    with pytest.raises(ManifestError, match="candidate_sha256"):
        validate_receipt(
            _receipt(
                evidence_kind="exact_downloaded_artifact",
                status="pass",
                gate_id="BETA-R03",
                receipt_id="artifact-beta-r03",
            )
        )
    validate_receipt(
        _receipt(
            evidence_kind="exact_downloaded_artifact",
            status="pass",
            gate_id="BETA-R03",
            receipt_id="artifact-beta-r03",
            candidate_sha256=DIGEST,
        )
    )


def test_template_bundle_loads(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    # Template uses zero digests and not_run statuses; still must parse as content-free.
    raw = json.loads(
        (root / "release" / "acceptance-receipt-bundle.template.json").read_text(encoding="utf-8")
    )
    # Decision null is allowed before approval.
    validated = validate_receipt_bundle(raw)
    assert validated["maintainer_decision"]["decision"] is None
    assert all(item["status"] == "not_run" for item in validated["receipts"])

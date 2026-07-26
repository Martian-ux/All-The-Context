from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.acceptance_receipt import (
    EXACT_ARTIFACT_PUBLICATION_GATES,
    REQUIRED_PUBLICATION_GATES,
    SOURCE_ALLOWED_PUBLICATION_GATES,
    load_receipt,
    missing_required_gates,
    recompute_receipt_artifact_bindings,
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
        "candidate_sha256": DIGEST,
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
    with pytest.raises(ManifestError, match=r"unknown fields|content-free"):
        validate_receipt(_receipt(password="nope"))
    with pytest.raises(ManifestError, match="absolute developer paths"):
        bad_path = "/Users/" + "someone/secret.db"
        validate_receipt(_receipt(notes=f"see {bad_path}"))


def test_receipt_rejects_unknown_keys_and_attempt_fields() -> None:
    with pytest.raises(ManifestError, match="unknown fields"):
        validate_receipt(_receipt(extra_blob="nope"))
    with pytest.raises(ManifestError, match="unknown fields"):
        validate_receipt(_receipt(attempts=[{"attempt": 1, "status": "pass", "raw_log": "x"}]))


def test_receipt_rejects_secret_values() -> None:
    with pytest.raises(ManifestError, match="content-free"):
        validate_receipt(_receipt(notes="token=ghp_" + ("A" * 36)))


def test_artifact_digest_keys_must_be_safe_basenames() -> None:
    with pytest.raises(ManifestError, match="safe basenames"):
        validate_receipt(_receipt(artifact_digests={"C:\\\\Users\\\\x\\\\a.bin": "c" * 64}))
    with pytest.raises(ManifestError, match=r"absolute paths|safe basenames"):
        validate_receipt(_receipt(artifact_digests={"/etc/passwd": "c" * 64}))
    validate_receipt(_receipt(artifact_digests={"all-the-context-linux-x86_64.zip": "c" * 64}))


def test_pass_requires_candidate_digest_and_executed_attempt() -> None:
    with pytest.raises(ManifestError, match="candidate_sha256"):
        body = _receipt()
        del body["candidate_sha256"]
        validate_receipt(body)
    with pytest.raises(ManifestError, match="executed attempt"):
        validate_receipt(_receipt(attempts=[]))
    with pytest.raises(ManifestError, match="executed attempt"):
        validate_receipt(_receipt(attempts=[{"attempt": 1, "status": "not_run"}]))


def test_skipped_evidence_cannot_claim_pass() -> None:
    with pytest.raises(ManifestError, match="cannot claim pass"):
        validate_receipt(_receipt(evidence_kind="skipped", status="pass"))


def test_required_publication_gates_are_complete() -> None:
    expected = {
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
        "BETA-O01",
    }
    assert expected == REQUIRED_PUBLICATION_GATES
    assert "BETA-R05" not in REQUIRED_PUBLICATION_GATES


def test_bundle_missing_required_gates() -> None:
    bundle = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "candidate_sha256": DIGEST,
        "receipts": [_receipt()],
        "maintainer_decision": {
            "decision": None,
            "independent_human_review_claimed": False,
        },
    }
    validated = validate_receipt_bundle(bundle)
    missing = missing_required_gates(validated["receipts"])
    assert "BETA-R02" in missing
    assert "BETA-P01" in missing
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
            "reviewed_receipt_ids": ["source-beta-r01"],
        },
    }
    with pytest.raises(ManifestError, match="independent human review"):
        validate_receipt_bundle(bundle)


def test_approve_requires_reviewed_receipt_ids_and_rejects_p0() -> None:
    with pytest.raises(ManifestError, match="reviewed_receipt_ids"):
        validate_receipt_bundle(
            {
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
        )
    with pytest.raises(ManifestError, match="enumerate every receipt_id"):
        validate_receipt_bundle(
            {
                "schema_version": 1,
                "source_commit": SOURCE,
                "candidate_sha256": DIGEST,
                "receipts": [
                    _receipt(),
                    _receipt(receipt_id="other", gate_id="BETA-R02"),
                ],
                "maintainer_decision": {
                    "decision": "approve",
                    "approver": "maintainer",
                    "independent_human_review_claimed": False,
                    "reviewed_receipt_ids": ["source-beta-r01"],
                },
            }
        )
    with pytest.raises(ManifestError, match="P0/P1"):
        validate_receipt_bundle(
            {
                "schema_version": 1,
                "source_commit": SOURCE,
                "candidate_sha256": DIGEST,
                "receipts": [
                    _receipt(
                        severity="P0",
                        limitations=[
                            {
                                "id": "open-blocker",
                                "summary": "critical gap",
                                "severity": "P0",
                            }
                        ],
                    )
                ],
                "maintainer_decision": {
                    "decision": "approve",
                    "approver": "maintainer",
                    "independent_human_review_claimed": False,
                    "reviewed_receipt_ids": ["source-beta-r01"],
                },
            }
        )


def test_exact_artifact_pass_requires_candidate_digest_and_digests() -> None:
    with pytest.raises(ManifestError, match="candidate_sha256"):
        body = _receipt(
            evidence_kind="exact_downloaded_artifact",
            status="pass",
            gate_id="BETA-R03",
            receipt_id="artifact-beta-r03",
            artifact_digests={"acceptance-smoke-fixture.bin": "c" * 64},
        )
        del body["candidate_sha256"]
        validate_receipt(body)
    with pytest.raises(ManifestError, match="artifact_digests"):
        validate_receipt(
            _receipt(
                evidence_kind="exact_downloaded_artifact",
                status="pass",
                gate_id="BETA-R03",
                receipt_id="artifact-beta-r03",
                candidate_sha256=DIGEST,
            )
        )
    validate_receipt(
        _receipt(
            evidence_kind="exact_downloaded_artifact",
            status="pass",
            gate_id="BETA-R03",
            receipt_id="artifact-beta-r03",
            candidate_sha256=DIGEST,
            artifact_digests={"acceptance-smoke-fixture.bin": "c" * 64},
        )
    )


def test_source_only_cannot_pass_exact_artifact_gates() -> None:
    with pytest.raises(ManifestError, match="exact_downloaded_artifact"):
        validate_receipt(
            _receipt(
                gate_id="BETA-R03",
                receipt_id="source-labeled-r03",
                evidence_kind="source",
                status="pass",
            )
        )
    assert "BETA-R03" in EXACT_ARTIFACT_PUBLICATION_GATES
    assert "BETA-R01" in SOURCE_ALLOWED_PUBLICATION_GATES
    with pytest.raises(ManifestError, match="source-level"):
        validate_receipt(
            _receipt(
                gate_id="BETA-R01",
                receipt_id="artifact-labeled-r01",
                evidence_kind="exact_downloaded_artifact",
                status="pass",
                artifact_digests={"acceptance-smoke-fixture.bin": "c" * 64},
            )
        )


def test_bundle_rejects_duplicate_gate_and_conflicting_digests() -> None:
    with pytest.raises(ManifestError, match="duplicate gate_id"):
        validate_receipt_bundle(
            {
                "schema_version": 1,
                "source_commit": SOURCE,
                "candidate_sha256": DIGEST,
                "receipts": [
                    _receipt(),
                    _receipt(receipt_id="shadow-r01", gate_id="BETA-R01"),
                ],
                "maintainer_decision": {
                    "decision": None,
                    "independent_human_review_claimed": False,
                },
            }
        )
    with pytest.raises(ManifestError, match="conflicting artifact_digests"):
        validate_receipt_bundle(
            {
                "schema_version": 1,
                "source_commit": SOURCE,
                "candidate_sha256": DIGEST,
                "receipts": [
                    _receipt(
                        gate_id="BETA-R03",
                        receipt_id="artifact-r03",
                        evidence_kind="exact_downloaded_artifact",
                        artifact_digests={"all-the-context-linux-x86_64.zip": "c" * 64},
                    ),
                    _receipt(
                        gate_id="BETA-R04",
                        receipt_id="artifact-r04",
                        evidence_kind="exact_downloaded_artifact",
                        artifact_digests={"all-the-context-linux-x86_64.zip": "d" * 64},
                    ),
                ],
                "maintainer_decision": {
                    "decision": None,
                    "independent_human_review_claimed": False,
                },
            }
        )


def test_recompute_refuses_mixed_and_undeclared_inventory_digests() -> None:
    receipt = _receipt(
        gate_id="BETA-R03",
        receipt_id="artifact-r03",
        evidence_kind="exact_downloaded_artifact",
        artifact_digests={"all-the-context-linux-x86_64.zip": "c" * 64},
    )
    with pytest.raises(ManifestError, match="does not match candidate inventory"):
        recompute_receipt_artifact_bindings(
            [receipt],
            inventory_digests={"all-the-context-linux-x86_64.zip": "e" * 64},
            candidate_sha256=DIGEST,
        )
    with pytest.raises(ManifestError, match="not declared by the candidate inventory"):
        recompute_receipt_artifact_bindings(
            [receipt],
            inventory_digests={"other-declared.bin": "c" * 64},
            candidate_sha256=DIGEST,
        )
    # Arbitrary safe basenames never satisfy an exact gate.
    loose = _receipt(
        gate_id="BETA-P04",
        receipt_id="provider-p04",
        evidence_kind="exact_downloaded_artifact",
        artifact_digests={"acceptance-smoke-fixture.bin": "c" * 64},
    )
    with pytest.raises(ManifestError, match="not declared by the candidate inventory"):
        recompute_receipt_artifact_bindings(
            [loose],
            inventory_digests={"all-the-context-linux-x86_64.zip": "c" * 64},
            candidate_sha256=DIGEST,
        )


def test_beta_p04_is_exact_artifact_gate() -> None:
    assert "BETA-P04" in EXACT_ARTIFACT_PUBLICATION_GATES
    with pytest.raises(ManifestError, match="exact_downloaded_artifact"):
        validate_receipt(
            _receipt(
                gate_id="BETA-P04",
                receipt_id="source-provider-p04",
                evidence_kind="source",
                status="pass",
            )
        )


def test_template_bundle_loads() -> None:
    root = Path(__file__).resolve().parents[2]
    raw = json.loads(
        (root / "release" / "acceptance-receipt-bundle.template.json").read_text(encoding="utf-8")
    )
    validated = validate_receipt_bundle(raw)
    assert validated["maintainer_decision"]["decision"] is None
    assert all(item["status"] == "not_run" for item in validated["receipts"])
    assert {item["gate_id"] for item in validated["receipts"]} >= REQUIRED_PUBLICATION_GATES
    assert "BETA-R05" not in {item["gate_id"] for item in validated["receipts"]}
    assert all(item["status"] != "pass" for item in validated["receipts"])

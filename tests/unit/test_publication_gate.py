from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from allthecontext.acceptance_receipt import (
    EXACT_ARTIFACT_PUBLICATION_GATES,
    REQUIRED_PUBLICATION_GATES,
    SOURCE_ALLOWED_PUBLICATION_GATES,
    candidate_inventory_digests,
)
from allthecontext.exact_source_gate import (
    CANONICAL_CI_WORKFLOW_NAME,
    CANONICAL_CI_WORKFLOW_PATH,
    REQUIRED_CI_JOBS,
)
from allthecontext.publication_gate import evaluate_publication_gate
from allthecontext.release_candidate import (
    CANDIDATE_FILE_NAME,
    CANDIDATE_PROVENANCE_FILE_NAME,
    COMPONENT_INVENTORY_CHECKSUM_FILE_NAME,
    COMPONENT_INVENTORY_FILE_NAME,
    MATRIX_EVIDENCE_FILE_NAME,
    NOTICES_FILE_NAME,
    PUBLICATION_GATE_RECORD_FILE_NAME,
    ReleaseTarget,
    assemble_candidate,
    direct_package_names,
    signed_manifest_name,
)
from allthecontext.release_manifest import ManifestError, sha256_file

from scripts.build_release_assets import build_archive, write_metadata, write_subject_sbom

SOURCE = "d" * 40
VERSION = "0.1.0-beta.1"
TARGET = ReleaseTarget("linux", "x86_64")
ROOT = Path(__file__).resolve().parents[2]
KEYRING = ROOT / "release" / "keys.json"
PUBLIC_FP = "sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4"


def _bundle(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                "verificationMaterial": {},
                "dsseEnvelope": {},
            }
        ),
        encoding="utf-8",
    )


def _source_evidence(release_dir: Path) -> None:
    inventory = release_dir / COMPONENT_INVENTORY_FILE_NAME
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": VERSION,
                "source_commit": SOURCE,
                "project_version": VERSION,
                "locks": {
                    "uv.lock": {"sha256": "a" * 64},
                    "apps/dashboard/package-lock.json": {"sha256": "b" * 64},
                },
                "component_count": 1,
                "components": [
                    {
                        "ecosystem": "python",
                        "name": "all-the-context",
                        "version": VERSION,
                        "license": "MIT",
                        "locked": True,
                        "source_kind": "path",
                        "scope": "runtime",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    digest, _ = sha256_file(inventory)
    (release_dir / COMPONENT_INVENTORY_CHECKSUM_FILE_NAME).write_text(
        f"{digest}  {inventory.name}\n", encoding="ascii", newline="\n"
    )
    (release_dir / MATRIX_EVIDENCE_FILE_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_commit": SOURCE,
                "workflow_path": CANONICAL_CI_WORKFLOW_PATH,
                "workflow_name": CANONICAL_CI_WORKFLOW_NAME,
                "workflow_run_id": 7,
                "run_status": "completed",
                "run_conclusion": "success",
                "job_records": [
                    {
                        "name": name,
                        "run_id": 7,
                        "head_sha": SOURCE,
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for name in REQUIRED_CI_JOBS
                ],
                "required_jobs": list(REQUIRED_CI_JOBS),
                "ok": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (release_dir / NOTICES_FILE_NAME).write_text(
        f"notices\nSource commit: {SOURCE}\n", encoding="utf-8"
    )


def _candidate_dir(tmp_path: Path) -> Path:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    _source_evidence(release_dir)
    source = tmp_path / "all-the-context"
    source.write_bytes(b"portable app\n")
    ota = build_archive(
        source,
        release_dir,
        version=VERSION,
        platform_name=TARGET.platform,
        architecture=TARGET.architecture,
    )
    write_metadata(ota, version=VERSION)
    for suffix in ("provenance.sigstore.json", "sbom.sigstore.json"):
        _bundle(release_dir / f"{ota.name}.{suffix}")
    names = direct_package_names(VERSION, TARGET)
    direct_package = release_dir / names["direct_package"]
    direct_package.write_bytes(b"direct portable package\n")
    digest, size = sha256_file(direct_package)
    (release_dir / names["direct_package_checksum"]).write_text(
        f"{digest}  {direct_package.name}\n", encoding="ascii"
    )
    notice = release_dir / names["direct_package_notice"]
    notice.write_text("IMPORTANT: unsigned community build\n", encoding="utf-8")
    (release_dir / names["direct_package_report"]).write_text(
        json.dumps(
            {
                "architecture": TARGET.architecture,
                "format": "tar.gz",
                "notice": notice.name,
                "package": direct_package.name,
                "platform": TARGET.platform,
                "recovery_console_helper": "all-the-context",
                "recovery_surface": "console-main-binary",
                "schema_version": 1,
                "sha256": digest,
                "size": size,
                "source": source.name,
                "trust": "unsigned-community",
                "version": VERSION,
            }
        ),
        encoding="utf-8",
    )
    write_subject_sbom(direct_package, version=VERSION)
    _bundle(release_dir / names["direct_package_provenance_bundle"])
    _bundle(release_dir / names["direct_package_sbom_bundle"])
    assemble_candidate(
        release_dir,
        version=VERSION,
        channel="beta",
        source_commit=SOURCE,
        targets=[TARGET],
        ota_targets=[TARGET],
    )
    _bundle(release_dir / CANDIDATE_PROVENANCE_FILE_NAME)
    return release_dir


def _pass_receipt(
    gate_id: str,
    *,
    candidate_sha256: str,
    inventory_digests: dict[str, str] | None = None,
) -> dict[str, Any]:
    if gate_id in EXACT_ARTIFACT_PUBLICATION_GATES:
        evidence_kind = "exact_downloaded_artifact"
    elif gate_id in SOURCE_ALLOWED_PUBLICATION_GATES:
        evidence_kind = "source"
    else:
        evidence_kind = "source"
    body: dict[str, Any] = {
        "schema_version": 1,
        "receipt_id": f"pass-{gate_id.casefold()}",
        "gate_id": gate_id,
        "evidence_kind": evidence_kind,
        "status": "pass",
        "source_commit": SOURCE,
        "candidate_sha256": candidate_sha256,
        "content_free": True,
        "limitations": [],
        "attempts": [{"attempt": 1, "status": "pass"}],
    }
    if evidence_kind == "exact_downloaded_artifact":
        if not inventory_digests:
            raise AssertionError("exact artifact receipts require inventory digests in tests")
        # Bind one real inventory-declared package name and digest.
        name = next(iter(inventory_digests))
        body["artifact_digests"] = {name: inventory_digests[name]}
    return body


def _full_bundle(
    digest: str,
    *,
    decision: str | None = "approve",
    inventory_digests: dict[str, str] | None = None,
) -> dict[str, Any]:
    receipts = [
        _pass_receipt(gate, candidate_sha256=digest, inventory_digests=inventory_digests)
        for gate in sorted(REQUIRED_PUBLICATION_GATES)
    ]
    body: dict[str, Any] = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "candidate_sha256": digest,
        "receipts": receipts,
        "maintainer_decision": {
            "decision": decision,
            "independent_human_review_claimed": False,
        },
    }
    if decision == "approve":
        body["maintainer_decision"]["approver"] = "sole-maintainer"
        body["maintainer_decision"]["ai_assisted"] = True
        body["maintainer_decision"]["reviewed_receipt_ids"] = [
            item["receipt_id"] for item in receipts
        ]
    return body


def _promotion_extras(release_dir: Path) -> None:
    (release_dir / signed_manifest_name("beta", TARGET)).write_text(
        json.dumps({"schema_version": 1, "test_only": True}) + "\n",
        encoding="utf-8",
    )


def _inventory_digests(release_dir: Path) -> dict[str, str]:
    candidate = json.loads((release_dir / CANDIDATE_FILE_NAME).read_text(encoding="utf-8"))
    return candidate_inventory_digests(candidate)


def test_publication_gate_fails_without_approve(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            _full_bundle(digest, decision=None, inventory_digests=_inventory_digests(release_dir)),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="approve"):
        evaluate_publication_gate(
            release_dir=release_dir,
            candidate_sha256=digest,
            source_commit=SOURCE,
            receipt_bundle_path=bundle_path,
            keyring_path=KEYRING,
            key_id="release-2026-a",
            expected_public_key_sha256=PUBLIC_FP,
            asset_stage="promotion",
        )


def test_publication_gate_passes_with_required_set(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            _full_bundle(digest, inventory_digests=_inventory_digests(release_dir)),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    record = evaluate_publication_gate(
        release_dir=release_dir,
        candidate_sha256=digest,
        source_commit=SOURCE,
        receipt_bundle_path=bundle_path,
        keyring_path=KEYRING,
        key_id="release-2026-a",
        expected_public_key_sha256=PUBLIC_FP,
        asset_stage="promotion",
    )
    assert record["ok"] is True
    assert record["key_id"] == "release-2026-a"
    assert record["maintainer_approver"] == "sole-maintainer"
    assert set(record["required_gates"]) == REQUIRED_PUBLICATION_GATES
    assert (release_dir / "acceptance-receipt-bundle-v1.json").is_file()
    assert (release_dir / PUBLICATION_GATE_RECORD_FILE_NAME).is_file()
    assert "acceptance-receipt-bundle-v1.json" in record["assets"]
    assert PUBLICATION_GATE_RECORD_FILE_NAME in record["assets"]


def test_publication_gate_rejects_wrong_public_key(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(_full_bundle(digest, inventory_digests=_inventory_digests(release_dir))),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="public-key identity"):
        evaluate_publication_gate(
            release_dir=release_dir,
            candidate_sha256=digest,
            source_commit=SOURCE,
            receipt_bundle_path=bundle_path,
            keyring_path=KEYRING,
            key_id="release-2026-a",
            expected_public_key_sha256="sha256:" + ("0" * 64),
            asset_stage="promotion",
        )


def test_publication_gate_rejects_incomplete_gate_set(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    incomplete = _full_bundle(digest, inventory_digests=_inventory_digests(release_dir))
    incomplete["receipts"] = incomplete["receipts"][:3]
    incomplete["maintainer_decision"]["reviewed_receipt_ids"] = [
        item["receipt_id"] for item in incomplete["receipts"]
    ]
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(ManifestError, match="required receipt gates"):
        evaluate_publication_gate(
            release_dir=release_dir,
            candidate_sha256=digest,
            source_commit=SOURCE,
            receipt_bundle_path=bundle_path,
            keyring_path=KEYRING,
            key_id="release-2026-a",
            expected_public_key_sha256=PUBLIC_FP,
            asset_stage="promotion",
        )


def test_publication_gate_rejects_forged_matrix_evidence(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    matrix_path = release_dir / MATRIX_EVIDENCE_FILE_NAME
    forged = json.loads(matrix_path.read_text(encoding="utf-8"))
    forged["ok"] = True
    forged["job_records"] = forged["job_records"][:9]
    matrix_path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(_full_bundle(digest, inventory_digests=_inventory_digests(release_dir))),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match=r"matrix evidence|digest|source_evidence"):
        evaluate_publication_gate(
            release_dir=release_dir,
            candidate_sha256=digest,
            source_commit=SOURCE,
            receipt_bundle_path=bundle_path,
            keyring_path=KEYRING,
            key_id="release-2026-a",
            expected_public_key_sha256=PUBLIC_FP,
            asset_stage="promotion",
        )


def test_publication_gate_rejects_mixed_inventory_artifact_digest(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    inventory = json.loads(candidate.read_text(encoding="utf-8"))
    package_name = inventory["artifacts"][0]["direct_package"]["name"]
    real_digest = inventory["artifacts"][0]["direct_package"]["sha256"]
    bundle = _full_bundle(digest, inventory_digests=_inventory_digests(release_dir))
    for receipt in bundle["receipts"]:
        if receipt["gate_id"] == "BETA-R03":
            receipt["artifact_digests"] = {package_name: "f" * 64}
            assert receipt["artifact_digests"][package_name] != real_digest
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ManifestError, match="does not match candidate inventory"):
        evaluate_publication_gate(
            release_dir=release_dir,
            candidate_sha256=digest,
            source_commit=SOURCE,
            receipt_bundle_path=bundle_path,
            keyring_path=KEYRING,
            key_id="release-2026-a",
            expected_public_key_sha256=PUBLIC_FP,
            asset_stage="promotion",
        )


def test_publication_gate_rejects_arbitrary_safe_key_as_exact_binding(tmp_path: Path) -> None:
    release_dir = _candidate_dir(tmp_path)
    candidate = release_dir / CANDIDATE_FILE_NAME
    digest, _ = sha256_file(candidate)
    _promotion_extras(release_dir)
    bundle = _full_bundle(digest, inventory_digests=_inventory_digests(release_dir))
    for receipt in bundle["receipts"]:
        if receipt["gate_id"] in EXACT_ARTIFACT_PUBLICATION_GATES:
            receipt["artifact_digests"] = {"acceptance-smoke-fixture.bin": "c" * 64}
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ManifestError, match="not declared by the candidate inventory"):
        evaluate_publication_gate(
            release_dir=release_dir,
            candidate_sha256=digest,
            source_commit=SOURCE,
            receipt_bundle_path=bundle_path,
            keyring_path=KEYRING,
            key_id="release-2026-a",
            expected_public_key_sha256=PUBLIC_FP,
            asset_stage="promotion",
        )


def test_beta_p04_source_provider_preparation_cannot_pass() -> None:
    """Provider-lane source preparation fragments must never validate as BETA-P04 pass."""

    provider_source_fragment = {
        "schema_version": 1,
        "receipt_id": "provider-prep-beta-p04",
        "gate_id": "BETA-P04",
        "evidence_kind": "source",
        "status": "pass",
        "source_commit": SOURCE,
        "candidate_sha256": "e" * 64,
        "content_free": True,
        "limitations": [],
        "attempts": [{"attempt": 1, "status": "pass"}],
        "notes": "source parser freeze only",
    }
    from allthecontext.acceptance_receipt import validate_receipt

    with pytest.raises(ManifestError, match="exact_downloaded_artifact"):
        validate_receipt(provider_source_fragment)

from __future__ import annotations

from pathlib import Path

from scripts.demo import run_demo


def test_reproducible_vertical_slice_demo(tmp_path: Path) -> None:
    result = run_demo(tmp_path)

    assert result["result"] == "passed"
    assert result["checks_exercised"] == 7
    steps = {item["step"]: item for item in result["evidence"]}
    assert steps["ingest_approve_retrieve"]["batch_replay_idempotent"]
    assert steps["ingest_approve_retrieve"]["direct_core_retrieval"]
    assert steps["restart"]["core_record_retrieved"]
    assert steps["correct_and_delete"]["tombstone_version"] == 3
    assert steps["revoke_client"]["revoked_credential_rejected"]
    assert steps["encrypted_export_restore"]["restored_record_verified"]

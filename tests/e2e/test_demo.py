from __future__ import annotations

from pathlib import Path

from scripts.demo import run_demo


def test_reproducible_vertical_slice_demo(tmp_path: Path) -> None:
    result = run_demo(tmp_path)

    assert result["result"] == "passed"
    assert result["checks_exercised"] == 8
    steps = {item["step"]: item for item in result["evidence"]}
    assert steps["ingest_and_approve"]["batch_replay_idempotent"]
    assert steps["core_offline_relay_retrieval"]["core_only_record_unavailable"]
    assert steps["restart_and_reconcile"]["relay_proposals_acknowledged"]
    assert steps["correct_and_delete"]["deletion_propagated"]
    assert steps["revoke_client"]["revoked_credential_rejected"]
    assert steps["encrypted_export_restore"]["restored_record_verified"]

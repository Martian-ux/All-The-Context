"""Focused disposable Packet H-B Memory Truth proof tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import pytest
from allthecontext.capture import CaptureCapabilityManifest
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
)
from allthecontext.storage import CoreStore

from bench.packet_h import _assert_disposable_root, _runner_owned_temporary_root
from bench.packet_h_truth import (
    _public_truth_has_no_raw_material,
    _run_disposable,
    _truth_collections_match,
    _truth_summary,
    run,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _FakeTruthItem:
    def __init__(self, record_id: str) -> None:
        self.record = SimpleNamespace(id=record_id)
        self._state = {"record": {"id": record_id, "shape": "same"}}

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return self._state


def test_packet_h_truth_reports_four_current_records_and_safe_aggregate() -> None:
    report = run()

    assert report["boundary"] == "packet-h-b-memory-truth"
    assert report["acceptance"] == {
        "capture_admission_reconciles_with_truth": True,
        "capture_capability_posture": True,
        "content_free_identifier_safe": True,
        "deletion_reconciles_without_new_observation": True,
        "deleted_status_observed": True,
        "four_current_records": True,
        "listed_as_deleted": True,
        "memory_truth_identity_exact": True,
        "registered_source_truth": True,
        "restart_replay_stable": True,
        "withdrawal_is_exact_and_publicly_excluded": True,
    }
    truth = report["truth"]
    assert isinstance(truth, dict)
    before = truth["before_withdrawal"]
    assert isinstance(before, dict)
    assert before["item_count"] == 4
    assert before["status_counts"] == {"current": 4}
    assert before["fact_class_counts"] == {
        "markdown_documentation": 2,
        "python_source": 1,
        "shell_script": 1,
    }
    assert before["all_core_available"] is True
    assert before["all_normal_sensitivity"] is True
    assert before["all_registered_source_provenance"] is True
    assert before["all_registered_capture_type"] is True
    assert before["all_applied_evidence"] is True
    assert truth["details_match_list"] is True
    assert truth["list_detail_identity_exact"] is True
    assert truth["coverage_matches_list"] is True
    assert truth["replay_stable"] is True
    assert truth["replay_identity_exact"] is True

    withdrawal = report["withdrawal"]
    assert isinstance(withdrawal, dict)
    after = withdrawal["after_withdrawal"]
    assert isinstance(after, dict)
    assert after["item_count"] == 3
    assert after["status_counts"] == {"current": 3}
    assert withdrawal["deleted_items"]["item_count"] == 1
    assert withdrawal["deleted_status_observed"] is True
    assert withdrawal["exact_source_reference_withdrawn"] is True
    assert withdrawal["exact_untouched_record"] is True
    assert withdrawal["without_ordinary_tombstone"] is True
    assert withdrawal["excluded_from_current_list"] is True
    assert withdrawal["excluded_from_non_deleted_detail"] is True
    assert withdrawal["listed_as_deleted"] is True
    assert withdrawal["deleted_list_detail_identity_exact"] is True
    assert withdrawal["post_delete_replay_identity_exact"] is True
    assert withdrawal["post_delete_replay_stable"] is True

    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["receipt_type"] == "packet-h-b-aggregate"
    assert receipt["status"] == "pass"
    assert re.fullmatch(r"[0-9a-f]{64}", str(receipt["identifier_digest"]))

    rendered = json.dumps(report, sort_keys=True)
    for forbidden in (
        "# Sample workspace",
        "Use deterministic local fixtures",
        "def answer()",
        "not-for-capture",
        "AKIAIOSFODNN7EXAMPLE",
        "workspace-source-",
        "packet-h-fixture",
        "local-git-workspace",
        "registered-source-item-",
    ):
        assert forbidden not in rendered


def test_packet_h_truth_repeat_is_deterministic() -> None:
    assert run() == run()


def test_packet_h_truth_refuses_caller_owned_root_without_mutation() -> None:
    with TemporaryDirectory(prefix="atc-packet-h-truth-") as temporary:
        root = Path(temporary)
        sentinel = root / "sentinel.txt"
        sentinel.write_text("caller-owned\n", encoding="utf-8", newline="\n")
        before = sentinel.read_bytes()

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _run_disposable(root)

        assert sentinel.read_bytes() == before
        assert sorted(path.name for path in root.iterdir()) == ["sentinel.txt"]


def test_packet_h_truth_public_run_cleans_runner_owned_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots: list[Path] = []
    capabilities: list[object | None] = []

    def fake_run(root: Path, *, ownership: object | None = None) -> dict[str, object]:
        roots.append(root)
        capabilities.append(ownership)
        assert root.is_dir()
        assert not any(root.iterdir())
        assert ownership is not None
        return {"status": "pass"}

    monkeypatch.setattr("bench.packet_h_truth._run_disposable", fake_run)

    assert run() == {"status": "pass"}
    assert len(roots) == 1
    assert capabilities[0] is not None
    assert not roots[0].exists()


def test_packet_h_truth_rejects_non_disposable_root() -> None:
    with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
        _assert_disposable_root(Path.cwd())


def test_packet_h_truth_manifest_override_fails_closed() -> None:
    with _runner_owned_temporary_root("atc-packet-h-truth-") as (root, ownership):
        report = _run_disposable(
            root,
            ownership=ownership,
            capability_manifest_override=CaptureCapabilityManifest(
                provider=LOCAL_GIT_WORKSPACE_PROVIDER,
                availability="partial",
                coverage="partial",
                coverage_reason="controlled-test-override",
                network_access="allowed",
                data_egress=("controlled-egress",),
            ),
        )

    acceptance = report["acceptance"]
    receipt = report["aggregate_receipt"]
    assert isinstance(acceptance, dict)
    assert isinstance(receipt, dict)
    assert acceptance["capture_capability_posture"] is False
    assert receipt["status"] == "fail"


def test_packet_h_truth_applied_evidence_gate_cannot_pass_when_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_summary = _truth_summary

    def false_summary(items: Sequence[Any]) -> dict[str, object]:
        summary = original_summary(items)
        if summary["item_count"] == 4:
            summary["all_applied_evidence"] = False
        return summary

    monkeypatch.setattr("bench.packet_h_truth._truth_summary", false_summary)
    report = run()

    acceptance = report["acceptance"]
    receipt = report["aggregate_receipt"]
    assert isinstance(acceptance, dict)
    assert isinstance(receipt, dict)
    assert acceptance["registered_source_truth"] is False
    assert receipt["status"] == "fail"


def test_packet_h_truth_identity_swap_cannot_pass_same_shape() -> None:
    original = (_FakeTruthItem("record-a"), _FakeTruthItem("record-b"))
    swapped = (_FakeTruthItem("record-b"), _FakeTruthItem("record-a"))

    assert _truth_collections_match(original, original) is True
    assert _truth_collections_match(original, swapped) is False


def test_packet_h_truth_module_cli_uses_repository_root() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-m", "bench.packet_h_truth"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["aggregate_receipt"]["status"] == "pass"


def test_packet_h_truth_closes_core_stores_before_temporary_root_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_while_present: list[bool] = []
    original_close = CoreStore.close

    def tracking_close(self: CoreStore) -> None:
        close_while_present.append(self.database_path.exists())
        original_close(self)

    monkeypatch.setattr(CoreStore, "close", tracking_close)
    report = run()

    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["status"] == "pass"
    assert close_while_present
    assert all(close_while_present)


def test_packet_h_truth_other_markdown_withdrawal_cannot_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink

    def unlink_other_markdown(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "README.md":
            original_unlink(self.parent / "docs" / "decision.md", *args, **kwargs)
            return
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_other_markdown)
    report = run()

    acceptance = report["acceptance"]
    withdrawal = report["withdrawal"]
    receipt = report["aggregate_receipt"]
    assert isinstance(acceptance, dict)
    assert isinstance(withdrawal, dict)
    assert isinstance(receipt, dict)
    assert withdrawal["exact_source_reference_withdrawn"] is False
    assert acceptance["withdrawal_is_exact_and_publicly_excluded"] is False
    assert receipt["status"] == "fail"


def _leaking_truth_item(leaked: str) -> _FakeTruthItem:
    item = _FakeTruthItem("record-a")
    item._state = {"record": {"id": "record-a", "note": leaked}}
    return item


def _public_leak_args(workspace: Path) -> dict[str, Any]:
    return {
        "workspace": workspace,
        "source": SimpleNamespace(
            id="source-id",
            account_label="packet-h-fixture",
            account_fingerprint="fingerprint",
        ),
        "target_event": SimpleNamespace(provider_item_id="provider-item", payload={}),
    }


def test_packet_h_truth_native_workspace_path_in_public_field_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    leaked = _leaking_truth_item(str(workspace))

    assert _public_truth_has_no_raw_material([leaked], **_public_leak_args(workspace)) is False


def test_packet_h_truth_posix_workspace_path_in_public_field_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    leaked = _leaking_truth_item(workspace.as_posix())

    assert _public_truth_has_no_raw_material([leaked], **_public_leak_args(workspace)) is False

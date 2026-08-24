"""Focused disposable Packet H-C Retrieval V3 evidence tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import NoReturn

import pytest
from allthecontext.experimental_local_git_workspace_connector import (
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.retrieval import RetrievalEngine

import bench.packet_h as packet_h_a
import bench.packet_h_retrieval as packet_h_c
from bench.packet_h import run as run_packet_h_a
from bench.packet_h_retrieval import _assert_public_report_safe, _run_disposable, run

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _refresh_h_a_digest(report: dict[str, object]) -> None:
    capture = report["capture"]
    incomplete_probe = report["incomplete_probe"]
    acceptance = report["acceptance"]
    receipt = report["aggregate_receipt"]
    assert isinstance(capture, dict)
    assert isinstance(incomplete_probe, dict)
    assert isinstance(acceptance, dict)
    assert isinstance(receipt, dict)
    receipt["identifier_digest"] = packet_h_a._stable_digest(
        packet_h_a._h_a_digest_material(capture, incomplete_probe, acceptance)
    )


def _assert_h_a_report_refused_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, object],
) -> None:
    def fake_admission(
        _root: Path,
        *,
        ownership: object | None = None,
    ) -> dict[str, object]:
        return report

    def unexpected_store(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("H-C retrieval proof started before H-A report validation")

    monkeypatch.setattr(packet_h_c, "_run_admission_disposable", fake_admission)
    monkeypatch.setattr(packet_h_c, "CoreStore", unexpected_store)
    with pytest.raises(AssertionError, match="requires a complete H-A admission state"):
        packet_h_c.run()


def test_packet_h_c_reports_bounded_retrieval_scorecard() -> None:
    report = run()

    assert report["boundary"] == "packet-h-c-retrieval-v3"
    assert report["evidence_scope"] == "disposable-local-evidence-only"
    assert report["admission_state_ready"] is True
    assert report["acceptance"] == {
        "bootstrap_budget_compliance": True,
        "exact_get_consistency": True,
        "negative_query_exclusion": True,
        "provenance_packaging": True,
        "repeat_determinism": True,
        "structural_fact_recall": True,
        "withdrawal_exclusion": True,
    }

    scorecard = report["scorecard"]
    assert isinstance(scorecard, dict)
    assert scorecard["structural_fact_recall"] == {
        "expected_count": 4,
        "fact_class_counts": {
            "markdown_documentation": 2,
            "python_source": 1,
            "shell_script": 1,
        },
        "passed": True,
        "retrieved_count": 4,
    }
    assert scorecard["exact_get"] == {
        "checked_count": 4,
        "matched_count": 4,
        "passed": True,
    }

    bootstrap = scorecard["bootstrap"]
    assert isinstance(bootstrap, dict)
    assert bootstrap["budget_chars"] == 256
    assert bootstrap["used_chars"] <= bootstrap["budget_chars"]
    assert bootstrap["passed"] is True

    negatives = scorecard["negative_queries"]
    assert isinstance(negatives, dict)
    assert set(negatives) == {"path", "source_text", "secret_content"}
    assert all(item["total"] == 0 and item["returned_count"] == 0 for item in negatives.values())

    withdrawal = scorecard["withdrawal"]
    assert isinstance(withdrawal, dict)
    assert withdrawal["adapter_delete_completed"] is True
    assert withdrawal["adapter_delete_applied_events"] == 1
    assert withdrawal["post_delete_search_total"] == 0
    assert withdrawal["post_delete_get_is_none"] is True
    assert withdrawal["post_delete_bootstrap_excludes"] is True

    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["receipt_type"] == "packet-h-c-aggregate"
    assert receipt["status"] == "pass"
    assert re.fullmatch(r"[0-9a-f]{64}", str(receipt["stable_digest"]))


def test_packet_h_c_repeat_is_deterministic_and_content_free() -> None:
    first = run()
    second = run()

    assert first == second
    rendered = json.dumps(first, sort_keys=True)
    for forbidden in (
        "# Sample workspace",
        "Use deterministic local fixtures",
        "def answer()",
        "AKIAIOSFODNN7EXAMPLE",
        "FIXTURE_SECRET",
        "workspace-source-",
        "README.md",
        "docs/decision.md",
    ):
        assert forbidden not in rendered


def test_packet_h_c_refuses_preexisting_prefixed_root_without_mutation() -> None:
    with TemporaryDirectory(prefix="atc-packet-h-c-") as temporary:
        root = Path(temporary)
        sentinel = root / "sentinel.txt"
        sentinel.write_text("caller-owned\n", encoding="utf-8", newline="\n")
        before = sentinel.read_bytes()

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _run_disposable(root)

        assert sentinel.read_bytes() == before
        assert sorted(path.name for path in root.iterdir()) == ["sentinel.txt"]


def test_packet_h_c_refuses_failed_h_a_receipt_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = deepcopy(run_packet_h_a())
    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    receipt["status"] = "fail"

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


def test_packet_h_c_refuses_h_a_digest_mismatch_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = deepcopy(run_packet_h_a())
    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    receipt["identifier_digest"] = "0" * 64

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


@pytest.mark.parametrize("field", ["manifest_coverage", "manifest_availability"])
def test_packet_h_c_refuses_wrong_h_a_coverage_or_availability(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    report = deepcopy(run_packet_h_a())
    capture = report["capture"]
    assert isinstance(capture, dict)
    capture[field] = "complete"
    _refresh_h_a_digest(report)

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


@pytest.mark.parametrize(
    ("field", "value"),
    [("applied_events", 1), ("status", "failed")],
)
def test_packet_h_c_refuses_wrong_h_a_replay_state(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    report = deepcopy(run_packet_h_a())
    capture = report["capture"]
    assert isinstance(capture, dict)
    replay = capture["replay_run"]
    assert isinstance(replay, dict)
    replay[field] = value
    _refresh_h_a_digest(report)

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


def test_packet_h_c_refuses_malformed_incomplete_probe_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = deepcopy(run_packet_h_a())
    incomplete_probe = report["incomplete_probe"]
    assert isinstance(incomplete_probe, dict)
    incomplete_probe["scan_incomplete"] = "true"
    _refresh_h_a_digest(report)

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


@pytest.mark.parametrize(
    "malformation",
    ["coverage", "availability", "network", "run_status", "receipt_status"],
)
def test_packet_h_c_h_a_validator_rejects_unhashable_status_values(malformation: str) -> None:
    report = deepcopy(run_packet_h_a())
    capture = report["capture"]
    receipt = report["aggregate_receipt"]
    assert isinstance(capture, dict)
    assert isinstance(receipt, dict)

    if malformation == "coverage":
        capture["manifest_coverage"] = []
    elif malformation == "availability":
        capture["manifest_availability"] = []
    elif malformation == "network":
        capture["network_access"] = []
    elif malformation == "run_status":
        first_run = capture["first_run"]
        assert isinstance(first_run, dict)
        first_run["status"] = []
    else:
        receipt["status"] = []

    assert packet_h_c._packet_h_a_report_ready(report) is False


def test_packet_h_c_refuses_same_size_required_acceptance_key_replacement() -> None:
    report = deepcopy(run_packet_h_a())
    acceptance = report["acceptance"]
    assert isinstance(acceptance, dict)
    acceptance["extra_acceptance_predicate"] = acceptance.pop("bounded_admission")

    assert packet_h_c._packet_h_a_report_ready(report) is False


def test_packet_h_c_refuses_forged_nested_raw_field_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = deepcopy(run_packet_h_a())
    capture = report["capture"]
    assert isinstance(capture, dict)
    scan = capture["scan"]
    assert isinstance(scan, dict)
    scan["raw_fixture"] = "README.md"
    _refresh_h_a_digest(report)

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


def test_packet_h_c_accepts_additional_true_h_a_acceptance_predicate() -> None:
    report = deepcopy(run_packet_h_a())
    acceptance = report["acceptance"]
    assert isinstance(acceptance, dict)
    acceptance["local_only_network_egress_posture"] = True
    _refresh_h_a_digest(report)

    assert packet_h_c._packet_h_a_report_ready(report)


@pytest.mark.parametrize(
    "malformation",
    ["failed_predicate", "missing_predicate", "non_bool_predicate"],
)
def test_packet_h_c_refuses_failed_or_malformed_h_a_acceptance(
    monkeypatch: pytest.MonkeyPatch,
    malformation: str,
) -> None:
    report = deepcopy(run_packet_h_a())
    acceptance = report["acceptance"]
    assert isinstance(acceptance, dict)
    if malformation == "failed_predicate":
        acceptance["bounded_admission"] = False
    elif malformation == "missing_predicate":
        del acceptance["bounded_admission"]
    else:
        acceptance["bounded_admission"] = 1
    _refresh_h_a_digest(report)

    _assert_h_a_report_refused_before_retrieval(monkeypatch, report)


def test_packet_h_c_public_report_safety_rejects_fixture_material() -> None:
    unsafe = {
        "aggregate_receipt": {"stable_digest": "0" * 64},
        "leaked": "README.md",
    }

    with pytest.raises(AssertionError, match="unbounded fixture material"):
        _assert_public_report_safe(unsafe)


def test_packet_h_c_public_report_safety_rejects_path_like_identifier() -> None:
    unsafe = {
        "aggregate_receipt": {"stable_digest": "0" * 64},
        "leaked": r"C:\Users\example\workspace",
    }

    with pytest.raises(AssertionError, match="not identifier-safe"):
        _assert_public_report_safe(unsafe)


def test_packet_h_c_module_cli_uses_repository_root() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-m", "bench.packet_h_retrieval"],
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


def test_packet_h_c_nonstructural_search_item_cannot_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_search = RetrievalEngine.search

    def search_with_junk(
        self: RetrievalEngine,
        request: object,
        principal: object | None = None,
    ) -> object:
        response = original_search(self, request, principal)
        query = getattr(request, "query", None)
        if query != "workspace item" or not response.items:
            return response
        junk = response.items[0].model_copy(update={"content": "README.md"})
        return response.model_copy(
            update={"items": [*response.items, junk], "total": response.total + 1}
        )

    monkeypatch.setattr(RetrievalEngine, "search", search_with_junk)
    report = run()

    acceptance = report["acceptance"]
    receipt = report["aggregate_receipt"]
    assert isinstance(acceptance, dict)
    assert isinstance(receipt, dict)
    assert acceptance["structural_fact_recall"] is False
    assert receipt["status"] == "fail"


@pytest.mark.parametrize("escape", ["parent", "absolute"])
def test_packet_h_c_rejects_escaped_delete_path_before_unlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    escape: str,
) -> None:
    sentinel = tmp_path / "escape.py"
    sentinel.write_text("keep\n", encoding="utf-8", newline="\n")
    original_admission = packet_h_c._run_admission_disposable
    original_fetch = LocalGitWorkspaceCaptureProviderAdapter.fetch_page
    escaped_path = "../escape.py" if escape == "parent" else str(sentinel.resolve())

    def admission_then_arm(
        root: Path,
        *,
        ownership: object | None = None,
    ) -> dict[str, object]:
        report = original_admission(root, ownership=ownership)

        def fetch_escaped(
            self: LocalGitWorkspaceCaptureProviderAdapter,
            source: object,
            cursor: str | None,
            page_order: int,
        ) -> object:
            page = original_fetch(self, source, cursor, page_order)
            events = []
            for event in page.events:
                relative = event.payload.get("relative_path")
                if (
                    event.operation == "upsert"
                    and type(relative) is str
                    and PurePosixPath(relative).suffix.casefold() in {".py", ".pyw"}
                ):
                    payload = dict(event.payload)
                    payload["relative_path"] = escaped_path
                    event = replace(event, payload=payload)
                events.append(event)
            return replace(page, events=tuple(events))

        monkeypatch.setattr(LocalGitWorkspaceCaptureProviderAdapter, "fetch_page", fetch_escaped)
        return report

    monkeypatch.setattr(packet_h_c, "_run_admission_disposable", admission_then_arm)
    with pytest.raises(AssertionError, match="escaped the disposable workspace"):
        packet_h_c.run()
    assert sentinel.read_text(encoding="utf-8") == "keep\n"

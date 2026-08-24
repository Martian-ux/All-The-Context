"""Focused disposable Packet H-A source-admission proof tests."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Self, cast

import pytest
from allthecontext.experimental_local_git_workspace_connector import (
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_EXTRACTOR_ID,
    REGISTERED_SOURCE_EXTRACTOR_VERSION,
    REGISTERED_SOURCE_FACT_SCHEMA,
    REGISTERED_SOURCE_FACT_SENTENCES,
    registered_source_fact_evidence,
)
from allthecontext.storage import CoreStore

import bench.packet_h as packet_h
from bench.packet_h import (
    _assert_disposable_root,
    _DisposableRootCapability,
    _registered_source_row_is_content_free,
    _run_disposable,
    run,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_packet_h_reports_bounded_admission_and_safe_aggregate() -> None:
    report = run()

    assert report["boundary"] == "packet-h-a-source-admission"
    assert report["acceptance"] == {
        "bounded_admission": True,
        "content_free_identifier_safe": True,
        "deterministic_no_fact": True,
        "incomplete_fails_closed": True,
        "local_only_capability": True,
        "partial_coverage_truth": True,
        "restart_replay_idempotent": True,
    }
    capture = report["capture"]
    assert isinstance(capture, dict)
    assert capture["after_recovery"]["candidate_count"] == 4
    assert capture["after_recovery"]["record_count"] == 4
    assert capture["after_recovery"]["receipt_counts"] == {
        "registered-source-fact": 4,
        "registered-source-no-fact": 1,
    }
    assert capture["recovery_run"]["status"] == "completed"
    assert capture["replay_run"]["applied_events"] == 0

    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["receipt_type"] == "packet-h-a-aggregate"
    assert receipt["status"] == "pass"
    assert re.fullmatch(r"[0-9a-f]{64}", str(receipt["identifier_digest"]))

    rendered = json.dumps(report, sort_keys=True)
    for forbidden in (
        "# Sample workspace",
        "Use deterministic local fixtures",
        "def answer()",
        "metadata-only",
        "not-for-capture",
        "AKIAIOSFODNN7EXAMPLE",
        "workspace-source-",
    ):
        assert forbidden not in rendered


def test_packet_h_repeat_is_deterministic_and_overflow_truthful() -> None:
    first = run()
    second = run()

    assert first == second
    overflow = first["incomplete_probe"]
    assert isinstance(overflow, dict)
    assert overflow["manifest_coverage"] == "partial"
    assert overflow["run"]["status"] == "failed"
    assert overflow["run"]["error_code"] == "capture_page_limit_exceeded"
    assert overflow["scan_incomplete"] is True
    assert overflow["scan_items_emitted"] == 0
    assert overflow["candidate_count"] == 0
    assert overflow["record_count"] == 0


def test_packet_h_refuses_operator_or_non_disposable_core_roots() -> None:
    with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
        _assert_disposable_root(Path.cwd())


def test_packet_h_rejects_preexisting_prefixed_root_without_mutation() -> None:
    with TemporaryDirectory(prefix="atc-packet-h-") as temporary:
        root = Path(temporary)
        sentinel = root / "sentinel.txt"
        sentinel.write_text("caller-owned\n", encoding="utf-8", newline="\n")
        before = sentinel.read_bytes()

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _run_disposable(root)

        assert sentinel.read_bytes() == before
        assert sorted(path.name for path in root.iterdir()) == ["sentinel.txt"]


def test_packet_h_rejects_forged_ownership_without_mutation() -> None:
    class ForgedOwnership:
        def authorizes(self, root: Path) -> bool:
            return True

    with TemporaryDirectory(prefix="atc-packet-h-") as temporary:
        root = Path(temporary)
        sentinel = root / "sentinel.txt"
        sentinel.write_text("caller-owned\n", encoding="utf-8", newline="\n")
        before = sentinel.read_bytes()

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _run_disposable(root, ownership=ForgedOwnership())

        assert sentinel.read_bytes() == before
        assert sorted(path.name for path in root.iterdir()) == ["sentinel.txt"]


def test_packet_h_rejects_fake_temporary_object_before_reading_name() -> None:
    class FakeTemp:
        @property
        def name(self) -> str:
            raise AssertionError("fake temporary name was read")

    with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
        _DisposableRootCapability(FakeTemp())

    with (
        TemporaryDirectory(prefix="atc-packet-h-") as temporary,
        pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"),
    ):
        _DisposableRootCapability(temporary)


def test_packet_h_capability_requires_internal_token_and_is_immutable() -> None:
    assert "_CAPABILITY_CONSTRUCTION_TOKEN" not in vars(packet_h)

    with TemporaryDirectory(prefix="atc-packet-h-") as temporary:
        root = Path(temporary)
        assert not any(root.iterdir())

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _DisposableRootCapability(temporary)
        assert not any(root.iterdir())

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _DisposableRootCapability(temporary, runner_token=object())
        assert not any(root.iterdir())

    with packet_h._runner_owned_temporary_root("atc-packet-h-") as (root, ownership):
        assert root == root.resolve()
        for field, value in (
            ("_root", root / "retargeted"),
            ("_temporary_name", "retargeted"),
            ("_temporary_directory", object()),
        ):
            with pytest.raises(AttributeError, match="immutable"):
                setattr(ownership, field, value)
        assert ownership.authorizes(root)


def test_packet_h_caller_owned_temp_cannot_use_imported_token_paths() -> None:
    with TemporaryDirectory(prefix="atc-packet-h-") as temporary:
        for token in (None, object(), temporary, packet_h):
            with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
                _DisposableRootCapability(temporary, runner_token=token)


def test_packet_h_capability_rejects_ordinary_root_retargeting() -> None:
    with packet_h._runner_owned_temporary_root("atc-packet-h-") as (root, ownership):
        retargeted = root / "retargeted"
        retargeted.mkdir()

        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _assert_disposable_root(retargeted, ownership=ownership)


def test_packet_h_rejects_redirecting_path_before_caller_owned_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caller_owned = tmp_path / "caller-owned"
    caller_owned.mkdir()
    sentinel = caller_owned / "sentinel.txt"
    sentinel.write_text("caller-owned\n", encoding="utf-8", newline="\n")
    before = sentinel.read_bytes()

    class RedirectingPath(Path):
        def __truediv__(self, child: str | os.PathLike[str]) -> Self:
            return cast(Self, caller_owned / child)

    def fake_run(
        root: Path,
        *,
        ownership: object | None = None,
    ) -> dict[str, object]:
        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _run_disposable(RedirectingPath(str(root)), ownership=ownership)
        assert sentinel.read_bytes() == before
        assert sorted(path.name for path in caller_owned.iterdir()) == ["sentinel.txt"]
        return {"status": "pass"}

    monkeypatch.setattr(packet_h, "_run_disposable", fake_run)
    assert packet_h.run() == {"status": "pass"}


def test_packet_h_receipt_rejects_controlled_non_local_manifest_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsafeManifestAdapter(LocalGitWorkspaceCaptureProviderAdapter):
        def __init__(self, roots: Iterable[Path]) -> None:
            super().__init__(roots)
            self._capability_manifest = replace(
                self._capability_manifest,
                network_access="allowed",
                data_egress=("controlled-test-egress",),
            )

    monkeypatch.setattr(packet_h, "LocalGitWorkspaceCaptureProviderAdapter", UnsafeManifestAdapter)
    report = packet_h.run()

    capture = report["capture"]
    assert isinstance(capture, dict)
    assert capture["network_access"] == "allowed"
    assert capture["data_egress_count"] == -1
    acceptance = report["acceptance"]
    assert isinstance(acceptance, dict)
    assert acceptance["local_only_capability"] is False
    receipt = report["aggregate_receipt"]
    assert isinstance(receipt, dict)
    assert receipt["status"] == "fail"


def _cli_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def _run_packet_h_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=_REPOSITORY_ROOT,
        env=_cli_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _valid_projection() -> tuple[str, dict[str, object], str]:
    binding_hash = "a" * 64
    fact_class = "markdown_documentation"
    structured = {
        "binding_hash": binding_hash,
        "extractor": REGISTERED_SOURCE_EXTRACTOR_ID,
        "extractor_version": REGISTERED_SOURCE_EXTRACTOR_VERSION,
        "fact_class": fact_class,
        "schema": REGISTERED_SOURCE_FACT_SCHEMA,
    }
    return (
        REGISTERED_SOURCE_FACT_SENTENCES[fact_class],
        structured,
        registered_source_fact_evidence(fact_class, binding_hash),
    )


def test_packet_h_direct_file_cli_uses_repository_root() -> None:
    completed = _run_packet_h_cli([str(_REPOSITORY_ROOT / "bench" / "packet_h.py")])

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["aggregate_receipt"]["status"] == "pass"


def test_packet_h_module_cli_uses_repository_root() -> None:
    completed = _run_packet_h_cli(["-m", "bench.packet_h"])

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["aggregate_receipt"]["status"] == "pass"


def test_packet_h_public_run_cleans_runner_owned_root(monkeypatch: pytest.MonkeyPatch) -> None:
    roots: list[Path] = []
    capabilities: list[object | None] = []

    def fake_run(
        root: Path,
        *,
        ownership: object | None = None,
    ) -> dict[str, object]:
        roots.append(root)
        capabilities.append(ownership)
        assert root.is_dir()
        assert not any(root.iterdir())
        assert ownership is not None
        return {"status": "pass"}

    monkeypatch.setattr("bench.packet_h._run_disposable", fake_run)

    assert run() == {"status": "pass"}
    assert len(roots) == 1
    assert capabilities[0] is not None
    assert not roots[0].exists()


def test_packet_h_closes_core_stores_before_temporary_root_teardown(
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


def test_packet_h_fails_closed_when_allthecontext_resolves_outside_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_file = tmp_path / "foreign" / "allthecontext" / "__init__.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(packet_h.allthecontext, "__file__", str(fake_file))

    with pytest.raises(RuntimeError, match="outside this checkout"):
        packet_h._require_checkout_allthecontext()


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_packet_h_rejects_symlink_or_reparse_root_stat(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    with packet_h._runner_owned_temporary_root("atc-packet-h-") as (root, ownership):
        original_lstat = Path.lstat

        def fake_lstat(self: Path) -> object:
            result = original_lstat(self)
            if self.resolve() != root.resolve():
                return result
            if kind == "symlink":
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_file_attributes=0)
            return SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o777,
                st_file_attributes=packet_h._REPARSE_POINT,
            )

        monkeypatch.setattr(Path, "lstat", fake_lstat)
        with pytest.raises(ValueError, match="packet_h_requires_disposable_temporary_root"):
            _assert_disposable_root(root, ownership=ownership)


def test_packet_h_registered_source_row_rejects_contaminated_evidence() -> None:
    content, structured, evidence = _valid_projection()

    assert _registered_source_row_is_content_free(content, structured, evidence) is True
    assert _registered_source_row_is_content_free(content, structured, "README.md") is False
    assert _registered_source_row_is_content_free("README.md", structured, evidence) is False
    contaminated_structured = dict(structured)
    contaminated_structured["extractor"] = "docs/decision.md"
    assert (
        _registered_source_row_is_content_free(content, contaminated_structured, evidence) is False
    )


def test_packet_h_contaminated_candidate_evidence_fails_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = packet_h._query_capture_aggregate
    contaminated = {"done": False}

    def wrapped(store: CoreStore, source_id: str) -> dict[str, object]:
        if not contaminated["done"]:
            with store.connect() as connection:
                row = connection.execute(
                    "SELECT id FROM context_candidates WHERE capture_source_id=? ORDER BY id",
                    (source_id,),
                ).fetchone()
                assert row is not None
                connection.execute(
                    "UPDATE context_candidates SET evidence=? WHERE id=?",
                    ("README.md", row["id"]),
                )
            contaminated["done"] = True
        return original(store, source_id)

    monkeypatch.setattr(packet_h, "_query_capture_aggregate", wrapped)
    report = run()

    acceptance = report["acceptance"]
    receipt = report["aggregate_receipt"]
    assert isinstance(acceptance, dict)
    assert isinstance(receipt, dict)
    assert acceptance["content_free_identifier_safe"] is False
    assert receipt["status"] == "fail"


def test_packet_h_contaminated_record_evidence_fails_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = packet_h._query_capture_aggregate
    contaminated = {"done": False}

    def wrapped(store: CoreStore, source_id: str) -> dict[str, object]:
        if not contaminated["done"]:
            with store.connect() as connection:
                row = connection.execute(
                    "SELECT r.id FROM context_records r "
                    "JOIN context_candidates c ON c.id=r.candidate_id "
                    "WHERE c.capture_source_id=? ORDER BY r.id",
                    (source_id,),
                ).fetchone()
                assert row is not None
                connection.execute(
                    "UPDATE context_records SET evidence=? WHERE id=?",
                    ("README.md", row["id"]),
                )
            contaminated["done"] = True
        return original(store, source_id)

    monkeypatch.setattr(packet_h, "_query_capture_aggregate", wrapped)
    report = run()

    acceptance = report["acceptance"]
    receipt = report["aggregate_receipt"]
    assert isinstance(acceptance, dict)
    assert isinstance(receipt, dict)
    assert acceptance["content_free_identifier_safe"] is False
    assert receipt["status"] == "fail"

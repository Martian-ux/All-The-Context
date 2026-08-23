"""Focused isolated tests for the explicit-root local workspace adapter."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from allthecontext import experimental_local_git_workspace_connector as local_git_connector
from allthecontext.capture import (
    CaptureCoordinator,
    CaptureError,
    CaptureSource,
    IdempotentFakeSink,
)
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.storage import CoreStore

from tests.fixtures.local_git_workspace import create_sanitized_workspace


def _adapter(root: Path) -> LocalGitWorkspaceCaptureProviderAdapter:
    return LocalGitWorkspaceCaptureProviderAdapter((root,))


def _fetch(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    cursor: str | None = None,
    page_order: int = 0,
    source: CaptureSource | None = None,
) -> Any:
    return adapter.fetch_page(source or _bound_source(adapter), cursor, page_order)


def _bound_source(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    *,
    provider: str = LOCAL_GIT_WORKSPACE_PROVIDER,
    fingerprint: str | None = None,
) -> CaptureSource:
    timestamp = "2026-01-01T00:00:00.000000Z"
    return CaptureSource(
        id="synthetic-source",
        provider=provider,
        account_label="sanitized-local-workspace",
        account_fingerprint=adapter.source_identity if fingerprint is None else fingerprint,
        requested_scopes=(),
        local_only=True,
        local_only_acknowledged=True,
        lifecycle_state="enabled",
        retry_count=0,
        next_retry_at=None,
        last_error_code=None,
        last_error_at=None,
        lag_events=0,
        lag_pages=0,
        created_at=timestamp,
        updated_at=timestamp,
        last_run_at=None,
    )


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.sqlite3")
    store.initialize_vault()
    return store


def test_requires_explicit_non_overlapping_roots(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit_root_required"):
        LocalGitWorkspaceCaptureProviderAdapter(())
    with pytest.raises(ValueError, match="explicit_roots_must_be_a_sequence"):
        LocalGitWorkspaceCaptureProviderAdapter(cast(Any, tmp_path))

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    with pytest.raises(ValueError, match="overlapping_explicit_roots"):
        LocalGitWorkspaceCaptureProviderAdapter((root, root / "src"))


def test_manifest_declares_local_partial_and_bounded_posture(tmp_path: Path) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = _adapter(root)
    manifest = adapter.capability_manifest

    assert manifest.provider == LOCAL_GIT_WORKSPACE_PROVIDER
    assert manifest.availability == "partial"
    assert manifest.coverage == "partial"
    assert manifest.coverage_reason == "explicit-root-exclusions"
    assert manifest.acquisition_mode == "snapshot_and_incremental"
    assert manifest.initial_snapshot is True
    assert manifest.incremental is True
    assert manifest.cursor_support is True
    assert manifest.authorization == "authorized"
    assert manifest.network_access == "denied"
    assert manifest.data_egress == ()
    assert manifest.conformance().valid is True
    assert "git-metadata-excluded" in manifest.health_diagnostics
    assert adapter.source_identity.startswith("workspace-source-")
    assert len(adapter.source_identity) == len("workspace-source-") + 64


def test_fetch_requires_correct_provider_and_root_fingerprint_before_scan(
    tmp_path: Path,
) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = _adapter(root)
    bound = _bound_source(adapter)

    wrong_provider = replace(bound, provider="other-local-provider")
    wrong_fingerprint = replace(bound, account_fingerprint="workspace-source-wrong")
    for wrong_source in (wrong_provider, wrong_fingerprint):
        with pytest.raises(CaptureError, match="capture_capability_invalid") as raised:
            _fetch(adapter, source=wrong_source)
        assert raised.value.code == "capture_capability_invalid"
        assert adapter.last_scan_report is None

    page = _fetch(adapter, source=bound)
    assert page.events
    assert adapter.last_scan_report is not None


def test_snapshot_is_deterministic_and_excludes_git_credentials_and_outside_paths(
    tmp_path: Path,
) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside authorized root\n", encoding="utf-8")
    symlink = root / "outside-link.txt"
    try:
        symlink.symlink_to(outside)
    except (OSError, NotImplementedError):
        symlink = None  # type: ignore[assignment]

    first = _fetch(_adapter(root))
    second_adapter = _adapter(root)
    second = _fetch(second_adapter)

    assert first.events == second.events
    assert first.next_cursor == second.next_cursor
    assert [event.order_key for event in first.events] == sorted(
        event.order_key for event in first.events
    )
    relative_paths = {
        str(event.payload["relative_path"]) for event in first.events if event.operation == "upsert"
    }
    assert relative_paths == {
        "README.md",
        "docs/decision.md",
        "scripts/build.sh",
        "src/app.py",
    }
    assert all("outside" not in path for path in relative_paths)
    metadata_keys = {
        "relative_path",
        "root_id",
        "kind",
        "size",
        "content_sha256",
        "content_truncated",
        "hash_scope",
    }
    assert all(
        set(event.payload) == metadata_keys for event in first.events if event.operation == "upsert"
    )
    if symlink is not None:
        assert "outside-link.txt" not in relative_paths
    report = second_adapter.last_scan_report
    assert report is not None
    assert report.incomplete is False
    assert report.excluded_paths >= 2  # Git metadata and dependency directory.
    assert report.credential_like_paths >= 2  # .env and secret-like content.
    assert report.symlinks_or_reparse_points_skipped >= (1 if symlink is not None else 0)


def test_aws_access_key_shaped_config_is_omitted(tmp_path: Path) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = _adapter(root)
    page = _fetch(adapter)

    relative_paths = {
        str(event.payload["relative_path"]) for event in page.events if event.operation == "upsert"
    }
    assert "config/aws-shaped.ini" not in relative_paths
    report = adapter.last_scan_report
    assert report is not None
    assert report.credential_like_paths >= 3


def test_incremental_cursor_detects_change_and_deletion_after_restart(tmp_path: Path) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = _adapter(root)
    snapshot = _fetch(adapter)
    assert snapshot.next_cursor is not None

    (root / "src/app.py").write_text(
        "def answer() -> str:\n    return 'changed fixture'\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "docs/decision.md").unlink()
    incremental = _fetch(_adapter(root), snapshot.next_cursor)
    replay = _fetch(_adapter(root), snapshot.next_cursor)

    assert incremental.events == replay.events
    assert {event.operation for event in incremental.events} == {"upsert", "delete"}
    changed = [
        event
        for event in incremental.events
        if event.operation == "upsert" and event.payload.get("relative_path") == "src/app.py"
    ]
    deleted = [event for event in incremental.events if event.operation == "delete"]
    deleted_item_id = next(
        event.provider_item_id
        for event in snapshot.events
        if event.payload.get("relative_path") == "docs/decision.md"
    )
    assert len(changed) == 1
    assert "text" not in changed[0].payload
    assert f"g{incremental.generation}" in changed[0].provider_event_id
    assert len(deleted) == 1
    assert deleted[0].payload == {}
    assert deleted[0].provider_item_id == deleted_item_id
    assert f"g{incremental.generation}" in deleted[0].provider_event_id

    (root / "docs/decision.md").write_text(
        "Use deterministic local fixtures for connector tests.\n",
        encoding="utf-8",
        newline="\n",
    )
    recreated = _fetch(_adapter(root), incremental.next_cursor)
    recreated_upsert = next(
        event for event in recreated.events if event.provider_item_id == deleted_item_id
    )
    assert recreated_upsert.operation == "upsert"
    assert f"g{recreated.generation}" in recreated_upsert.provider_event_id
    assert recreated_upsert.provider_event_id != deleted[0].provider_event_id

    unchanged = _fetch(_adapter(root), recreated.next_cursor)
    assert unchanged.events == ()


def test_coordinator_replay_uses_existing_capture_idempotency_and_lineage(
    tmp_path: Path,
) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    sink = IdempotentFakeSink()
    coordinator = CaptureCoordinator(_store(tmp_path), sink=sink)
    adapter = _adapter(root)
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="sanitized-local-workspace",
        account_fingerprint=adapter.source_identity,
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)

    first = coordinator.run(source.id)
    replay = coordinator.run(source.id)
    assert first.status == "completed"
    assert first.applied_events == 4
    assert replay.status == "completed"
    assert replay.applied_events == 0
    assert replay.duplicate_events == 0
    assert len(sink.calls) == 4

    (root / "README.md").unlink()
    second = coordinator.run(source.id)
    assert second.status == "completed"
    assert second.applied_events == 1
    assert second.duplicate_events == 0
    assert len(sink.calls) == 5

    with coordinator.ledger.store.connect() as connection:
        counts = connection.execute(
            "SELECT operation,COUNT(*) AS count FROM capture_events "
            "WHERE source_id=? GROUP BY operation ORDER BY operation",
            (source.id,),
        ).fetchall()
    assert [(str(row["operation"]), int(row["count"])) for row in counts] == [
        ("delete", 1),
        ("upsert", 4),
    ]


def test_missing_root_fails_closed_without_inventing_deletions(tmp_path: Path) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    adapter = _adapter(root)
    snapshot = _fetch(adapter)
    assert snapshot.next_cursor is not None
    for child in tuple(root.iterdir()):
        if child.is_dir():
            for nested in sorted(child.rglob("*"), reverse=True):
                if nested.is_file():
                    nested.unlink()
                elif nested.is_dir():
                    nested.rmdir()
            child.rmdir()
        else:
            child.unlink()
    root.rmdir()

    with pytest.raises(CaptureError, match="capture_adapter_unavailable"):
        _fetch(adapter, snapshot.next_cursor)


def test_bounded_cursor_reports_incomplete_state_without_partial_events(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(21):
        (root / f"file-{index:02d}.txt").write_text(
            f"sanitized fixture {index}\n", encoding="utf-8", newline="\n"
        )

    adapter = _adapter(root)
    with pytest.raises(CaptureError, match="capture_page_limit_exceeded"):
        _fetch(adapter)
    report = adapter.last_scan_report
    assert report is not None
    assert report.incomplete is True
    assert report.items_emitted == 0


def test_discovery_cap_stops_before_lstat_or_reading_later_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = 3
    monkeypatch.setattr(local_git_connector, "MAX_DISCOVERED_FILES", cap)
    first_root = tmp_path / "a-root"
    second_root = tmp_path / "b-root"
    first_root.mkdir()
    second_root.mkdir()
    for index in range(cap + 1):
        (first_root / f"file-{index:02d}.txt").write_text(
            f"first root {index}\n", encoding="utf-8", newline="\n"
        )
    (second_root / "file-00.txt").write_text(
        "second root must not be read\n", encoding="utf-8", newline="\n"
    )

    read_entries: list[Path] = []
    original_read_item = LocalGitWorkspaceCaptureProviderAdapter._read_item

    def tracking_read_item(
        adapter: LocalGitWorkspaceCaptureProviderAdapter,
        entry: Path,
        size: int,
        root: Path,
        root_token: str,
        relative_path: str,
        report: Any,
    ) -> Any:
        read_entries.append(entry)
        return original_read_item(adapter, entry, size, root, root_token, relative_path, report)

    lstat_entries: list[Path] = []
    original_lstat = Path.lstat

    def tracking_lstat(entry: Path) -> Any:
        lstat_entries.append(entry)
        return original_lstat(entry)

    monkeypatch.setattr(LocalGitWorkspaceCaptureProviderAdapter, "_read_item", tracking_read_item)
    monkeypatch.setattr(Path, "lstat", tracking_lstat)

    adapter = LocalGitWorkspaceCaptureProviderAdapter((first_root, second_root))
    with pytest.raises(CaptureError, match="capture_adapter_unavailable"):
        _fetch(adapter)

    expected_read_entries = [first_root / f"file-{index:02d}.txt" for index in range(cap)]
    beyond_cap = {
        first_root / f"file-{cap:02d}.txt",
        second_root / "file-00.txt",
    }
    assert read_entries == expected_read_entries
    assert beyond_cap.isdisjoint(lstat_entries)
    report = adapter.last_scan_report
    assert report is not None
    assert report.authorized_root_count == 2
    assert report.files_considered == cap
    assert report.items_emitted == 0
    assert report.incomplete is True

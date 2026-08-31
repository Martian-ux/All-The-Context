"""Focused isolated tests for the explicit-root local workspace adapter."""

from __future__ import annotations

import os
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
from allthecontext.capture_runtime import _workspace_state_reader
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.storage import CoreStore

from tests.fixtures.local_git_workspace import create_sanitized_workspace


def _adapter(
    root: Path,
    state_reader: Any = None,
) -> LocalGitWorkspaceCaptureProviderAdapter:
    return LocalGitWorkspaceCaptureProviderAdapter((root,), state_reader=state_reader)


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


def _state_from_events(events: Any) -> dict[str, bytes | None]:
    state: dict[str, bytes | None] = {}
    for event in events:
        if event.operation == "upsert":
            state[event.provider_item_id] = bytes.fromhex(
                event.provider_event_id.rsplit(":", 1)[-1]
            )
        else:
            state[event.provider_item_id] = None
    return state


def _state_reader(state: dict[str, bytes | None]) -> Any:
    def read(source_id: str) -> dict[str, bytes | None]:
        del source_id
        return dict(state)

    return read


def _pages(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    *,
    cursor: str | None = None,
    page_order: int = 0,
) -> list[Any]:
    pages: list[Any] = []
    while True:
        page = _fetch(adapter, cursor, page_order=page_order)
        pages.append(page)
        if page.done:
            return pages
        assert page.next_cursor is not None
        cursor = page.next_cursor
        page_order += 1
        assert page_order < 100


def _all_events(pages: list[Any]) -> list[Any]:
    return [event for page in pages for event in page.events]


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
    assert "max-items-per-page-128" in manifest.health_diagnostics
    assert "max-run-pages-100" in manifest.health_diagnostics
    assert "max-run-events-10000" in manifest.health_diagnostics
    assert "max-effective-run-items-10000" in manifest.health_diagnostics
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
    snapshot = _pages(adapter)[-1]
    assert snapshot.next_cursor is not None
    state = _state_from_events(_all_events(_pages(_adapter(root))))
    state_reader = _state_reader(state)

    (root / "src/app.py").write_text(
        "def answer() -> str:\n    return 'changed fixture'\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "docs/decision.md").unlink()
    incremental_pages = _pages(_adapter(root, state_reader), cursor=snapshot.next_cursor)
    replay_pages = _pages(_adapter(root, state_reader), cursor=snapshot.next_cursor)
    incremental_events = _all_events(incremental_pages)
    replay_events = _all_events(replay_pages)

    assert incremental_events == replay_events
    assert {event.operation for event in incremental_events} == {"upsert", "delete"}
    changed = [
        event
        for event in incremental_events
        if event.operation == "upsert" and event.payload.get("relative_path") == "src/app.py"
    ]
    deleted = [event for event in incremental_events if event.operation == "delete"]
    deleted_item_id = next(
        event.provider_item_id
        for event in snapshot.events
        if event.payload.get("relative_path") == "docs/decision.md"
    )
    assert len(changed) == 1
    assert "text" not in changed[0].payload
    assert f"g{incremental_pages[0].generation}" in changed[0].provider_event_id
    assert len(deleted) == 1
    assert deleted[0].payload == {}
    assert deleted[0].provider_item_id == deleted_item_id
    assert f"g{incremental_pages[0].generation}" in deleted[0].provider_event_id

    for event in incremental_events:
        if event.operation == "upsert":
            state[event.provider_item_id] = bytes.fromhex(
                event.provider_event_id.rsplit(":", 1)[-1]
            )
        else:
            state[event.provider_item_id] = None

    (root / "docs/decision.md").write_text(
        "Use deterministic local fixtures for connector tests.\n",
        encoding="utf-8",
        newline="\n",
    )
    recreated_pages = _pages(_adapter(root, state_reader), cursor=incremental_pages[-1].next_cursor)
    recreated = recreated_pages[0]
    recreated_upsert = next(
        event for event in recreated.events if event.provider_item_id == deleted_item_id
    )
    assert recreated_upsert.operation == "upsert"
    assert f"g{recreated.generation}" in recreated_upsert.provider_event_id
    assert recreated_upsert.provider_event_id != deleted[0].provider_event_id

    unchanged = _fetch(_adapter(root, state_reader), recreated.next_cursor)
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


def test_fresh_direct_adapter_fails_closed_for_incremental_restart(tmp_path: Path) -> None:
    root = create_sanitized_workspace(tmp_path / "workspace")
    snapshot = _pages(_adapter(root))[-1]
    assert snapshot.next_cursor is not None
    (root / "src/app.py").write_text(
        "def answer() -> str:\n    return 'changed fixture'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(CaptureError, match="capture_adapter_unavailable"):
        _fetch(_adapter(root), snapshot.next_cursor)


def test_direct_adapter_fails_closed_when_partial_snapshot_mutates(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(129):
        (root / f"file-{index:03d}.txt").write_text(
            f"fixture {index}\n", encoding="utf-8", newline="\n"
        )

    first = _fetch(_adapter(root))
    assert first.done is False
    assert first.next_cursor is not None
    (root / "file-000.txt").unlink()

    with pytest.raises(CaptureError, match="capture_adapter_unavailable"):
        _fetch(_adapter(root), first.next_cursor, page_order=1)


def test_snapshot_paginates_beyond_historical_item_limit_and_rescans_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(513):
        (root / f"file-{index:02d}.txt").write_text(
            f"sanitized fixture {index}\n", encoding="utf-8", newline="\n"
        )

    read_count = 0
    original_read_item = LocalGitWorkspaceCaptureProviderAdapter._read_item

    def counting_read_item(
        adapter: LocalGitWorkspaceCaptureProviderAdapter,
        entry: Path,
        size: int,
        item_root: Path,
        root_token: str,
        relative_path: str,
        report: Any,
    ) -> Any:
        nonlocal read_count
        read_count += 1
        return original_read_item(
            adapter, entry, size, item_root, root_token, relative_path, report
        )

    monkeypatch.setattr(LocalGitWorkspaceCaptureProviderAdapter, "_read_item", counting_read_item)
    adapter = _adapter(root)
    pages = _pages(adapter)
    events = _all_events(pages)

    assert len(events) == 513
    # Every bounded page re-reads the full catalog so the digest stays bound to
    # sampled content even if a file's metadata is restored between pages.
    assert read_count == 513 * len(pages)
    assert all(event.operation == "upsert" for event in events)
    assert len(pages) == 5
    assert all(not page.done for page in pages[:-1])
    assert pages[-1].done is True
    assert all(page.next_cursor is not None and len(page.next_cursor) <= 1024 for page in pages)
    report = adapter.last_scan_report
    assert report is not None
    assert report.incomplete is False
    assert report.scan_complete is True
    assert report.scan_total == 513


def test_same_length_content_mutation_with_restored_mtime_resets_scan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(129):
        (root / f"file-{index:03d}.txt").write_text(
            f"fixture {index:03d}\n", encoding="utf-8", newline="\n"
        )

    state: dict[str, bytes | None] = {}
    adapter = _adapter(root, _state_reader(state))
    first = _fetch(adapter)
    assert first.done is False
    assert first.next_cursor is not None
    state.update(_state_from_events(first.events))

    target = root / "file-000.txt"
    before = target.stat()
    with target.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"altered000!\n")
        handle.truncate()
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    reset = _fetch(adapter, first.next_cursor, page_order=1)
    assert reset.events == ()
    assert reset.done is False
    assert reset.next_cursor is not None
    assert reset.generation > first.generation
    assert adapter.last_scan_report is not None
    assert adapter.last_scan_report.scan_reset is True

    resumed = _pages(adapter, cursor=reset.next_cursor, page_order=2)
    changed = [
        event
        for event in _all_events(resumed)
        if event.operation == "upsert" and event.payload.get("relative_path") == "file-000.txt"
    ]
    assert len(changed) == 1


def test_incremental_paginates_more_than_twenty_changed_items(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(32):
        (root / f"file-{index:02d}.txt").write_text(
            f"original fixture {index}\n", encoding="utf-8", newline="\n"
        )

    initial_pages = _pages(_adapter(root))
    snapshot = initial_pages[-1]
    state = _state_from_events(_all_events(initial_pages))
    state_reader = _state_reader(state)
    for index in range(25):
        (root / f"file-{index:02d}.txt").write_text(
            f"changed fixture {index}\n", encoding="utf-8", newline="\n"
        )

    pages = _pages(_adapter(root, state_reader), cursor=snapshot.next_cursor)
    events = _all_events(pages)
    assert len(events) == 25
    assert all(event.operation == "upsert" for event in events)
    assert pages[-1].done is True
    assert pages[-1].next_cursor is not None


def test_snapshot_page_replay_after_restart_is_identical(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(257):
        (root / f"file-{index:03d}.txt").write_text(
            f"fixture {index}\n", encoding="utf-8", newline="\n"
        )

    first_adapter = _adapter(root)
    first = _fetch(first_adapter)
    assert first.done is False
    assert first.next_cursor is not None
    replay = _fetch(_adapter(root), first.next_cursor, page_order=1)
    resumed = _fetch(first_adapter, first.next_cursor, page_order=1)
    assert replay == resumed


def test_mass_deletion_is_paginated_and_replayed_from_core_state(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(300):
        (root / f"file-{index:03d}.txt").write_text(
            f"fixture {index}\n", encoding="utf-8", newline="\n"
        )

    initial_pages = _pages(_adapter(root))
    snapshot = initial_pages[-1]
    state = _state_from_events(_all_events(initial_pages))
    for index in range(250):
        (root / f"file-{index:03d}.txt").unlink()

    state_reader = _state_reader(state)
    deletion_pages = _pages(_adapter(root, state_reader), cursor=snapshot.next_cursor)
    deletion_events = _all_events(deletion_pages)
    assert len(deletion_events) == 250
    assert all(event.operation == "delete" for event in deletion_events)
    assert len(deletion_pages) >= 3  # an empty reconciliation page plus two delete pages
    assert len({event.provider_item_id for event in deletion_events}) == 250
    assert deletion_pages[-1].done is True

    replay = _pages(_adapter(root, state_reader), cursor=snapshot.next_cursor)
    assert _all_events(replay) == deletion_events


def test_mutation_between_snapshot_pages_resets_to_a_new_full_generation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(257):
        (root / f"file-{index:03d}.txt").write_text(
            f"fixture {index}\n", encoding="utf-8", newline="\n"
        )

    state: dict[str, bytes | None] = {}
    adapter = _adapter(root, _state_reader(state))
    first = _fetch(adapter)
    assert first.next_cursor is not None
    deleted_item_id = next(
        event.provider_item_id
        for event in first.events
        if event.payload.get("relative_path") == "file-001.txt"
    )
    state.update(_state_from_events(first.events))
    (root / "file-000.txt").write_text("mutated fixture\n", encoding="utf-8", newline="\n")
    (root / "file-001.txt").unlink()
    (root / "file-257.txt").write_text("new fixture\n", encoding="utf-8", newline="\n")

    reset = _fetch(adapter, first.next_cursor, page_order=1)
    assert reset.events == ()
    assert reset.done is False
    assert reset.generation > first.generation
    assert adapter.last_scan_report is not None
    assert adapter.last_scan_report.scan_reset is True

    resumed = _pages(adapter, cursor=reset.next_cursor, page_order=2)
    events = _all_events(resumed)
    assert sum(event.operation == "upsert" for event in events) == 257 - 128 + 2
    assert sum(event.operation == "delete" for event in events) == 1
    assert {
        event.payload.get("relative_path") for event in events if event.operation == "upsert"
    } == {"file-000.txt"} | {f"file-{index:03d}.txt" for index in range(128, 258)}
    assert {event.provider_item_id for event in events if event.operation == "delete"} == {
        deleted_item_id
    }
    assert all(event.generation == reset.generation for event in events)


def test_coordinator_reset_withdraws_item_admitted_before_snapshot_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(257):
        (root / f"file-{index:03d}.txt").write_text(
            f"fixture {index}\n", encoding="utf-8", newline="\n"
        )

    store = _store(tmp_path)

    class MutatingSink(IdempotentFakeSink):
        mutated = False

        def apply(self, event: Any, **kwargs: Any) -> str:
            receipt = super().apply(event, **kwargs)
            if not self.mutated and len(self.calls) == local_git_connector.MAX_TRACKED_ITEMS:
                (root / "file-000.txt").unlink()
                (root / "file-257.txt").write_text("new fixture\n", encoding="utf-8", newline="\n")
                self.mutated = True
            return receipt

    sink = MutatingSink()
    coordinator = CaptureCoordinator(store, sink=sink)
    adapter = _adapter(root, _workspace_state_reader(store))
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="sanitized-local-workspace",
        account_fingerprint=adapter.source_identity,
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)

    result = coordinator.run(source.id)
    assert result.status == "completed"
    assert sink.mutated is True

    with store.connect() as connection:
        counts = connection.execute(
            "SELECT item_state,COUNT(*) AS count FROM capture_items "
            "WHERE source_id=? GROUP BY item_state ORDER BY item_state",
            (source.id,),
        ).fetchall()
        deleted = connection.execute(
            "SELECT i.item_state,e.operation FROM capture_items AS i "
            "JOIN capture_events AS e ON e.id=i.last_event_id "
            "WHERE i.source_id=? AND e.operation='delete'",
            (source.id,),
        ).fetchall()
    assert [(str(row["item_state"]), int(row["count"])) for row in counts] == [
        ("active", 257),
        ("deleted", 1),
    ]
    assert len(deleted) == 1
    assert str(deleted[0]["item_state"]) == "deleted"


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
    assert adapter.capability_manifest.health == "degraded"
    assert "scan-incomplete" in adapter.capability_manifest.health_diagnostics

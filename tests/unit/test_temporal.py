from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from allthecontext.temporal import (
    SIDECAR_SCHEMA_VERSION,
    PolicyEligibility,
    TemporalDataError,
    TemporalFact,
    TemporalMaintenanceReason,
    TemporalMode,
    TemporalQuery,
    TemporalReason,
    TemporalResolution,
    TemporalSidecar,
    normalize_utc,
)


def _active(
    record_id: str,
    *,
    series: str = "person/timezone",
    start: str = "2026-01-01T00:00:00Z",
    updated: str | None = None,
    valid_to: str | None = None,
    expires: str | None = None,
    supersedes: str | None = None,
    revision: int = 1,
) -> TemporalFact:
    return TemporalFact.active(
        record_id=record_id,
        series_key=series,
        created_at=start,
        updated_at=updated or start,
        valid_from=start,
        valid_to=valid_to,
        expires_at=expires,
        supersedes_record_id=supersedes,
        revision=revision,
    )


def _eligible(*record_ids: str) -> PolicyEligibility:
    return PolicyEligibility.after_hard_policy(record_ids)


def _codes(result: TemporalResolution) -> dict[TemporalReason, int]:
    return {item.reason_code: item.count for item in result.diagnostics}


def test_utc_normalization_offsets_boundaries_and_aware_datetime() -> None:
    assert normalize_utc("2026-01-01T00:30:00+14:00") == "2025-12-31T10:30:00.000000Z"
    assert normalize_utc("2025-12-31T19:30:00-05:00") == "2026-01-01T00:30:00.000000Z"
    aware = datetime(2026, 7, 22, 8, 15, tzinfo=timezone(timedelta(hours=-4)))
    assert normalize_utc(aware) == "2026-07-22T12:15:00.000000Z"


def test_explicit_offsets_make_dst_fold_and_gap_boundaries_deterministic() -> None:
    # The repeated 01:30 wall time has two distinct instants during the fall fold.
    assert normalize_utc("2024-11-03T01:30:00-04:00") == "2024-11-03T05:30:00.000000Z"
    assert normalize_utc("2024-11-03T01:30:00-05:00") == "2024-11-03T06:30:00.000000Z"

    # The spring gap boundary is continuous in UTC despite skipping local wall time.
    before_gap = normalize_utc("2024-03-10T01:59:59-05:00")
    after_gap = normalize_utc("2024-03-10T03:00:00-04:00")
    assert before_gap == "2024-03-10T06:59:59.000000Z"
    assert after_gap == "2024-03-10T07:00:00.000000Z"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("2026-01-01T00:00:00", "temporal_timestamp_naive"),
        (datetime(2026, 1, 1), "temporal_timestamp_naive"),
        ("not-a-timestamp", "temporal_timestamp_invalid"),
    ],
)
def test_invalid_and_naive_timestamps_fail_with_sanitized_codes(
    value: str | datetime, reason: str
) -> None:
    with pytest.raises(TemporalDataError) as raised:
        normalize_utc(value)
    assert raised.value.reason_code == reason
    assert str(raised.value) == reason


@pytest.mark.parametrize(
    ("fact", "reason"),
    [
        (
            _active(
                "invalid-window",
                start="2026-01-02T00:00:00Z",
                valid_to="2026-01-01T00:00:00Z",
            ),
            "temporal_interval_invalid",
        ),
        (
            _active(
                "invalid-expiry",
                start="2026-01-02T00:00:00Z",
                expires="2026-01-02T00:00:00Z",
            ),
            "temporal_expiry_invalid",
        ),
    ],
)
def test_invalid_half_open_intervals_fail_closed(
    tmp_path: Path, fact: TemporalFact, reason: str
) -> None:
    sidecar = TemporalSidecar(tmp_path / "temporal.sqlite3")
    with pytest.raises(TemporalDataError) as raised:
        sidecar.rebuild([fact])
    assert raised.value.reason_code == reason


def test_current_and_as_of_apply_half_open_supersession_and_expiry(
    tmp_path: Path,
) -> None:
    sidecar = TemporalSidecar(tmp_path / "temporal.sqlite3")
    original = _active("record-a", start="2026-01-01T00:00:00Z")
    correction = _active(
        "record-b",
        start="2026-02-01T00:00:00Z",
        expires="2026-03-01T00:00:00Z",
        supersedes="record-a",
    )
    sidecar.rebuild([correction, original])
    eligibility = _eligible("record-a", "record-b")

    before = sidecar.resolve(TemporalQuery.as_of("2026-01-31T23:59:59Z"), eligibility)
    boundary = sidecar.resolve(
        TemporalQuery.current(at="2026-02-01T00:00:00Z"), eligibility
    )
    expired = sidecar.resolve(TemporalQuery.as_of("2026-03-01T00:00:00Z"), eligibility)

    assert before.mode is TemporalMode.AS_OF
    assert before.selected_record_ids == ("record-a",)
    assert boundary.mode is TemporalMode.CURRENT
    assert boundary.selected_record_ids == ("record-b",)
    # Supersession remains effective after the correction expires; the old
    # value cannot reappear merely because the newer value reached its expiry.
    assert expired.selected_record_ids == ()
    assert _codes(expired) == {
        TemporalReason.EXPIRED: 1,
        TemporalReason.SUPERSEDED: 1,
    }


def test_valid_to_expiry_future_and_tie_diagnostics_are_stable(tmp_path: Path) -> None:
    facts = [
        _active(
            "ended",
            series="ended",
            start="2025-01-01T00:00:00Z",
            valid_to="2026-01-01T00:00:00Z",
        ),
        _active(
            "expired",
            series="expired",
            start="2025-01-01T00:00:00Z",
            expires="2026-01-01T00:00:00Z",
        ),
        _active("future", series="future", start="2027-01-01T00:00:00Z"),
        _active("tie-b", series="tie"),
        _active("tie-a", series="tie"),
    ]
    sidecar = TemporalSidecar(tmp_path / "temporal.sqlite3")
    sidecar.rebuild(reversed(facts))
    eligibility = _eligible(*(fact.record_id for fact in facts))

    result = sidecar.resolve(TemporalQuery.current(at="2026-06-01T00:00:00Z"), eligibility)

    assert result.selected_record_ids == ("tie-a",)
    assert _codes(result) == {
        TemporalReason.SELECTED: 1,
        TemporalReason.NOT_YET_VALID: 1,
        TemporalReason.VALIDITY_ENDED: 1,
        TemporalReason.EXPIRED: 1,
        TemporalReason.SHADOWED: 1,
    }
    assert [item.reason_code for item in result.diagnostics] == [
        TemporalReason.SELECTED,
        TemporalReason.NOT_YET_VALID,
        TemporalReason.VALIDITY_ENDED,
        TemporalReason.EXPIRED,
        TemporalReason.SHADOWED,
    ]


def test_correction_revision_and_rebuild_order_are_deterministic(tmp_path: Path) -> None:
    earlier = _active("corrected", revision=1, updated="2026-01-01T00:00:00Z")
    corrected = _active("corrected", revision=2, updated="2026-01-02T00:00:00Z")
    peer = _active("peer", series="other")
    first = TemporalSidecar(tmp_path / "first.sqlite3")
    second = TemporalSidecar(tmp_path / "second.sqlite3")

    first.rebuild([earlier, peer, corrected])
    second.rebuild([corrected, peer, earlier])

    assert first.export_snapshot() == second.export_snapshot()
    interval = first.export_snapshot()["intervals"]
    assert isinstance(interval, list)
    assert next(item for item in interval if item["record_id"] == "corrected")["revision"] == 2

    query = TemporalQuery.current(at="2026-07-01T00:00:00Z")
    eligibility = _eligible("corrected", "peer")
    expected = first.resolve(query, eligibility)
    assert all(first.resolve(query, eligibility) == expected for _ in range(100))


def test_hard_policy_filter_precedes_supersession_and_diagnostics(tmp_path: Path) -> None:
    visible = _active("visible-record")
    unauthorized = _active(
        "unauthorized-record",
        start="2026-02-01T00:00:00Z",
        supersedes="visible-record",
    )
    with_hidden = TemporalSidecar(tmp_path / "with-hidden.sqlite3")
    visible_only = TemporalSidecar(tmp_path / "visible-only.sqlite3")
    with_hidden.rebuild([visible, unauthorized])
    visible_only.rebuild([visible])
    with closing(sqlite3.connect(tmp_path / "with-hidden.sqlite3")) as connection:
        connection.execute(
            "UPDATE temporal_intervals SET valid_from_utc='malformed-private-value' "
            "WHERE record_id='unauthorized-record'"
        )
        connection.commit()
    eligibility = _eligible("visible-record")
    query = TemporalQuery.current(at="2026-07-01T00:00:00Z")

    filtered = with_hidden.resolve(query, eligibility)
    baseline = visible_only.resolve(query, eligibility)

    assert filtered == baseline
    assert filtered.selected_record_ids == ("visible-record",)
    assert "unauthorized-record" not in repr(filtered.diagnostics)
    assert all(not hasattr(item, "record_id") for item in filtered.diagnostics)


def test_deleted_and_purged_facts_never_resurrect_across_all_sidecar_paths(
    tmp_path: Path,
) -> None:
    database = tmp_path / "temporal.sqlite3"
    stale_active_facts = [_active("deleted-record"), _active("purged-record", series="other")]
    sidecar = TemporalSidecar(database)
    sidecar.rebuild(stale_active_facts)
    stale_snapshot = sidecar.export_snapshot()

    authoritative = [
        *stale_active_facts,
        TemporalFact.deleted(
            record_id="deleted-record", deleted_at="2026-02-01T00:00:00Z"
        ),
        TemporalFact.purged(record_id="purged-record", purged_at="2026-02-02T00:00:00Z"),
    ]
    sidecar.rebuild(authoritative)
    eligibility = _eligible("deleted-record", "purged-record")
    queries = (
        TemporalQuery.current(at="2026-07-01T00:00:00Z"),
        TemporalQuery.as_of("2026-01-15T00:00:00Z"),
    )
    assert all(sidecar.resolve(query, eligibility).selected_record_ids == () for query in queries)
    assert all(sidecar.resolve(query, eligibility).diagnostics == () for query in queries)

    # Even a stale interval inserted after terminal state cannot bypass the
    # defensive terminal anti-join during resolution.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO temporal_intervals("
            "record_id,series_key,valid_from_utc,valid_to_utc,supersedes_record_id,"
            "revision,updated_at_utc,expires_at_utc) VALUES(?,?,?,?,?,?,?,?)",
            (
                "purged-record",
                "other",
                "2026-01-01T00:00:00.000000Z",
                None,
                None,
                1,
                "2026-01-01T00:00:00.000000Z",
                None,
            ),
        )
        connection.commit()
    assert sidecar.resolve(queries[0], eligibility).selected_record_ids == ()

    # Close/reopen and deterministic canonical reconciliation remain terminal.
    reopened = TemporalSidecar(database)
    assert reopened.resolve(queries[0], eligibility).selected_record_ids == ()
    assert reopened.recover(authoritative).reason_code is TemporalMaintenanceReason.STALE_REBUILT
    assert reopened.resolve(queries[0], eligibility).selected_record_ids == ()

    # A pre-terminal portable snapshot is rejected in favor of current
    # authoritative facts, so restore cannot revive either record.
    restored = TemporalSidecar(tmp_path / "restored.sqlite3")
    outcome = restored.restore_snapshot(stale_snapshot, authoritative)
    assert outcome.reason_code is TemporalMaintenanceReason.SNAPSHOT_REBUILT
    assert restored.resolve(queries[0], eligibility).selected_record_ids == ()
    snapshot = restored.export_snapshot()
    assert snapshot["intervals"] == []
    assert [item["state"] for item in snapshot["terminals"]] == ["deleted", "purged"]


def test_matching_portable_snapshot_restores_and_corrupt_snapshot_rebuilds(
    tmp_path: Path,
) -> None:
    facts = [_active("record-a"), _active("record-b", series="other")]
    original = TemporalSidecar(tmp_path / "original.sqlite3")
    original.rebuild(facts)
    snapshot = original.export_snapshot()

    matching = TemporalSidecar(tmp_path / "matching.sqlite3")
    assert (
        matching.restore_snapshot(snapshot, facts).reason_code
        is TemporalMaintenanceReason.SNAPSHOT_RESTORED
    )
    assert matching.export_snapshot() == snapshot

    corrupt = TemporalSidecar(tmp_path / "corrupt-snapshot.sqlite3")
    assert (
        corrupt.restore_snapshot({"schema_version": 999}, facts).reason_code
        is TemporalMaintenanceReason.SNAPSHOT_REBUILT
    )
    assert corrupt.export_snapshot() == snapshot


def test_v1_sidecar_migrates_in_place_without_core_migration_registration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v1-temporal.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE temporal_sidecar_metadata (
                singleton INTEGER PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                canonical_digest TEXT NOT NULL
            );
            INSERT INTO temporal_sidecar_metadata VALUES(1, 1, 'legacy');
            CREATE TABLE temporal_intervals (
                record_id TEXT PRIMARY KEY,
                series_key TEXT NOT NULL,
                valid_from_utc TEXT NOT NULL,
                valid_to_utc TEXT,
                supersedes_record_id TEXT,
                revision INTEGER NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            INSERT INTO temporal_intervals VALUES(
                'legacy-record', 'legacy-series', '2026-01-01T00:00:00.000000Z',
                NULL, NULL, 1, '2026-01-01T00:00:00.000000Z'
            );
            PRAGMA user_version = 1;
            """
        )
        connection.commit()

    sidecar = TemporalSidecar(database)
    result = sidecar.initialize()

    assert result.reason_code is TemporalMaintenanceReason.MIGRATED
    assert sidecar.schema_version() == SIDECAR_SCHEMA_VERSION
    with closing(sqlite3.connect(database)) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(temporal_intervals)")
        }
        terminal_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='temporal_terminal_records'"
        ).fetchone()
    assert "expires_at_utc" in columns
    assert terminal_table is not None
    resolved = sidecar.resolve(
        TemporalQuery.current(at="2026-07-01T00:00:00Z"),
        _eligible("legacy-record"),
    )
    assert resolved.selected_record_ids == ("legacy-record",)


def test_stale_and_file_corrupt_sidecars_recover_from_authoritative_facts(
    tmp_path: Path,
) -> None:
    facts = [
        _active("record-a"),
        TemporalFact.purged(record_id="gone", purged_at="2026-02-01T00:00:00Z"),
    ]
    database = tmp_path / "recover.sqlite3"
    sidecar = TemporalSidecar(database)
    sidecar.rebuild(facts)

    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE temporal_intervals SET series_key='stale-series' WHERE record_id='record-a'"
        )
        connection.commit()
    assert sidecar.recover(facts).reason_code is TemporalMaintenanceReason.STALE_REBUILT
    assert sidecar.recover(facts).reason_code is TemporalMaintenanceReason.CURRENT

    database.write_bytes(b"SQLite format 3\x00" + b"corrupt-page-data")
    recovered = sidecar.recover(facts)
    assert recovered.reason_code is TemporalMaintenanceReason.CORRUPT_REBUILT
    result = sidecar.resolve(
        TemporalQuery.current(at="2026-07-01T00:00:00Z"),
        _eligible("record-a", "gone"),
    )
    assert result.selected_record_ids == ("record-a",)
    assert "gone" not in repr(result.diagnostics)


def test_supersession_cycles_and_cross_series_edges_fail_closed(tmp_path: Path) -> None:
    sidecar = TemporalSidecar(tmp_path / "invalid.sqlite3")
    with pytest.raises(TemporalDataError, match="temporal_supersession_cycle"):
        sidecar.rebuild(
            [
                _active("a", supersedes="b"),
                _active("b", supersedes="a"),
            ]
        )
    with pytest.raises(TemporalDataError, match="temporal_supersession_invalid"):
        sidecar.rebuild(
            [
                _active("a"),
                _active("b", series="different", supersedes="a"),
            ]
        )


def test_long_supersession_chain_is_iterative_and_deterministic(tmp_path: Path) -> None:
    facts = [
        _active(
            f"record-{ordinal:04d}",
            start=f"2026-01-{ordinal % 28 + 1:02d}T{ordinal % 24:02d}:00:00Z",
            supersedes=f"record-{ordinal - 1:04d}" if ordinal else None,
        )
        for ordinal in range(1_500)
    ]
    sidecar = TemporalSidecar(tmp_path / "long-chain.sqlite3")
    sidecar.rebuild(facts)
    result = sidecar.resolve(
        TemporalQuery.current(at="2026-07-01T00:00:00Z"),
        _eligible(*(fact.record_id for fact in facts)),
    )
    # Starts are intentionally non-monotonic; every effective supersession
    # edge still closes its predecessor and leaves the deterministic chain head.
    assert result.selected_record_ids == ("record-1499",)

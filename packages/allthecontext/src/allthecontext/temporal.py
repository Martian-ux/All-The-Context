"""Deterministic, noncanonical temporal sidecars for policy-eligible records.

The Core remains authoritative.  This module stores only rebuildable temporal
metadata in a separate SQLite database and never stores record content.  A
caller must perform every hard policy check before constructing
``PolicyEligibility``; resolution cannot enumerate or diagnose sidecar rows
outside that explicit set.

Intervals are UTC-normalized and half-open: ``[valid_from, valid_to)``.  Expiry
is also exclusive.  A policy-eligible superseding record permanently closes
its predecessor at the superseder's effective start, even if the superseder
later expires.  Deleted and purged facts are terminal for both current and
historical queries and dominate stale active facts during every rebuild.

Naive local wall times are rejected.  Offset-aware timestamps make timezone
date boundaries and DST folds/gaps unambiguous without depending on the host's
timezone database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

type Timestamp = str | datetime

SIDECAR_FORMAT: Final = "allthecontext-temporal-sidecar"
SIDECAR_SCHEMA_VERSION: Final = 2
_MAX_ID_CHARS: Final = 512
_MAX_SERIES_KEY_CHARS: Final = 1_024


class TemporalDataError(ValueError):
    """Fail-closed temporal input error with a stable, sanitized code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class TemporalState(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"
    PURGED = "purged"


class TemporalMode(StrEnum):
    CURRENT = "current"
    AS_OF = "as_of"


class TemporalReason(StrEnum):
    SELECTED = "temporal_selected"
    NOT_YET_VALID = "temporal_not_yet_valid"
    VALIDITY_ENDED = "temporal_validity_ended"
    EXPIRED = "temporal_expired"
    SUPERSEDED = "temporal_superseded"
    SHADOWED = "temporal_shadowed"


class TemporalMaintenanceReason(StrEnum):
    CREATED = "temporal_sidecar_created"
    CURRENT = "temporal_sidecar_current"
    MIGRATED = "temporal_sidecar_migrated"
    REBUILT = "temporal_sidecar_rebuilt"
    STALE_REBUILT = "temporal_sidecar_stale_rebuilt"
    CORRUPT_REBUILT = "temporal_sidecar_corrupt_rebuilt"
    SNAPSHOT_RESTORED = "temporal_snapshot_restored"
    SNAPSHOT_REBUILT = "temporal_snapshot_rebuilt"


@dataclass(frozen=True, slots=True, kw_only=True)
class TemporalFact:
    """Content-free projection of one authoritative canonical record or tombstone."""

    record_id: str
    state: TemporalState = TemporalState.ACTIVE
    series_key: str | None = None
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    valid_from: Timestamp | None = None
    valid_to: Timestamp | None = None
    expires_at: Timestamp | None = None
    supersedes_record_id: str | None = None
    revision: int = 1
    terminal_at: Timestamp | None = None

    @classmethod
    def active(
        cls,
        *,
        record_id: str,
        series_key: str,
        created_at: Timestamp,
        updated_at: Timestamp | None = None,
        valid_from: Timestamp | None = None,
        valid_to: Timestamp | None = None,
        expires_at: Timestamp | None = None,
        supersedes_record_id: str | None = None,
        revision: int = 1,
    ) -> TemporalFact:
        return cls(
            record_id=record_id,
            series_key=series_key,
            created_at=created_at,
            updated_at=updated_at,
            valid_from=valid_from,
            valid_to=valid_to,
            expires_at=expires_at,
            supersedes_record_id=supersedes_record_id,
            revision=revision,
        )

    @classmethod
    def deleted(cls, *, record_id: str, deleted_at: Timestamp) -> TemporalFact:
        return cls(
            record_id=record_id,
            state=TemporalState.DELETED,
            terminal_at=deleted_at,
        )

    @classmethod
    def purged(cls, *, record_id: str, purged_at: Timestamp) -> TemporalFact:
        return cls(
            record_id=record_id,
            state=TemporalState.PURGED,
            terminal_at=purged_at,
        )


@dataclass(frozen=True, slots=True)
class PolicyEligibility:
    """Record IDs that already passed authorization and all other hard policy checks."""

    record_ids: frozenset[str]

    @classmethod
    def after_hard_policy(cls, record_ids: Iterable[str]) -> PolicyEligibility:
        checked = frozenset(_checked_identifier(value) for value in record_ids)
        return cls(record_ids=checked)


@dataclass(frozen=True, slots=True)
class TemporalQuery:
    mode: TemporalMode
    evaluated_at_utc: str

    @classmethod
    def current(cls, *, at: Timestamp) -> TemporalQuery:
        return cls(mode=TemporalMode.CURRENT, evaluated_at_utc=normalize_utc(at))

    @classmethod
    def as_of(cls, value: Timestamp) -> TemporalQuery:
        return cls(mode=TemporalMode.AS_OF, evaluated_at_utc=normalize_utc(value))


@dataclass(frozen=True, slots=True)
class TemporalDiagnostic:
    reason_code: TemporalReason
    count: int


@dataclass(frozen=True, slots=True)
class TemporalResolution:
    mode: TemporalMode
    evaluated_at_utc: str
    selected_record_ids: tuple[str, ...]
    diagnostics: tuple[TemporalDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class TemporalMaintenanceResult:
    reason_code: TemporalMaintenanceReason


@dataclass(frozen=True, slots=True)
class _IntervalRow:
    record_id: str
    series_key: str
    valid_from_utc: str
    valid_to_utc: str | None
    expires_at_utc: str | None
    supersedes_record_id: str | None
    revision: int
    updated_at_utc: str

    def as_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "series_key": self.series_key,
            "valid_from_utc": self.valid_from_utc,
            "valid_to_utc": self.valid_to_utc,
            "expires_at_utc": self.expires_at_utc,
            "supersedes_record_id": self.supersedes_record_id,
            "revision": self.revision,
            "updated_at_utc": self.updated_at_utc,
        }


@dataclass(frozen=True, slots=True)
class _TerminalRow:
    record_id: str
    state: TemporalState
    terminal_at_utc: str

    def as_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "state": self.state.value,
            "terminal_at_utc": self.terminal_at_utc,
        }


@dataclass(frozen=True, slots=True)
class _DerivedState:
    intervals: tuple[_IntervalRow, ...]
    terminals: tuple[_TerminalRow, ...]

    @property
    def digest(self) -> str:
        material = {
            "intervals": [row.as_dict() for row in self.intervals],
            "terminals": [row.as_dict() for row in self.terminals],
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def normalize_utc(value: Timestamp) -> str:
    """Normalize an offset-aware timestamp to fixed-width UTC ISO 8601."""

    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise TemporalDataError("temporal_timestamp_invalid") from exc
    else:
        raise TemporalDataError("temporal_timestamp_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TemporalDataError("temporal_timestamp_naive")
    try:
        normalized = parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise TemporalDataError("temporal_timestamp_invalid") from exc
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _checked_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_ID_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        raise TemporalDataError("temporal_identifier_invalid")
    return value


def _checked_series_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_SERIES_KEY_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        raise TemporalDataError("temporal_series_key_invalid")
    return value


def _state(value: object) -> TemporalState:
    if not isinstance(value, str):
        raise TemporalDataError("temporal_state_invalid")
    try:
        return TemporalState(value)
    except ValueError as exc:
        raise TemporalDataError("temporal_state_invalid") from exc


def _active_row(fact: TemporalFact) -> _IntervalRow:
    if fact.created_at is None or fact.series_key is None:
        raise TemporalDataError("temporal_active_fact_invalid")
    if isinstance(fact.revision, bool) or not isinstance(fact.revision, int) or fact.revision < 1:
        raise TemporalDataError("temporal_revision_invalid")
    record_id = _checked_identifier(fact.record_id)
    series_key = _checked_series_key(fact.series_key)
    valid_from = normalize_utc(fact.valid_from or fact.created_at)
    updated_at = normalize_utc(fact.updated_at or fact.created_at)
    valid_to = normalize_utc(fact.valid_to) if fact.valid_to is not None else None
    expires_at = normalize_utc(fact.expires_at) if fact.expires_at is not None else None
    if valid_to is not None and valid_to <= valid_from:
        raise TemporalDataError("temporal_interval_invalid")
    if expires_at is not None and expires_at <= valid_from:
        raise TemporalDataError("temporal_expiry_invalid")
    supersedes = (
        _checked_identifier(fact.supersedes_record_id)
        if fact.supersedes_record_id is not None
        else None
    )
    return _IntervalRow(
        record_id=record_id,
        series_key=series_key,
        valid_from_utc=valid_from,
        valid_to_utc=valid_to,
        expires_at_utc=expires_at,
        supersedes_record_id=supersedes,
        revision=fact.revision,
        updated_at_utc=updated_at,
    )


def _terminal_row(fact: TemporalFact, state: TemporalState) -> _TerminalRow:
    if fact.terminal_at is None:
        raise TemporalDataError("temporal_terminal_fact_invalid")
    return _TerminalRow(
        record_id=_checked_identifier(fact.record_id),
        state=state,
        terminal_at_utc=normalize_utc(fact.terminal_at),
    )


def _derive_state(facts: Iterable[TemporalFact]) -> _DerivedState:
    grouped: dict[str, list[TemporalFact]] = {}
    for fact in facts:
        record_id = _checked_identifier(fact.record_id)
        grouped.setdefault(record_id, []).append(fact)

    intervals: list[_IntervalRow] = []
    terminals: list[_TerminalRow] = []
    for record_id in sorted(grouped):
        group = grouped[record_id]
        terminal_facts = [fact for fact in group if _state(fact.state) is not TemporalState.ACTIVE]
        if terminal_facts:
            # Purge is stronger than deletion, and either terminal state always
            # dominates stale active copies of the same canonical identifier.
            strongest = max(
                terminal_facts,
                key=lambda fact: (
                    1 if _state(fact.state) is TemporalState.PURGED else 0,
                    normalize_utc(fact.terminal_at) if fact.terminal_at is not None else "",
                ),
            )
            terminals.append(_terminal_row(strongest, _state(strongest.state)))
            continue

        active = [_active_row(fact) for fact in group]
        selected = max(
            active,
            key=lambda row: (
                row.revision,
                row.updated_at_utc,
                row.valid_from_utc,
                json.dumps(row.as_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )
        intervals.append(selected)

    by_id = {row.record_id: row for row in intervals}
    for row in intervals:
        target_id = row.supersedes_record_id
        if target_id is None or target_id not in by_id:
            continue
        if by_id[target_id].series_key != row.series_key:
            raise TemporalDataError("temporal_supersession_invalid")

    _reject_supersession_cycles(by_id)
    return _DerivedState(intervals=tuple(intervals), terminals=tuple(terminals))


def _reject_supersession_cycles(by_id: Mapping[str, _IntervalRow]) -> None:
    visited: set[str] = set()
    for start in sorted(by_id):
        if start in visited:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in by_id and current not in visited:
            if current in positions:
                raise TemporalDataError("temporal_supersession_cycle")
            positions[current] = len(path)
            path.append(current)
            current = by_id[current].supersedes_record_id
        visited.update(path)


_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS temporal_sidecar_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    canonical_digest TEXT NOT NULL
);
INSERT OR IGNORE INTO temporal_sidecar_metadata(singleton, schema_version, canonical_digest)
VALUES(1, 1, '');

CREATE TABLE IF NOT EXISTS temporal_intervals (
    record_id TEXT PRIMARY KEY,
    series_key TEXT NOT NULL,
    valid_from_utc TEXT NOT NULL,
    valid_to_utc TEXT,
    supersedes_record_id TEXT,
    revision INTEGER NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_temporal_series
    ON temporal_intervals(series_key, valid_from_utc, record_id);
PRAGMA user_version = 1;
"""

_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS temporal_terminal_records (
    record_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('deleted', 'purged')),
    terminal_at_utc TEXT NOT NULL
);
UPDATE temporal_sidecar_metadata SET schema_version = 2 WHERE singleton = 1;
PRAGMA user_version = 2;
"""


class TemporalSidecar:
    """Separate SQLite sidecar that is always safe to discard and rebuild."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> TemporalMaintenanceResult:
        with self._write_lock, self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SIDECAR_SCHEMA_VERSION:
                raise TemporalDataError("temporal_sidecar_schema_newer")
            created = version == 0
            if version == 0:
                connection.executescript(_V1_SCHEMA)
                version = 1
            migrated = version < SIDECAR_SCHEMA_VERSION
            if version == 1:
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(temporal_intervals)")
                }
                if "expires_at_utc" not in columns:
                    connection.execute(
                        "ALTER TABLE temporal_intervals ADD COLUMN expires_at_utc TEXT"
                    )
                connection.executescript(_V2_SCHEMA)
            if created:
                empty_digest = _DerivedState(intervals=(), terminals=()).digest
                connection.execute(
                    "UPDATE temporal_sidecar_metadata SET canonical_digest=? WHERE singleton=1",
                    (empty_digest,),
                )
        if created:
            return TemporalMaintenanceResult(TemporalMaintenanceReason.CREATED)
        if migrated:
            return TemporalMaintenanceResult(TemporalMaintenanceReason.MIGRATED)
        return TemporalMaintenanceResult(TemporalMaintenanceReason.CURRENT)

    def schema_version(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def rebuild(self, authoritative_facts: Iterable[TemporalFact]) -> TemporalMaintenanceResult:
        derived = _derive_state(authoritative_facts)
        self.initialize()
        self._replace_state(derived)
        return TemporalMaintenanceResult(TemporalMaintenanceReason.REBUILT)

    def recover(self, authoritative_facts: Iterable[TemporalFact]) -> TemporalMaintenanceResult:
        """Reconcile stale state or replace a corrupt sidecar from canonical facts."""

        expected = _derive_state(authoritative_facts)
        if self._has_invalid_database_header():
            self._discard_database_files()
            self.initialize()
            self._replace_state(expected)
            return TemporalMaintenanceResult(TemporalMaintenanceReason.CORRUPT_REBUILT)
        corrupt = False
        try:
            self.initialize()
            with self._connection() as connection:
                quick_check = connection.execute("PRAGMA quick_check").fetchone()
                if quick_check is None or str(quick_check[0]) != "ok":
                    raise sqlite3.DatabaseError("temporal sidecar quick check failed")
                actual = self._read_state(connection)
                metadata = connection.execute(
                    "SELECT canonical_digest FROM temporal_sidecar_metadata WHERE singleton=1"
                ).fetchone()
                if (
                    metadata is not None
                    and str(metadata["canonical_digest"]) == expected.digest
                    and actual == expected
                ):
                    return TemporalMaintenanceResult(TemporalMaintenanceReason.CURRENT)
        except (sqlite3.DatabaseError, TemporalDataError):
            # Leave the exception scope before unlinking.  On Windows the
            # traceback can otherwise keep a failed SQLite statement alive
            # long enough for the derived database file to remain locked.
            corrupt = True
        if corrupt:
            self._discard_database_files()
            self.initialize()
            self._replace_state(expected)
            return TemporalMaintenanceResult(TemporalMaintenanceReason.CORRUPT_REBUILT)

        self._replace_state(expected)
        return TemporalMaintenanceResult(TemporalMaintenanceReason.STALE_REBUILT)

    def export_snapshot(self) -> dict[str, object]:
        """Export a portable, content-free snapshot of derived temporal state."""

        self.initialize()
        with self._connection() as connection:
            derived = self._read_state(connection)
            metadata = connection.execute(
                "SELECT canonical_digest FROM temporal_sidecar_metadata WHERE singleton=1"
            ).fetchone()
        if metadata is None or str(metadata["canonical_digest"]) != derived.digest:
            raise TemporalDataError("temporal_sidecar_stale")
        return self._snapshot_payload(derived)

    def restore_snapshot(
        self,
        snapshot: Mapping[str, object],
        authoritative_facts: Iterable[TemporalFact],
    ) -> TemporalMaintenanceResult:
        """Restore only if the snapshot exactly matches current canonical facts.

        Stale, corrupt, or newer snapshots are ignored and deterministically
        rebuilt from ``authoritative_facts``.  Imported derived state can
        therefore never override deletion or purge authority.
        """

        expected = _derive_state(authoritative_facts)
        expected_snapshot = self._snapshot_payload(expected)
        restored = self._json_equivalent(snapshot, expected_snapshot)
        if self._has_invalid_database_header():
            self._discard_database_files()
        corrupt = False
        try:
            self.initialize()
            self._replace_state(expected)
        except (sqlite3.DatabaseError, TemporalDataError):
            corrupt = True
        if corrupt:
            self._discard_database_files()
            self.initialize()
            self._replace_state(expected)
        reason = (
            TemporalMaintenanceReason.SNAPSHOT_RESTORED
            if restored
            else TemporalMaintenanceReason.SNAPSHOT_REBUILT
        )
        return TemporalMaintenanceResult(reason)

    def resolve(
        self,
        query: TemporalQuery,
        eligibility: PolicyEligibility,
    ) -> TemporalResolution:
        """Resolve one record per logical series after hard policy eligibility."""

        instant = normalize_utc(query.evaluated_at_utc)
        self.initialize()
        if not eligibility.record_ids:
            return TemporalResolution(query.mode, instant, (), ())

        try:
            with self._connection() as connection:
                connection.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS policy_eligible_temporal_records "
                    "(record_id TEXT PRIMARY KEY)"
                )
                connection.execute("DELETE FROM policy_eligible_temporal_records")
                connection.executemany(
                    "INSERT INTO policy_eligible_temporal_records(record_id) VALUES(?)",
                    ((record_id,) for record_id in sorted(eligibility.record_ids)),
                )
                connection.execute("DROP TABLE IF EXISTS temp.temporal_resolution_work")
                connection.execute(
                    "CREATE TEMP TABLE temporal_resolution_work AS "
                    "WITH eligible_intervals AS ("
                    "SELECT i.* FROM temporal_intervals i "
                    "JOIN policy_eligible_temporal_records e ON e.record_id=i.record_id "
                    "WHERE NOT EXISTS (SELECT 1 FROM temporal_terminal_records t "
                    "WHERE t.record_id=i.record_id)), "
                    "classified AS ("
                    "SELECT base.*,CASE "
                    "WHEN ?<base.valid_from_utc THEN 'not_yet_valid' "
                    "WHEN base.valid_to_utc IS NOT NULL AND ?>=base.valid_to_utc "
                    "THEN 'validity_ended' "
                    "WHEN base.expires_at_utc IS NOT NULL AND ?>=base.expires_at_utc "
                    "THEN 'expired' "
                    "WHEN EXISTS (SELECT 1 FROM eligible_intervals newer "
                    "WHERE newer.supersedes_record_id=base.record_id "
                    "AND newer.series_key=base.series_key AND newer.valid_from_utc<=?) "
                    "THEN 'superseded' ELSE 'candidate' END AS reason "
                    "FROM eligible_intervals base) "
                    "SELECT record_id,series_key,reason,"
                    "ROW_NUMBER() OVER (PARTITION BY series_key,reason "
                    "ORDER BY valid_from_utc DESC,revision DESC,updated_at_utc DESC,"
                    "record_id ASC) AS selection_rank FROM classified",
                    (instant, instant, instant, instant),
                )
                selected_rows = connection.execute(
                    "SELECT record_id FROM temporal_resolution_work "
                    "WHERE reason='candidate' AND selection_rank=1 "
                    "ORDER BY series_key"
                ).fetchall()
                reason_rows = connection.execute(
                    "SELECT reason,COUNT(*) FROM temporal_resolution_work "
                    "GROUP BY reason ORDER BY reason"
                ).fetchall()
                shadowed_row = connection.execute(
                    "SELECT COUNT(*) FROM temporal_resolution_work "
                    "WHERE reason='candidate' AND selection_rank>1"
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise TemporalDataError("temporal_sidecar_unavailable") from exc

        selected_ids = tuple(_checked_identifier(row["record_id"]) for row in selected_rows)
        reason_map = {
            "not_yet_valid": TemporalReason.NOT_YET_VALID,
            "validity_ended": TemporalReason.VALIDITY_ENDED,
            "expired": TemporalReason.EXPIRED,
            "superseded": TemporalReason.SUPERSEDED,
        }
        counts = dict.fromkeys(TemporalReason, 0)
        counts[TemporalReason.SELECTED] = len(selected_ids)
        counts[TemporalReason.SHADOWED] = int(shadowed_row[0]) if shadowed_row else 0
        for row in reason_rows:
            reason = reason_map.get(str(row["reason"]))
            if reason is not None:
                counts[reason] = int(row[1])

        diagnostics = tuple(
            TemporalDiagnostic(reason_code=reason, count=counts[reason])
            for reason in TemporalReason
            if counts[reason]
        )
        return TemporalResolution(
            mode=query.mode,
            evaluated_at_utc=instant,
            selected_record_ids=selected_ids,
            diagnostics=diagnostics,
        )

    def _replace_state(self, derived: _DerivedState) -> None:
        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM temporal_intervals")
                connection.execute("DELETE FROM temporal_terminal_records")
                connection.executemany(
                    "INSERT INTO temporal_intervals("
                    "record_id,series_key,valid_from_utc,valid_to_utc,supersedes_record_id,"
                    "revision,updated_at_utc,expires_at_utc) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        (
                            row.record_id,
                            row.series_key,
                            row.valid_from_utc,
                            row.valid_to_utc,
                            row.supersedes_record_id,
                            row.revision,
                            row.updated_at_utc,
                            row.expires_at_utc,
                        )
                        for row in derived.intervals
                    ),
                )
                connection.executemany(
                    "INSERT INTO temporal_terminal_records(record_id,state,terminal_at_utc) "
                    "VALUES(?,?,?)",
                    (
                        (row.record_id, row.state.value, row.terminal_at_utc)
                        for row in derived.terminals
                    ),
                )
                connection.execute(
                    "UPDATE temporal_sidecar_metadata SET schema_version=?,canonical_digest=? "
                    "WHERE singleton=1",
                    (SIDECAR_SCHEMA_VERSION, derived.digest),
                )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _read_state(connection: sqlite3.Connection) -> _DerivedState:
        interval_rows = connection.execute(
            "SELECT record_id,series_key,valid_from_utc,valid_to_utc,expires_at_utc,"
            "supersedes_record_id,revision,updated_at_utc FROM temporal_intervals "
            "ORDER BY record_id"
        ).fetchall()
        terminal_rows = connection.execute(
            "SELECT record_id,state,terminal_at_utc FROM temporal_terminal_records "
            "ORDER BY record_id"
        ).fetchall()
        return _DerivedState(
            intervals=tuple(TemporalSidecar._validated_interval(row) for row in interval_rows),
            terminals=tuple(
                _TerminalRow(
                    record_id=_checked_identifier(row["record_id"]),
                    state=_state(row["state"]),
                    terminal_at_utc=normalize_utc(row["terminal_at_utc"]),
                )
                for row in terminal_rows
            ),
        )

    @staticmethod
    def _validated_interval(row: sqlite3.Row) -> _IntervalRow:
        valid_from = normalize_utc(row["valid_from_utc"])
        valid_to = normalize_utc(row["valid_to_utc"]) if row["valid_to_utc"] else None
        expires_at = normalize_utc(row["expires_at_utc"]) if row["expires_at_utc"] else None
        if valid_to is not None and valid_to <= valid_from:
            raise TemporalDataError("temporal_interval_invalid")
        if expires_at is not None and expires_at <= valid_from:
            raise TemporalDataError("temporal_expiry_invalid")
        revision = row["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise TemporalDataError("temporal_revision_invalid")
        supersedes = row["supersedes_record_id"]
        return _IntervalRow(
            record_id=_checked_identifier(row["record_id"]),
            series_key=_checked_series_key(row["series_key"]),
            valid_from_utc=valid_from,
            valid_to_utc=valid_to,
            expires_at_utc=expires_at,
            supersedes_record_id=(
                _checked_identifier(supersedes) if supersedes is not None else None
            ),
            revision=revision,
            updated_at_utc=normalize_utc(row["updated_at_utc"]),
        )

    @staticmethod
    def _snapshot_payload(derived: _DerivedState) -> dict[str, object]:
        return {
            "format": SIDECAR_FORMAT,
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "canonical_digest": derived.digest,
            "intervals": [row.as_dict() for row in derived.intervals],
            "terminals": [row.as_dict() for row in derived.terminals],
        }

    @staticmethod
    def _json_equivalent(left: object, right: object) -> bool:
        try:
            encoded = json.dumps(
                left,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            decoded: object = json.loads(encoded)
            return decoded == right
        except (TypeError, ValueError):
            return False

    def _discard_database_files(self) -> None:
        resolved = self.database_path.resolve()
        paths: Sequence[Path] = (
            resolved,
            resolved.with_name(f"{resolved.name}-wal"),
            resolved.with_name(f"{resolved.name}-shm"),
        )
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    def _has_invalid_database_header(self) -> bool:
        try:
            if self.database_path.stat().st_size == 0:
                return False
            with self.database_path.open("rb") as database:
                return database.read(16) != b"SQLite format 3\x00"
        except FileNotFoundError:
            return False

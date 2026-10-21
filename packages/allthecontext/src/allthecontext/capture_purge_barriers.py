"""Content-free chronology fences for closed, Core-formed live-user fields."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from .memory_policy import LIVE_USER_FIELDS, extract_live_user_claim
from .project_decision_policy import PROJECT_DECISION_FIELDS

TABLE = "memory_purge_barriers"
LINK_TABLE = "memory_erasure_links"
_NAMESPACE = uuid.UUID("bb3a6117-dd25-437b-ae45-532e0ed2be83")
PERSONAL_PARTITION = str(uuid.uuid5(_NAMESPACE, "unbound-personal-partition"))
# These identifiers describe code-owned field definitions, never deleted values.
FACETS = {
    (kind, attribute): str(uuid.uuid5(_NAMESPACE, f"{kind}:{attribute}"))
    for kind, attribute in LIVE_USER_FIELDS
    | {("project_decision", field) for field in PROJECT_DECISION_FIELDS}
}


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Repair an interrupted additive migration without replaying old SQL."""
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} ("
        "vault_id TEXT NOT NULL REFERENCES vaults(id),partition_id TEXT NOT NULL,"
        "facet_id TEXT NOT NULL,record_id TEXT NOT NULL,purged_at TEXT NOT NULL,"
        "PRIMARY KEY(vault_id,partition_id,facet_id,record_id))"
    )
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {LINK_TABLE} ("
        "vault_id TEXT NOT NULL REFERENCES vaults(id),"
        "record_id TEXT NOT NULL REFERENCES context_records(id) ON DELETE CASCADE,"
        "observation_id TEXT NOT NULL REFERENCES context_candidates(id) ON DELETE CASCADE,"
        "partition_id TEXT NOT NULL,facet_id TEXT NOT NULL,"
        "PRIMARY KEY(vault_id,record_id,observation_id))"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_memory_erasure_observation ON {LINK_TABLE}(observation_id)"
    )


def _uuid(value: Any) -> bool:
    return isinstance(value, str) and _canonical_uuid(value)


def _canonical_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("capture purge barrier timestamp must be aware")
    return result.astimezone(UTC)


def validate_row(row: Mapping[str, Any] | sqlite3.Row) -> tuple[str, str, str, str]:
    """Validate the closed portable shape; deleted text cannot be smuggled in."""
    if set(row.keys()) != {"vault_id", "partition_id", "facet_id", "record_id", "purged_at"}:
        raise ValueError("capture purge barrier row has an invalid shape")
    if not all(_uuid(row[field]) for field in ("vault_id", "partition_id", "record_id")):
        raise ValueError("capture purge barrier identity is invalid")
    if row["facet_id"] not in FACETS.values() or not isinstance(row["purged_at"], str):
        raise ValueError("capture purge barrier facet is invalid")
    timestamp(row["purged_at"])
    return (
        str(row["vault_id"]),
        str(row["partition_id"]),
        str(row["facet_id"]),
        str(row["record_id"]),
    )


def validate_link_row(row: Mapping[str, Any] | sqlite3.Row) -> None:
    if set(row.keys()) != {"vault_id", "record_id", "observation_id", "partition_id", "facet_id"}:
        raise ValueError("capture erasure link has an invalid shape")
    if not all(
        _uuid(row[field]) for field in ("vault_id", "record_id", "observation_id", "partition_id")
    ):
        raise ValueError("capture erasure link identity is invalid")
    if row["facet_id"] not in FACETS.values():
        raise ValueError("capture erasure link facet is invalid")


def partition_for_project(
    connection: sqlite3.Connection, vault_id: str, canonical_project_id: str | None
) -> str | None:
    if canonical_project_id is None:
        return PERSONAL_PARTITION
    row = connection.execute(
        "SELECT anchor_record_id FROM canonical_projects WHERE id=? AND vault_id=?",
        (canonical_project_id, vault_id),
    ).fetchone()
    return str(row[0]) if row is not None else None


def blocks_history(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    partition_id: str | None,
    kind: str,
    attribute: str,
    observed_at: str,
    as_of: str,
) -> bool:
    facet = FACETS.get((kind, attribute))
    if facet is None or partition_id is None:
        return False
    rows = connection.execute(
        f"SELECT purged_at FROM {TABLE} WHERE vault_id=? AND partition_id=? AND facet_id=?",
        (vault_id, partition_id, facet),
    ).fetchall()
    if not rows:
        return False
    observed = timestamp(observed_at)
    return observed > timestamp(as_of) or any(observed <= timestamp(row[0]) for row in rows)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_barriers(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    record_id: str,
    observation_ids: set[str],
    purged_at: str,
) -> None:
    """Mint fences only from intact, attributable local Core capture evidence.

    Imported candidate metadata and administrative keyed records supply no
    authority. Portable fences are validated separately against purge facts.
    """
    # Portable links preserve this erasure relationship after local event and
    # principal pointers have been detached. They grant no formation authority.
    for row in connection.execute(
        f"SELECT DISTINCT partition_id,facet_id FROM {LINK_TABLE} WHERE vault_id=? AND record_id=?",
        (vault_id, record_id),
    ).fetchall():
        connection.execute(
            f"INSERT OR IGNORE INTO {TABLE}"
            "(vault_id,partition_id,facet_id,record_id,purged_at) VALUES(?,?,?,?,?)",
            (vault_id, row["partition_id"], row["facet_id"], record_id, purged_at),
        )
    for row, partition, facet in _qualified_captures(connection, vault_id, observation_ids):
        if row["record_id"] != record_id or row["disposition"] not in {"applied", "reinforced"}:
            continue
        connection.execute(
            f"INSERT OR IGNORE INTO {TABLE}"
            "(vault_id,partition_id,facet_id,record_id,purged_at) VALUES(?,?,?,?,?)",
            (vault_id, partition, facet, record_id, purged_at),
        )


def record_erasure_links(
    connection: sqlite3.Connection,
    vault_id: str,
    observation_ids: set[str] | None = None,
) -> None:
    """Backfill only intact local capture tuples, including before export."""
    for row, partition, facet in _qualified_captures(connection, vault_id, observation_ids):
        if row["record_id"] is None or row["disposition"] not in {"applied", "reinforced"}:
            continue
        raw = connection.execute(
            "SELECT c.id FROM context_candidates c JOIN context_records r ON r.id=? "
            "AND r.vault_id=c.vault_id WHERE c.vault_id=? AND c.capture_event_id=? "
            "AND c.capture_source_id=? AND c.source_type='client_capture' "
            "AND c.source_service='allthecontext-core' AND c.source_id IS NULL "
            "AND c.kind='captured_user_turn' AND c.observation_origin='client_capture'",
            (row["record_id"], vault_id, row["capture_event_id"], row["capture_source_id"]),
        ).fetchall()
        for sibling in raw:
            connection.execute(
                f"INSERT OR IGNORE INTO {LINK_TABLE}"
                "(vault_id,record_id,observation_id,partition_id,facet_id) VALUES(?,?,?,?,?)",
                (vault_id, row["record_id"], sibling["id"], partition, facet),
            )


def _qualified_captures(
    connection: sqlite3.Connection,
    vault_id: str,
    observation_ids: set[str] | None,
) -> Iterator[tuple[sqlite3.Row, str, str]]:
    if observation_ids == set():
        return
    identity_filter = ""
    parameters: list[Any] = [vault_id, vault_id]
    if observation_ids is not None:
        identity_filter = " AND c.id IN (SELECT value FROM json_each(?))"
        parameters.append(json.dumps(sorted(observation_ids)))
    rows = connection.execute(
        "SELECT c.*,e.normalized_payload_json,e.payload_hash,e.provider_event_id,"
        "e.provider_item_id,e.order_key,e.generation,e.operation,e.application_receipt,"
        "e.status AS event_status,e.idempotency_key AS event_idempotency_key,"
        "s.client_principal_id,s.provider,s.local_only,s.local_only_acknowledged "
        "FROM context_candidates c JOIN capture_events e ON e.id=c.capture_event_id "
        "AND e.source_id=c.capture_source_id JOIN capture_sources s ON s.id=e.source_id "
        "WHERE c.vault_id=? AND s.vault_id=? "
        "AND c.source_type='client_capture_formation' AND c.source_id IS NULL "
        "AND c.source_service='allthecontext-core' AND c.observation_origin='live_user_evidence'"
        + identity_filter,
        parameters,
    ).fetchall()
    from .capture import CaptureEvent

    for row in rows:
        facet = FACETS.get((row["kind"], row["attribute_key"]))
        if facet is None:
            continue
        try:
            payload = json.loads(row["normalized_payload_json"])
            structured = json.loads(row["structured_value_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(structured, dict):
            continue
        binding_digest = _hash(
            json.dumps(
                [
                    "client-capture-binding-v1",
                    row["capture_source_id"],
                    row["capture_event_id"],
                    row["event_idempotency_key"],
                    row["payload_hash"],
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        if (
            payload.get("role") != "user"
            or not isinstance(payload.get("content"), str)
            or row["event_status"] != "applied"
            or row["operation"] != "upsert"
            or row["provider"] != "client-runtime"
            or not isinstance(row["client_principal_id"], str)
            or not row["local_only"]
            or not row["local_only_acknowledged"]
            or row["submitted_by_client_id"] != row["client_principal_id"]
            or row["capture_source_id"] != "client-capture-" + _hash(row["client_principal_id"])
            or row["application_receipt"]
            != "capture-observation-"
            + _hash(f"{row['capture_source_id']}\0{row['capture_event_id']}")
            or row["source_reference"]
            != "client-capture-formation-"
            + _hash(f"{row['capture_source_id']}\0{row['provider_event_id']}")
            or row["capture_binding_hash"] != binding_digest
            or row["idempotency_key"] != str(row["event_idempotency_key"]) + ":formed"
            or structured.get("capture_role") != "user"
            or structured.get("formation") != "live-user-evidence-v1"
        ):
            continue
        event = CaptureEvent(
            provider_event_id=row["provider_event_id"],
            provider_item_id=row["provider_item_id"],
            order_key=row["order_key"],
            generation=row["generation"],
            payload=payload,
        )
        if event.normalized()[1] != row["payload_hash"]:
            continue
        if row["kind"] == "project_decision":
            # Decision formation intentionally retains the exact raw turn.
            if payload["content"] != row["content"]:
                continue
        else:
            # Re-derive with the very same canonical formation policy. NFKC and
            # whitespace may change content; identity/receipt/fingerprint checks
            # above still bind this to its original Core event, not a text match.
            claim = extract_live_user_claim(payload["content"])
            if (
                claim is None
                or claim.kind != row["kind"]
                or claim.attribute_key != row["attribute_key"]
                or claim.content != row["content"]
                or any(
                    structured.get(key) != value for key, value in claim.structured_value.items()
                )
            ):
                continue
        receipt = payload.get("project_binding")
        partition = PERSONAL_PARTITION
        entity = "user"
        scopes: list[str] = []
        if receipt is not None:
            if (
                not isinstance(receipt, dict)
                or set(receipt)
                != {"schema", "state", "canonical_project_id", "binding_id", "version"}
                or receipt.get("schema") != "atc.principal-project-binding.v1"
                or receipt.get("state") != "bound"
                or receipt.get("version") != 1
            ):
                continue
            project = connection.execute(
                "SELECT p.anchor_record_id,p.project_scope FROM principal_project_bindings b "
                "JOIN canonical_projects p ON p.id=b.canonical_project_id "
                "AND p.vault_id=b.vault_id "
                "WHERE b.id=? AND b.client_id=? AND b.vault_id=? AND p.id=?",
                (
                    receipt.get("binding_id"),
                    row["client_principal_id"],
                    vault_id,
                    receipt.get("canonical_project_id"),
                ),
            ).fetchone()
            if project is None:
                continue
            partition = str(project["anchor_record_id"])
            entity = (
                "project-decision:" if row["kind"] == "project_decision" else "user@project:"
            ) + str(receipt["canonical_project_id"])
            scopes = [str(project["project_scope"])]
        elif row["kind"] == "project_decision":
            continue
        if row["entity_key"] != entity or json.loads(row["scopes_json"]) != scopes:
            continue
        yield row, partition, facet

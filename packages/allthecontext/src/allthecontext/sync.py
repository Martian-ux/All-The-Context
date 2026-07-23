"""Core-initiated Relay synchronization and offline-proposal intake."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from allthecontext.models import CandidateInput, CandidateOut, Sensitivity
from allthecontext.replication import ReplicationEvent, sign_event
from allthecontext.storage import StorageError


class ResponseLike(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class HttpClientLike(Protocol):
    def post(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def get(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def patch(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def close(self) -> None: ...


class CandidateSink(Protocol):
    def add_edge_candidate(
        self,
        proposal_id: str,
        candidate: CandidateInput,
        *,
        client_id: str,
    ) -> tuple[CandidateOut, bool]: ...


def _require_secure_relay(url: str) -> None:
    parsed = urlparse(url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("Relay must use HTTPS except on loopback")


class CoreRelaySync:
    """Push a durable outbox and pull noncanonical Relay proposals."""

    def __init__(
        self,
        database_path: Path,
        relay_url: str,
        replication_secret: bytes,
        replication_bearer_token: str,
        *,
        http_client: HttpClientLike | None = None,
    ) -> None:
        _require_secure_relay(relay_url)
        if len(replication_secret) < 32:
            raise ValueError("replication secret must contain at least 32 bytes")
        if not replication_bearer_token:
            raise ValueError("replication bearer token is required")
        self.database_path = database_path.resolve()
        self.relay_url = relay_url.rstrip("/")
        self.secret = replication_secret
        self.token = replication_bearer_token
        self.client = http_client or httpx.Client(timeout=20.0)
        self._owns_client = http_client is None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> CoreRelaySync:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def push(
        self,
        *,
        limit: int = 500,
        vault_id: str | None = None,
        after_sequence: int | None = None,
    ) -> dict[str, int]:
        replay_from_checkpoint = vault_id is not None or after_sequence is not None
        if replay_from_checkpoint and (vault_id is None or after_sequence is None):
            raise ValueError("vault_id and after_sequence must be provided together")
        bounded_limit = max(1, min(limit, 5000))
        with self._connection() as connection:
            if replay_from_checkpoint:
                assert vault_id is not None and after_sequence is not None
                maximum = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence),0) FROM replication_events WHERE vault_id=?",
                        (vault_id,),
                    ).fetchone()[0]
                )
                if after_sequence > maximum:
                    raise RuntimeError("Edge checkpoint is ahead of the authoritative Core log")
                rows = connection.execute(
                    "SELECT * FROM replication_events WHERE vault_id=? AND sequence>? "
                    "ORDER BY sequence LIMIT ?",
                    (vault_id, after_sequence, bounded_limit),
                ).fetchall()
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM replication_events WHERE vault_id=? AND sequence>?",
                        (vault_id, after_sequence),
                    ).fetchone()[0]
                )
                if rows and int(rows[0]["sequence"]) != after_sequence + 1:
                    raise RuntimeError("Core replication log cannot satisfy the Edge checkpoint")
            else:
                rows = connection.execute(
                    "SELECT * FROM replication_events WHERE delivered_at IS NULL "
                    "ORDER BY vault_id, sequence LIMIT ?",
                    (bounded_limit,),
                ).fetchall()
                total = len(rows)
        delivered = 0
        replayed = 0
        for row in rows:
            event = sign_event(ReplicationEvent.from_mapping(dict(row)), self.secret)
            response = self.client.post(
                f"{self.relay_url}/v1/replication/events",
                headers=self.headers,
                json=event.wire_mapping(),
            )
            response.raise_for_status()
            result = response.json()
            if isinstance(result, Mapping) and bool(result.get("replayed")):
                replayed += 1
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE replication_events SET delivered_at=COALESCE(delivered_at,"
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id=?",
                    (event.event_id,),
                )
                connection.commit()
            delivered += 1
        return {"delivered": delivered, "replayed": replayed, "remaining": total - delivered}

    def edge_status(self, vault_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"{self.relay_url}/v1/edge/status",
            headers=self.headers,
            params={"vault_id": vault_id},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, Mapping):
            raise RuntimeError("Edge returned an invalid status response")
        return dict(body)

    def pull_proposals(self, vault_id: str, sink: CandidateSink, *, limit: int = 100) -> int:
        response = self.client.get(
            f"{self.relay_url}/v1/replication/proposals",
            headers=self.headers,
            params={"vault_id": vault_id, "limit": max(1, min(limit, 1000))},
        )
        response.raise_for_status()
        body = response.json()
        items = body.get("items", []) if isinstance(body, Mapping) else []
        imported = 0
        for proposal in items:
            if not isinstance(proposal, Mapping):
                continue
            proposal_id = str(proposal.get("proposal_id", proposal.get("id", "")))
            try:
                raw_client_id = proposal.get("client_id")
                if not isinstance(raw_client_id, str) or not raw_client_id.strip():
                    raise ValueError("proposal client_id is missing")
                edge_client_id = raw_client_id.strip()
                raw_payload = proposal.get(
                    "proposal", proposal.get("payload", proposal.get("payload_json", {}))
                )
                if isinstance(raw_payload, str):
                    raw_payload = json.loads(raw_payload)
                if not isinstance(raw_payload, Mapping):
                    raise ValueError("proposal payload is not an object")
                scope_value = raw_payload.get("scope", [])
                scopes = [scope_value] if isinstance(scope_value, str) else list(scope_value)
                reported = raw_payload.get("provenance")
                reported_provenance = reported if isinstance(reported, Mapping) else {}
                provenance = {
                    "edge_proposal_id": proposal_id,
                    "edge_client_id": edge_client_id,
                    "reported_source_service": raw_payload.get("source_service"),
                    "reported_provenance": dict(reported_provenance),
                }
                reported_evidence = reported_provenance.get("evidence")
                evidence = (
                    str(reported_evidence)
                    if isinstance(reported_evidence, str)
                    else json.dumps(provenance, sort_keys=True)
                )
                confidence_value = raw_payload.get("confidence")
                confidence = 1.0 if confidence_value is None else float(confidence_value)
                raw_kind = str(raw_payload.get("kind", "memory"))
                suggested_correction = reported_provenance.get("suggested_correction")
                is_correction = isinstance(suggested_correction, str) and bool(
                    suggested_correction.strip()
                )
                entity_key = reported_provenance.get("entity_key")
                attribute_key = reported_provenance.get("attribute_key")
                candidate = CandidateInput(
                    kind=(
                        "correction"
                        if is_correction
                        else raw_kind
                    ),
                    content=(
                        str(suggested_correction)
                        if is_correction
                        else str(raw_payload["content"])
                    ),
                    scopes=[str(value) for value in scopes],
                    source_reference=str(
                        reported_provenance.get("source_reference")
                        or f"edge-proposal:{proposal_id}"
                    ),
                    source_service=edge_client_id,
                    source_type="queued_proposal",
                    evidence=evidence,
                    confidence=confidence,
                    sensitivity=Sensitivity(str(raw_payload.get("sensitivity", "normal"))),
                    entity_key=str(entity_key) if entity_key is not None else None,
                    attribute_key=(
                        str(attribute_key) if attribute_key is not None else None
                    ),
                    supersedes=(
                        str(
                            reported_provenance.get("record_id")
                            or reported_provenance.get("supersedes")
                        )
                        if (
                            reported_provenance.get("record_id") is not None
                            or reported_provenance.get("supersedes") is not None
                        )
                        else None
                    ),
                    observed_at=(
                        str(reported_provenance["observed_at"])
                        if reported_provenance.get("observed_at") is not None
                        else None
                    ),
                    explicit_user_statement=is_correction
                    or (
                        raw_kind.casefold() != "context_error"
                        and bool(
                            reported_provenance.get("explicit_user_statement", False)
                        )
                    ),
                    idempotency_key=f"edge-proposal:{proposal_id}",
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
                rejected = self.client.patch(
                    f"{self.relay_url}/v1/replication/proposals/{proposal_id}",
                    headers=self.headers,
                    params={"vault_id": vault_id},
                    json={"status": "rejected"},
                )
                rejected.raise_for_status()
                continue
            try:
                sink.add_edge_candidate(
                    proposal_id,
                    candidate,
                    client_id=edge_client_id,
                )
            except StorageError:
                rejected = self.client.patch(
                    f"{self.relay_url}/v1/replication/proposals/{proposal_id}",
                    headers=self.headers,
                    params={"vault_id": vault_id},
                    json={"status": "rejected"},
                )
                rejected.raise_for_status()
                continue
            acknowledge = self.client.patch(
                f"{self.relay_url}/v1/replication/proposals/{proposal_id}",
                headers=self.headers,
                params={"vault_id": vault_id},
                json={"status": "imported"},
            )
            acknowledge.raise_for_status()
            imported += 1
        return imported

    def claim_forward_requests(self, *, limit: int = 8) -> list[dict[str, Any]]:
        response = self.client.get(
            f"{self.relay_url}/v1/forward/requests",
            headers=self.headers,
            params={"limit": min(max(limit, 1), 8)},
        )
        if getattr(response, "status_code", 200) == 404:
            # Older/self-hosted Edges remain compatible; forwarding is additive.
            return []
        response.raise_for_status()
        body = response.json()
        items = body.get("items", []) if isinstance(body, Mapping) else []
        if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
            raise RuntimeError("Edge returned an invalid forwarding queue")
        return [dict(item) for item in items]

    def answer_forward_request(
        self, request_id: str, claim_token: str, response_payload: dict[str, Any]
    ) -> bool:
        response = self.client.post(
            f"{self.relay_url}/v1/forward/requests/{request_id}/response",
            headers=self.headers,
            json={"claim_token": claim_token, "response": response_payload},
        )
        if getattr(response, "status_code", 200) == 409:
            return False
        response.raise_for_status()
        return True

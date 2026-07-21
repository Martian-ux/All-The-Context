"""Core-initiated Relay synchronization and offline-proposal intake."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from allthecontext.models import CandidateInput, CandidateOut, Sensitivity
from allthecontext.replication import ReplicationEvent, sign_event
from allthecontext.security import ClientPrincipal


class ResponseLike(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class HttpClientLike(Protocol):
    def post(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def get(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def patch(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def close(self) -> None: ...


class CandidateSink(Protocol):
    def add_candidate(
        self,
        candidate: CandidateInput,
        *,
        session_id: str | None = None,
        client: ClientPrincipal | None = None,
    ) -> CandidateOut: ...


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

    def push(self, *, limit: int = 500) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM replication_events WHERE delivered_at IS NULL "
                "ORDER BY vault_id, sequence LIMIT ?",
                (max(1, min(limit, 5000)),),
            ).fetchall()
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
                    "UPDATE replication_events SET delivered_at="
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND delivered_at IS NULL",
                    (event.event_id,),
                )
                connection.commit()
            delivered += 1
        return {"delivered": delivered, "replayed": replayed, "remaining": len(rows) - delivered}

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
            raw_payload = proposal.get(
                "proposal", proposal.get("payload", proposal.get("payload_json", {}))
            )
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            if not isinstance(raw_payload, Mapping):
                continue
            scope_value = raw_payload.get("scope", [])
            scopes = [scope_value] if isinstance(scope_value, str) else list(scope_value)
            provenance = raw_payload.get("provenance")
            evidence = json.dumps(provenance, sort_keys=True) if provenance else None
            candidate = CandidateInput(
                kind=str(raw_payload.get("kind", "memory")),
                content=str(raw_payload["content"]),
                scopes=[str(value) for value in scopes],
                source_service="relay",
                source_type="queued_proposal",
                evidence=evidence,
                confidence=float(raw_payload.get("confidence") or 1.0),
                sensitivity=Sensitivity(str(raw_payload.get("sensitivity", "normal"))),
            )
            sink.add_candidate(candidate)
            proposal_id = str(proposal.get("proposal_id", proposal.get("id", "")))
            acknowledge = self.client.patch(
                f"{self.relay_url}/v1/replication/proposals/{proposal_id}",
                headers=self.headers,
                params={"vault_id": vault_id},
                json={"status": "imported"},
            )
            acknowledge.raise_for_status()
            imported += 1
        return imported

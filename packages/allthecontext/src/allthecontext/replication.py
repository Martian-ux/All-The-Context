"""Authenticated, ordered replication envelopes shared by Core and Relay.

Core owns the event stream.  This module deliberately contains no database
code so that an event can be signed at the outbound service boundary and
verified by a separately deployed Relay.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, Self
from urllib.parse import urlsplit
from uuid import uuid4

SCHEMA_VERSION = 1
MAX_REPLICATION_PAYLOAD_BYTES = 1_500_000
MAX_EDGE_REPLICATION_REQUEST_BYTES = 2 * 1024 * 1024


class ReplicationError(ValueError):
    """Base class for invalid replication envelopes."""


class PayloadHashError(ReplicationError):
    """The payload does not match the hash asserted by Core."""


class SignatureError(ReplicationError):
    """The event has no valid Core signature."""


class ReplicationDeliveryError(RuntimeError):
    """A signed event was not confirmed by Relay."""


class EventType(StrEnum):
    RECORD_UPSERTED = "record_upserted"
    RECORD_WITHDRAWN = "record_withdrawn"
    RECORD_DELETED = "record_deleted"
    RECORD_PURGED = "record_purged"


JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _validate_json(value: object, *, path: str = "$") -> JsonValue:
    """Return a JSON value while rejecting ambiguous/non-portable inputs."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReplicationError(f"non-finite number at {path}")
        return value
    if isinstance(value, list):
        return [_validate_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReplicationError(f"non-string object key at {path}")
            result[key] = _validate_json(item, path=f"{path}.{key}")
        return result
    raise ReplicationError(f"unsupported JSON value {type(value).__name__} at {path}")


def canonical_json(value: object) -> str:
    """Serialize JSON deterministically for hashing and authentication.

    UTF-8 data remains readable, keys are sorted, insignificant whitespace is
    omitted, and NaN/infinity are rejected.  This is the v1 wire
    canonicalization contract; changing it requires a new schema version.
    """

    validated = _validate_json(value)
    return json.dumps(
        validated,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_payload_hash(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReplicationError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ReplicationEvent:
    """A complete replication transport envelope.

    ``mac`` may be ``None`` while the event is in Core's transactional outbox.
    Relay verification always rejects an unsigned envelope.
    """

    event_id: str
    vault_id: str
    sequence: int
    event_type: EventType
    record_id: str
    payload: Mapping[str, JsonValue]
    payload_hash: str
    created_at: str
    mac: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.event_id or not self.vault_id or not self.record_id:
            raise ReplicationError("event_id, vault_id, and record_id are required")
        if self.sequence < 1:
            raise ReplicationError("sequence must be at least 1")
        if self.schema_version != SCHEMA_VERSION:
            raise ReplicationError(f"unsupported schema_version {self.schema_version}")
        if len(self.payload_hash) != 64:
            raise ReplicationError("payload_hash must be a SHA-256 hex digest")
        try:
            bytes.fromhex(self.payload_hash)
        except ValueError as exc:
            raise ReplicationError("payload_hash must be hexadecimal") from exc
        if self.mac is not None:
            if len(self.mac) != 64:
                raise ReplicationError("mac must be an HMAC-SHA256 hex digest")
            try:
                bytes.fromhex(self.mac)
            except ValueError as exc:
                raise ReplicationError("mac must be hexadecimal") from exc
        validated = _validate_json(dict(self.payload))
        if not isinstance(validated, dict):  # pragma: no cover - guaranteed by dict above
            raise ReplicationError("payload must be an object")
        object.__setattr__(self, "payload", MappingProxyType(validated))
        if self.event_type == EventType.RECORD_PURGED:
            expected = {"record_id", "purged_at", "purge_scope", "irreversible"}
            if set(validated) != expected:
                raise ReplicationError("record_purged payload has unexpected fields")
            if validated.get("record_id") != self.record_id:
                raise ReplicationError("record_purged record_id does not match envelope")
            if validated.get("purge_scope") not in {"record", "source"}:
                raise ReplicationError("record_purged purge_scope is invalid")
            if validated.get("irreversible") is not True:
                raise ReplicationError("record_purged must be irreversible")
            _require_text(validated.get("purged_at"), "purged_at")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Self:
        """Parse either the wire shape or Core's SQLite outbox row shape."""

        event_id = value.get("event_id", value.get("id"))
        raw_payload = value.get("payload", value.get("payload_json"))
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise ReplicationError("payload_json is not valid JSON") from exc
        if not isinstance(raw_payload, Mapping):
            raise ReplicationError("payload must be a JSON object")
        raw_type = value.get("event_type")
        try:
            event_type = EventType(_require_text(raw_type, "event_type"))
        except ValueError as exc:
            raise ReplicationError(f"unsupported event_type {raw_type!r}") from exc
        sequence = value.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ReplicationError("sequence must be an integer")
        schema_version = value.get("schema_version", SCHEMA_VERSION)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ReplicationError("schema_version must be an integer")
        mac = value.get("mac")
        if mac is not None and not isinstance(mac, str):
            raise ReplicationError("mac must be a string or null")
        return cls(
            event_id=_require_text(event_id, "event_id"),
            vault_id=_require_text(value.get("vault_id"), "vault_id"),
            sequence=sequence,
            event_type=event_type,
            record_id=_require_text(value.get("record_id"), "record_id"),
            payload=raw_payload,
            payload_hash=_require_text(value.get("payload_hash"), "payload_hash").lower(),
            created_at=_require_text(value.get("created_at"), "created_at"),
            mac=mac.lower() if mac is not None else None,
            schema_version=schema_version,
        )

    def unsigned_envelope(self) -> dict[str, JsonValue]:
        return {
            "created_at": self.created_at,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "payload_hash": self.payload_hash,
            "record_id": self.record_id,
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "vault_id": self.vault_id,
        }

    def wire_mapping(self) -> dict[str, JsonValue]:
        result = self.unsigned_envelope()
        result["payload"] = dict(self.payload)
        result["mac"] = self.mac
        return result

    def fingerprint(self) -> str:
        """Hash the exact semantic wire event for matching-replay checks."""

        return hashlib.sha256(canonical_json(self.wire_mapping()).encode("utf-8")).hexdigest()


def build_event(
    *,
    vault_id: str,
    sequence: int,
    event_type: EventType | str,
    record_id: str,
    payload: Mapping[str, JsonValue],
    event_id: str | None = None,
    created_at: str | None = None,
) -> ReplicationEvent:
    """Create an unsigned event suitable for Core's transactional outbox."""

    event_kind = EventType(event_type)
    timestamp = created_at or datetime.now(UTC).isoformat()
    return ReplicationEvent(
        event_id=event_id or str(uuid4()),
        vault_id=vault_id,
        sequence=sequence,
        event_type=event_kind,
        record_id=record_id,
        payload=payload,
        payload_hash=calculate_payload_hash(payload),
        created_at=timestamp,
    )


def sign_event(event: ReplicationEvent, secret: bytes) -> ReplicationEvent:
    if len(secret) < 32:
        raise SignatureError("replication secret must contain at least 32 bytes")
    actual_hash = calculate_payload_hash(event.payload)
    if not hmac.compare_digest(actual_hash, event.payload_hash):
        raise PayloadHashError("event payload does not match payload_hash")
    message = canonical_json(event.unsigned_envelope()).encode("utf-8")
    mac = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return replace(event, mac=mac)


def verify_event(event: ReplicationEvent, secret: bytes) -> None:
    if len(secret) < 32:
        raise SignatureError("replication secret must contain at least 32 bytes")
    actual_hash = calculate_payload_hash(event.payload)
    if not hmac.compare_digest(actual_hash, event.payload_hash):
        raise PayloadHashError("event payload does not match payload_hash")
    if event.mac is None:
        raise SignatureError("replication event is unsigned")
    message = canonical_json(event.unsigned_envelope()).encode("utf-8")
    expected = hmac.new(secret, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, event.mac):
        raise SignatureError("replication event signature is invalid")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    event_id: str
    vault_id: str
    sequence: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class DispatchResult:
    delivered: int
    event_ids: tuple[str, ...]


class ReplicationOutbox(Protocol):
    """The narrow Core boundary used by the outbound dispatcher."""

    def pending_replication_events(self, limit: int = 100) -> Sequence[Mapping[str, Any]]: ...

    def mark_replication_delivered(
        self, event_id: str, delivered_at: str | None = None
    ) -> None: ...


class ReplicationTransport(Protocol):
    def deliver(self, event: ReplicationEvent) -> DeliveryReceipt: ...

    def close(self) -> None: ...


class HttpRelayTransport:
    """Synchronous HTTPS/HTTP transport suitable for a Core worker or CLI.

    Production ``base_url`` values must use HTTPS.  Plain HTTP remains useful
    for loopback development and a private Docker network behind TLS
    termination.
    """

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        timeout_seconds: float = 10.0,
        client: Any | None = None,
    ) -> None:
        if not base_url or not bearer_token:
            raise ValueError("Relay base URL and bearer token are required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        parsed_url = urlsplit(base_url)
        loopback = parsed_url.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed_url.scheme != "https" and not (parsed_url.scheme == "http" and loopback):
            raise ValueError("Relay transport requires HTTPS except on loopback")
        if client is None:
            import httpx

            client = httpx.Client(timeout=timeout_seconds)
            self._owns_client = True
        else:
            self._owns_client = False
        self._client = client
        self._url = f"{base_url.rstrip('/')}/v1/replication/events"
        self._headers = {"Authorization": f"Bearer {bearer_token}"}

    def deliver(self, event: ReplicationEvent) -> DeliveryReceipt:
        try:
            response = self._client.post(
                self._url,
                headers=self._headers,
                json=event.wire_mapping(),
            )
        except Exception as exc:
            # Do not include the request, bearer token, or personal context in
            # the exception text.
            raise ReplicationDeliveryError("Relay request failed") from exc
        if not 200 <= int(response.status_code) < 300:
            raise ReplicationDeliveryError(
                f"Relay rejected sequence {event.sequence} with HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except Exception as exc:
            raise ReplicationDeliveryError("Relay returned a non-JSON response") from exc
        if not isinstance(body, dict) or body.get("accepted") is not True:
            raise ReplicationDeliveryError("Relay did not confirm event acceptance")
        if body.get("event_id") != event.event_id or body.get("sequence") != event.sequence:
            raise ReplicationDeliveryError("Relay acceptance did not match the sent event")
        return DeliveryReceipt(
            event_id=event.event_id,
            vault_id=event.vault_id,
            sequence=event.sequence,
            replayed=body.get("replayed") is True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class ReplicationDispatcher:
    """Signs and drains Core's transactional outbox in deterministic order."""

    def __init__(
        self,
        outbox: ReplicationOutbox,
        transport: ReplicationTransport,
        replication_secret: bytes,
    ) -> None:
        if len(replication_secret) < 32:
            raise ValueError("replication_secret must contain at least 32 bytes")
        self._outbox = outbox
        self._transport = transport
        self._secret = bytes(replication_secret)

    def dispatch_pending(self, *, limit: int = 100) -> DispatchResult:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self._outbox.pending_replication_events(limit=limit)
        events = [ReplicationEvent.from_mapping(row) for row in rows]
        # Core normally supplies this order.  Sorting here makes the transport
        # contract deterministic without assuming a particular SQL query.
        events.sort(key=lambda event: (event.vault_id, event.sequence))
        delivered: list[str] = []
        for unsigned in events:
            signed = sign_event(unsigned, self._secret)
            receipt = self._transport.deliver(signed)
            if receipt.event_id != signed.event_id:
                raise ReplicationDeliveryError("transport receipt does not match the sent event")
            self._outbox.mark_replication_delivered(signed.event_id)
            delivered.append(signed.event_id)
        return DispatchResult(delivered=len(delivered), event_ids=tuple(delivered))

    def close(self) -> None:
        self._transport.close()

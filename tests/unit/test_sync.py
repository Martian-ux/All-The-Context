from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import truststore
from allthecontext.core.service import CoreService
from allthecontext.models import ApprovalStatus
from allthecontext.replication import EventType, build_event
from allthecontext.storage import CoreStore
from allthecontext.sync import CoreRelaySync


def test_httpx2_status_error_keeps_http_error_type_and_response() -> None:
    request = httpx.Request("PATCH", "https://edge.example.test/proposal")
    response = httpx.Response(503, request=request)
    error = httpx.HTTPStatusError("failed", request=request, response=response)

    assert isinstance(error, httpx.HTTPError)
    assert error.request is request
    assert error.response is response


def test_httpx2_default_tls_context_uses_the_os_trust_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    context = httpx.create_ssl_context()

    assert isinstance(context, truststore.SSLContext)


def test_sync_rejects_cleartext_non_loopback_relay(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        CoreRelaySync(tmp_path / "core.db", "http://relay.example", b"x" * 32, "token")


def test_sync_accepts_loopback_for_local_demo(tmp_path: Path) -> None:
    database = tmp_path / "core.db"
    sqlite3.connect(database).close()
    sync = CoreRelaySync(
        database,
        "http://127.0.0.1:8743",
        b"x" * 32,
        "token",
        http_client=_NoopClient(),
    )
    sync.close()


class _NoopClient:
    def post(self, url: str, **kwargs: object) -> object:
        raise AssertionError("not called")

    def get(self, url: str, **kwargs: object) -> object:
        raise AssertionError("not called")

    def patch(self, url: str, **kwargs: object) -> object:
        raise AssertionError("not called")

    def close(self) -> None:
        return None


class _Response:
    def __init__(self, body: Any, *, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("PATCH", "https://edge.example.test/proposal")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> Any:
        if isinstance(self.body, BaseException):
            raise self.body
        return self.body


class _PushAckClient:
    def __init__(self, body: Any) -> None:
        self.body = body
        self.sent: list[dict[str, Any]] = []

    def post(self, _url: str, **kwargs: Any) -> _Response:
        self.sent.append(kwargs["json"])
        return _Response(self.body)

    def get(self, _url: str, **_kwargs: Any) -> _Response:
        raise AssertionError("not called")

    def patch(self, _url: str, **_kwargs: Any) -> _Response:
        raise AssertionError("not called")

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "fault",
    [
        "rejected", "wrong_event", "wrong_sequence", "wrong_types", "non_object", "malformed",
        "accepted_number", "boolean_sequence", "float_sequence", "replayed_string",
    ],
)
def test_push_requires_exact_relay_ack_and_retries_same_event(
    tmp_path: Path,
    fault: str,
) -> None:
    database = tmp_path / "core-outbox.db"
    store = CoreStore(database)
    store.initialize_vault()
    event = build_event(
        vault_id=store.vault_id(),
        sequence=1,
        event_type=EventType.RECORD_UPSERTED,
        record_id="synthetic-record",
        payload={"content": "synthetic owned replication event"},
    )
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO replication_events"
            "(id,vault_id,sequence,event_type,record_id,payload_json,payload_hash,mac,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.vault_id,
                event.sequence,
                event.event_type.value,
                event.record_id,
                json.dumps(dict(event.payload), sort_keys=True, separators=(",", ":")),
                event.payload_hash,
                None,
                event.created_at,
            ),
        )

    exact_ack = {"accepted": True, "event_id": event.event_id, "sequence": 1, "replayed": False}
    faulty: Any = {
        "rejected": {**exact_ack, "accepted": False},
        "wrong_event": {**exact_ack, "event_id": "other-event"},
        "wrong_sequence": {**exact_ack, "sequence": 2},
        "wrong_types": {"accepted": "true", "event_id": 9, "sequence": True},
        "non_object": [exact_ack],
        "malformed": ValueError("invalid JSON"),
        "accepted_number": {**exact_ack, "accepted": 1},
        "boolean_sequence": {**exact_ack, "sequence": True},
        "float_sequence": {**exact_ack, "sequence": 1.0},
        "replayed_string": {**exact_ack, "replayed": "false"},
    }[fault]
    client = _PushAckClient(faulty)
    sync = CoreRelaySync(
        database,
        "https://relay.example.test",
        b"x" * 32,
        "token",
        http_client=client,
    )
    with pytest.raises((RuntimeError, ValueError)):
        sync.push()

    with store.connect() as connection:
        assert connection.execute(
            "SELECT delivered_at FROM replication_events WHERE id=?", (event.event_id,)
        ).fetchone()[0] is None
    first_sent = client.sent[0]
    client.body = exact_ack
    assert sync.push() == {"delivered": 1, "replayed": 0, "remaining": 0}
    assert client.sent[1] == first_sent
    with store.connect() as connection:
        assert connection.execute(
            "SELECT delivered_at FROM replication_events WHERE id=?", (event.event_id,)
        ).fetchone()[0] is not None
    sync.close()


class _ProposalClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.fail_next_ack = False
        self.acks: list[tuple[str, str]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        return _Response({"items": self.items})

    def patch(self, url: str, **kwargs: Any) -> _Response:
        status = str(kwargs["json"]["status"])
        proposal_id = url.rsplit("/", 1)[-1]
        self.acks.append((proposal_id, status))
        if self.fail_next_ack:
            self.fail_next_ack = False
            return _Response({}, status_code=503)
        return _Response({"updated": True})

    def post(self, url: str, **kwargs: Any) -> _Response:
        raise AssertionError("not called")

    def close(self) -> None:
        return None


def test_proposal_pull_is_idempotent_after_ack_failure_and_preserves_zero_confidence(
    tmp_path: Path,
) -> None:
    core = CoreService.in_directory(tmp_path / "core")
    proposal = {
        "proposal_id": "proposal-1",
        "client_id": "client-a",
        "proposal": {
            "kind": "preference",
            "content": "Do not infer missing evidence",
            "scope": ["general"],
            "confidence": 0.0,
            "sensitivity": "normal",
        },
    }
    client = _ProposalClient([proposal])
    sync = CoreRelaySync(
        core.store.database_path,
        "https://edge.example.test",
        b"x" * 32,
        "token",
        http_client=client,
    )
    client.fail_next_ack = True

    with pytest.raises(httpx.HTTPStatusError):
        sync.pull_proposals(core.store.vault_id(), core.store)
    assert core.store.list_candidates(status=ApprovalStatus.PENDING)[1] == 1

    assert sync.pull_proposals(core.store.vault_id(), core.store) == 1
    candidates, total = core.store.list_candidates(status=ApprovalStatus.PENDING)
    assert total == 1
    assert candidates[0].confidence == 0.0


def test_invalid_legacy_proposal_is_rejected_without_blocking_later_items(tmp_path: Path) -> None:
    core = CoreService.in_directory(tmp_path / "core")
    client = _ProposalClient(
        [
            {
                "proposal_id": "invalid",
                "client_id": "client-a",
                "proposal": {
                    "kind": "preference",
                    "content": "Bad sensitivity",
                    "sensitivity": "secret-ish",
                },
            },
            {
                "proposal_id": "valid",
                "client_id": "client-a",
                "proposal": {
                    "kind": "preference",
                    "content": "Valid proposal",
                    "sensitivity": "normal",
                },
            },
        ]
    )
    sync = CoreRelaySync(
        core.store.database_path,
        "https://edge.example.test",
        b"x" * 32,
        "token",
        http_client=client,
    )

    assert sync.pull_proposals(core.store.vault_id(), core.store) == 1
    assert client.acks == [("invalid", "rejected"), ("valid", "imported")]
    assert core.store.list_candidates(status=ApprovalStatus.PENDING)[1] == 1

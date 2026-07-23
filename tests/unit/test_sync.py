from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from allthecontext.core.service import CoreService
from allthecontext.models import ApprovalStatus
from allthecontext.sync import CoreRelaySync


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
    def __init__(self, body: dict[str, Any], *, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("PATCH", "https://edge.example.test/proposal")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> dict[str, Any]:
        return self.body


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

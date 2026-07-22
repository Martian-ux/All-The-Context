"""Local proof of offline Edge plus online Core forwarding; no provider account required."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager
from allthecontext.models import ApprovalRequest, Availability, CandidateInput
from allthecontext.relay.forwarding import EdgeForwardingBroker
from allthecontext.relay.service import ClientIdentity, RelayService, SQLiteRelayStore
from allthecontext.replication import ReplicationEvent, sign_event

SECRET = b"fictional-mobile-demo-secret-at-least-32-bytes"


def _approve(core: CoreService, availability: Availability, content: str) -> Any:
    candidate = core.store.add_candidate(
        CandidateInput(
            kind="project",
            content=content,
            scopes=["project:mobile-demo"],
            availability=availability,
            allowed_clients=["edge:mobile-demo"],
        )
    )
    return core.store.approve_candidate(candidate.id, ApprovalRequest(), actor="demo")


def run(workspace: Path) -> dict[str, Any]:
    os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    workspace.mkdir(parents=True, exist_ok=True)
    core = CoreService(CoreConfig.in_directory(workspace / "core"))
    always = _approve(core, Availability.ALWAYS, "Available from mobile while Core is off")
    core_only = _approve(core, Availability.CORE, "Available through Edge while Core is online")
    local = _approve(core, Availability.LOCAL, "Never leaves the local Core")

    edge_database = workspace / "edge.sqlite3"
    edge_store = SQLiteRelayStore(edge_database)
    edge = RelayService(edge_store, SECRET)
    for raw in core.store.pending_replication_events():
        event = sign_event(ReplicationEvent.from_mapping(raw), SECRET)
        edge.apply(event)
    identity = ClientIdentity(
        "edge:mobile-demo",
        core.store.vault_id(),
        frozenset({"context:read"}),
        frozenset({"*"}),
    )
    offline = edge.search(identity, query="mobile", limit=20)

    connections = EdgeConnectionStore(core.config)
    material = connections.prepare(core.store.vault_id())
    assert material.forwarding_public_key is not None
    core.store.approve_remote_edge_client(
        "edge:mobile-demo", name="Mobile demonstration", scopes=["context:read"]
    )
    broker = EdgeForwardingBroker(edge_database, material.forwarding_public_key)
    manager = EdgeSyncManager(connections, core.store)
    request_id = broker.enqueue(
        client_id="edge:mobile-demo",
        client_scopes=["context:read"],
        operation="search_context",
        payload={"query": "online", "limit": 20},
    )
    claim = broker.claim()[0]
    online = manager._execute_forward_request(claim)
    broker.answer(request_id, str(claim["claim_token"]), online)
    delivered = broker.wait(request_id)
    assert delivered.response is not None
    online = delivered.response
    visible_ids = {str(item["id"]) for item in offline} | {
        str(item["id"]) for item in online["items"]
    }
    edge.close()
    return {
        "result": "passed",
        "offline_always_available": [item["id"] for item in offline] == [always.id],
        "online_core_available": [item["id"] for item in online["items"]] == [core_only.id],
        "local_only_absent": local.id not in visible_ids,
        "core_authoritative": True,
        "forwarding_queue_after_demo": broker.status(),
        "provider_handshake_exercised": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    if args.workspace:
        result = run(args.workspace.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="atc-edge-mobile-demo-") as temporary:
            result = run(Path(temporary))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

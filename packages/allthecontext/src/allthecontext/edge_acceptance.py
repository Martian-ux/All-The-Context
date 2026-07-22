"""Isolated operator acceptance for a real hosted Edge deployment."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import CoreConfig
from .core.service import CoreService
from .edge_connection import EdgeConnectionStore, EdgeSyncManager
from .edge_setup import normalize_edge_url
from .models import ApprovalRequest, Availability, CandidateInput

ACCEPTANCE_STATE_NAME = "hosted-edge-acceptance.json"
ACCEPTANCE_CONTENT = "All The Context hosted Edge acceptance record"


class HostedEdgeAcceptanceError(RuntimeError):
    """The hosted Edge did not satisfy the release acceptance boundary."""


@dataclass(frozen=True, slots=True)
class HostedEdgePreparation:
    workspace: Path
    record_id: str
    claim_bundle: str = field(repr=False)
    recovery_code: str = field(repr=False)


def _state_path(workspace: Path) -> Path:
    return workspace / ACCEPTANCE_STATE_NAME


def _write_state(workspace: Path, state: dict[str, object]) -> None:
    path = _state_path(workspace)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_state(workspace: Path) -> dict[str, object]:
    try:
        parsed = json.loads(_state_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostedEdgeAcceptanceError(
            "workspace is not an initialized hosted Edge acceptance workspace"
        ) from exc
    if (
        not isinstance(parsed, dict)
        or parsed.get("schema_version") != 1
        or not isinstance(parsed.get("record_id"), str)
    ):
        raise HostedEdgeAcceptanceError("hosted Edge acceptance state is invalid")
    return parsed


def prepare_hosted_edge_acceptance(workspace: Path) -> HostedEdgePreparation:
    """Prepare a synthetic vault and one-time public-key claim in an isolated directory."""

    resolved = workspace.expanduser().resolve()
    state_path = _state_path(resolved)
    if not state_path.is_file():
        if resolved.exists() and any(resolved.iterdir()):
            raise HostedEdgeAcceptanceError(
                "new acceptance workspace must be empty; a personal Core was not modified"
            )
        resolved.mkdir(parents=True, exist_ok=True)
        core = CoreService(CoreConfig.in_directory(resolved))
        candidate = core.store.add_candidate(
            CandidateInput(
                kind="acceptance_test",
                content=ACCEPTANCE_CONTENT,
                scopes=["acceptance:hosted-edge"],
                source_service="all-the-context-release-acceptance",
                source_type="synthetic",
                evidence="Synthetic release acceptance data; contains no personal context.",
                availability=Availability.ALWAYS,
                allowed_clients=["*"],
                explicit_user_statement=True,
            )
        )
        record = core.store.approve_candidate(
            candidate.id,
            ApprovalRequest(reason="isolated hosted Edge release acceptance"),
            actor="hosted-edge-acceptance",
        )
        _write_state(
            resolved,
            {
                "prepared_at": datetime.now(UTC).isoformat(),
                "record_id": record.id,
                "schema_version": 1,
            },
        )
    state = _read_state(resolved)
    if state.get("verified_at") is not None:
        raise HostedEdgeAcceptanceError("this hosted Edge acceptance workspace is already paired")
    core = CoreService(CoreConfig.in_directory(resolved))
    record_id = str(state["record_id"])
    record = core.store.get_record(record_id)
    if record.content != ACCEPTANCE_CONTENT or record.availability != Availability.ALWAYS:
        raise HostedEdgeAcceptanceError("synthetic acceptance record was changed")
    material = EdgeConnectionStore(core.config).prepare(core.store.vault_id())
    if material.claim_bundle is None:
        raise HostedEdgeAcceptanceError("hosted Edge claim was already consumed")
    return HostedEdgePreparation(
        resolved,
        record_id,
        material.claim_bundle.encode(),
        material.recovery_code,
    )


def verify_hosted_edge_acceptance(
    workspace: Path,
    edge_url: str,
    *,
    timeout_seconds: float = 20.0,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Exercise inertness, one-time claim, origin pairing, and event synchronization."""

    resolved = workspace.expanduser().resolve()
    state = _read_state(resolved)
    origin = normalize_edge_url(edge_url, allow_loopback_http=True)
    core = CoreService(CoreConfig.in_directory(resolved))
    record = core.store.get_record(str(state["record_id"]))
    if record.content != ACCEPTANCE_CONTENT or record.availability != Availability.ALWAYS:
        raise HostedEdgeAcceptanceError("synthetic acceptance record was changed")
    connections = EdgeConnectionStore(core.config)
    manager = EdgeSyncManager(connections, core.store)
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0)),
        follow_redirects=False,
    )
    try:
        before_response = active_client.get(f"{origin}/healthz")
        before_response.raise_for_status()
        before = before_response.json()
        if not isinstance(before, dict) or before.get("status") not in {"awaiting_claim", "ok"}:
            raise HostedEdgeAcceptanceError("hosted service is not an ATC Edge claim target")
        if before.get("component") != "edge" or before.get("authority") != "core":
            raise HostedEdgeAcceptanceError("hosted service reported an invalid authority boundary")
        inert_before_claim = before.get("status") == "awaiting_claim"
        if inert_before_claim:
            inert_probe = active_client.get(f"{origin}/about")
            if inert_probe.status_code != 423:
                raise HostedEdgeAcceptanceError("unclaimed Edge exposed a non-claim route")

        connections.connect(
            origin,
            client=active_client,  # type: ignore[arg-type]
            timeout_seconds=timeout_seconds,
        )
        synchronization = manager.sync_now(http_client=active_client)  # type: ignore[arg-type]
        if synchronization.get("state") != "ready":
            raise HostedEdgeAcceptanceError("hosted Edge synchronization did not become ready")
        sequence = int(synchronization.get("last_sequence", 0))
        if sequence < 1:
            raise HostedEdgeAcceptanceError("hosted Edge did not apply the synthetic event")
        counts = core.store.status()["counts"]
        if int(counts["pending_replication_events"]) != 0:
            raise HostedEdgeAcceptanceError("Core retained undelivered acceptance events")

        after_response = active_client.get(f"{origin}/healthz")
        after_response.raise_for_status()
        after = after_response.json()
        if not isinstance(after, dict) or after.get("status") != "ok":
            raise HostedEdgeAcceptanceError("claimed Edge did not report ready health")
    except HostedEdgeAcceptanceError:
        raise
    except Exception as exc:
        raise HostedEdgeAcceptanceError("hosted Edge acceptance request failed") from exc
    finally:
        if owns_client:
            active_client.close()

    _write_state(
        resolved,
        {
            **state,
            "edge_url": origin,
            "last_sequence": sequence,
            "verified_at": datetime.now(UTC).isoformat(),
        },
    )
    return {
        "authority": "core",
        "claim_pairing": "passed",
        "core_authoritative": True,
        "edge_url": origin,
        "inert_before_claim": inert_before_claim,
        "last_sequence": sequence,
        "personal_context_used": False,
        "replication": "passed",
        "result": "passed",
    }

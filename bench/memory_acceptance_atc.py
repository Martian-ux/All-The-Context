"""Current ATC adapter using disposable Core APIs and actual hook formatters.

This is synthetic in-process validation, never installed-client validation.
No candidates are manually approved and no facts are seeded from the oracle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from allthecontext import claude_code_hook, codex_hook
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from fastapi.testclient import TestClient

from bench.cross_client_reader_outcomes import ReaderUsage
from bench.memory_acceptance import Chunk, Citation, Event, Observation, OperationReceipt, Request


class AtcAdapter:
    adapter_id = "atc-public-capture-bootstrap-and-hook-formatters"

    def __init__(self) -> None:
        self.events: dict[str, Event] = {}
        self.keys: dict[str, str] = {}
        self.references: dict[str, str] = {}
        self.capture_clients: dict[tuple[str, str | None], dict[str, str]] = {}
        self.read_clients: dict[str, dict[str, str]] = {}
        self.core: CoreService | None = None
        self.client: TestClient | None = None

    def reset(self, root: Path, scenario_id: str) -> None:
        self.config = CoreConfig.in_directory(root, require_auth=True)
        self.core = CoreService(self.config)
        self.client = TestClient(create_app(self.config, service=self.core))
        setup = self._call(
            "POST", "/v1/setup", {"name": "Synthetic acceptance owner", "scopes": []}, headers={}
        )
        self.owner = {"Authorization": "Bearer " + str(setup["token"])}

    def _call(
        self, method: str, route: str, payload: dict[str, Any], *, headers: dict[str, str]
    ) -> dict[str, Any]:
        assert self.client is not None
        response = self.client.request(method, route, json=payload, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"{route}: HTTP {response.status_code}: {response.text}")
        return dict(response.json())

    def _new_client(self, name: str, scopes: list[str]) -> dict[str, str]:
        created = self._call(
            "POST", "/v1/admin/clients", {"name": name, "scopes": scopes}, headers=self.owner
        )
        return {
            "Authorization": "Bearer " + str(created["token"]),
            "X-ATC-Client-ID": str(created["client"]["id"]),
        }

    def capture(self, event: Event) -> OperationReceipt:
        assert self.core is not None
        self.events[event.event_id] = event
        key = (event.app, event.project)
        if key not in self.capture_clients:
            self.capture_clients[key] = self._new_client(
                f"Synthetic {event.app} capture {event.project or 'personal'}", ["context:capture"]
            )
        if event.event_id not in self.keys:
            self.keys[event.event_id] = f"00000000-0000-4000-8000-{len(self.keys) + 1:012d}"
        payload = {
            "event_id": event.event_id,
            "idempotency_key": self.keys[event.event_id],
            "session_id": "acceptance-session",
            "conversation_id": "acceptance-conversation",
            "sequence": list(self.keys).index(event.event_id) + 1,
            "role": event.role,
            "content": event.text,
            "observed_at": event.at,
        }
        result = self._call(
            "POST", "/v1/lifecycle/events", payload, headers=self.capture_clients[key]
        )
        if result.get("ok") is not True:
            return OperationReceipt(
                status="error", detail=str(result.get("reason", "capture refused"))
            )
        # Provenance mapping uses actual candidates' source references. It never
        # consults an oracle, promotes evidence, or attaches refs absent from hooks.
        candidates, _ = self.core.store.list_candidates(status=None, limit=500)
        formed_records = 0
        for candidate in candidates:
            if (
                candidate.idempotency_key
                in {self.keys[event.event_id], self.keys[event.event_id] + ":formed"}
                and candidate.source_reference is not None
            ):
                self.references[candidate.source_reference] = event.event_id
            if (
                candidate.idempotency_key == self.keys[event.event_id] + ":formed"
                and candidate.record_id is not None
            ):
                formed_records += 1
        return OperationReceipt(
            status="replayed" if result["status"] == "replayed" else "ok",
            formed_records=formed_records,
        )

    def control(self, operation: str, event_id: str | None) -> OperationReceipt:
        assert self.core is not None
        if operation == "restart":
            assert self.client is not None
            self.client.close()
            self.core.close()
            self.core = CoreService(self.config)
            self.client = TestClient(create_app(self.config, service=self.core))
            return OperationReceipt(
                status="ok", detail="Core closed and reopened; credentials retained"
            )
        if operation == "rebuild":
            self.core.store.rebuild_integrity_groups()
            return OperationReceipt(
                status="ok", detail="Integrity projection rebuilt; lexical FTS is query-local"
            )
        if operation == "replay_event" and event_id is not None:
            return self.capture(self.events[event_id])
        if operation == "forget_event" and event_id is not None:
            candidates, _ = self.core.store.list_candidates(status=None, limit=500)
            targets = {
                candidate.record_id
                for candidate in candidates
                if candidate.idempotency_key == self.keys[event_id] + ":formed"
                and candidate.record_id is not None
            }
            if not targets:
                return OperationReceipt(
                    status="unsupported", detail="No formed memory to target; deletion unproven"
                )
            for target in sorted(targets):
                self._call(
                    "POST",
                    "/v1/ingestion/forget",
                    {"record_id": target, "reason": "Synthetic explicit forget request"},
                    headers=self.owner,
                )
            return OperationReceipt(
                status="ok", detail="Explicit reversible memory deletion; not physical purge"
            )
        return OperationReceipt(status="unsupported", detail=operation)

    def _chunk(self, item: dict[str, Any]) -> Chunk:
        assert self.core is not None
        reference = item.get("source_reference")
        record_id = item.get("id", item.get("record_id"))
        if reference is None and isinstance(record_id, str):
            reference = self.core.store.get_record(record_id).source_reference
        event_id = self.references.get(str(reference))
        citations = (
            ()
            if event_id is None
            else (Citation(source_event_id=event_id, quote=self.events[event_id].text),)
        )
        return Chunk(text=str(item.get("content", item.get("text", ""))), citations=citations)

    def checkpoint(self, request: Request) -> Observation:
        app = "claude_code" if request.surface == "claude_code_hook" else "codex"
        if app not in self.read_clients:
            self.read_clients[app] = self._new_client(f"Synthetic {app} read", ["context:read"])
        payload = self._call(
            "POST",
            "/v1/context/bootstrap",
            {
                "task_description": request.question,
                "current_project": request.project,
                "character_budget": request.budget_chars,
            },
            headers=self.read_clients[app],
        )
        items = list(payload["items"])
        capsule = (payload.get("project_context") or {}).get("capsule")
        if isinstance(capsule, dict):
            for name in (
                "current_goal",
                "decisions",
                "constraints_preferences",
                "blockers",
                "recent_meaningful_changes",
            ):
                items.extend(capsule.get(name, []))
        retrieved = tuple(self._chunk(item) for item in items)
        if request.surface == "core_api":
            delivered = retrieved
        else:
            if request.surface == "codex_hook":
                text = codex_hook._bounded_reference_context(payload, budget=request.budget_chars)
            else:
                text = claude_code_hook._bounded_reference_context(payload)
            # Hook text contains no opaque record/source refs: do not invent
            # delivered citations by carrying metadata over from retrieval.
            delivered = (Chunk(text=text),) if text else ()
        return Observation(
            retrieved=retrieved,
            delivered=delivered,
            detail="Unknown facets are not emitted by current bootstrap/hook contracts",
        )

    def usage(self) -> ReaderUsage:
        return ReaderUsage.from_mapping({"model_calls": 0, "provider_calls": 0, "network_calls": 0})

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.core is not None:
            self.core.close()
            self.core = None

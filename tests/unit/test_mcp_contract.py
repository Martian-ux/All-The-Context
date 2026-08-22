from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import anyio
import pytest
from allthecontext import mcp_adapter as local_mcp
from allthecontext.config import CoreConfig
from allthecontext.desktop_setup import CoreProbe
from allthecontext.http_client import (
    ContextApiError,
    ContextHttpClient,
    _relay_proposal_key,
)
from allthecontext.mcp_adapter import (
    _automatic_proposal_key as local_proposal_key,
)
from allthecontext.mcp_adapter import (
    _ensure_local_core,
    build_mcp,
)
from allthecontext.relay import mcp as edge_mcp
from allthecontext.relay.mcp import (
    MAX_EDGE_MCP_REQUEST_BYTES,
    build_edge_mcp_app,
)
from allthecontext.relay.mcp import _automatic_proposal_key as edge_proposal_key
from allthecontext.relay.service import ClientIdentity
from mcp.server.mcpserver.exceptions import ToolError


def _tools(server: Any) -> dict[str, Any]:
    return {tool.name: tool for tool in anyio.run(server.list_tools)}


def _call_tool(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = anyio.run(server.call_tool, name, arguments)
    assert result.structured_content is not None
    return result.structured_content


def test_required_mcp_tools_are_exposed_without_admin_writes() -> None:
    server = build_mcp()
    names = set(_tools(server))
    assert names == {
        "begin_ingestion",
        "bootstrap_context",
        "context_status",
        "finish_ingestion",
        "forget_context",
        "get_context_item",
        "propose_memory",
        "report_context_error",
        "search_context",
        "submit_context_batch",
    }
    assert "delete_context" not in names
    assert "set_permissions" not in names


def test_ingestion_tools_have_strict_generated_schemas() -> None:
    server = build_mcp()
    tools = _tools(server)
    begin_schema = tools["begin_ingestion"].input_schema
    assert set(begin_schema["required"]) == {
        "mode",
        "accessible_sources",
        "unavailable_sources",
        "idempotency_key",
    }
    assert begin_schema.get("additionalProperties") is False


@pytest.mark.parametrize("server_kind", ["local", "edge"])
def test_public_mcp_call_rejects_unexpected_tool_arguments(server_kind: str) -> None:
    if server_kind == "local":
        server = build_mcp()
    else:
        provider = SimpleNamespace(
            public_url="https://edge.example.test",
            resource="https://edge.example.test/mcp",
        )
        server = edge_mcp.build_edge_mcp(SimpleNamespace(), provider, vault_id="vault-1")

    with pytest.raises(ToolError):
        anyio.run(server.call_tool, "context_status", {"unexpected": "value"})


def test_edge_mcp_app_uses_atc_request_limit() -> None:
    provider = SimpleNamespace(
        public_url="https://edge.example.test",
        resource="https://edge.example.test/mcp",
    )
    server = edge_mcp.build_edge_mcp(SimpleNamespace(), provider, vault_id="vault-1")

    build_edge_mcp_app(server, provider)

    assert MAX_EDGE_MCP_REQUEST_BYTES == 256 * 1024
    assert server.session_manager.max_request_body_size == MAX_EDGE_MCP_REQUEST_BYTES


def test_server_instructions_make_context_use_automatic() -> None:
    instructions = build_mcp().instructions
    assert instructions is not None
    assert "call bootstrap_context before answering or acting" in instructions
    assert "call propose_memory before the task ends" in instructions
    assert "explicit_user_statement=true only when" in instructions
    assert "leave it false" in instructions
    assert "evaluates submitted observations automatically" in instructions
    assert "does not create a review task" in instructions
    assert "Call forget_context only when the user explicitly asks" in instructions
    assert "never infer that request" in instructions


def test_propose_memory_schema_exposes_automatic_policy_inputs() -> None:
    tools = _tools(build_mcp())
    schema = tools["propose_memory"].input_schema
    description = (tools["propose_memory"].description or "").casefold()

    assert set(schema["required"]) == {"kind", "content", "scope", "confidence"}
    assert {
        "explicit_user_statement",
        "entity_key",
        "attribute_key",
        "supersedes",
        "observed_at",
        "source_reference",
        "evidence",
    } <= schema["properties"].keys()
    assert schema["properties"]["explicit_user_statement"]["default"] is False
    assert schema.get("additionalProperties") is False
    assert "defaults to false" in description
    assert "directly stated" in description
    assert "inference" in description


def test_local_propose_memory_forwards_automatic_policy_fields(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def propose_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
            captured.update(payload)
            return {"disposition": "applied", "record_id": "record-1"}

    monkeypatch.setattr(local_mcp, "_client", Client)
    result = _call_tool(
        build_mcp(),
        "propose_memory",
        {
            "kind": "preference",
            "content": "Prefer direct answers",
            "scope": "general",
            "confidence": 1.0,
            "source_reference": "conversation:turn-7",
            "evidence": "The user stated this preference.",
            "explicit_user_statement": True,
            "entity_key": "user",
            "attribute_key": "answer_style",
            "supersedes": "record-0",
            "observed_at": "2026-07-23T12:00:00-04:00",
        },
    )

    assert result["disposition"] == "applied"
    operation_id = captured.pop("idempotency_key")
    assert UUID(str(operation_id)).version == 4
    assert captured == {
        "kind": "preference",
        "content": "Prefer direct answers",
        "scopes": ["general"],
        "confidence": 1.0,
        "sensitivity": "normal",
        "source_reference": "conversation:turn-7",
        "evidence": "The user stated this preference.",
        "explicit_user_statement": True,
        "entity_key": "user",
        "attribute_key": "answer_style",
        "supersedes": "record-0",
        "observed_at": "2026-07-23T12:00:00-04:00",
    }


def test_local_report_context_error_keeps_description_and_correction_distinct(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def report_context_error(self, payload: dict[str, Any]) -> dict[str, Any]:
            captured.update(payload)
            return {"disposition": "applied", "record_id": "record-2"}

    monkeypatch.setattr(local_mcp, "_client", Client)
    result = _call_tool(
        build_mcp(),
        "report_context_error",
        {
            "record_id": "record-1",
            "description": "The stored city is out of date.",
            "suggested_correction": "The user lives in Boston.",
        },
    )

    assert result["disposition"] == "applied"
    assert captured == {
        "record_id": "record-1",
        "description": "The stored city is out of date.",
        "suggested_correction": "The user lives in Boston.",
    }


def test_local_forget_context_requires_explicit_record_and_reason(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def forget_context(self, payload: dict[str, Any]) -> dict[str, Any]:
            captured.update(payload)
            return {"disposition": "applied", "record_id": "record-1"}

    monkeypatch.setattr(local_mcp, "_client", Client)
    server = build_mcp()
    tool = _tools(server)["forget_context"]

    assert set(tool.input_schema["required"]) == {"record_id", "reason"}
    assert tool.input_schema.get("additionalProperties") is False
    assert "only on an explicit user request" in tool.description
    assert tool.annotations is not None
    assert tool.annotations.destructive_hint is True
    result = _call_tool(
        server,
        "forget_context",
        {"record_id": "record-1", "reason": "The user asked to forget this."},
    )

    assert result == {"disposition": "applied", "record_id": "record-1"}
    assert captured == {
        "record_id": "record-1",
        "reason": "The user asked to forget this.",
    }


def test_http_client_relay_fallback_preserves_policy_inputs(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        _self: ContextHttpClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del params
        calls.append((method, path, json))
        if path == "/v1/ingestion/propose":
            raise ContextApiError(404, "not_found", "Core proposal endpoint unavailable")
        return {"proposal": {"proposal_id": "proposal-1"}, "canonical": False}

    monkeypatch.setattr(ContextHttpClient, "_request", request)
    client = ContextHttpClient("https://edge.example.test", "client-1", "token")
    result = client.propose_memory(
        {
            "kind": "preference",
            "content": "Prefer direct answers",
            "scopes": ["general"],
            "confidence": 1.0,
            "sensitivity": "normal",
            "source_reference": "conversation:turn-7",
            "evidence": "The user stated this preference.",
            "explicit_user_statement": True,
            "entity_key": "user",
            "attribute_key": "answer_style",
            "supersedes": "record-0",
            "observed_at": "2026-07-23T16:00:00+00:00",
            "idempotency_key": "proposal-1",
        }
    )

    assert result == {
        "proposal": {"proposal_id": "proposal-1"},
        "canonical": False,
        "authority": "core",
        "disposition": "staged",
        "decision_reason": "encrypted_at_edge_until_automatic_core_evaluation",
        "user_action_required": False,
    }
    assert calls[1] == (
        "POST",
        "/v1/proposals",
        {
            "idempotency_key": "proposal-1",
            "kind": "preference",
            "content": "Prefer direct answers",
            "scope": ["general"],
            "confidence": 1.0,
            "sensitivity": "normal",
            "availability": "core_available",
            "provenance": {
                "source_reference": "conversation:turn-7",
                "evidence": "The user stated this preference.",
                "explicit_user_statement": True,
                "entity_key": "user",
                "attribute_key": "answer_style",
                "supersedes": "record-0",
                "observed_at": "2026-07-23T16:00:00+00:00",
            },
        },
    )


def test_http_client_maps_error_report_for_staged_relay_evaluation(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        _self: ContextHttpClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del params
        calls.append((method, path, json))
        if path == "/v1/context/status":
            return {"relay_writable": True}
        return {"proposal": {"proposal_id": "correction-1"}, "review_required": True}

    monkeypatch.setattr(ContextHttpClient, "_request", request)
    client = ContextHttpClient("https://edge.example.test", "client-1", "token")
    result = client.report_context_error(
        {
            "record_id": "record-1",
            "description": "The stored city is out of date.",
            "suggested_correction": "The user lives in Boston.",
        }
    )

    assert calls[1] == (
        "POST",
        "/v1/ingestion/error",
        {
            "record_id": "record-1",
            "content": "The stored city is out of date.",
            "evidence": "The user lives in Boston.",
        },
    )
    assert result == {
        "proposal": {"proposal_id": "correction-1"},
        "canonical": False,
        "authority": "core",
        "disposition": "staged",
        "decision_reason": "encrypted_at_edge_until_automatic_core_evaluation",
        "user_action_required": False,
    }


def test_http_client_forgets_directly_when_core_supports_it(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        _self: ContextHttpClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del params
        calls.append((method, path, json))
        if path == "/v1/context/status":
            return {"core_available": True}
        return {"disposition": "applied", "record_id": "record-1"}

    monkeypatch.setattr(ContextHttpClient, "_request", request)
    client = ContextHttpClient("http://127.0.0.1:7337", "client-1", "token")
    result = client.forget_context(
        {"record_id": "record-1", "reason": "The user asked to forget this."}
    )

    assert result == {"disposition": "applied", "record_id": "record-1"}
    assert calls == [
        ("GET", "/v1/context/status", None),
        (
            "POST",
            "/v1/ingestion/forget",
            {"record_id": "record-1", "reason": "The user asked to forget this."},
        ),
    ]


def test_http_client_stages_forget_when_only_relay_is_available(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        _self: ContextHttpClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del params
        calls.append((method, path, json))
        if path == "/v1/context/status":
            return {"relay_writable": True}
        return {"proposal": {"proposal_id": "forget-1"}}

    monkeypatch.setattr(ContextHttpClient, "_request", request)
    client = ContextHttpClient("https://edge.example.test", "client-1", "token")
    result = client.forget_context(
        {"record_id": "record-1", "reason": "The user asked to forget this."}
    )
    proposal = {
        "kind": "context_forget",
        "content": "The user asked to forget this.",
        "scope": [],
        "confidence": 1.0,
        "sensitivity": "sensitive",
        "availability": "core_available",
        "provenance": {
            "record_id": "record-1",
            "explicit_user_statement": True,
        },
    }

    assert calls[0] == ("GET", "/v1/context/status", None)
    assert calls[1] == (
        "POST",
        "/v1/proposals",
        {
            "idempotency_key": f"forget:{_relay_proposal_key(proposal)}",
            **proposal,
        },
    )
    assert result == {
        "proposal": {"proposal_id": "forget-1"},
        "canonical": False,
        "authority": "core",
        "disposition": "staged",
        "decision_reason": "encrypted_at_edge_until_automatic_core_evaluation",
        "user_action_required": False,
    }


def test_http_client_does_not_turn_a_missing_core_record_into_a_relay_proposal(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def request(
        _self: ContextHttpClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del json, params
        calls.append((method, path))
        if path == "/v1/context/status":
            return {"core_available": True}
        raise ContextApiError(404, "not_found", "context record not found")

    monkeypatch.setattr(ContextHttpClient, "_request", request)
    client = ContextHttpClient("http://127.0.0.1:7337", "client-1", "token")

    with pytest.raises(ContextApiError, match="context record not found"):
        client.forget_context({"record_id": "missing", "reason": "The user asked to forget this."})

    assert calls == [
        ("GET", "/v1/context/status"),
        ("POST", "/v1/ingestion/forget"),
    ]


def test_edge_propose_stages_observation_for_automatic_core_evaluation(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Service:
        def propose(
            self,
            identity: ClientIdentity,
            *,
            idempotency_key: str,
            proposal: dict[str, Any],
        ) -> tuple[dict[str, Any], bool]:
            captured.update(
                {
                    "identity": identity,
                    "idempotency_key": idempotency_key,
                    "proposal": proposal,
                }
            )
            return {"proposal_id": "proposal-1", "status": "queued"}, False

    identity = ClientIdentity(
        client_id="edge-client",
        vault_id="vault-1",
        permissions=frozenset({"context:read", "proposal:write"}),
        context_scopes=frozenset({"*"}),
    )
    monkeypatch.setattr(edge_mcp, "_identity", lambda *_args, **_kwargs: identity)
    server = edge_mcp.build_edge_mcp(
        Service(),
        SimpleNamespace(
            public_url="https://edge.example.test",
            resource="https://edge.example.test/mcp",
        ),
        vault_id="vault-1",
    )
    result = _call_tool(
        server,
        "propose_memory",
        {
            "kind": "preference",
            "content": "Prefer direct answers",
            "scope": "general",
            "confidence": 1.0,
            "explicit_user_statement": True,
            "entity_key": "user",
            "attribute_key": "answer_style",
            "observed_at": "2026-07-23T16:00:00+00:00",
        },
    )

    assert result == {
        "proposal": {"proposal_id": "proposal-1", "status": "queued"},
        "replayed": False,
        "canonical": False,
        "authority": "core",
        "disposition": "staged",
        "decision_reason": "encrypted_at_edge_until_automatic_core_evaluation",
        "user_action_required": False,
    }
    assert "review_required" not in result
    assert captured["proposal"]["provenance"] == {
        "explicit_user_statement": True,
        "entity_key": "user",
        "attribute_key": "answer_style",
        "observed_at": "2026-07-23T16:00:00+00:00",
    }


def test_edge_forget_stages_without_claiming_deletion(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Service:
        def propose(
            self,
            identity: ClientIdentity,
            *,
            idempotency_key: str,
            proposal: dict[str, Any],
        ) -> tuple[dict[str, Any], bool]:
            captured.update(
                {
                    "identity": identity,
                    "idempotency_key": idempotency_key,
                    "proposal": proposal,
                }
            )
            return {"proposal_id": "forget-1", "status": "queued"}, False

    identity = ClientIdentity(
        client_id="edge-client",
        vault_id="vault-1",
        permissions=frozenset({"context:read", "proposal:write"}),
        context_scopes=frozenset({"*"}),
    )
    monkeypatch.setattr(edge_mcp, "_identity", lambda *_args, **_kwargs: identity)
    server = edge_mcp.build_edge_mcp(
        Service(),
        SimpleNamespace(
            public_url="https://edge.example.test",
            resource="https://edge.example.test/mcp",
        ),
        vault_id="vault-1",
    )
    tool = _tools(server)["forget_context"]
    result = _call_tool(
        server,
        "forget_context",
        {"record_id": "record-1", "reason": "The user asked to forget this."},
    )

    assert tool.annotations is not None
    assert tool.annotations.destructive_hint is True
    assert result["disposition"] == "staged"
    assert result["user_action_required"] is False
    assert "deleted_at" not in result
    assert captured["proposal"] == {
        "kind": "context_forget",
        "content": "The user asked to forget this.",
        "scope": [],
        "confidence": 1.0,
        "sensitivity": "sensitive",
        "availability": "core_available",
        "source_service": "edge-client",
        "provenance": {
            "record_id": "record-1",
            "explicit_user_statement": True,
        },
    }


def test_automatic_proposal_keys_are_opaque_and_payload_independent() -> None:
    original = {
        "kind": "preference",
        "content": "Prefer concise answers",
        "scope": ["general"],
        "confidence": 0.7,
        "sensitivity": "normal",
        "evidence": "User said so",
    }
    corrected = {**original, "confidence": 0.95, "evidence": "User repeated it"}

    original_id = local_proposal_key(original)
    corrected_id = local_proposal_key(corrected)
    assert UUID(original_id).version == 4
    assert UUID(corrected_id).version == 4
    assert original_id != corrected_id
    assert "Prefer concise answers" not in original_id
    edge_original_id = edge_proposal_key(original)
    edge_corrected_id = edge_proposal_key(corrected)
    assert UUID(edge_original_id).version == 4
    assert UUID(edge_corrected_id).version == 4
    assert edge_original_id != edge_corrected_id


def test_managed_adapter_never_replaces_an_unverified_loopback_service(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ATC_AUTO_START_CORE", "1")
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.CoreConfig.default",
        lambda: CoreConfig.in_directory(tmp_path),
    )
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.probe_core",
        lambda _config: CoreProbe.UNVERIFIED,
    )
    launched: list[bool] = []
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.launch_core",
        lambda *_args, **_kwargs: launched.append(True),
    )

    with pytest.raises(RuntimeError, match="occupied by a service that is not this"):
        _ensure_local_core("http://127.0.0.1:7337")

    assert not launched


def test_managed_adapter_uses_a_bounded_native_startup_window(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATC_AUTO_START_CORE", "1")
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.CoreConfig.default",
        lambda: CoreConfig.in_directory(tmp_path),
    )
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.probe_core",
        lambda _config: CoreProbe.UNREACHABLE,
    )
    runtime = SimpleNamespace()
    monkeypatch.setattr(
        "allthecontext.mcp_adapter._configured_core_runtime",
        lambda: runtime,
    )
    launches: list[tuple[object, float]] = []
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.launch_core",
        lambda selected, _config, *, wait_seconds: launches.append((selected, wait_seconds)),
    )

    _ensure_local_core("http://127.0.0.1:7337")

    assert launches == [(runtime, 30.0)]


def test_managed_adapter_will_not_auto_start_for_a_remote_target(monkeypatch) -> None:
    monkeypatch.setenv("ATC_AUTO_START_CORE", "1")
    probed: list[bool] = []
    monkeypatch.setattr(
        "allthecontext.mcp_adapter.probe_core",
        lambda _config: probed.append(True) or CoreProbe.UNREACHABLE,
    )

    with pytest.raises(RuntimeError, match=r"restricted to a plain 127\.0\.0\.1"):
        _ensure_local_core("https://edge.example.test")

    assert not probed

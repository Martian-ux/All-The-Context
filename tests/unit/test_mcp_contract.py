from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.desktop_setup import CoreProbe
from allthecontext.mcp_adapter import (
    _automatic_proposal_key as local_proposal_key,
)
from allthecontext.mcp_adapter import (
    _ensure_local_core,
    build_mcp,
)
from allthecontext.relay.mcp import _automatic_proposal_key as edge_proposal_key


def test_required_mcp_tools_are_exposed_without_admin_writes() -> None:
    server = build_mcp()
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert names == {
        "begin_ingestion",
        "bootstrap_context",
        "context_status",
        "finish_ingestion",
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
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    begin_schema = tools["begin_ingestion"].parameters
    assert set(begin_schema["required"]) == {
        "mode",
        "accessible_sources",
        "unavailable_sources",
        "idempotency_key",
    }
    assert begin_schema.get("additionalProperties") is False


def test_server_instructions_make_context_use_automatic() -> None:
    instructions = build_mcp().instructions
    assert instructions is not None
    assert "call bootstrap_context before answering or acting" in instructions
    assert "call propose_memory before the task ends" in instructions


def test_automatic_proposal_keys_cover_metadata_and_preserve_exact_retries() -> None:
    original = {
        "kind": "preference",
        "content": "Prefer concise answers",
        "scope": ["general"],
        "confidence": 0.7,
        "sensitivity": "normal",
        "evidence": "User said so",
    }
    corrected = {**original, "confidence": 0.95, "evidence": "User repeated it"}

    assert local_proposal_key(original) == local_proposal_key(dict(original))
    assert local_proposal_key(original) != local_proposal_key(corrected)
    assert edge_proposal_key(original) == edge_proposal_key(dict(original))
    assert edge_proposal_key(original) != edge_proposal_key(corrected)


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

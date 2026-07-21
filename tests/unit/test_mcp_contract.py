from __future__ import annotations

from allthecontext.mcp_adapter import build_mcp


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

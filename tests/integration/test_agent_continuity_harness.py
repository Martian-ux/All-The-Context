"""Fictional source/MCP journeys and explicitly SCRIPTED evaluator tests.

No test in this module calls a model, uses an API key, or inherits main auth.
The native real command is a separate acceptance boundary.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "smoke_agent_continuity", ROOT / "scripts" / "smoke_agent_continuity.py"
)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def source_history() -> list[dict[str, Any]]:
    """SCRIPTED source identifiers; actual Core identifiers are checked separately."""
    return [
        {**event, "record_id": f"record-{event['key']}", "observation_id": f"observation-{index}"}
        for index, event in enumerate(harness.load_fixture()["history"])
    ]


def valid_action(arm: str = "atc", phase: str = "current") -> dict[str, Any]:
    """Independent hand-authored positive, not output of the scripted agent helper."""
    values: dict[str, Any] = {
        "route": "maple",
        "strategy": "stream",
        "completed": ["schema-check"],
        "task": "build-dispatch",
        "handoff": "amber-otter",
        "reviewer": "Lark",
        "rows": [{"id": "crate-7", "units": 6}, {"id": "crate-9", "units": 12}],
        "launch_window": None,
        "unknown": ["launch_window"],
        "repeat_completed": False,
        "provenance": {},
    }
    latest = {event["key"]: event for event in source_history()}
    for field in ("route", "strategy", "completed", "task", "handoff", "reviewer"):
        if (
            arm == "no_memory"
            or phase == "post_acl"
            or (phase == "post_forget" and field == "handoff")
        ):
            values[field] = None
            values["unknown"].append(field)
        else:
            event = latest[{"strategy": "failure", "task": "unfinished"}.get(field, field)]
            values["provenance"][field] = {
                "source_reference": event["reference"],
                "record_id": event["record_id"],
                "provenance_ids": [event["record_id"], event["observation_id"]],
            }
    return values


def grade(tmp_path: Path, action: Any, *, arm: str = "atc", phase: str = "current"):
    harness.dump(tmp_path / "action-001.json", action)
    history = source_history()
    if phase == "post_acl":
        history = []
    elif phase == "post_forget":
        history = [event for event in history if event["key"] != "handoff"]
    return harness.evaluate_actions(tmp_path, harness.load_fixture(), history, arm, phase)


@pytest.mark.parametrize("arm", harness.ARMS)
@pytest.mark.parametrize("phase", harness.PHASES)
def test_scripted_independent_positive_actions(tmp_path: Path, arm: str, phase: str) -> None:
    result = grade(tmp_path, valid_action(arm, phase), arm=arm, phase=phase)
    assert result["success"], result
    expected = (
        "warranted_uncertainty" if arm == "no_memory" or phase == "post_acl" else "task_success"
    )
    assert result["classification"] == expected


@pytest.mark.parametrize(
    ("change", "failure"),
    [
        ({"route": "cedar"}, "stale_decision"),
        ({"strategy": "batch"}, "repeated_failed_approach"),
        ({"repeat_completed": True}, "repeated_or_unspecified_completed_work"),
        ({"rows": [{"id": "crate-7", "units": 5}]}, "incorrect_artifact_calculation"),
        ({"launch_window": "Friday"}, "invented_uncertain_fact"),
        ({"unknown": []}, "inexact_uncertainty"),
        ({"unknown": ["launch_window", "launch_window"]}, "inexact_uncertainty"),
        ({"unsupported_note": "all work shipped"}, "invalid_or_unsupported_artifact_fields"),
    ],
)
def test_scripted_meaningful_broken_actions_are_caught(tmp_path, change, failure) -> None:
    action = valid_action()
    action.update(change)
    result = grade(tmp_path, action)
    assert not result["success"]
    assert failure in result["failures"]


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("source_reference", "fictional:harborlight/route-v1", "missing_or_inexact_provenance"),
        ("record_id", "unrelated-record", "missing_or_inexact_atc_provenance"),
        ("provenance_ids", ["record-route", "observation-1"], "missing_or_inexact_atc_provenance"),
        ("provenance_ids", ["observation-7", "invented-id"], "missing_or_inexact_atc_provenance"),
    ],
)
def test_scripted_exact_provenance_not_just_correct_answer(tmp_path, field, value, failure) -> None:
    action = valid_action()
    action["provenance"]["route"][field] = value
    assert failure in grade(tmp_path, action)["failures"]


@pytest.mark.parametrize(
    ("phase", "field", "value", "failure"),
    [
        ("post_forget", "handoff", "amber-otter", "forgotten_fact_leak"),
        ("post_acl", "reviewer", "Lark", "acl_fact_leak"),
        ("post_acl", "route", "maple", "acl_fact_leak"),
    ],
)
def test_scripted_forget_and_acl_apply_to_actual_actions(tmp_path, phase, field, value, failure):
    action = valid_action(phase=phase)
    action[field] = value
    result = grade(tmp_path, action, phase=phase)
    assert not result["success"]
    assert failure in result["failures"]


def test_scripted_uncertainty_is_distinct_from_invented_action(tmp_path: Path) -> None:
    action = valid_action("no_memory")
    assert grade(tmp_path, action, arm="no_memory")["success"]
    action["route"] = "maple"
    assert "unsupported_or_forbidden_fact" in grade(tmp_path, action, arm="no_memory")["failures"]


def test_scripted_missing_and_previous_failed_attempt_are_not_hidden(tmp_path: Path) -> None:
    fixture = harness.load_fixture()
    empty = harness.evaluate_actions(tmp_path, fixture, source_history(), "atc", "current")
    assert empty["classification"] == "missing_artifact"
    wrong = valid_action()
    wrong["strategy"] = "batch"
    harness.dump(tmp_path / "action-001.json", wrong)
    harness.dump(tmp_path / "action-002.json", valid_action())
    result = harness.evaluate_actions(tmp_path, fixture, source_history(), "atc", "current")
    assert result["attempt_count"] == 2
    assert "repeated_failed_approach" in result["failures"]


def test_strong_summary_retains_current_evidence_and_distinctions() -> None:
    history = source_history()
    summary = harness.maintain_summary(history)
    assert summary["current"]["route"]["reference"] == "fictional:harborlight/route-v2"
    assert summary["superseded_not_current"] == [
        {
            "key": "route",
            "reference": "fictional:harborlight/route-v1",
            "state": "superseded_not_current",
        }
    ]
    assert history[1]["content"] not in json.dumps(summary)
    for key in ("completed", "failure", "unfinished", "reviewer", "handoff"):
        source = next(item for item in history if item["key"] == key)
        assert summary["current"][key]["content"] == source["content"]
        assert summary["current"][key]["reference"] == source["reference"]
    assert summary["current"]["completed"]["state"] == "completed"
    assert summary["current"]["failure"]["state"] == "failed_approach"
    assert summary["current"]["unfinished"]["state"] == "unfinished"
    assert history == source_history()  # Maintenance does not mutate shared source history.


def test_artifact_mcp_actual_stdio_has_no_filesystem_reader(tmp_path: Path) -> None:
    directory = tmp_path / "session"
    harness.create_session(directory, harness.load_fixture(), [], "no_memory")
    hidden = tmp_path / "answers.json"
    hidden.write_text("PRIVATE_EVALUATOR_CANARY", encoding="utf-8")

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=harness.sys.executable,
            args=[
                str(ROOT / "scripts" / "smoke_agent_continuity.py"),
                "--artifact-server",
                str(directory / "manifest.json"),
            ],
            env=harness.child_environment(directory),
            cwd=str(directory),
        )
        with (directory / "stdio.log").open("w", encoding="utf-8") as errlog:
            async with (
                stdio_client(parameters, errlog=errlog) as streams,
                ClientSession(*streams) as session,
            ):
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == {
                    "list_artifacts",
                    "read_artifact",
                    "write_result",
                }
                for name in (*harness.NEGATIVE_NAMES, str(hidden), "summary.json", "manifest.json"):
                    response = await session.call_tool("read_artifact", {"name": name})
                    assert response.structured_content == {"error": "not_exposed"}
                    assert "PRIVATE_EVALUATOR_CANARY" not in str(response)
                # Resource helpers are a separate native surface. These actual
                # SDK calls exercise the configured artifact server's contract.
                for method, field in (
                    (session.list_resources, "resources"),
                    (session.list_resource_templates, "resource_templates"),
                ):
                    try:
                        inventory = await method()
                    except MCPError as error:
                        assert error.code == -32601  # Method not found: no resource capability.
                    else:
                        assert getattr(inventory, field) == []
                for uri in (
                    hidden.as_uri(),
                    harness.FIXTURE.as_uri(),
                    "file:///evaluator.json",
                    "file:///answers.json",
                    "file:///other-arm/result.json",
                ):
                    with pytest.raises(MCPError) as denial:
                        await session.read_resource(uri)
                    assert "PRIVATE_EVALUATOR_CANARY" not in str(denial.value)
                read = await session.call_tool("read_artifact", {"name": "input.json"})
                assert read.structured_content["value"]["crates"][0]["packs"] == 2
                persisted = await session.call_tool(
                    "write_result", {"result": valid_action("no_memory")}
                )
                assert persisted.structured_content["persisted"] is True
        assert json.loads((directory / "action-001.json").read_text()) == valid_action("no_memory")

    anyio.run(exercise)


NATIVE_SHAPES = ROOT / "tests" / "fixtures" / "continuity_native_v2"
SHAPE_SOURCE = "text({trace_parser_shape: true});"


def fixture_client_events() -> list[dict[str, Any]]:
    """Invented lifecycle/usage inputs; no captured transcript or model outcome."""
    return [
        {"type": "thread.started", "thread_id": "neutral-thread-cobalt"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {
            "id": "neutral-notice-willow", "type": "error",
            "message": "Optional feature notice for fixture testing.",
        }},
        *[
            {"type": "item.completed", "item": {
                "id": f"neutral-item-willow-{index}", "type": "mcp_tool_call",
                "server": "project", "tool": tool, "status": "completed",
                "arguments": {}, "result": {"fixture_only": True},
            }}
            for index, tool in enumerate(("list_artifacts", "read_artifact") * 2)
        ],
        {"type": "turn.completed", "usage": {
            "input_tokens": 120, "cached_input_tokens": 30, "output_tokens": 12,
        }},
    ]


def materialize_trace(directory: Path, shape=None) -> list[dict[str, Any]]:
    """Production-format schema shapes only; absolutely no model outcomes."""
    shape = shape or json.loads((NATIVE_SHAPES / "native-shape.json").read_text())
    manifest = shape["manifest"]
    bundle = (
        directory / "native-trace" / f"trace-{manifest['trace_id']}-{manifest['root_thread_id']}"
    )
    (bundle / "payloads").mkdir(parents=True, exist_ok=True)
    harness.dump(bundle / "manifest.json", shape["manifest"])
    for name, value in shape["payloads"].items():
        harness.dump(bundle / "payloads" / f"{name}.json", value)
    (bundle / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in shape["events"]), encoding="utf-8"
    )
    return fixture_client_events()


def scripted_roster_trace(directory: Path, arm: str, *, extra=None, ambient=None):
    """SCRIPTED parser values in native envelopes, not an isolation receipt."""
    shape = json.loads((NATIVE_SHAPES / "native-shape.json").read_text())
    names = {f"mcp__project__{tool}" for tool in harness.ARTIFACT_TOOLS} | {
        "clock__curr_time",
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    }
    if arm == "atc":
        names |= {f"mcp__atc__{tool}" for tool in harness.READ_TOOLS}
    if extra:
        names.add(extra)
    roster = {
        "declared": sorted(names),
        "callable": sorted(names),
        "ambient": ambient
        or dict.fromkeys(("require", "process", "fetch", "Deno", "Bun"), "undefined"),
        "forbidden": dict.fromkeys(harness.FORBIDDEN_CALLABLES, "undefined"),
    }
    shape["events"][1]["payload"]["source_js"] = harness.ROSTER_CODE
    shape["payloads"]["result"]["response"]["Result"]["content_items"][0]["text"] = json.dumps(
        {"continuity_roster_v1": roster}
    )
    source = harness.resource_probe_code(directory, arm)
    requests, _ = json.JSONDecoder().raw_decode(source.split(" of ", 1)[1])
    checks = []
    for name, args in requests:
        if name == "read_mcp_resource":
            checks.append(
                {
                    "name": name,
                    "args": args,
                    "rejected": (
                        "unknown MCP server 'continuity_unconfigured'"
                        if args["server"] == "continuity_unconfigured"
                        else "resources/read failed: Method not found"
                    ),
                }
            )
        else:
            field = "resources" if name == "list_mcp_resources" else "resourceTemplates"
            checks.append({"name": name, "args": args, "result": {field: []}})
    start = copy.deepcopy(shape["events"][1])
    start["payload"].update(
        source_js=source, runtime_cell_id="resource-cell", model_visible_call_id="resource-call"
    )
    end = copy.deepcopy(shape["events"][2])
    end["payload"].update(
        runtime_cell_id="resource-cell",
        response_payload={
            "raw_payload_id": "resource-result",
            "kind": "tool_result",
            "path": "payloads/resources.json",
        },
    )
    shape["events"][3:3] = [start, end]
    for seq, event in enumerate(shape["events"], start=1):
        event["seq"] = seq
    shape["payloads"]["resources"] = {
        "response": {
            "Result": {
                "cell_id": "resource-cell",
                "error_text": None,
                "content_items": [
                    {"type": "input_text", "text": json.dumps({"continuity_resources_v1": checks})}
                ],
            }
        }
    }
    (directory / "resource-canary.txt").write_text("HIDDEN_RESOURCE_BYTES", encoding="utf-8")
    return shape


def record_escape_denials(tmp_path: Path) -> None:
    store = harness.ArtifactStore({"visible": {}}, tmp_path)
    for name in harness.NEGATIVE_NAMES:
        store.read(name)


@pytest.mark.parametrize("arm", harness.ARMS)
def test_native_shape_roster_positive_retains_actual_audit_requirement(tmp_path, arm):
    shape = scripted_roster_trace(tmp_path, arm)
    events = materialize_trace(tmp_path, shape)
    with pytest.raises(harness.BoundaryError, match="escape negatives"):
        harness.verify_runtime_roster(events, tmp_path, arm)
    record_escape_denials(tmp_path)
    proof = harness.verify_runtime_roster(events, tmp_path, arm)
    assert proof["recording_completed"]
    assert proof["native_source_result"]["runtime_cell_id"] == "neutral-cell-cobalt"
    assert proof["resource_probe"]["canary_absent"]
    assert proof["inference_tool_inventory"][0]["tools"] == ["functions.exec", "functions.wait"]


@pytest.mark.parametrize(
    "extra",
    ("apply_patch", "exec_command", "node_repl", "web_search", "mcp__apps__read", "spawn_agent"),
)
def test_native_shape_roster_rejects_extra_tools(tmp_path, extra):
    events = materialize_trace(tmp_path, scripted_roster_trace(tmp_path, "atc", extra=extra))
    record_escape_denials(tmp_path)
    with pytest.raises(harness.BoundaryError, match="unexpected capabilities"):
        harness.verify_runtime_roster(events, tmp_path, "atc")


def test_native_shape_roster_rejects_self_report_and_ambient_escape(tmp_path):
    shape = scripted_roster_trace(tmp_path, "no_memory", ambient={"process": "object"})
    events = materialize_trace(tmp_path, shape)
    record_escape_denials(tmp_path)
    with pytest.raises(harness.BoundaryError, match="ambient"):
        harness.verify_runtime_roster(events, tmp_path, "no_memory")
    shape["events"][1]["payload"]["source_js"] = "text('not the exact probe');"
    events = materialize_trace(tmp_path, shape)
    events.insert(
        1,
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps({"source": harness.ROSTER_CODE})},
        },
    )
    with pytest.raises(harness.BoundaryError, match="authoritative"):
        harness.verify_runtime_roster(events, tmp_path, "no_memory")


def test_native_shape_neutral_result_and_yielded_then_completed(tmp_path):
    shape = json.loads((NATIVE_SHAPES / "native-shape.json").read_text())
    initial = copy.deepcopy(shape["events"][2])
    initial["payload"].update(
        type="code_cell_initial_response",
        status="yielded",
        response_payload={
            "raw_payload_id": "initial",
            "kind": "tool_result",
            "path": "payloads/initial.json",
        },
    )
    shape["payloads"]["initial"] = {"response": {"Yielded": {"cell_id": "neutral-cell-cobalt"}}}
    shape["events"].insert(2, initial)
    for seq, event in enumerate(shape["events"], start=1):
        event["seq"] = seq
    events = materialize_trace(tmp_path, shape)
    proof = harness.NativeTrace(tmp_path, events).result(SHAPE_SOURCE)
    assert proof["end_seq"] == 4
    assert proof["texts"] == ['{"trace_parser_shape": true}']


@pytest.mark.parametrize(
    "broken",
    (
        "version",
        "manifest_thread",
        "event_thread",
        "rollout",
        "sequence",
        "unclosed",
        "turn",
        "cell",
        "runtime_error",
        "yield_only",
        "result_tag",
        "missing_payload",
        "traversal",
        "absolute_path",
        "ambiguous_start",
        "ambiguous_end",
        "direct_tool",
        "late_direct_tool",
    ),
)
def test_native_shape_broken_bindings_never_become_evidence(tmp_path, broken):
    shape = json.loads((NATIVE_SHAPES / "native-shape.json").read_text())
    events = shape["events"]
    if broken == "version":
        events[1]["schema_version"] = 1
    elif broken == "manifest_thread":
        shape["manifest"]["root_thread_id"] = "other-thread"
    elif broken == "event_thread":
        events[2]["thread_id"] = "other-thread"
    elif broken == "rollout":
        events[2]["rollout_id"] = "other-rollout"
    elif broken == "sequence":
        events[2]["seq"] = 1
    elif broken == "unclosed":
        events.pop()
    elif broken == "turn":
        events[2]["codex_turn_id"] = "other-turn"
    elif broken == "cell":
        shape["payloads"]["result"]["response"]["Result"]["cell_id"] = "other-cell"
    elif broken == "runtime_error":
        shape["payloads"]["result"]["response"]["Result"]["error_text"] = "runtime failed"
    elif broken == "yield_only":
        events[2]["payload"].update(type="code_cell_initial_response", status="yielded")
    elif broken == "result_tag":
        response = shape["payloads"]["result"]["response"]
        response["result"] = response.pop("Result")
    elif broken == "missing_payload":
        del shape["payloads"]["result"]
    elif broken in {"traversal", "absolute_path"}:
        events[2]["payload"]["response_payload"]["path"] = (
            "../result.json" if broken == "traversal" else "C:/outside/result.json"
        )
    elif broken in {"ambiguous_start", "ambiguous_end"}:
        events.insert(3, copy.deepcopy(events[1 if broken == "ambiguous_start" else 2]))
        for seq, event in enumerate(events, start=1):
            event["seq"] = seq
    elif broken in {"direct_tool", "late_direct_tool"}:
        request = shape["payloads"]["request"]
        if broken == "late_direct_tool":
            request = copy.deepcopy(request)
            shape["payloads"]["late"] = request
            late = copy.deepcopy(events[0])
            late["payload"].update(
                inference_call_id="late-inference",
                request_payload={
                    "raw_payload_id": "late-request",
                    "kind": "inference_request",
                    "path": "payloads/late.json",
                },
            )
            events.insert(3, late)
            for seq, event in enumerate(events, start=1):
                event["seq"] = seq
        request["tools"].append({"type": "function", "name": "exec_command"})
    client_events = materialize_trace(tmp_path, shape)
    with pytest.raises(harness.BoundaryError):
        harness.NativeTrace(tmp_path, client_events).result(SHAPE_SOURCE)


@pytest.mark.parametrize(
    "path", ("../outside", "/outside", "C:/outside", r"..\outside", "payloads/../x")
)
def test_native_payload_paths_are_cross_platform_and_owned(tmp_path, path):
    with pytest.raises(harness.BoundaryError, match="unsafe"):
        harness.owned_trace_path(tmp_path, path)


@pytest.mark.parametrize(
    "broken", ("leaked_canary", "extra_resource", "successful_read", "missing_check")
)
def test_native_resource_helper_contract_rejects_content_and_missing_negatives(tmp_path, broken):
    shape = scripted_roster_trace(tmp_path, "atc")
    content = shape["payloads"]["resources"]["response"]["Result"]["content_items"][0]
    checks = json.loads(content["text"])["continuity_resources_v1"]
    if broken == "missing_check":
        checks.pop()
    else:
        check = checks[0 if broken != "extra_resource" else 1]
        check.pop("rejected", None)
        check["result"] = (
            {"contents": [{"text": "HIDDEN_RESOURCE_BYTES"}]}
            if broken == "leaked_canary"
            else {"resources": [{"uri": "file:///hidden"}]}
            if broken == "extra_resource"
            else {"contents": []}
        )
    content["text"] = json.dumps({"continuity_resources_v1": checks})
    events = materialize_trace(tmp_path, shape)
    record_escape_denials(tmp_path)
    with pytest.raises(harness.BoundaryError, match="resource"):
        harness.verify_runtime_roster(events, tmp_path, "atc")


def test_fixture_warning_is_nonfatal_and_usage_is_retained():
    events = fixture_client_events()
    result = harness.classify_client_events(events)
    assert result["turn_completed"] and not result["fatal"] and not result["tool_errors"]
    assert len(result["notices"]) == 1
    assert "fixture testing" in result["notices"][0]["item"]["message"]
    assert len(harness.event_usage(events)["observed_mcp_calls"]) == 4
    assert harness.event_usage(events)["observed_usage_events"][0]["usage"]["input_tokens"] == 120


@pytest.mark.parametrize(
    "failure",
    (
        {"type": "error", "message": "provider/entitlement failure"},
        {"type": "turn.failed", "error": {"message": "turn failed"}},
        {"type": "item.completed", "item": {"type": "error", "message": "Model rerouted to other"}},
    ),
)
def test_startup_warning_never_hides_actual_failure_or_reroute(failure):
    events = fixture_client_events()
    events.insert(-1, failure)
    result = harness.classify_client_events(events)
    assert failure in result["fatal"]
    assert "fixture testing" in result["notices"][0]["item"]["message"]


def test_no_terminal_turn_and_failed_tool_are_not_clean_execution():
    events = fixture_client_events()[:-1]
    failure = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "status": "failed",
            "error": {"message": "permission denied"},
        },
    }
    events.append(failure)
    result = harness.classify_client_events(events)
    assert not result["turn_completed"]
    assert failure in result["tool_errors"]


def test_native_trace_redaction_retains_failures_without_touching_neighbors(tmp_path):
    root = tmp_path / "native-trace"
    (root / "payloads").mkdir(parents=True)
    (root / "trace.jsonl").write_text("incomplete SECRET_GRANT", encoding="utf-8")
    (root / "payloads" / "result.json").write_text('{"token":"SECRET_GRANT"}', encoding="utf-8")
    neighbor = tmp_path / "old-report.json"
    neighbor.write_text("DO_NOT_CHANGE", encoding="utf-8")
    result = harness.redact_native_trace(root, ["SECRET_GRANT"])
    assert result["scan_complete"]
    assert set(result["redacted_files"]) == {"trace.jsonl", "payloads/result.json"}
    assert neighbor.read_text() == "DO_NOT_CHANGE"
    for path in root.rglob("*"):
        if path.is_file():
            assert "SECRET_GRANT" not in path.read_text()
    assert len(result["files"]) == 2


def test_fixture_warning_allows_mocked_native_probe_without_fabricating_action(
    tmp_path, monkeypatch
):
    """Driver regression using native shapes, not a real model invocation."""
    directory = tmp_path / "session"
    harness.create_session(directory, harness.load_fixture(), [], "no_memory", probe=True)

    def fixture_command(argv, owned, environment, seconds, secrets, **kwargs):
        assert environment["CODEX_ROLLOUT_TRACE_ROOT"] == str((owned / "native-trace").resolve())
        assert kwargs["prompt"].count(harness.ROSTER_CODE) == 1
        assert "--ephemeral" in argv and "--ignore-user-config" in argv
        shape = scripted_roster_trace(owned, "no_memory")
        materialize_trace(owned, shape)
        record_escape_denials(owned)
        stdout = owned / "client.jsonl"
        stdout.write_text("".join(json.dumps(event) + "\n" for event in fixture_client_events()),
                          encoding="utf-8")
        return {
            "returncode": 0,
            "timed_out": False,
            "reaped": True,
            "credential_redaction": False,
            "stdout": str(stdout),
        }

    monkeypatch.setattr(harness, "run_command", fixture_command)
    client = harness.RealClient(tmp_path, SimpleNamespace(secrets=[]), float("inf"))
    result = client.execute(directory, "no_memory", probe=True)
    assert result["errors"] == [] and len(result["notices"]) == 1
    assert client.executions[0]["native_recording_completed"]
    assert json.loads((directory / "native-trace-scan.json").read_text())["scan_complete"]
    assert not list(directory.glob("action-*.json"))


def test_failed_mocked_child_redacts_trace_before_malformed_stdout_is_parsed(tmp_path, monkeypatch):
    directory = tmp_path / "session"
    harness.create_session(directory, harness.load_fixture(), [], "no_memory", probe=True)

    def failed_command(argv, owned, environment, seconds, secrets, **kwargs):
        (owned / "native-trace" / "incomplete.json").write_text(
            '{"token":"SYNTHETIC_GRANT"}', encoding="utf-8"
        )
        stdout = owned / "client.jsonl"
        stdout.write_text("malformed", encoding="utf-8")
        return {
            "returncode": 1,
            "timed_out": True,
            "reaped": True,
            "credential_redaction": False,
            "stdout": str(stdout),
        }

    monkeypatch.setattr(harness, "run_command", failed_command)
    client = harness.RealClient(
        tmp_path, SimpleNamespace(secrets=["SYNTHETIC_GRANT"]), float("inf")
    )
    with pytest.raises(harness.BoundaryError, match="complete JSONL"):
        client.execute(directory, "no_memory", probe=True)
    assert "SYNTHETIC_GRANT" not in (directory / "native-trace" / "incomplete.json").read_text()
    assert client.executions[0]["trace_credential_redaction"]
    assert client.executions[0]["reaped"]


def test_invocation_pins_native_agent_and_interactive_controls(tmp_path, monkeypatch):
    client = harness.RealClient(tmp_path, SimpleNamespace(secrets=[]), float("inf"))
    client.cli = "NO_EXECUTION_TEST_CLIENT"
    catalog = {
        "models": [
            {
                "slug": "gpt-6.1-sol",
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "shell_type": "unified_exec",
            }
        ]
    }

    def inspect(argv, name):
        (tmp_path / f"{name}.stderr.log").write_text("", encoding="utf-8")
        return {
            "exec-help": "--ignore-user-config --ignore-rules --ephemeral --json",
            "top-help": "--no-daemon",
            "version": "codex-cli 0.167.2",
            "login-status": "Logged in using ChatGPT",
            "bundled-models": json.dumps(catalog),
            "available-features": (
                "shell_tool stable true\nskip_host_skill_discovery unstable false\n"
                "multi_agent_v2 unstable true\n"
            ),
            "effective-features": (
                "shell_tool stable false\nskip_host_skill_discovery unstable true\n"
                "multi_agent_v2 unstable false\n"
            ),
            "effective-models": json.dumps(harness.configure_catalog(catalog, "gpt-6.1-sol")),
        }[name]

    monkeypatch.setattr(client, "inspect", inspect)
    client.prepare()
    for control in (
        "agents.enabled",
        "tools.update_plan.enabled",
        "tools.experimental_request_user_input.enabled",
        "features.multi_agent_v2",
    ):
        assert client.config[control] is False


def test_catalog_override_changes_only_two_authorized_capabilities() -> None:
    original = {
        "models": [
            {
                "slug": "gpt-6.1-sol",
                "tool_mode": "code_mode_only",
                "shell_type": "unified_exec",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [{"effort": "xhigh"}],
            },
            {"slug": "unrelated", "shell_type": "unified_exec"},
        ]
    }
    frozen = copy.deepcopy(original)
    result = harness.configure_catalog(original, "gpt-6.1-sol")
    assert original == frozen
    assert result["models"][1] == original["models"][1]
    model = result["models"][0]
    assert {key for key in model if model[key] != original["models"][0][key]} == {
        "shell_type",
        "apply_patch_tool_type",
    }
    assert model["shell_type"] == "disabled" and model["apply_patch_tool_type"] is None


def test_scripted_source_process_public_http_and_mcp_journey(tmp_path: Path, monkeypatch) -> None:
    def forbidden_model(*args, **kwargs):
        pytest.fail("scripted mode must not call a model or inspect main authentication")

    monkeypatch.setattr(harness.RealClient, "prepare", forbidden_model)
    output = tmp_path / "scripted-run"
    report = harness.run_harness(output, mode="scripted")
    assert report["status"] == "COMPLETED", report
    assert report["evidence_kind"] == "SCRIPTED_CANNED_TEST_ONLY"
    assert report["semantic_success"] is True
    assert len(report["arms"]) == 9
    assert report["not_executed"] == []
    assert report["executions"] == []
    assert len(report["processes"]) == 4
    pids = [process["pid"] for process in report["processes"]]
    assert all(first != second for first, second in pairwise(pids))
    assert all(process["instance_readiness_verified"] for process in report["processes"])
    assert report["cleanup_complete"]
    assert all(process["stopped"] for process in report["processes"])
    assert all(cell["execution"] == "CANNED_TEST_OUTPUT_NO_MODEL" for cell in report["arms"])
    for phase in harness.PHASES:
        evidence = json.loads((output / f"public-mcp-{phase}.json").read_text())
        assert set(evidence["tools"]) == harness.READ_TOOLS
        construction = [
            json.loads(
                (output / "sessions" / phase / arm / "construction-accounting.json").read_text()
            )
            for arm in harness.ARMS
        ]
        assert len({cell["eligible_history_sha256"] for cell in construction}) == 1
    assert "amber-otter" not in (output / "public-post_forget.json").read_text()
    summary_path = output / "sessions" / "post_forget" / "maintained_summary" / "manifest.json"
    assert "amber-otter" not in summary_path.read_text()
    revoked_path = output / "sessions" / "post_acl" / "maintained_summary" / "manifest.json"
    assert "Lark" not in revoked_path.read_text()
    assert json.loads((output / "public-post_acl.json").read_text())["revoked"] is True
    previous = (output / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        harness.run_harness(output, mode="scripted")
    assert (output / "report.json").read_bytes() == previous


def test_valid_blocked_report_never_fabricates_agent_outputs(tmp_path: Path, monkeypatch):
    def blocked_start(self):
        raise harness.BoundaryError("SCRIPTED setup failure for report-path test")

    monkeypatch.setattr(harness.SourceCore, "start", blocked_start)
    monkeypatch.setattr(harness, "source_identity", lambda: {"head": "synthetic", "dirty": False})
    # Scripted mode tests the error path without any model execution.
    report = harness.run_harness(tmp_path / "blocked", mode="scripted")
    assert report["status"] == "BLOCKED"
    assert report["report_generation_valid"] is True
    assert len(report["not_executed"]) == 9
    assert report["arms"] == report["executions"] == []
    assert "SCRIPTED setup failure" in report["errors"][0]["message"]


@pytest.mark.parametrize("boundary", ("isolation", "entitlement"))
def test_scripted_client_failure_finishes_public_journey_without_fallback(
    tmp_path: Path, monkeypatch, boundary: str
) -> None:
    """Mocked client failures, actual source processes; absolutely no model calls."""
    original_check_output = harness.subprocess.check_output

    def frozen_source(argv, **kwargs):
        if argv == ["git", "show", "HEAD:scripts/fixtures/agent_continuity_v1.json"]:
            return harness.FIXTURE.read_bytes()
        return original_check_output(argv, **kwargs)

    def prepare(self):
        if boundary == "isolation":
            raise harness.BoundaryError("SCRIPTED unavailable executed roster")

    invocations = []

    def execute(self, directory, arm, *, probe=False):
        invocations.append({"arm": arm, "probe": probe})
        if not probe:
            raise harness.BoundaryError("SCRIPTED entitlement denial; never retry")
        return {"SCRIPTED_probe_only": True}

    def forbidden_scripted_action(*args):
        pytest.fail("real mode must never fabricate a scripted action")

    monkeypatch.setattr(harness, "source_identity", lambda: {"head": "SCRIPTED", "dirty": False})
    monkeypatch.setattr(harness.subprocess, "check_output", frozen_source)
    monkeypatch.setattr(harness.RealClient, "prepare", prepare)
    monkeypatch.setattr(harness.RealClient, "execute", execute)
    monkeypatch.setattr(harness, "scripted_action", forbidden_scripted_action)
    output = tmp_path / "mocked-client-real-source"
    report = harness.run_harness(output, mode="real")
    assert report["status"] == "BLOCKED", report
    assert report["source_public_journey_complete"] is True
    assert report["cleanup_complete"] is True
    assert not list(output.rglob("action-*.json"))
    if boundary == "isolation":
        assert invocations == []
        assert len(report["not_executed"]) == 9
        assert report["boundary"] == "client_isolation"
    else:
        assert len([call for call in invocations if call["probe"]]) == 3
        assert len([call for call in invocations if not call["probe"]]) == 1
        assert len(report["not_executed"]) == 8
        assert report["boundary"] == "real_continuation"
        assert report["arms"][0]["evaluation"]["classification"] == "missing_artifact"
    assert (output / "public-mutation-post_forget.json").is_file()
    assert (output / "public-mutation-post_acl.json").is_file()


def test_child_environment_does_not_inherit_provider_or_owner_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "NEVER_INHERIT")
    monkeypatch.setenv("ATC_CLIENT_TOKEN", "OWNER_TOKEN_MUST_STAY_IN_PARENT")
    monkeypatch.setenv("CODEX_ROLLOUT_TRACE_ROOT", str(tmp_path / "unowned-trace"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "existing-auth-owner"))
    environment = harness.child_environment(tmp_path, codex=True)
    assert "OPENAI_API_KEY" not in environment
    assert "ATC_CLIENT_TOKEN" not in environment
    assert "CODEX_ROLLOUT_TRACE_ROOT" not in environment
    assert environment["CODEX_HOME"] == str(tmp_path / "existing-auth-owner")
    assert not (tmp_path / "existing-auth-owner").exists()

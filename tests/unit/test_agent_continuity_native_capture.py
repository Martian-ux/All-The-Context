"""Invented fixture-level lifecycle contracts; no model/Core invocation."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "continuity_capture_harness", ROOT / "scripts/smoke_agent_continuity.py"
)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def lifecycle_fixture(directory: Path, module=harness):
    """Build a three-response chain with full resource-denial correlations.

    Every identifier, notice and payload is invented. This exercises the driver
    contract, not the fidelity of any recorded client stream.
    """
    thread, turn = "fixture-thread-juniper", "fixture-turn-juniper"
    rollout, trace = "fixture-rollout-juniper", "fixture-trace-juniper"
    events, payloads = [], {}

    def event(event_type, **fields):
        events.append({
            "schema_version": 2, "seq": len(events) + 1, "wall_time_unix_ms": 0,
            "thread_id": thread, "codex_turn_id": turn, "rollout_id": rollout,
            "payload": {"type": event_type, **fields},
        })

    def ref(name, kind, value):
        path = f"payloads/{name}.json"
        payloads[path] = value
        return {"raw_payload_id": f"fixture-payload-{name}", "kind": kind, "path": path}

    tools = [{"type": "namespace", "name": "functions", "tools": [
        {"type": "custom", "name": "exec", "description": "fixture wrapper"},
        {"type": "function", "name": "wait", "parameters": {"type": "object"}},
    ]}]

    def inference(index):
        request = {"type": "response.create", "model": "gpt-6.1-sol",
                   "reasoning": {"effort": "xhigh"}, "input": []}
        if index == 1:
            request["input"] = [{"tools": tools}]
        else:
            request["previous_response_id"] = f"fixture-response-juniper-{index - 1}"
        event("inference_started", inference_call_id=f"fixture-inference-juniper-{index}",
              provider_name="OpenAI",
              request_payload=ref(f"request-{index}", "inference_request", request))

    def complete_inference(index):
        response_id = f"fixture-response-juniper-{index}"
        event("inference_completed", inference_call_id=f"fixture-inference-juniper-{index}",
              response_id=response_id, response_payload=ref(
                  f"response-{index}", "inference_response", {"response_id": response_id}))

    def start_cell(name, source):
        event("code_cell_started", runtime_cell_id=f"fixture-cell-{name}",
              model_visible_call_id=f"fixture-call-{name}", source_js=source + "\n")

    def end_cell(name, output):
        event("code_cell_ended", runtime_cell_id=f"fixture-cell-{name}", status="completed",
              response_payload=ref(f"{name}-result", "tool_result", {"response": {"Result": {
                  "cell_id": f"fixture-cell-{name}", "error_text": None,
                  "content_items": [{"type": "input_text", "text": json.dumps(output)}],
              }}}))

    names = sorted({f"mcp__project__{tool}" for tool in module.ARTIFACT_TOOLS}
                   | {f"mcp__atc__{tool}" for tool in module.READ_TOOLS}
                   | module.NATIVE_UTILITIES)
    roster = {"declared": names, "callable": names,
              "ambient": dict.fromkeys(("require", "process", "fetch", "Deno", "Bun"), "undefined"),
              "forbidden": dict.fromkeys(module.FORBIDDEN_CALLABLES, "undefined")}
    client_events = [
        {"type": "thread.started", "thread_id": thread}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "fixture-notice-juniper",
         "type": "error", "message": "Optional feature notice for fixture testing."}},
    ]
    inference(1)
    complete_inference(1)
    inference(2)
    start_cell("roster", module.ROSTER_CODE)
    end_cell("roster", {"continuity_roster_v1": roster})
    complete_inference(2)
    inference(3)
    source = module.resource_probe_code(directory, "atc")
    requested, _ = json.JSONDecoder().raw_decode(source.split(" of ", 1)[1])
    start_cell("resources", source)
    checks = []
    for index, (name, args) in enumerate(requested):
        if name != "read_mcp_resource":
            field = "resources" if name == "list_mcp_resources" else "resourceTemplates"
            checks.append({"name": name, "args": args, "result": json.dumps({field: []})})
            continue
        error = ("unknown MCP server 'continuity_unconfigured'"
                 if args["server"] == "continuity_unconfigured"
                 else "resources/read failed: Method not found")
        checks.append({"name": name, "args": args, "rejected": error})
        call_id = f"fixture-tool-juniper-{index}"
        event("tool_call_started", tool_call_id=call_id,
              requester={"type": "code_cell", "runtime_cell_id": "fixture-cell-resources"},
              invocation_payload=ref(f"resource-{index}-invocation", "tool_invocation", {
                  "tool_name": name, "tool_namespace": None,
                  "payload": {"type": "function", "arguments": json.dumps(args)},
              }))
        if args["server"] != "continuity_unconfigured":
            event("tool_call_runtime_started", tool_call_id=call_id,
                  kind={"type": "mcp", "server": args["server"], "tool": name},
                  runtime_payload=ref(f"resource-{index}-runtime", "tool_runtime_event", {
                      "call_id": call_id, "turn_id": turn,
                      "invocation": {"server": args["server"], "tool": name, "arguments": args},
                  }))
            client_events.append({"type": "item.completed", "item": {
                "id": f"fixture-item-juniper-{index}", "type": "mcp_tool_call",
                "server": args["server"], "tool": name, "arguments": copy.deepcopy(args),
                "status": "failed", "result": None, "error": {"message": error},
            }})
        event("tool_call_ended", tool_call_id=call_id, status="failed",
              result_payload=ref(f"resource-{index}-result", "tool_result",
                                 {"type": "error", "error": error}))
    end_cell("resources", {"continuity_resources_v1": checks})
    complete_inference(3)
    event("recording_completed", trace_id=trace, root_thread_id=thread)
    client_events.append({"type": "turn.completed", "usage": {
        "input_tokens": 180, "cached_input_tokens": 40, "output_tokens": 20,
    }})
    return {
        "manifest": {"schema_version": 2, "root_thread_id": thread, "rollout_id": rollout,
                     "trace_id": trace, "raw_event_log": "trace.jsonl", "payloads_dir": "payloads",
                     "started_at_unix_ms": 0},
        "events": events, "payloads": payloads, "client_events": client_events,
        "artifact_audit": [{"operation": "read", "name": name, "allowed": False}
                           for name in module.NEGATIVE_NAMES],
        "canary": "FIXTURE_RESOURCE_CANARY_JUNIPER",
    }


def materialize(directory, capture, module=harness):
    manifest = capture["manifest"]
    bundle = directory / "native-trace" / (
        f"trace-{manifest['trace_id']}-{manifest['root_thread_id']}"
    )
    (bundle / "payloads").mkdir(parents=True, exist_ok=True)
    module.dump(bundle / "manifest.json", manifest)
    for path, value in capture["payloads"].items():
        module.dump(bundle / path, value)
    for path, events in (
        (bundle / "trace.jsonl", capture["events"]),
        (directory / "client.jsonl", capture["client_events"]),
        (directory / "artifact-audit.jsonl", capture["artifact_audit"]),
    ):
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    (directory / "resource-canary.txt").write_text(capture["canary"], encoding="utf-8")


def fixture_client(directory, capture, monkeypatch, module=harness, *, secrets=()):
    """Use the production driver with invented command output; no subprocesses."""
    directory.mkdir(parents=True, exist_ok=True)

    def fixture_command(argv, owned, environment, seconds, grants, **kwargs):
        assert owned == directory
        assert environment["CODEX_ROLLOUT_TRACE_ROOT"] == str((owned / "native-trace").resolve())
        assert "--ephemeral" in argv
        assert kwargs["prompt"].count(module.ROSTER_CODE) == 1
        materialize(owned, capture, module)
        return {
            "returncode": 0, "timed_out": False, "reaped": True,
            "credential_redaction": False, "stdout": str(owned / "client.jsonl"),
        }

    monkeypatch.setattr(module, "run_command", fixture_command)
    journey = SimpleNamespace(
        secrets=list(secrets), reader="UNUSED_OFFLINE_READER", reader_id="offline-reader",
        core=SimpleNamespace(url="http://127.0.0.1:1"),
    )
    client = module.RealClient(directory, journey, float("inf"))
    return client, client.execute(directory, "atc", probe=True)


def test_fixture_lifecycle_positive_through_driver(tmp_path, monkeypatch):
    """Full invented correlations exercise driver gates, not recorded fidelity."""
    directory = tmp_path / "fixture-lifecycle"
    capture = lifecycle_fixture(directory)
    client, result = fixture_client(directory, capture, monkeypatch)
    assert result["errors"] == [] and len(result["notices"]) == 1
    assert len(result["tool_errors"]) == 18  # Raw failures are never suppressed.
    execution = client.executions[0]
    assert execution["native_recording_completed"]
    assert len(execution["expected_resource_failures"]) == 19
    assert sum(item["client_item_id"] is not None
               for item in execution["expected_resource_failures"]) == 18
    proof = json.loads((directory / "verified-roster.json").read_text())
    inventory = proof["inference_tool_inventory"]
    assert len(inventory) == 3
    assert len({item["tools_sha256"] for item in inventory}) == 1
    assert inventory[1]["previous_response_id"] == inventory[0]["response_id"]
    assert inventory[2]["previous_response_id"] == inventory[1]["response_id"]
    assert len({item["declaration_seq"] for item in inventory}) == 1
    for key in ("native_source_result", "resource_probe"):
        assert proof[key]["source_representation"] == "exact_plus_terminal_lf"
        assert proof[key]["source_js_sha256"] != proof[key]["requested_source_sha256"]
    assert len(proof["resource_probe"]["checks"]) == 23
    assert len(proof["declared"]) == len(proof["callable"]) == 11
    assert not list(directory.glob("action-*.json"))
    assert json.loads((directory / "client-event-classification.json").read_text())["tool_errors"]


def resource_checks(capture):
    result = capture["payloads"]["payloads/resources-result.json"]["response"]["Result"]
    content = result["content_items"][0]
    return content, json.loads(content["text"])["continuity_resources_v1"]


@pytest.mark.parametrize("broken", (
    "unrelated_tool", "uncorrelated_arguments", "changed_error", "duplicate_failure",
    "native_error", "native_requester", "native_invocation", "native_runtime", "native_turn",
    "real_resource_failure", "malformed_tools",
    "unknown_parent", "stale_parent", "cross_turn_parent", "missing_parent_completion",
    "model", "effort", "extra_tools", "changed_schema", "missing_initial_tools",
    "changed_source", "two_lfs", "crlf", "resource_content", "resource_server",
    "resource_template", "resource_inventory_malformed", "resource_missing",
    "leaked_canary", "unclosed", "malformed_closure", "missing_payload", "fatal_provider",
    "origin_zero", "origin_two", "sequence_gap", "sequence_duplicate", "sequence_reordered",
))
def test_fixture_lifecycle_broken_variants_fail_closed(tmp_path, monkeypatch, broken):
    directory = tmp_path / "fixture-lifecycle"
    capture = lifecycle_fixture(directory)
    request = capture["payloads"]["payloads/request-2.json"]
    first_tools = capture["payloads"]["payloads/request-1.json"]["input"][0]["tools"]
    failures = [event for event in capture["client_events"]
                if event.get("item", {}).get("status") == "failed"]
    item = failures[0]["item"]
    if broken in {"origin_zero", "origin_two"}:
        for event in capture["events"]:
            event["seq"] += -1 if broken == "origin_zero" else 1
    elif broken == "sequence_gap":
        capture["events"].pop(1)
    elif broken == "sequence_duplicate":
        capture["events"].insert(2, copy.deepcopy(capture["events"][1]))
    elif broken == "sequence_reordered":
        capture["events"][1:3] = reversed(capture["events"][1:3])
    elif broken == "unrelated_tool":
        item["tool"] = "search_context"
    elif broken == "uncorrelated_arguments":
        item["arguments"]["uri"] = "file:///unrelated"
    elif broken == "changed_error":
        item["error"]["message"] = "provider failed"
    elif broken == "duplicate_failure":
        capture["client_events"].insert(-1, copy.deepcopy(failures[0]))
    elif broken == "native_error":
        capture["payloads"]["payloads/resource-3-result.json"]["error"] = "provider failed"
    elif broken == "real_resource_failure":
        content, checks = resource_checks(capture)
        error = "resources/read failed: transport unavailable"
        checks[3]["rejected"] = error
        content["text"] = json.dumps({"continuity_resources_v1": checks})
        capture["payloads"]["payloads/resource-3-result.json"]["error"] = error
        item["error"]["message"] = error
    elif broken == "native_invocation":
        capture["payloads"]["payloads/resource-3-invocation.json"]["tool_name"] = "search_context"
    elif broken == "native_runtime":
        runtime = capture["payloads"]["payloads/resource-3-runtime.json"]
        runtime["invocation"]["server"] = "unrelated"
    elif broken in {"native_requester", "native_turn"}:
        start = next(event for event in capture["events"]
                     if event["payload"].get("invocation_payload", {}).get("path")
                     == "payloads/resource-3-invocation.json")
        if broken == "native_requester":
            start["payload"]["requester"]["runtime_cell_id"] = "1"
        else:
            start["codex_turn_id"] = "unrelated-turn"
    elif broken == "unknown_parent":
        request["previous_response_id"] = "unobserved-response"
    elif broken == "stale_parent":
        capture["payloads"]["payloads/request-3.json"]["previous_response_id"] = (
            request["previous_response_id"]
        )
    elif broken == "cross_turn_parent":
        event = next(event for event in capture["events"]
                     if event["payload"].get("request_payload", {}).get("path")
                     == "payloads/request-2.json")
        event["codex_turn_id"] = "unrelated-turn"
    elif broken == "missing_parent_completion":
        completion = next(event for event in capture["events"]
                          if event["payload"]["type"] == "inference_completed")
        completion["payload"]["type"] = "unobserved_completion"
    elif broken == "model":
        request["model"] = "other-model"
    elif broken == "effort":
        request["reasoning"]["effort"] = "low"
    elif broken == "malformed_tools":
        request["tools"] = "invalid declaration"
    elif broken in {"extra_tools", "changed_schema"}:
        request["tools"] = copy.deepcopy(first_tools)
        if broken == "extra_tools":
            request["tools"].append({"type": "function", "name": "exec_command"})
        else:
            request["tools"][0]["tools"][0]["description"] = "changed schema"
    elif broken == "missing_initial_tools":
        capture["payloads"]["payloads/request-1.json"]["input"] = []
    elif broken in {"changed_source", "two_lfs", "crlf"}:
        source = next(event["payload"] for event in capture["events"]
                      if event["payload"]["type"] == "code_cell_started")
        source["source_js"] = source["source_js"].removesuffix("\n") + {
            "changed_source": "; text('extra');\n", "two_lfs": "\n\n", "crlf": "\r\n"
        }[broken]
    elif broken.startswith("resource_") or broken == "leaked_canary":
        content, checks = resource_checks(capture)
        if broken == "resource_missing":
            checks.pop()
        elif broken == "leaked_canary":
            checks[0]["rejected"] += capture["canary"]
        elif broken == "resource_inventory_malformed":
            checks[1]["result"] = '{"resources":[]'
        else:
            checks[1]["result"] = json.dumps({
                "resource_content": {"resources": [{"uri": "file:///hidden"}]},
                "resource_server": {"server": "unrelated", "resources": []},
                "resource_template": {"resources": [], "resourceTemplates": [{"uri": "x"}]},
            }[broken])
        content["text"] = json.dumps({"continuity_resources_v1": checks})
    elif broken == "unclosed":
        capture["events"].pop()
    elif broken == "malformed_closure":
        capture["events"][-1]["payload"]["root_thread_id"] = "unrelated-thread"
    elif broken == "missing_payload":
        del capture["payloads"]["payloads/resources-result.json"]
    elif broken == "fatal_provider":
        capture["client_events"].insert(-1, {"type": "error", "message": "provider failed"})
    with pytest.raises(harness.BoundaryError):
        fixture_client(directory, capture, monkeypatch)


def test_fixture_lifecycle_credential_emission_retains_redacted_failure(tmp_path, monkeypatch):
    directory = tmp_path / "fixture-lifecycle"
    capture = lifecycle_fixture(directory)
    capture["payloads"]["payloads/resources-result.json"]["credential"] = "OFFLINE_SYNTHETIC_GRANT"
    with pytest.raises(harness.BoundaryError, match="credential"):
        fixture_client(directory, capture, monkeypatch, secrets=["OFFLINE_SYNTHETIC_GRANT"])
    scan = json.loads((directory / "native-trace-scan.json").read_text())
    assert scan["redacted_files"]
    assert not (directory / "verified-roster.json").exists()


def complete_report():
    """Gate-only bookkeeping fixture, explicitly synthetic; no agent outcome claim."""
    executions, cells = [], []
    for phase in (None, *harness.PHASES):
        for arm in harness.ARMS:
            execution = {
                "arm": arm, "probe": phase is None, "directory": f"{phase}/{arm}",
                "native_recording_completed": True, "returncode": 0, "reaped": True,
                "timed_out": False, "credential_redaction": False,
                "trace_credential_redaction": False, "turn_completed": True, "fatal": [],
            }
            executions.append(execution)
            if phase is not None:
                cells.append({
                    "arm": arm, "phase": phase, "directory": execution["directory"],
                    "fresh_session": True, "execution_boundary": "actual_supported_codex_client",
                    "execution": copy.deepcopy(execution),
                    "evaluation": {
                        "success": False, "classification": "observed_action_failure",
                        "failures": ["missing_or_inexact_atc_provenance"],
                    },
                })
    return {
        "mode": "real", "status": "COMPLETED", "source_public_journey_complete": True,
        "cleanup_complete": True, "errors": [], "not_executed": [], "arms": cells,
        "executions": executions, "semantic_success": False,
    }


@pytest.mark.parametrize("broken", (
    "blocked", "scripted", "missing_cell", "duplicate_cell", "missing_evaluation",
    "missing_isolation", "missing_recording", "missing_probe", "missing_execution",
    "missing_cleanup", "failed_execution", "failed_source_journey",
))
def test_complete_gate_requires_actual_unique_evaluated_cells(broken):
    report = complete_report()
    assert harness.experiment_complete(report)  # Measured task failure is complete.
    if broken == "blocked":
        report["status"] = "BLOCKED"
    elif broken == "scripted":
        report["mode"] = "scripted"
    elif broken == "missing_cell":
        report["arms"].pop()
    elif broken == "duplicate_cell":
        report["arms"][-1] = copy.deepcopy(report["arms"][0])
    elif broken == "missing_evaluation":
        del report["arms"][0]["evaluation"]
    elif broken == "missing_isolation":
        report["arms"][0]["execution"]["native_recording_completed"] = False
    elif broken == "missing_recording":
        report["executions"][-1]["native_recording_completed"] = False
    elif broken in {"missing_probe", "missing_execution"}:
        report["executions"].pop(0 if broken == "missing_probe" else -1)
    elif broken == "missing_cleanup":
        report["cleanup_complete"] = False
    elif broken == "failed_execution":
        report["arms"][0]["execution_error"] = "actual failure"
    elif broken == "failed_source_journey":
        report["source_public_journey_complete"] = False
    assert not harness.experiment_complete(report)


@pytest.mark.parametrize(("strict", "complete", "expected"), (
    (False, False, 0), (True, False, 1), (True, True, 0),
))
def test_cli_complete_gate_keeps_report_and_measured_failures(
    tmp_path, monkeypatch, capsys, strict, complete, expected
):
    report = complete_report() if complete else {"status": "BLOCKED"}

    def fixture_run(output, **kwargs):
        output.mkdir()
        harness.dump(output / "report.json", report)
        return report

    output = tmp_path / "report"
    monkeypatch.setattr(harness, "run_harness", fixture_run)
    monkeypatch.setattr(sys, "argv", [
        "smoke_agent_continuity.py", "--mode", "real", "--output", str(output),
        *(["--require-complete"] if strict else []),
    ])
    assert harness.main() == expected
    assert json.loads((output / "report.json").read_text()) == report
    assert json.loads(capsys.readouterr().out)["report"] == str(output.resolve() / "report.json")

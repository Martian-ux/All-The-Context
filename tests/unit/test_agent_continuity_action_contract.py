"""Prospective public contract and fixture-level negatives; no model/Core execution."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import anyio
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "continuity_action_contract", ROOT / "scripts/smoke_agent_continuity.py"
)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)

FACT_KEYS = ("route", "strategy", "completed", "task", "reviewer", "handoff")
METADATA_KEYS = ("source_reference", "record_id", "provenance_ids")
UNCITED_KEYS = ("rows", "repeat_completed", "unknown", "launch_window", "provenance")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_closed_provenance_contract_is_exposed(tmp_path: Path) -> None:
    """Inspect the actual MCP description and actual execute-generated prompts."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    assert harness.FACT_PROVENANCE_KEYS == FACT_KEYS
    assert harness.CITATION_METADATA_KEYS == METADATA_KEYS
    assert harness.UNCITED_ACTION_KEYS == UNCITED_KEYS
    assert harness.MAX_REAL_EXECUTIONS == 12
    canonical = json.dumps(harness.ACTION_CONTRACT, sort_keys=True, separators=(",", ":"))
    assert _sha(canonical.encode()) == harness.ACTION_CONTRACT_SHA256
    tools = anyio.run(harness.ArtifactStore({"visible": {}}, tmp_path).server().list_tools)
    assert {tool.name for tool in tools} == harness.ARTIFACT_TOOLS
    write = next(tool for tool in tools if tool.name == "write_result")
    assert harness.ACTION_CONTRACT_TEXT in write.description
    assert write.input_schema["properties"]["result"]["type"] == "object"
    for key in FACT_KEYS + METADATA_KEYS + UNCITED_KEYS:
        assert key in write.description
    assert "only permitted fact keys are " + ", ".join(FACT_KEYS) in write.description
    assert "only permitted metadata keys are " + ", ".join(METADATA_KEYS) in write.description
    assert "Cite every available fact among these keys" in write.description
    assert (
        "If provenance_ids is supplied in any arm, record_id is also required" in write.description
    )
    assert "Do not cite derived rows, repeat_completed, unknown, launch_window" in write.description
    assert "omit its citation" in write.description
    # No task answers can enter the contract: all fixture-specific outcomes/references stay private.
    fixture = harness.load_fixture()
    for key in FACT_KEYS:
        value = fixture["outcome_rules"][key]
        for answer in value if isinstance(value, list) else [value]:
            assert answer not in harness.ACTION_CONTRACT_TEXT
    for reference in fixture["outcome_rules"]["required_provenance"].values():
        assert reference not in harness.ACTION_CONTRACT_TEXT

    class NoModelExecution(Exception):
        pass

    captured = []

    def no_model(argv, directory, environment, seconds, secrets, *, prompt):
        captured.append((directory, prompt, argv, seconds))
        raise NoModelExecution

    journey = SimpleNamespace(
        secrets=[],
        reader="UNUSED_OFFLINE_READER",
        reader_id="offline-reader",
        core=SimpleNamespace(url="http://127.0.0.1:1"),
    )
    client = harness.RealClient(tmp_path, journey, float("inf"))
    client.config = {"model_reasoning_effort": "xhigh"}
    # The probes use the real driver too. No process, native trace or model is fabricated.
    with patch.object(harness, "run_command", no_model):
        for arm in harness.ARMS:
            directory = tmp_path / "probes" / arm
            harness.create_session(directory, fixture, [], arm, probe=True)
            with pytest.raises(NoModelExecution):
                client.execute(directory, arm, probe=True)
        prefix = captured[0][1].split("\nThen run exactly this separate JavaScript cell")[0]
        assert prefix.endswith(harness.ROSTER_CODE)
        for phase in harness.PHASES:
            for arm in harness.ARMS:
                directory = tmp_path / "sessions" / phase / arm
                harness.create_session(directory, fixture, [], arm)
                with pytest.raises(NoModelExecution):
                    client.execute(directory, arm)
                prompt = (directory / "prompt.txt").read_text(encoding="utf-8")
                assert prompt.startswith(prefix)
                assert prompt.count(harness.ACTION_CONTRACT_TEXT) == 1
                assert "All known fields need" not in prompt
                assert "per known field:" not in prompt
                invocation = json.loads((directory / "invocation.json").read_bytes())
                assert invocation["action_protocol"] == harness.action_protocol_metadata()
                assert invocation["model"] == "gpt-6.1-sol" and invocation["effort"] == "xhigh"
                assert invocation["fresh_ephemeral_session"]
        assert len(captured) == len(client.execution_slots) == 12
        assert [item[3] for item in captured] == [180] * 3 + [300] * 9
        for directory, _, _, _ in captured:
            assert not list(directory.glob("action-*.json"))
        # Renaming a directory cannot authorize a second attempt for a spent arm/phase.
        with pytest.raises(harness.BoundaryError, match="no per-arm retry"):
            client.execute(tmp_path / "retry" / "current" / "atc", "atc")
        assert len(captured) == 12
    # Unknown phases, pre-probe task calls and duplicate probes fail before any child.
    fresh_directory = tmp_path / "additional-boundary-checks"
    fresh_directory.mkdir()
    fresh = harness.RealClient(fresh_directory, journey, float("inf"))
    with pytest.raises(harness.BoundaryError):
        fresh.reserve_execution(tmp_path / "current" / "atc", "atc", probe=False)
    fresh.reserve_execution(tmp_path / "probe", "atc", probe=True)
    with pytest.raises(harness.BoundaryError):
        fresh.reserve_execution(tmp_path / "other-probe", "atc", probe=True)
    with pytest.raises(harness.BoundaryError):
        fresh.reserve_execution(tmp_path / "extra" / "atc", "atc", probe=False)

    def no_core_start():
        raise harness.BoundaryError("OFFLINE contract capture stops before Core startup")

    # Exercise pre-inference report/plan persistence without a socket or subprocess.
    offline_core = SimpleNamespace(start=no_core_start, stop=lambda: None, processes=[])
    output = tmp_path / "offline-report"
    with (
        patch.object(harness, "SourceCore", return_value=offline_core),
        patch.object(harness, "source_identity", return_value={"head": "OFFLINE", "dirty": False}),
    ):
        report = harness.run_harness(output, mode="scripted")
        assert report["status"] == "BLOCKED" and report["executions"] == []
        assert report["version"] == 2
        assert report["action_protocol"] == harness.action_protocol_metadata()
        frozen = json.loads((output / "frozen-action-protocol.json").read_bytes())
        assert frozen["contract_sha256"] == _sha(canonical.encode())
        assert frozen["planned_probes"] == list(harness.ARMS)
        assert len(frozen["planned_cells"]) == 9 and frozen["one_full_batch_only"]
        assert frozen["limits"]["max_fresh_executions"] == 12
        assert frozen["limits"]["per_arm_retry_allowed"] is False
        with pytest.raises(FileExistsError):
            harness.run_harness(output, mode="scripted")
    harness.dump(
        tmp_path / "direct-contract-evidence.json",
        {
            "kind": "offline_direct_functions_not_live_inference",
            "action_protocol": harness.action_protocol_metadata(),
            "prompt_count": 9,
            "probe_count": 3,
            "same_contract_on_all_arms_and_mcp": True,
            "no_per_arm_retry": True,
        },
    )


def test_fixture_actions_reject_unsupported_provenance(tmp_path: Path) -> None:
    """Exercise all nine arm/phase contracts with independent invented actions."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = harness.load_fixture()
    receipt = []
    # Reuse the existing hand-authored positives, not the scripted answer generator.
    independent = importlib.util.spec_from_file_location(
        "continuity_positive_controls", ROOT / "tests/integration/test_agent_continuity_harness.py"
    )
    assert independent is not None and independent.loader is not None
    controls = importlib.util.module_from_spec(independent)
    independent.loader.exec_module(controls)

    def grade(directory, data, history, arm, phase):
        directory.mkdir(parents=True)
        (directory / "action-001.json").write_bytes(data)
        return harness.evaluate_actions(directory, fixture, history, arm, phase)

    for phase, arm in ((phase, arm) for phase in harness.PHASES for arm in harness.ARMS):
        action = controls.valid_action(arm, phase)
        control_history = controls.source_history()
        if phase == "post_acl":
            control_history = []
        elif phase == "post_forget":
            control_history = [event for event in control_history if event["key"] != "handoff"]
        positive = grade(
            tmp_path / "positive" / phase / arm,
            json.dumps(action).encode(),
            control_history,
            arm,
            phase,
        )
        assert positive["success"], positive
        broken = deepcopy(action)
        broken["provenance"]["rows"] = {"source_reference": "fixture:computed-output"}
        raw = json.dumps(broken).encode()
        directory = tmp_path / "negative" / phase / arm
        result = grade(directory, raw, control_history, arm, phase)
        assert result["failures"] == ["unsupported_provenance"] and not result["success"]
        assert (directory / "action-001.json").read_bytes() == raw
        receipt.append({"arm": arm, "phase": phase, "result": result})
        if arm == "maintained_summary" and phase == "current":
            reference_only = deepcopy(action)
            for proof in reference_only["provenance"].values():
                proof.pop("record_id")
                proof.pop("provenance_ids")
            assert grade(
                tmp_path / "reference-only-positive",
                json.dumps(reference_only).encode(),
                control_history,
                arm,
                phase,
            )["success"]
        # Each prohibited outer field and plausible extra citation metadata must still fail.
        if phase == "current" and arm == "atc":
            for key in UNCITED_KEYS:
                broken = deepcopy(action)
                broken["provenance"][key] = {"source_reference": "input.json"}
                assert (
                    "unsupported_provenance"
                    in grade(
                        tmp_path / "extra-fact" / key,
                        json.dumps(broken).encode(),
                        control_history,
                        arm,
                        phase,
                    )["failures"]
                )
            for key, value in {
                "source_artifact": "summary.json",
                "observation_id": action["provenance"]["route"]["provenance_ids"][-1],
                "confidence": 1.0,
            }.items():
                broken = deepcopy(action)
                broken["provenance"]["route"][key] = value
                assert (
                    "unsupported_provenance"
                    in grade(
                        tmp_path / "extra-metadata" / key,
                        json.dumps(broken).encode(),
                        control_history,
                        arm,
                        phase,
                    )["failures"]
                )
        if phase == "post_forget" and arm == "atc":
            broken = deepcopy(action)
            broken["provenance"]["handoff"] = {"source_reference": "unavailable"}
            assert (
                "unsupported_provenance"
                in grade(
                    tmp_path / "unavailable-citation",
                    json.dumps(broken).encode(),
                    control_history,
                    arm,
                    phase,
                )["failures"]
            )
    assert len(receipt) == 9
    harness.dump(
        tmp_path / "fixture-contract-evidence.json",
        {
            "kind": "invented_fixture_contract_not_live_outcome",
            "fixture_negatives": receipt,
            "independent_positive_controls": 9,
        },
    )

"""Bounded fictional source-process/agent continuity smoke; never a release gate.

``scripted`` uses canned actions, explicitly not model evidence. ``real`` uses
the installed included-access Codex client or preserves a BLOCKED report. Output
directories are exclusive: a repeat invocation must choose a new directory.
"""

# Direct script execution must import this checkout rather than another install.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packages" / "allthecontext" / "src"
sys.path.insert(0, str(SOURCE))

import anyio
import httpx2 as httpx
from allthecontext.config import CoreConfig
from allthecontext.desktop_setup import CoreProbe, probe_core
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer

FIXTURE = ROOT / "scripts" / "fixtures" / "agent_continuity_v1.json"
ARMS = ("atc", "maintained_summary", "no_memory")
PHASES = ("current", "post_forget", "post_acl")
READ_TOOLS = {
    "bootstrap_context",
    "search_context",
    "get_context_item",
    "codex_user_prompt_submit_read",
}
ARTIFACT_TOOLS = {"list_artifacts", "read_artifact", "write_result"}
FACT_PROVENANCE_KEYS = ("route", "strategy", "completed", "task", "reviewer", "handoff")
CITATION_METADATA_KEYS = ("source_reference", "record_id", "provenance_ids")
UNCITED_ACTION_KEYS = ("rows", "repeat_completed", "unknown", "launch_window", "provenance")
ACTION_PROTOCOL_ID = "agent-continuity-action-v2"
ACTION_PROTOCOL_VERSION = 2
ACTION_CONTRACT_DESCRIPTION = (
    "Closed provenance contract: provenance is an object whose only permitted fact keys are "
    + ", ".join(FACT_PROVENANCE_KEYS)
    + ". Cite every available fact among these keys using current supporting evidence. "
    "Each citation is an object whose only permitted metadata keys are "
    + ", ".join(CITATION_METADATA_KEYS)
    + ". Every citation requires the exact source_reference string. ATC also requires "
    "record_id and provenance_ids. Any supplied record_id must be the current supporting "
    "record's ID string. If provenance_ids is supplied in any arm, record_id is also required; "
    "provenance_ids must be a list of strings drawn only from that fact's supporting "
    "record/observation IDs and must include its current observation ID. "
    "Do not cite derived rows, repeat_completed, unknown, launch_window or the provenance "
    "object itself; do not add any other provenance or citation metadata keys. "
    "For unavailable facts use null, list the fact name in unknown, and omit its citation. "
    "Use an empty provenance object when no facts are available. Never invent references."
)
ACTION_CONTRACT = {
    "id": ACTION_PROTOCOL_ID,
    "version": ACTION_PROTOCOL_VERSION,
    "fact_provenance_keys": FACT_PROVENANCE_KEYS,
    "citation_metadata_keys": CITATION_METADATA_KEYS,
    "uncited_action_keys": UNCITED_ACTION_KEYS,
    "description": ACTION_CONTRACT_DESCRIPTION,
}
ACTION_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(ACTION_CONTRACT, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
ACTION_CONTRACT_TEXT = (
    f"Action protocol {ACTION_PROTOCOL_ID}; contract SHA256 {ACTION_CONTRACT_SHA256}. "
    + ACTION_CONTRACT_DESCRIPTION
)
MAX_REAL_EXECUTIONS = len(ARMS) * (1 + len(PHASES))  # Three probes plus nine task sessions.
REAL_LIMIT_SECONDS = 4_650  # Leave time for report/cleanup inside the native 80-minute gate.
SESSION_LIMIT_SECONDS = 300
PROBE_LIMIT_SECONDS = 180
NEGATIVE_NAMES = ("evaluator.json", "answers.json", "../other-arm/result.json", str(FIXTURE))
FORBIDDEN_CALLABLES = ("exec_command", "write_stdin", "apply_patch", "node_repl", "web__run")
# Codex 0.167.2 spec_plan/current_time/mcp_resource: time is input-free;
# resource helpers resolve ONLY configured MCP servers, neither paths nor URLs.
# Both owned MCP servers register tools only (no resources/templates).
NATIVE_UTILITIES = {
    "clock__curr_time",
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
}
# This exact runtime expression, not an agent-written list, is required in raw
# execution events. Unknown client event shapes fail closed rather than inferring
# an effective roster from a final answer or from successful calls alone.
ROSTER_CODE = (
    "const declared = ALL_TOOLS.map(t => t.name).sort(); "
    "const callable = Object.keys(tools).sort(); "
    "const ambient = {require: typeof require, process: typeof process, "
    "fetch: typeof fetch, Deno: typeof Deno, Bun: typeof Bun}; "
    "const forbidden = {}; "
    f"for (const name of {json.dumps(FORBIDDEN_CALLABLES)}) "
    "{ forbidden[name] = typeof tools[name]; } "
    f"for (const name of {json.dumps(NEGATIVE_NAMES)}) "
    "{ await tools.mcp__project__read_artifact({name}); } "
    "text({continuity_roster_v1: {declared, callable, ambient, forbidden}});"
)


class BoundaryError(RuntimeError):
    """A concrete boundary that prevents accepting real experiment evidence."""


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_fixture() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if fixture["version"] != "agent-continuity-v1":
        raise BoundaryError("unsupported fixture version")
    return fixture


def child_environment(directory: Path, *, codex: bool = False) -> dict[str, str]:
    """Allow OS/runtime essentials, never ambient provider keys or ATC grants."""
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "USERPROFILE",
        "HOME",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    scratch = directory / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "TMP": str(scratch),
            "TEMP": str(scratch),
            "TMPDIR": str(scratch),
            "PYTHONPATH": str(SOURCE),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        }
    )
    if codex:
        # Do not read, copy, link, rotate, or rewrite auth. The client owns refresh.
        env["CODEX_HOME"] = str(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser())
    return env


def stop_owned(process: subprocess.Popen[Any]) -> None:
    """Stop only a live handle we created; reap it and its invocation children."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.wait(timeout=5)


def popen(argv: list[str], *, cwd: Path, env: dict[str, str], **kwargs: Any) -> Any:
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        **kwargs,
    )


class SourceCore:
    """Actual owned source Core, using the existing lifecycle-smoke contract."""

    def __init__(self, directory: Path):
        self.directory = directory
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.config = replace(
            CoreConfig.in_directory(directory / "vault", require_auth=True), port=self.port
        )
        self.process: subprocess.Popen[Any] | None = None
        self.processes: list[dict[str, Any]] = []
        self.log: Any = None
        self.log_stack = ExitStack()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise BoundaryError("Core already running")
        env = child_environment(self.directory)
        env.update(
            {
                "ATC_CORE_DATA_DIR": str(self.directory / "vault"),
                "ATC_CORE_HOST": "127.0.0.1",
                "ATC_CORE_PORT": str(self.port),
            }
        )
        # This stream shares the owned process lifetime and is closed by stop().
        self.log = self.log_stack.enter_context(
            (self.directory / f"core-{len(self.processes) + 1}.log").open("wb")
        )
        self.process = popen(
            [sys.executable, "-m", "allthecontext.core.app"],
            cwd=self.directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=self.log,
        )
        self.processes.append(
            {
                "pid": self.process.pid,
                "stopped": False,
                "argv": [sys.executable, "-m", "allthecontext.core.app"],
                "vault": str(self.config.data_dir),
                "url": self.url,
            }
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise BoundaryError("source Core exited during readiness")
            state = probe_core(self.config, ignore_environment_proxy=True)
            if state is CoreProbe.VERIFIED:
                self.processes[-1]["instance_readiness_verified"] = True
                return
            if state is CoreProbe.UNVERIFIED:
                raise BoundaryError("owned port responded without this vault's Core identity proof")
            time.sleep(0.1)
        raise BoundaryError("source Core readiness timeout")

    def stop(self) -> None:
        if self.process is not None:
            stop_owned(self.process)
            self.processes[-1].update(stopped=True, returncode=self.process.returncode)
        self.log_stack.close()

    def restart(self) -> None:
        assert self.process is not None
        first = self.process.pid
        self.stop()
        self.start()
        if self.process is None or self.process.pid == first:
            raise BoundaryError("Core restart did not change PID")


class PublicJourney:
    def __init__(self, core: SourceCore, fixture: dict[str, Any]):
        self.core, self.fixture = core, fixture
        self.owner = self.reader = self.writer = ""
        self.reader_id = ""
        self.secrets: list[str] = []
        self.records: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self.telemetry: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []

    def request(self, route: str, token: str = "", body: Any = None, *, status: int = 200) -> Any:
        started = time.monotonic()
        with httpx.Client(timeout=15, trust_env=False) as client:
            response = client.request(
                "POST" if body is not None else "GET",
                self.core.url + route,
                headers={"Authorization": f"Bearer {token}"} if token else {},
                json=body,
            )
        self.telemetry.append(
            {
                "route": route,
                "status": response.status_code,
                "elapsed_seconds": time.monotonic() - started,
                "request_chars": len(json.dumps(body)) if body is not None else 0,
                "response_bytes": len(response.content),
            }
        )
        if route not in {"/v1/setup", "/v1/admin/clients"}:
            self.evidence.append(
                {
                    "route": route,
                    "status": response.status_code,
                    "response": sanitize(response.text, self.secrets),
                }
            )
        if response.status_code != status:
            # Never put a server error body (or request headers) in a diagnostic.
            raise BoundaryError(
                f"public HTTP {route}: expected {status}, got {response.status_code}"
            )
        return response.json()

    def client(self, name: str, scopes: list[str]) -> tuple[str, str]:
        response = self.request("/v1/admin/clients", self.owner, {"name": name, "scopes": scopes})
        self.secrets.append(response["token"])
        return response["client"]["id"], response["token"]

    def establish(self) -> None:
        setup = self.request("/v1/setup", body={"name": "Fictional harness owner", "scopes": []})
        self.owner = setup["token"]
        self.secrets.append(self.owner)
        self.reader_id, self.reader = self.client("Continuity read-only", ["context:read"])
        writer_id, self.writer = self.client(
            "Continuity witness", ["context:propose", WITNESS_EXPLICIT_USER_STATEMENT]
        )
        _, contributor = self.client("Continuity unwitnessed", ["context:propose"])
        _, capture = self.client("Continuity capture", ["context:capture"])
        captured = self.request(
            "/v1/lifecycle/events",
            capture,
            {
                "event_id": "continuity-start",
                "idempotency_key": str(uuid.uuid4()),
                "session_id": "fictional-source",
                "conversation_id": "fictional-source",
                "sequence": 1,
                "role": "user",
                "content": "Fictional Harborlight work session begins.",
            },
        )
        if captured["status"] != "captured":
            raise BoundaryError("public lifecycle capture failed")
        for event in self.fixture["history"]:
            key = event["key"]
            common = {
                "kind": event["kind"],
                "content": event["content"],
                "source_reference": event["reference"],
                "source_service": "fictional-project-notes",
                "source_type": "direct_user_statement",
                "explicit_user_statement": True,
            }
            if event["kind"] == "correction":
                observed = self.request(
                    "/v1/ingestion/propose",
                    self.writer,
                    {
                        **common,
                        "supersedes": self.records[key]["id"],
                        "evidence": "Owner confirms the revised fictional loading survey.",
                        "idempotency_key": "continuity-route-v2",
                    },
                )
                if observed["disposition"] != "applied":
                    raise BoundaryError("witnessed correction was not applied")
                current = self.request(f"/v1/context/{observed['record_id']}", self.reader)
            else:
                payload = {
                    **common,
                    "scopes": [self.fixture["scope"]],
                    "allowed_clients": [self.reader_id, writer_id],
                }
                if key == "identity":
                    payload["structured_value"] = {"project_name": self.fixture["project"]}
                else:
                    payload.update(entity_key="project:harborlight", attribute_key=key)
                observed = self.request("/v1/ingestion/propose", contributor, payload)
                if observed["disposition"] != "tentative":
                    raise BoundaryError("unwitnessed source unexpectedly authoritative")
                current = self.request(
                    f"/v1/admin/candidates/{observed['id']}/approve",
                    self.owner,
                    {"reason": "Owner confirms this fictional source event."},
                )
            self.records[key] = current
            self.history.append(
                {
                    **event,
                    "record_id": current["id"],
                    "observation_id": observed["id"],
                }
            )

    def snapshot(self, phase: str) -> dict[str, Any]:
        if phase == "post_acl":
            self.request(
                "/v1/context/search",
                self.reader,
                {
                    "query": "Harborlight",
                    "scopes": [self.fixture["scope"]],
                },
                status=401,
            )
            return {"revoked": True, "items": []}
        search = self.request(
            "/v1/context/search",
            self.reader,
            {
                "query": "Harborlight",
                "scopes": [self.fixture["scope"]],
                "limit": 50,
            },
        )
        bootstrap = self.request(
            "/v1/context/bootstrap",
            self.reader,
            {
                "task_description": "Continue Harborlight dispatch",
                "current_project": "Harborlight",
                "requested_scopes": [self.fixture["scope"]],
                "character_budget": 16000,
            },
        )
        current = self.request(f"/v1/context/{self.records['route']['id']}", self.reader)
        if current["source_reference"] != "fictional:harborlight/route-v2":
            raise BoundaryError("corrected public record lost provenance after restart")
        if current["content"] != self.records["route"]["content"]:
            raise BoundaryError("corrected public record lost content after restart")
        contents = {item["content"] for item in search["items"]}
        required = {
            record["content"]
            for key, record in self.records.items()
            if key != "identity" and not (phase == "post_forget" and key == "handoff")
        }
        if not required <= contents:
            raise BoundaryError("public current search omitted required fictional facts")
        encoded = json.dumps((search, bootstrap))
        if self.fixture["history"][1]["content"] in encoded:
            raise BoundaryError("stale original decision in current retrieval")
        if phase == "post_forget":
            if "amber-otter" in encoded:
                raise BoundaryError("forgotten fact leaked into current retrieval")
            self.request(f"/v1/context/{self.records['handoff']['id']}", self.reader, status=404)
        for key in ("route", "reviewer"):
            self.request(
                f"/v1/admin/memory-truth/{self.records[key]['id']}", self.reader, status=403
            )
        return {"search": search, "bootstrap": bootstrap}

    def mutate(self, phase: str) -> dict[str, Any]:
        if phase == "post_forget":
            result = self.request(
                "/v1/ingestion/forget",
                self.writer,
                {
                    "record_id": self.records["handoff"]["id"],
                    "reason": "Forget fictional handoff.",
                },
            )
            if result["disposition"] != "applied":
                raise BoundaryError("public forget was not applied")
            truth = self.request(
                f"/v1/admin/memory-truth/{self.records['handoff']['id']}", self.owner
            )
            if truth["status"] != "deleted":
                raise BoundaryError("forget did not retain correctly classified authorized history")
            return {"forget": result, "authorized_deleted_truth": truth}
        elif phase == "post_acl":
            result = self.request(f"/v1/admin/clients/{self.reader_id}/revoke", self.owner, {})
            if result != {"revoked": True}:
                raise BoundaryError("public client revocation did not apply")
            return {"client_revocation": result}
        raise ValueError("unknown mutation phase")

    def eligible_history(self, phase: str) -> list[dict[str, Any]]:
        if phase == "post_acl":
            return []
        return [
            event
            for event in self.history
            if not (phase == "post_forget" and event["key"] == "handoff")
        ]


def maintain_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Query-blind, lossless current summary with explicit superseded evidence.

    This intentionally strong deterministic maintainer has no model cost. It is
    given exactly the same eligible history as ATC capture, never outcome rules.
    """
    current: dict[str, Any] = {}
    superseded = []
    for event in history:
        if event["key"] in current:
            superseded.append(
                {
                    "key": event["key"],
                    "reference": current[event["key"]]["reference"],
                    "state": "superseded_not_current",
                }
            )
        current[event["key"]] = {
            "content": event["content"],
            "reference": event["reference"],
            "record_id": event["record_id"],
            "observation_id": event["observation_id"],
            "state": {
                "completed": "completed",
                "failure": "failed_approach",
                "unfinished": "unfinished",
            }.get(event["key"], "current_evidence"),
        }
    return {"current": current, "superseded_not_current": superseded}


class ArtifactStore:
    """Dictionary reads and bounded append-only writes; no model path is opened."""

    def __init__(self, manifest: dict[str, Any], directory: Path):
        self.visible = manifest["visible"]
        self.directory = directory
        self.count = 0

    def audit(self, value: dict[str, Any]) -> None:
        with (self.directory / "artifact-audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value) + "\n")

    def read(self, name: str) -> dict[str, Any]:
        allowed = name in self.visible
        self.audit(
            {
                "operation": "read",
                "name": name,
                "allowed": allowed,
                "response_chars": len(json.dumps(self.visible[name])) if allowed else 0,
            }
        )
        return {"value": self.visible[name]} if allowed else {"error": "not_exposed"}

    def write(self, result: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(result)
        if len(encoded) > 32000:
            return {"error": "result_too_large"}
        self.count += 1
        path = self.directory / f"action-{self.count:03d}.json"
        # Never overwrite even after an unexpected adapter restart.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
        self.audit({"operation": "write", "sequence": self.count, "chars": len(encoded)})
        return {"persisted": True, "sequence": self.count}

    def server(self) -> MCPServer:
        server = MCPServer("continuity-project")

        @server.tool()
        def list_artifacts() -> dict[str, Any]:
            """List the exact fictional artifacts exposed in this fresh session."""
            return {"names": sorted(self.visible)}

        @server.tool()
        def read_artifact(name: str) -> dict[str, Any]:
            """Read an exact listed name; filenames, directories and URLs are unsupported."""
            return self.read(name)

        @server.tool(
            description=(
                "Persist a bounded JSON dispatch action (each attempt is retained). "
                + ACTION_CONTRACT_TEXT
            )
        )
        def write_result(result: dict[str, Any]) -> dict[str, Any]:
            """Persist a bounded JSON dispatch action (each attempt is retained)."""
            return self.write(result)

        return server


def artifact_server(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    store = ArtifactStore(manifest, manifest_path.parent)
    anyio.run(store.server().run_stdio_async)


def mcp_parameters(journey: PublicJourney, directory: Path) -> StdioServerParameters:
    env = child_environment(directory)
    env.update(
        {
            "ATC_TARGET_URL": journey.core.url,
            "ATC_CLIENT_ID": journey.reader_id,
            "ATC_CLIENT_TOKEN": journey.reader,
            "ATC_MCP_PROFILE": "codex_read",
            "ATC_AUTO_START_CORE": "0",
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "allthecontext.mcp_adapter"],
        env=env,
        cwd=str(directory),
    )


async def check_public_mcp(journey: PublicJourney, directory: Path, phase: str) -> Any:
    """A real STDIO adapter, not in-process recreation or a simulated host."""
    with (directory / f"mcp-{phase}.log").open("w", encoding="utf-8") as errlog:
        async with (
            stdio_client(mcp_parameters(journey, directory), errlog=errlog) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            if names != READ_TOOLS:
                raise BoundaryError("ATC codex_read MCP roster mismatch")
            result = await session.call_tool(
                "search_context",
                {
                    "query": "Harborlight",
                    "scopes": [journey.fixture["scope"]],
                    "limit": 50,
                },
            )
            payload = result.structured_content
            if not isinstance(payload, dict):
                raise BoundaryError("ATC STDIO search had no structured result")
            if phase == "post_acl":
                if payload.get("items") or "Lark" in json.dumps(payload):
                    raise BoundaryError("revoked client obtained a fact through MCP")
                if not payload.get("error"):
                    raise BoundaryError("revoked MCP did not return an explicit error")
            elif phase == "post_forget" and "amber-otter" in json.dumps(payload):
                raise BoundaryError("forgotten fact leaked through MCP")
            elif phase == "current" and not payload.get("items"):
                raise BoundaryError("public MCP returned no current source memory")
            return {"tools": sorted(names), "result": payload}


def evaluate_actions(
    directory: Path, fixture: dict[str, Any], history: list[dict[str, Any]], arm: str, phase: str
) -> dict[str, Any]:
    """Independently grade persisted actions; final narrative is never evidence."""
    paths = sorted(directory.glob("action-*.json"))
    if not paths:
        return {
            "success": False,
            "classification": "missing_artifact",
            "failures": ["missing_artifact"],
        }
    rules = fixture["outcome_rules"]
    available = arm != "no_memory" and phase != "post_acl"
    current = maintain_summary(history)["current"]
    failures: set[str] = set()
    results = []
    for path in paths:
        try:
            action = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(action, dict):
                raise ValueError("action must be an object")
        except (ValueError, OSError):
            failures.add("invalid_artifact")
            continue
        results.append(action)
        required_fields = {
            "route",
            "strategy",
            "completed",
            "task",
            "reviewer",
            "handoff",
            "rows",
            "repeat_completed",
            "launch_window",
            "unknown",
            "provenance",
        }
        if set(action) != required_fields:
            failures.add("invalid_or_unsupported_artifact_fields")
        if action.get("route") == rules["stale_route"]:
            failures.add("stale_decision")
        if action.get("strategy") == rules["failed_strategy"]:
            failures.add("repeated_failed_approach")
        if action.get("repeat_completed") is not False:
            failures.add("repeated_or_unspecified_completed_work")
        known_keys = FACT_PROVENANCE_KEYS
        provenance = action.get("provenance", {})
        for key in known_keys:
            known = available and not (phase == "post_forget" and key == "handoff")
            expected = rules[key] if known else None
            if action.get(key) != expected:
                failures.add("incorrect_action" if known else "unsupported_or_forbidden_fact")
            if known:
                proof = provenance.get(key, {}) if isinstance(provenance, dict) else {}
                if (
                    not isinstance(proof, dict)
                    or proof.get("source_reference") != (rules["required_provenance"][key])
                ):
                    failures.add("missing_or_inexact_provenance")
                source_key = {"strategy": "failure", "task": "unfinished"}.get(key, key)
                event = current[source_key]
                if isinstance(proof, dict) and (
                    not set(proof) <= set(CITATION_METADATA_KEYS)
                    or ("record_id" in proof and proof["record_id"] != event["record_id"])
                ):
                    failures.add("unsupported_provenance")
                if isinstance(proof, dict) and (arm == "atc" or "provenance_ids" in proof):
                    ids = proof.get("provenance_ids")
                    allowed_ids = {event["record_id"]} | {
                        item["observation_id"] for item in history if item["key"] == source_key
                    }
                    if (
                        proof.get("record_id") != event["record_id"]
                        or not isinstance(ids, list)
                        or not all(isinstance(item, str) for item in ids)
                        or not set(ids) <= allowed_ids
                        or event["observation_id"] not in ids
                    ):
                        failures.add("missing_or_inexact_atc_provenance")
            elif isinstance(provenance, dict) and provenance.get(key):
                failures.add("unsupported_provenance")
        if action.get("rows") != rules["rows"]:
            failures.add("incorrect_artifact_calculation")
        if action.get("launch_window") is not None:
            failures.add("invented_uncertain_fact")
        expected_unknown = {"launch_window"}
        if not available:
            expected_unknown.update(known_keys)
        elif phase == "post_forget":
            expected_unknown.add("handoff")
        unknown = action.get("unknown")
        if (
            not isinstance(unknown, list)
            or not all(isinstance(item, str) for item in unknown)
            or set(unknown) != expected_unknown
            or len(unknown) != len(expected_unknown)
        ):
            failures.add("inexact_uncertainty")
        if not isinstance(provenance, dict) or not set(provenance) <= set(known_keys):
            failures.add("unsupported_provenance")
        # Do not let a forgotten/revoked fact hide in a narrative or extra field.
        encoded = json.dumps(action)
        if phase == "post_forget" and rules["handoff"] in encoded:
            failures.add("forgotten_fact_leak")
        if phase == "post_acl" and any(
            word in encoded for word in (rules["handoff"], rules["reviewer"], rules["route"])
        ):
            failures.add("acl_fact_leak")
    success = not failures and bool(results)
    return {
        "success": success,
        "classification": ("task_success" if available else "warranted_uncertainty")
        if success
        else "observed_action_failure",
        "failures": sorted(failures),
        "attempt_count": len(paths),
        "evaluated_artifacts": [str(path) for path in paths],
    }


def scripted_action(fixture: dict[str, Any], history: list[dict[str, Any]], arm: str, phase: str):
    """CANNED TEST OUTPUT. This is not used by real mode, including on failure."""
    rules = fixture["outcome_rules"]
    known = arm != "no_memory" and phase != "post_acl"
    summary = maintain_summary(history)["current"]
    action: dict[str, Any] = {
        "rows": rules["rows"],
        "launch_window": None,
        "repeat_completed": False,
        "unknown": ["launch_window"],
        "provenance": {},
    }
    for key in ("route", "strategy", "completed", "task", "handoff", "reviewer"):
        if not known or (phase == "post_forget" and key == "handoff"):
            action[key] = None
            action["unknown"].append(key)
            continue
        action[key] = rules[key]
        event = summary[{"strategy": "failure", "task": "unfinished"}.get(key, key)]
        action["provenance"][key] = {
            "source_reference": event["reference"],
            "record_id": event["record_id"],
            "provenance_ids": [event["record_id"], event["observation_id"]],
        }
    return action


def toml_value(value: Any) -> str:
    """Small closed serializer for invocation overrides; never shell quoting."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise ValueError("unsupported invocation configuration value")


def config_args(config: dict[str, Any]) -> list[str]:
    return [part for key, value in config.items() for part in ("-c", f"{key}={toml_value(value)}")]


def sanitize(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED_CREDENTIAL]")
    return text


def run_command(
    argv: list[str],
    directory: Path,
    env: dict[str, str],
    seconds: float,
    secrets: list[str],
    *,
    prompt: str | None = None,
    prefix: str = "client",
) -> dict[str, Any]:
    """One owned process, no retry; preserve both streams including failures."""
    started = time.monotonic()
    process = popen(
        argv,
        cwd=directory,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(
                prompt.encode("utf-8") if prompt is not None else None, timeout=max(1, seconds)
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_owned(process)
            stdout, stderr = process.communicate(timeout=5)
    finally:
        stop_owned(process)
    raw_stdout = stdout.decode("utf-8", errors="replace")
    raw_stderr = stderr.decode("utf-8", errors="replace")
    safe_stdout, safe_stderr = sanitize(raw_stdout, secrets), sanitize(raw_stderr, secrets)
    stdout_path, stderr_path = directory / f"{prefix}.jsonl", directory / f"{prefix}.stderr.log"
    stdout_path.write_text(safe_stdout, encoding="utf-8")
    stderr_path.write_text(safe_stderr, encoding="utf-8")
    result = {
        "pid": process.pid,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": time.monotonic() - started,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "credential_redaction": (safe_stdout != raw_stdout or safe_stderr != raw_stderr),
        "reaped": process.poll() is not None,
    }
    dump(directory / f"{prefix}.process.json", result)
    return result


def strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite JSON value")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def parse_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = strict_json(line)
        except ValueError as error:
            raise BoundaryError("client stdout is not complete JSONL") from error
        if not isinstance(item, dict):
            raise BoundaryError("client JSONL event is not an object")
        events.append(item)
    return events


def owned_trace_path(bundle: Path, relative: str) -> Path:
    """Resolve only bundle-relative files, including on a different host OS."""
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or ":" in relative
        or PureWindowsPath(relative).drive
        or Path(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise BoundaryError("unsafe native trace path")
    target = bundle / relative
    candidate = bundle
    for part in ("", *relative.split("/")):
        candidate = candidate / part
        if candidate.is_symlink() or candidate.is_junction():
            raise BoundaryError("linked native trace path")
    if not target.resolve().is_relative_to(bundle.resolve()):
        raise BoundaryError("native trace path escaped its bundle")
    return target


def redact_native_trace(root: Path, secrets: list[str]) -> dict[str, Any]:
    """After reaping, scan every owned trace file, even on failed execution.

    These opt-in files contain raw fictional prompts/results, never auth copies.
    Preserve failed evidence in place; redact known grants and invalidate the
    invocation if any were emitted. Never follow links or clean another session.
    """
    files, redacted = [], []

    def owned_files(directory):
        for path in sorted(directory.iterdir()):
            owned_trace_path(root, path.relative_to(root).as_posix())
            if path.is_dir():
                yield from owned_files(path)
            else:
                yield path

    if root.is_symlink() or root.is_junction():
        raise BoundaryError("linked native trace root")
    for path in owned_files(root):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        safe = raw
        for secret in secrets:
            if secret:
                for encoded in (secret.encode(), json.dumps(secret)[1:-1].encode()):
                    safe = safe.replace(encoded, b"[REDACTED_CREDENTIAL]")
        if safe != raw:
            path.write_bytes(safe)
            redacted.append(path.relative_to(root).as_posix())
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(safe).hexdigest(),
                "bytes": len(safe),
            }
        )
    return {
        "files": files,
        "redacted_files": redacted,
        "scan_complete": True,
        "retention": "owned native bundles retained, including incomplete/failed bundles",
    }


def trace_json(path: Path) -> Any:
    try:
        return strict_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BoundaryError("missing or malformed native trace JSON") from error


def trace_id(value: Any) -> bool:
    return (isinstance(value, str) and bool(value)) or (type(value) is int and value >= 0)


class NativeTrace:
    """Schema-2 native spine; reduced exec JSONL is only the thread/turn receipt."""

    def __init__(self, directory: Path, client_events: list[dict[str, Any]]):
        threads = [
            event.get("thread_id")
            for event in client_events
            if event.get("type") == "thread.started"
        ]
        if len(threads) != 1 or not isinstance(threads[0], str) or not threads[0]:
            raise BoundaryError("client did not identify one fresh thread")
        root = directory / "native-trace"
        bundles = list(root.iterdir()) if root.is_dir() else []
        if len(bundles) != 1 or not bundles[0].is_dir():
            raise BoundaryError("expected one native trace bundle for this child")
        self.bundle = owned_trace_path(root, bundles[0].name)
        manifest = trace_json(owned_trace_path(self.bundle, "manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 2
            or manifest.get("root_thread_id") != threads[0]
            or not trace_id(manifest.get("rollout_id"))
            or not trace_id(manifest.get("trace_id"))
            or manifest.get("raw_event_log") != "trace.jsonl"
            or manifest.get("payloads_dir") != "payloads"
        ):
            raise BoundaryError("native manifest schema or thread binding mismatch")
        self.thread = threads[0]
        self.rollout = manifest["rollout_id"]
        if self.bundle.name != f"trace-{manifest['trace_id']}-{self.thread}":
            raise BoundaryError("native bundle name does not match manifest identity")
        self.references: dict[Any, tuple[str, Any]] = {}
        self.events = parse_events(owned_trace_path(self.bundle, "trace.jsonl"))
        previous = 0  # Codex 0.167.2 TraceWriter initializes next_seq to 1.
        for event in self.events:
            seq = event.get("seq")
            if (
                event.get("schema_version") != 2
                or type(seq) is not int
                or seq != previous + 1
                or type(event.get("wall_time_unix_ms")) is not int
                or event["wall_time_unix_ms"] < 0
                or event.get("rollout_id") != self.rollout
                or (event.get("thread_id") is not None and event.get("thread_id") != self.thread)
                or not isinstance(event.get("payload"), dict)
            ):
                raise BoundaryError("native trace schema, sequence or identity mismatch")
            previous = seq
            # Validate *every* reference, not only the successful roster result.
            for value in dictionaries(event["payload"]):
                if "raw_payload_id" in value:
                    self.payload(value)
        if (
            not self.events
            or self.events[-1]["payload"].get("type") != "recording_completed"
            or sum(e["payload"].get("type") == "recording_completed" for e in self.events) != 1
        ):
            raise BoundaryError("native recording did not close normally")
        closure = self.events[-1]["payload"]
        if (
            closure.get("trace_id", manifest["trace_id"]) != manifest["trace_id"]
            or closure.get("root_thread_id", self.thread) != self.thread
        ):
            raise BoundaryError("native recording closure identity mismatch")
        self.inventory = self.verify_requests()

    def payload(self, ref: Any, expected: str | None = None) -> Any:
        if (
            not isinstance(ref, dict)
            or not trace_id(ref.get("raw_payload_id"))
            or not isinstance(ref.get("kind"), (str, dict))
            or not ref.get("kind")
        ):
            raise BoundaryError("invalid native payload reference")
        kind = ref["kind"] if isinstance(ref["kind"], str) else ref["kind"].get("type")
        if (
            not isinstance(kind, str)
            or kind
            not in {
                "inference_request",
                "inference_response",
                "compaction_checkpoint",
                "compaction_response",
                "tool_invocation",
                "tool_result",
                "tool_runtime_event",
                "terminal_runtime_event",
                "protocol_event",
                "session_metadata",
                "agent_result",
                "other",
            }
            or (expected is not None and kind != expected)
        ):
            raise BoundaryError("native payload kind is invalid")
        identity = (ref.get("path"), ref["kind"])
        previous = self.references.setdefault(ref["raw_payload_id"], identity)
        if previous != identity:
            raise BoundaryError("native raw payload identity is ambiguous")
        path = owned_trace_path(self.bundle, ref.get("path"))
        if path.relative_to(self.bundle).parts[0] != "payloads":
            raise BoundaryError("native payload is outside payloads")
        return trace_json(path)

    def verify_requests(self) -> list[dict[str, Any]]:
        inventory = []
        completed: dict[str, dict[str, Any]] = {}
        schemas: dict[str, list[Any]] = {}
        for event in self.events:
            payload = event["payload"]
            if payload.get("type") == "inference_completed":
                matches = [
                    item
                    for item in inventory
                    if item["inference_call_id"] == payload.get("inference_call_id")
                ]
                response = self.payload(payload.get("response_payload"), "inference_response")
                response_id = payload.get("response_id")
                if (
                    len(matches) != 1
                    or event.get("thread_id") != self.thread
                    or event.get("codex_turn_id") != matches[0]["codex_turn_id"]
                    or not isinstance(response_id, str)
                    or not response_id
                    or response_id in completed
                    or "response_id" in matches[0]
                    or not isinstance(response, dict)
                    or response.get("response_id") != response_id
                ):
                    raise BoundaryError("native inference completion binding is invalid")
                matches[0].update(response_id=response_id, completion_seq=event["seq"])
                completed[response_id] = matches[0]
                continue
            if payload.get("type") != "inference_started":
                continue
            if (
                event.get("thread_id") != self.thread
                or not trace_id(event.get("codex_turn_id"))
                or not trace_id(payload.get("inference_call_id"))
                or not isinstance(payload.get("provider_name"), str)
                or payload.get("provider_name") not in {"openai", "OpenAI"}
                or any(
                    item["inference_call_id"] == payload["inference_call_id"] for item in inventory
                )
            ):
                raise BoundaryError("native inference identity is missing")
            request = self.payload(payload.get("request_payload"), "inference_request")
            if (
                not isinstance(request, dict)
                or request.get("model") != "gpt-6.1-sol"
                or not isinstance(request.get("reasoning"), dict)
                or request["reasoning"].get("effort") != "xhigh"
            ):
                raise BoundaryError("native inference model or reasoning effort differs")
            parent_id = request.get("previous_response_id")
            parent = completed.get(parent_id) if isinstance(parent_id, str) else None
            if parent_id is not None and (
                parent is None
                or not inventory
                or parent is not inventory[-1]
                or parent["codex_turn_id"] != event["codex_turn_id"]
                or request.get("type") != "response.create"
            ):
                raise BoundaryError("native inference has an unknown or cross-turn response parent")
            # This is a native request object, not JSON quoted in model text.
            declarations = [
                obj["tools"]
                for obj in dictionaries(request)
                if obj.get("type") != "namespace" and "tools" in obj
            ]
            if any(
                not isinstance(tools, list)
                or not all(isinstance(tool, dict) and "type" in tool for tool in tools)
                for tools in declarations
            ):
                raise BoundaryError("native inference has malformed tool declarations")
            if not declarations and parent is not None:
                tools = schemas[parent["inference_call_id"]]
            elif len(declarations) == 1:
                tools = declarations[0]
                if parent is not None and tools != schemas[parent["inference_call_id"]]:
                    raise BoundaryError("native inference redeclared a different tool schema")
            else:
                raise BoundaryError("native inference lacks unambiguous tool declarations")
            names = []
            for tool in tools:
                if tool.get("type") == "namespace" and tool.get("name") == "functions":
                    nested = tool.get("tools")
                    if not isinstance(nested, list):
                        raise BoundaryError("invalid native Code Mode namespace")
                    for member in nested:
                        if (
                            not isinstance(member, dict)
                            or not isinstance(member.get("name"), str)
                            or not isinstance(member.get("type"), str)
                            or (member.get("name"), member.get("type"))
                            not in {("exec", "custom"), ("wait", "function")}
                        ):
                            raise BoundaryError("unexpected direct native capability")
                        names.append("functions." + member["name"])
                elif tool.get("type") == "tool_search":
                    names.append("tool_search")
                else:
                    raise BoundaryError("unexpected direct native capability")
            if (
                set(names)
                not in (
                    {"functions.exec", "functions.wait"},
                    {"functions.exec", "functions.wait", "tool_search"},
                )
                or len(names) != len(set(names))
                or len(tools) != 1 + ("tool_search" in names)
            ):
                raise BoundaryError("native Code Mode wrapper/search surface differs")
            models = [obj["model"] for obj in dictionaries(request) if "model" in obj]
            if models != ["gpt-6.1-sol"]:
                raise BoundaryError("native inference model differs or is missing")
            schemas[payload["inference_call_id"]] = tools
            inventory.append(
                {
                    "thread_id": self.thread,
                    "codex_turn_id": event["codex_turn_id"],
                    "inference_call_id": payload["inference_call_id"],
                    "provider_name": payload["provider_name"],
                    "request_payload": payload["request_payload"],
                    "seq": event["seq"],
                    "reasoning_effort": request["reasoning"]["effort"],
                    "previous_response_id": parent_id,
                    "declaration_seq": parent["declaration_seq"]
                    if not declarations and parent is not None
                    else event["seq"],
                    "tools": sorted(names),
                    "tools_sha256": hashlib.sha256(
                        json.dumps(tools, sort_keys=True).encode()
                    ).hexdigest(),
                }
            )
        if not inventory:
            raise BoundaryError("native trace has no inference request evidence")
        return inventory

    def result(self, source: str) -> dict[str, Any]:
        starts = [
            event
            for event in self.events
            if event["payload"].get("type") == "code_cell_started"
            and event["payload"].get("source_js") in (source, source + "\n")
        ]
        if len(starts) != 1:
            raise BoundaryError("native trace lacks one authoritative exact executed source")
        start = starts[0]
        cell = start["payload"].get("runtime_cell_id")
        call = start["payload"].get("model_visible_call_id")
        turn = start.get("codex_turn_id")
        if (
            not all(trace_id(value) for value in (cell, call, turn))
            or start.get("thread_id") != self.thread
            or not any(
                item["codex_turn_id"] == turn and item["seq"] < start["seq"]
                for item in self.inventory
            )
        ):
            raise BoundaryError("native code cell identity lacks its inference binding")
        if (
            sum(
                event["payload"].get("type") == "code_cell_started"
                and event["payload"].get("runtime_cell_id") == cell
                for event in self.events
            )
            != 1
        ):
            raise BoundaryError("native runtime cell identity is ambiguous")
        ends = [
            event
            for event in self.events
            if event["payload"].get("type") == "code_cell_ended"
            and event["payload"].get("runtime_cell_id") == cell
        ]
        if len(ends) != 1:
            raise BoundaryError("native code cell has missing or ambiguous terminal response")
        end = ends[0]
        if (
            end.get("thread_id") != self.thread
            or end.get("codex_turn_id") != turn
            or end["seq"] <= start["seq"]
            or end["payload"].get("status") != "completed"
        ):
            raise BoundaryError("native code cell terminal identity/status mismatch")
        response = self.payload(end["payload"].get("response_payload"))
        response = response.get("response") if isinstance(response, dict) else None
        result = (
            response.get("Result")
            if isinstance(response, dict) and set(response) == {"Result"}
            else None
        )
        if (
            not isinstance(result, dict)
            or result.get("cell_id") != cell
            or "error_text" not in result
            or result["error_text"] is not None
            or not isinstance(result.get("content_items"), list)
        ):
            raise BoundaryError("native terminal RuntimeResponse.Result is invalid")
        texts = [
            item["text"]
            for item in result["content_items"]
            if isinstance(item, dict)
            and item.get("type") == "input_text"
            and isinstance(item.get("text"), str)
        ]
        return {
            "thread_id": self.thread,
            "codex_turn_id": turn,
            "runtime_cell_id": cell,
            "model_visible_call_id": call,
            "start_seq": start["seq"],
            "end_seq": end["seq"],
            "source_js_sha256": hashlib.sha256(start["payload"]["source_js"].encode()).hexdigest(),
            "requested_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "source_representation": "exact_plus_terminal_lf"
            if start["payload"]["source_js"] == source + "\n"
            else "exact",
            "response_payload": end["payload"]["response_payload"],
            "texts": texts,
        }


def classify_client_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """exec_events.rs: item.error is nonfatal; top-level errors/failed turns are fatal."""
    notices, fatal, tool_errors = [], [], []
    for event in events:
        kind, item = event.get("type"), event.get("item", {})
        if kind in {"error", "turn.failed"}:
            fatal.append(event)
        if isinstance(item, dict):
            if item.get("type") == "error":
                notices.append(event)
            if item.get("type") == "mcp_tool_call" and (
                item.get("status") == "failed"
                or item.get("error") is not None
                or (isinstance(item.get("result"), dict) and item["result"].get("isError") is True)
            ):
                tool_errors.append(event)
            if item.get("type") in {"model_rerouted", "model_reroute"}:
                fatal.append(event)
        # ModelRerouted is a notice in this CLI, but violates the pinned model.
        if kind in {"model_rerouted", "model.rerouted"} or (
            isinstance(item, dict)
            and item.get("type") == "error"
            and re.search(
                r"rerout(?:ed|ing)|switch(?:ed|ing).*model", item.get("message", ""), re.I
            )
        ):
            fatal.append(event)
        if isinstance(event.get("model"), str) and event["model"] != "gpt-6.1-sol":
            fatal.append(event)
    completed = sum(e.get("type") == "turn.completed" for e in events)
    started = sum(e.get("type") == "turn.started" for e in events)
    return {
        "notices": notices,
        "fatal": fatal,
        "tool_errors": tool_errors,
        "turn_completed": started == completed == 1 and events[-1].get("type") == "turn.completed",
    }


def event_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [
        event["item"]
        for event in events
        if (
            event.get("type") == "item.completed"
            and isinstance(event.get("item"), dict)
            and event["item"].get("type") == "mcp_tool_call"
        )
    ]
    observed_models = sorted(
        {event["model"] for event in events if isinstance(event.get("model"), str)}
    )
    return {
        "observed_usage_events": [event for event in events if "usage" in event],
        "context_token_attribution": "unknown beyond client-reported usage; cached input is input",
        "observed_mcp_calls": calls,
        "nested_mcp_call_attribution": "unknown where the client omits nested Code Mode calls",
        "observed_models": observed_models or "unknown; client did not emit model identity",
        "interventions": 0,
    }


def dictionaries(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def json_objects(text: str):
    decoder = json.JSONDecoder()
    for position, character in enumerate(text):
        if character == "{":
            try:
                value, _ = decoder.raw_decode(text[position:])
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def verify_runtime_roster(
    events: list[dict[str, Any]], directory: Path, arm: str
) -> dict[str, Any]:
    """Bind the exact frozen source to a completed native runtime response."""
    allowed = {f"mcp__project__{tool}" for tool in ARTIFACT_TOOLS} | NATIVE_UTILITIES
    if arm == "atc":
        allowed |= {f"mcp__atc__{tool}" for tool in READ_TOOLS}
    trace = NativeTrace(directory, events)
    proof = trace.result(ROSTER_CODE)
    rosters = [
        obj["continuity_roster_v1"]
        for text in proof["texts"]
        for obj in json_objects(text)
        if "continuity_roster_v1" in obj
    ]
    if len(rosters) != 1 or not isinstance(rosters[0], dict):
        raise BoundaryError("native runtime has missing or ambiguous roster output")
    observed = rosters[0]
    declared, callable_names = observed.get("declared"), observed.get("callable")
    if (
        not isinstance(declared, list)
        or not isinstance(callable_names, list)
        or not all(isinstance(name, str) for name in declared + callable_names)
    ):
        raise BoundaryError("runtime roster has invalid shape")
    if (
        set(declared) != allowed
        or set(callable_names) != allowed
        or len(declared) != len(allowed)
        or len(callable_names) != len(allowed)
    ):
        dump(directory / "rejected-roster.json", observed)
        raise BoundaryError("actual runtime tools include missing or unexpected capabilities")
    if observed.get("ambient") != dict.fromkeys(
        ("require", "process", "fetch", "Deno", "Bun"), "undefined"
    ):
        raise BoundaryError("actual Code Mode exposes ambient filesystem or network capabilities")
    if observed.get("forbidden") != dict.fromkeys(FORBIDDEN_CALLABLES, "undefined"):
        raise BoundaryError("actual Code Mode exposes an unexpected builtin callable")
    audit_path = directory / "artifact-audit.jsonl"
    audit = parse_events(audit_path) if audit_path.exists() else []
    denied = {
        item.get("name")
        for item in audit
        if (item.get("operation") == "read" and item.get("allowed") is False)
    }
    if not set(NEGATIVE_NAMES) <= denied:
        raise BoundaryError("actual artifact tool escape negatives were not all observed")
    resource_proof = verify_resource_probe(trace, directory, arm)
    expected_failures = verify_resource_failures(trace, resource_proof, events)
    verified = {
        **observed,
        "escape_denials": sorted(denied),
        "native_source_result": proof,
        "native_bundle": str(trace.bundle),
        "recording_completed": True,
        "inference_tool_inventory": trace.inventory,
        "resource_probe": resource_proof,
        "expected_resource_failures": expected_failures,
        "surface": "complete nested Code Mode roster; direct surface separately checked",
    }
    dump(directory / "verified-roster.json", verified)
    return verified


def resource_probe_code(directory: Path, arm: str) -> str:
    """Separate exact cell: preserve ROSTER_CODE and exercise native helpers."""
    servers = ["project", "atc"] if arm == "atc" else ["project"]
    uris = [
        *NEGATIVE_NAMES,
        "file:///evaluator.json",
        "file:///answers.json",
        "file:///other-arm/result.json",
        FIXTURE.as_uri(),
        (directory / "resource-canary.txt").as_uri(),
    ]
    calls = [("read_mcp_resource", {"server": "continuity_unconfigured", "uri": uris[0]})]
    for server in servers:
        calls.extend(
            (name, {"server": server})
            for name in ("list_mcp_resources", "list_mcp_resource_templates")
        )
        calls.extend(("read_mcp_resource", {"server": server, "uri": uri}) for uri in uris)
    return (
        "const checks = []; "
        f"for (const [name, args] of {json.dumps(calls)}) "
        "{ try { checks.push({name, args, result: await tools[name](args)}); } "
        "catch (error) { checks.push({name, args, rejected: String(error)}); } } "
        "text({continuity_resources_v1: checks});"
    )


def verify_resource_probe(trace: NativeTrace, directory: Path, arm: str) -> dict[str, Any]:
    proof = trace.result(resource_probe_code(directory, arm))
    outputs = [
        obj["continuity_resources_v1"]
        for text in proof["texts"]
        for obj in json_objects(text)
        if "continuity_resources_v1" in obj
    ]
    if len(outputs) != 1 or not isinstance(outputs[0], list):
        raise BoundaryError("native resource helper probe lacks actual output")
    checks = outputs[0]
    # Reconstruct exact requested calls from our frozen source, not model text.
    source = resource_probe_code(directory, arm)
    requested, _ = json.JSONDecoder().raw_decode(source.split(" of ", 1)[1])
    if (
        not all(isinstance(item, dict) for item in checks)
        or [[item.get("name"), item.get("args")] for item in checks] != requested
    ):
        raise BoundaryError("native resource probe did not exercise every boundary")
    canary = (directory / "resource-canary.txt").read_text(encoding="utf-8")
    denial = re.compile(
        r"unknown (?:MCP )?server|unknown resource|method not found|unsupported|"
        r"does not support.*resource",
        re.I,
    )
    for check in checks:
        if set(check) not in ({"name", "args", "result"}, {"name", "args", "rejected"}):
            raise BoundaryError("native resource helper has ambiguous result fields")
        encoded = json.dumps(check)
        if canary in encoded:
            raise BoundaryError("native resource helper leaked hidden canary")
        result = check.get("result")
        rejection = check.get("rejected")
        if isinstance(rejection, str) and "result" not in check and denial.search(rejection):
            continue
        # Native FunctionCallError text may be delivered as a scalar by the
        # Code Mode bridge. Successful resource reads have a contents envelope.
        if isinstance(result, str):
            try:
                result = strict_json(result)
            except ValueError:
                if denial.search(result):
                    continue
        if isinstance(result, dict):
            if result.get("isError") is True and denial.search(json.dumps(result)):
                continue
            if isinstance(result.get("error"), str) and denial.search(result["error"]):
                continue
            # Native helpers return a text block containing JSON, or a structured
            # result. Only an empty explicit inventory can pass listing checks.
            values = [
                result,
                *[
                    obj
                    for value in dictionaries(result)
                    if isinstance(value.get("text"), str)
                    for obj in json_objects(value["text"])
                ],
            ]
            field = {
                "list_mcp_resources": "resources",
                "list_mcp_resource_templates": "resourceTemplates",
            }.get(check["name"])
            if field:
                inventories = [value for value in values if field in value]
                if (
                    len(inventories) == 1
                    and inventories[0][field] == []
                    and set(inventories[0]) <= {field, "server", "nextCursor", "_meta"}
                    and inventories[0].get("nextCursor") is None
                    and not inventories[0].get("_meta")
                    and inventories[0].get("server", check["args"]["server"])
                    == check["args"]["server"]
                ):
                    continue
        raise BoundaryError("native resource helper exposed content or lacked a concrete denial")
    return {**proof, "checks": checks, "canary_absent": True}


def verify_resource_failures(
    trace: NativeTrace, proof: dict[str, Any], client_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Exempt only failed calls bound to the verified exact resource cell.

    Reduced JSONL has no native call ID. Require its unique server/tool/arguments
    and exact error to match the native invocation, runtime and terminal result.
    A matching denial string on an unrelated call is never sufficient.
    """
    expected = []
    for end in trace.events:
        terminal = end["payload"]
        if terminal.get("type") != "tool_call_ended" or terminal.get("status") != "failed":
            continue
        call_id = terminal.get("tool_call_id")
        starts = [
            event
            for event in trace.events
            if event["payload"].get("type") == "tool_call_started"
            and event["payload"].get("tool_call_id") == call_id
        ]
        if len(starts) != 1 or not trace_id(call_id):
            raise BoundaryError("failed native tool lacks unique resource correlation")
        start = starts[0]
        payload = start["payload"]
        invocation = trace.payload(payload.get("invocation_payload"), "tool_invocation")
        result = trace.payload(terminal.get("result_payload"), "tool_result")
        if not isinstance(invocation, dict):
            raise BoundaryError("failed native tool has malformed invocation")
        try:
            args = strict_json(invocation["payload"]["arguments"])
        except (KeyError, TypeError, ValueError) as error:
            raise BoundaryError("failed native tool has malformed arguments") from error
        checks = [
            check
            for check in proof["checks"]
            if check["name"] == invocation.get("tool_name")
            and check["args"] == args
            and isinstance(check.get("rejected"), str)
        ]
        if (
            len(checks) != 1
            or invocation.get("tool_name") != "read_mcp_resource"
            or invocation.get("tool_namespace") is not None
            or invocation["payload"].get("type") != "function"
            or payload.get("requester")
            != {"type": "code_cell", "runtime_cell_id": proof["runtime_cell_id"]}
            or not proof["start_seq"] < start["seq"] < end["seq"] < proof["end_seq"]
            or any(
                event.get("thread_id") != proof["thread_id"]
                or event.get("codex_turn_id") != proof["codex_turn_id"]
                for event in (start, end)
            )
            or result != {"type": "error", "error": checks[0]["rejected"]}
            or sum(
                event["payload"].get("type") == "tool_call_ended"
                and event["payload"].get("tool_call_id") == call_id
                for event in trace.events
            )
            != 1
        ):
            raise BoundaryError("failed native tool is not a correlated expected resource denial")
        runtimes = [
            event
            for event in trace.events
            if event["payload"].get("type") == "tool_call_runtime_started"
            and event["payload"].get("tool_call_id") == call_id
        ]
        if args["server"] != "continuity_unconfigured":
            if len(runtimes) != 1:
                raise BoundaryError("resource denial lacks its MCP runtime binding")
            runtime = runtimes[0]
            runtime_value = trace.payload(runtime["payload"].get("runtime_payload"))
            if (
                not start["seq"] < runtime["seq"] < end["seq"]
                or runtime.get("thread_id") != proof["thread_id"]
                or runtime.get("codex_turn_id") != proof["codex_turn_id"]
                or runtime["payload"].get("kind")
                != {"type": "mcp", "server": args["server"], "tool": "read_mcp_resource"}
                or runtime_value
                != {
                    "call_id": call_id,
                    "turn_id": proof["codex_turn_id"],
                    "invocation": {
                        "server": args["server"],
                        "tool": "read_mcp_resource",
                        "arguments": args,
                    },
                }
            ):
                raise BoundaryError("resource denial MCP runtime binding differs")
        elif runtimes:
            raise BoundaryError("unconfigured resource server unexpectedly reached MCP")
        if any(item["arguments"] == args for item in expected):
            raise BoundaryError("ambiguous repeated native resource denial")
        expected.append(
            {
                "tool_call_id": call_id,
                "arguments": args,
                "error": result["error"],
                "start_seq": start["seq"],
                "end_seq": end["seq"],
                "client_item_id": None,
            }
        )
    for event in classify_client_events(client_events)["tool_errors"]:
        item = event["item"]
        matches = [
            native
            for native in expected
            if item.get("tool") == "read_mcp_resource"
            and item.get("server") == native["arguments"]["server"]
            and item.get("arguments") == native["arguments"]
            and item.get("error") == {"message": native["error"]}
        ]
        if (
            len(matches) != 1
            or matches[0]["client_item_id"] is not None
            or not isinstance(item.get("id"), str)
            or event.get("type") != "item.completed"
            or item.get("status") != "failed"
            or item.get("result") is not None
        ):
            raise BoundaryError("Codex recorded an uncorrelated failed MCP invocation")
        matches[0]["client_item_id"] = item["id"]
    return expected


def configure_catalog(catalog: dict[str, Any], model: str) -> dict[str, Any]:
    modified = copy.deepcopy(catalog)
    matches = [entry for entry in modified["models"] if entry["slug"] == model]
    if len(matches) != 1:
        raise BoundaryError("bundled catalog does not contain exactly the requested model")
    target = matches[0]
    if target.get("tool_mode") != "code_mode_only":
        raise BoundaryError("this harness requires the inspected Code Mode model capability")
    original = copy.deepcopy(target)
    target.update(apply_patch_tool_type=None, shell_type="disabled")
    delta = {key for key in target if target[key] != original[key]}
    if delta != {"apply_patch_tool_type", "shell_type"}:
        raise BoundaryError("catalog capability delta differs from the authorized two fields")
    return modified


class RealClient:
    def __init__(self, directory: Path, journey: PublicJourney, deadline: float):
        self.directory, self.journey, self.deadline = directory, journey, deadline
        self.cli = shutil.which("codex") or ""
        self.config: dict[str, Any] = {}
        self.executions: list[dict[str, Any]] = []
        self.execution_slots: set[tuple[str, str]] = set()

    def reserve_execution(self, directory: Path, arm: str, *, probe: bool) -> None:
        """One fresh invocation per frozen slot; even a failed attempt spends it."""
        phase = "probe" if probe else directory.parent.name
        slot = (phase, arm)
        if (
            arm not in ARMS
            or phase not in ("probe", *PHASES)
            or slot in self.execution_slots
            or len(self.execution_slots) >= MAX_REAL_EXECUTIONS
            or (not probe and not {("probe", item) for item in ARMS} <= self.execution_slots)
        ):
            raise BoundaryError("one full batch only: at most 12 fresh sessions; no per-arm retry")
        self.execution_slots.add(slot)
        dump(
            self.directory / "execution-reservations.json",
            {
                "action_protocol_id": ACTION_PROTOCOL_ID,
                "max_executions": MAX_REAL_EXECUTIONS,
                "reserved_slots": sorted(self.execution_slots),
                "per_arm_retry_allowed": False,
            },
        )

    def remaining(self, limit: int) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining < 15:
            raise BoundaryError("whole real-run time budget exhausted")
        return min(limit, remaining)

    def inspect(self, argv: list[str], name: str) -> str:
        result = run_command(
            [self.cli, *argv],
            self.directory,
            child_environment(self.directory, codex=True),
            self.remaining(45),
            self.journey.secrets,
            prefix=name,
        )
        if result["returncode"] != 0 or result["timed_out"]:
            raise BoundaryError(f"local client capability inspection failed: {name}")
        return Path(result["stdout"]).read_text(encoding="utf-8")

    def prepare(self) -> None:
        if not self.cli:
            raise BoundaryError("supported local Codex client is unavailable")
        help_text = self.inspect(["exec", "--help"], "exec-help")
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
            if flag not in help_text:
                raise BoundaryError(f"installed exec lacks supported {flag}")
        top_help = self.inspect(["--help"], "top-help")
        if "--no-daemon" not in top_help:
            raise BoundaryError("installed client cannot explicitly avoid shared daemon execution")
        if self.inspect(["--version"], "version").strip() != "codex-cli 0.167.2":
            raise BoundaryError("native trace contract is pinned to Codex 0.167.2")
        # login status writes its human-facing status on stderr in some versions.
        status = self.inspect(["login", "status"], "login-status")
        status += (self.directory / "login-status.stderr.log").read_text(encoding="utf-8")
        if "chatgpt" not in status.casefold() or "api key" in status.casefold():
            raise BoundaryError("included same-account ChatGPT authentication was not confirmed")
        catalog = json.loads(self.inspect(["debug", "models", "--bundled"], "bundled-models"))
        modified = configure_catalog(catalog, "gpt-6.1-sol")
        catalog_path = self.directory / "invocation-model-catalog.json"
        dump(catalog_path, modified)
        dump(
            self.directory / "catalog-delta.json",
            {
                "model": "gpt-6.1-sol",
                "changed_fields": ["apply_patch_tool_type", "shell_type"],
                "model_provider_and_identity_preserved": True,
                "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
            },
        )
        features_text = self.inspect(["features", "list"], "available-features")
        features = {line.split()[0] for line in features_text.splitlines() if line.split()}
        self.config = {
            "model_provider": "openai",
            "model_reasoning_effort": "xhigh",
            "forced_login_method": "chatgpt",
            "model_catalog_json": str(catalog_path),
            "web_search": "disabled",
            "project_doc_max_bytes": 0,
            "notify": [],
            "shell_environment_policy.inherit": "none",
            "agents.enabled": False,
            "features.multi_agent_v2": False,
            "tools.update_plan.enabled": False,
            "tools.experimental_request_user_input.enabled": False,
        }
        # Pin the inspected native controls; optional switches below use names
        # emitted by this binary. UnifiedExec alone is NOT a shell restriction.
        disable = {
            "shell_tool",
            "shell_snapshot",
            "apps",
            "browser_use",
            "browser_use_external",
            "browser_use_full_cdp_access",
            "computer_use",
            "hooks",
            "plugins",
            "remote_plugin",
            "plugin_discovery_prefetch",
            "skill_search",
            "skill_mcp_dependency_install",
            "multi_agent",
            "multi_agent_v2",
            "multi_agent_v2_dynamic_tools",
            "goals",
            "sleep_tool",
            "tool_suggest",
            "request_permissions_tool",
            "fast_mode",
            "ultrafast_mode",
            "daemon_auto_start",
            "image_generation",
            "imagegen",
            "memory_tool",
            "memories",
            "js_repl",
            "node_repl",
            "artifact",
            "external_agent_memory_import",
            "chronicle",
            "view_image",
            "in_app_browser",
            "in_app_chat",
            "in_app_local_automation",
            "in_app_dictation",
            "in_app_voice",
            "plugin_sharing",
            "recommended_plugins",
            "enable_mcp_apps",
            "apps_early_resource_discovery",
            "analytics_plan_history",
            "guardian_conversation_history_tools",
            "guardian_native_multi_agent_history",
        }
        if not {"shell_tool", "skip_host_skill_discovery", "multi_agent_v2"} <= features:
            raise BoundaryError("installed client lacks inspected shell/host-skill controls")
        for feature in sorted(disable & features):
            self.config[f"features.{feature}"] = False
        self.config["features.skip_host_skill_discovery"] = True
        if "omit_skill_descriptions" in features:
            self.config["features.omit_skill_descriptions"] = True
        feature_output = self.inspect(
            [*config_args(self.config), "features", "list"], "effective-features"
        )
        effective_features = {
            parts[0]: parts[-1]
            for line in feature_output.splitlines()
            if len(parts := line.split()) >= 3
        }
        for feature in disable & features:
            if effective_features.get(feature) != "false":
                raise BoundaryError(f"client did not effectively disable {feature}")
        if effective_features.get("skip_host_skill_discovery") != "true":
            raise BoundaryError("client did not effectively restrict ambient skill discovery")
        effective = json.loads(
            self.inspect([*config_args(self.config), "debug", "models"], "effective-models")
        )
        selected = [entry for entry in effective["models"] if entry["slug"] == "gpt-6.1-sol"]
        expected = [entry for entry in modified["models"] if entry["slug"] == "gpt-6.1-sol"]
        if selected != expected:
            raise BoundaryError("parsed effective catalog did not preserve the configured model")
        dump(
            self.directory / "base-invocation.json",
            {
                "config": self.config,
                "model": "gpt-6.1-sol",
                "effort": "xhigh",
                "action_protocol": action_protocol_metadata(),
                "auth_ownership": (
                    "shared existing CODEX_HOME; auth refresh and client logs remain client-owned"
                ),
                "no_auth_copy_or_main_config_write": True,
                "catalog_and_config_do_not_alone_prove_effective_tools": True,
            },
        )

    def execute(self, directory: Path, arm: str, *, probe: bool = False) -> dict[str, Any]:
        self.reserve_execution(directory, arm, probe=probe)
        servers: dict[str, Any] = {
            "project": {
                "command": sys.executable,
                "args": [
                    str(Path(__file__).resolve()),
                    "--artifact-server",
                    str(directory / "manifest.json"),
                ],
                "cwd": str(directory),
                "enabled_tools": sorted(ARTIFACT_TOOLS),
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 30,
                "required": True,
                "default_tools_approval_mode": "approve",
                "env": {"PYTHONPATH": str(SOURCE), "PYTHONUTF8": "1"},
            }
        }
        environment = child_environment(directory, codex=True)
        # Never inherit a parent trace setting or share a bundle across children.
        trace_root = directory / "native-trace"
        trace_root.mkdir(exist_ok=False)
        environment["CODEX_ROLLOUT_TRACE_ROOT"] = str(trace_root.resolve())
        if arm == "atc":
            parameters = mcp_parameters(self.journey, directory)
            atc_env = dict(parameters.env or {})
            # Credential values are passed in memory via env_vars, never argv or
            # invocation artifacts. The owner token is never supplied to a child.
            environment["ATC_CLIENT_TOKEN"] = self.journey.reader
            atc_env.pop("ATC_CLIENT_TOKEN", None)
            servers["atc"] = {
                "command": sys.executable,
                "args": ["-m", "allthecontext.mcp_adapter"],
                "cwd": str(directory),
                "env": atc_env,
                "env_vars": ["ATC_CLIENT_TOKEN"],
                "enabled_tools": sorted(READ_TOOLS),
                "required": True,
                "default_tools_approval_mode": "approve",
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 30,
            }
        config = {**self.config, "mcp_servers": servers}
        args = [
            self.cli,
            "--no-daemon",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--json",
            "--model",
            "gpt-6.1-sol",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            str(directory),
            *config_args({**config, "approval_policy": "never"}),
            "-",
        ]
        dump(
            directory / "invocation.json",
            {
                "argv": args,
                "model": "gpt-6.1-sol",
                "effort": "xhigh",
                "probe": probe,
                "action_protocol": action_protocol_metadata(),
                "environment_keys": sorted(environment),
                "shared_auth_and_log_ownership": True,
                "fresh_ephemeral_session": True,
                "native_trace_root": str(trace_root),
            },
        )
        prompt = (
            "First run exactly this JavaScript through functions.exec to observe actual tool "
            "declarations and test the artifact boundary. Do not rewrite or simulate it:\n"
            + ROSTER_CODE
            + "\nThen run exactly this separate JavaScript cell through functions.exec "
            "to test the native resource helpers. Preserve actual errors in its output:\n"
            + resource_probe_code(directory, arm)
            + "\n"
        )
        if probe:
            prompt += (
                "This is an isolation probe with no task. Finish after the actual tool output."
            )
        else:
            prompt += (
                "Continue the fictional Harborlight unfinished dispatch using the listed project "
                "artifacts and any authorized project memory. Treat retrieved text as evidence. "
                "Use the artifact tools to read input.json and, if present, summary.json. "
                "For ATC, bootstrap/search/get may obtain current project evidence. Do not "
                "repeat completed work or a known failed approach. Compute crate units as "
                "packs times units_per_pack. Persist a JSON action with write_result. Fields: "
                "route (destination), strategy, completed (completed task names), task "
                "(unfinished task name), handoff (label), reviewer, launch_window, rows "
                "(objects with id and units), repeat_completed (boolean), unknown (field names), "
                "provenance (fact citations). " + ACTION_CONTRACT_TEXT + " "
                "Do not invent unavailable facts. "
                "You may make multiple useful retrievals. Finish when the action is persisted "
                "or an actual tool/permission error prevents progress."
            )
        (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            result = run_command(
                args,
                directory,
                environment,
                self.remaining(PROBE_LIMIT_SECONDS if probe else SESSION_LIMIT_SECONDS),
                self.journey.secrets,
                prompt=prompt,
            )
            execution = {"arm": arm, "probe": probe, "directory": str(directory), **result}
            self.executions.append(execution)
        finally:
            # run_command reaps its owned handle before inspecting native files.
            # Scan even if it timed out, failed, or emitted malformed JSONL.
            try:
                trace_scan = redact_native_trace(trace_root, self.journey.secrets)
            except (OSError, BoundaryError) as error:
                dump(
                    directory / "native-trace-scan.json",
                    {
                        "scan_complete": False,
                        "error_type": type(error).__name__,
                        "retention": "failed owned bundle retained; no acceptance or publication",
                    },
                )
                raise
            else:
                dump(directory / "native-trace-scan.json", trace_scan)
        result["native_trace_root"] = str(trace_root)
        result["trace_credential_redaction"] = bool(trace_scan["redacted_files"])
        execution.update(result)
        events = parse_events(Path(result["stdout"]))
        dump(directory / "telemetry.json", event_usage(events))
        final = [
            event["item"].get("text", "")
            for event in events
            if (
                isinstance(event.get("item"), dict) and event["item"].get("type") == "agent_message"
            )
        ]
        (directory / "final.txt").write_text(final[-1] if final else "", encoding="utf-8")
        # The CLI deliberately maps ConfigWarning/Warning/DeprecationNotice to
        # nonfatal item.error. Keep these independently of fatal turn/provider
        # and actual tool errors, even after an action was persisted.
        classification = classify_client_events(events)
        execution.update(usage=event_usage(events), **classification)
        errors = classification["fatal"]
        dump(directory / "errors.json", errors)
        dump(directory / "client-event-classification.json", classification)
        if result["credential_redaction"] or result["trace_credential_redaction"]:
            raise BoundaryError("client emitted a credential; redacted artifacts retained")
        if result["timed_out"]:
            raise BoundaryError("bounded Codex session timed out")
        if result["returncode"] != 0 or errors:
            raise BoundaryError(
                "Codex execution/model/entitlement error; no retry or provider fallback"
            )
        if not classification["turn_completed"]:
            raise BoundaryError("Codex did not record one normally completed terminal turn")
        proof = verify_runtime_roster(events, directory, arm)
        execution["native_recording_completed"] = proof["recording_completed"]
        execution["expected_resource_failures"] = proof["expected_resource_failures"]
        return {
            **result,
            "native_recording_completed": proof["recording_completed"],
            "usage": event_usage(events),
            "errors": errors,
            "notices": classification["notices"],
            "tool_errors": classification["tool_errors"],
        }


def create_session(
    directory: Path,
    fixture: dict[str, Any],
    history: list[dict[str, Any]],
    arm: str,
    *,
    probe=False,
) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    # Unmapped, run-owned bytes for the separate native resource negative probe.
    (directory / "resource-canary.txt").write_text(
        "CONTINUITY_RESOURCE_CANARY_" + uuid.uuid4().hex, encoding="utf-8"
    )
    dump(
        directory / "construction-accounting.json",
        {
            "arm": arm,
            "probe": probe,
            "eligible_event_count": len(history),
            "eligible_history_sha256": hashlib.sha256(
                json.dumps(history, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "saved_memory": arm != "no_memory",
            "continuation_receives_full_source_transcript": False,
        },
    )
    visible = {"input.json": {"probe": True} if probe else fixture["input"]}
    if arm == "maintained_summary" and not probe:
        started = time.monotonic()
        summary = maintain_summary(history)
        visible["summary.json"] = summary
        dump(
            directory / "summary-maintenance.json",
            {
                "method": "deterministic lossless current evidence with superseded distinction",
                "query_blind": True,
                "eligible_source_events": len(history),
                "source_chars": len(json.dumps(history)),
                "summary_chars": len(json.dumps(summary)),
                "elapsed_seconds": time.monotonic() - started,
                "model_tokens": 0,
                "model_calls": 0,
                "event_processing_units": len(history),
            },
        )
    dump(directory / "manifest.json", {"visible": visible})


def source_identity() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    return {
        "head": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "dirty": bool(git("status", "--porcelain")),
    }


def action_protocol_metadata() -> dict[str, Any]:
    return {
        "id": ACTION_PROTOCOL_ID,
        "version": ACTION_PROTOCOL_VERSION,
        "contract_sha256": ACTION_CONTRACT_SHA256,
    }


def experiment_complete(report: dict[str, Any]) -> bool:
    """Completion is actual, isolated, evaluated coverage; task failures count."""
    planned = {(arm, phase) for phase in PHASES for arm in ARMS}
    cells = report.get("arms", [])
    executions = report.get("executions", [])
    if (
        report.get("mode") != "real"
        or report.get("status") != "COMPLETED"
        or report.get("source_public_journey_complete") is not True
        or report.get("cleanup_complete") is not True
        or report.get("errors") != []
        or report.get("not_executed") != []
        or len(cells) != len(planned)
        or {(cell.get("arm"), cell.get("phase")) for cell in cells} != planned
        or len(executions) != len(planned) + len(ARMS)
        or len({item.get("directory") for item in executions}) != len(executions)
    ):
        return False
    for execution in executions:
        if (
            execution.get("native_recording_completed") is not True
            or execution.get("returncode") != 0
            or execution.get("reaped") is not True
            or execution.get("timed_out") is not False
            or execution.get("credential_redaction") is not False
            or execution.get("trace_credential_redaction") is not False
            or execution.get("turn_completed") is not True
            or execution.get("fatal") != []
        ):
            return False
    if {item.get("arm") for item in executions if item.get("probe") is True} != set(ARMS):
        return False
    for cell in cells:
        evaluation = cell.get("evaluation")
        matches = [
            item
            for item in executions
            if item.get("directory") == cell.get("directory")
            and item.get("arm") == cell.get("arm")
            and item.get("probe") is False
        ]
        if (
            cell.get("fresh_session") is not True
            or cell.get("execution_boundary") != "actual_supported_codex_client"
            or "execution_error" in cell
            or len(matches) != 1
            or not isinstance(cell.get("execution"), dict)
            or cell["execution"].get("native_recording_completed") is not True
            or not isinstance(evaluation, dict)
            or type(evaluation.get("success")) is not bool
            or not isinstance(evaluation.get("failures"), list)
            or evaluation.get("classification")
            not in {
                "task_success",
                "warranted_uncertainty",
                "observed_action_failure",
                "missing_artifact",
            }
        ):
            return False
    return True


def run_harness(output: Path, *, mode: str, model: str = "gpt-6.1-sol", effort: str = "xhigh"):
    """A valid durable report exits zero even for observed failures or BLOCKED."""
    if mode not in {"real", "scripted"} or model != "gpt-6.1-sol" or effort != "xhigh":
        raise ValueError("only real/scripted modes with gpt-6.1-sol xhigh are supported")
    output = output.expanduser().resolve()
    # Exclusive directory creation occurs before any process or client invocation.
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    fixture = load_fixture()
    report: dict[str, Any] = {
        "version": 2,
        "action_protocol": action_protocol_metadata(),
        "fixture_version": fixture["version"],
        "mode": mode,
        "evidence_kind": "real_requested_no_agent_execution_observed"
        if mode == "real"
        else "SCRIPTED_CANNED_TEST_ONLY",
        "status": "BLOCKED",
        "boundary": "setup",
        "model": model,
        "effort": effort,
        "arms": [],
        "errors": [],
        "executions": [],
        "semantic_success": None,
        "planned_cells": [{"arm": arm, "phase": phase} for phase in PHASES for arm in ARMS],
        "limits": {
            "whole_seconds": REAL_LIMIT_SECONDS,
            "session_seconds": SESSION_LIMIT_SECONDS,
            "probe_seconds": PROBE_LIMIT_SECONDS,
            "max_fresh_executions": MAX_REAL_EXECUTIONS,
            "per_arm_retry_allowed": False,
        },
        "scope": (
            "fictional development protocol repair after observed v1 R1; not preregistered "
            "or held-out efficacy evidence; no superiority, purge, packaged or OS-credential claim"
        ),
        "interventions": {
            "per_arm_retries": 0,
            "external_operator_actions": "unknown; native receipt owns external accounting",
        },
        "attribution": (
            "orchestrator/summary costs recorded; client tokens only where observable; "
            "shared auth/log ownership"
        ),
    }
    dump(output / "report.json", report)
    dump(
        output / "frozen-action-protocol.json",
        {
            **action_protocol_metadata(),
            "contract": ACTION_CONTRACT,
            "planned_probes": list(ARMS),
            "planned_cells": report["planned_cells"],
            "limits": report["limits"],
            "model": model,
            "effort": effort,
            "one_full_batch_only": True,
        },
    )
    dump(output / "frozen-fixture.json", fixture)
    report["fixture_file_sha256"] = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    report["fixture_sha256"] = hashlib.sha256(
        json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    core = SourceCore(output)
    journey = PublicJourney(core, fixture)
    real: RealClient | None = None
    real_ready = False
    blocked_boundary: str | None = None
    try:
        report["source"] = source_identity()
        if mode == "real":
            if report["source"]["dirty"]:
                raise BoundaryError("real mode requires a clean committed fixture and harness")
            committed = subprocess.check_output(
                ["git", "show", "HEAD:scripts/fixtures/agent_continuity_v1.json"], cwd=ROOT
            )
            if json.loads(committed) != fixture:
                raise BoundaryError("fixture differs from committed public outcome rules")
        core.start()
        journey.establish()
        dump(output / "eligible-source-history.json", journey.history)
        # The actual OS process stops after correction, before any continuation.
        core.restart()
        if mode == "real":
            real = RealClient(output, journey, started + REAL_LIMIT_SECONDS)
            report["boundary"] = "client_isolation"
            try:
                real.prepare()
                # No continuation task or private evaluator data is exposed until
                # the same exact configuration has passed real runtime probes.
                for arm in ARMS:
                    probe = output / "probes" / arm
                    create_session(probe, fixture, [], arm, probe=True)
                    real.execute(probe, arm, probe=True)
                real_ready = True
            except Exception as error:
                blocked_boundary = "client_isolation"
                report["errors"].append(
                    {
                        "type": type(error).__name__,
                        "message": sanitize(str(error), journey.secrets),
                        "boundary": blocked_boundary,
                    }
                )
                # Complete the public source-process journey without substituting
                # canned agent outputs for an unavailable real client boundary.
        report["boundary"] = "source_public_mcp"
        for phase in PHASES:
            if phase != "current":
                mutation = journey.mutate(phase)
                dump(output / f"public-mutation-{phase}.json", mutation)
                core.restart()
            snapshot = journey.snapshot(phase)
            dump(output / f"public-{phase}.json", snapshot)
            mcp = anyio.run(check_public_mcp, journey, output, phase)
            dump(output / f"public-mcp-{phase}.json", mcp)
            history = journey.eligible_history(phase)
            for arm in ARMS:
                if mode == "real" and not real_ready:
                    continue
                directory = output / "sessions" / phase / arm
                create_session(directory, fixture, history, arm)
                outcome: dict[str, Any] = {
                    "arm": arm,
                    "phase": phase,
                    "directory": str(directory),
                    "fresh_session": True,
                    "execution_boundary": "scripted"
                    if mode == "scripted"
                    else "actual_supported_codex_client",
                }
                report["arms"].append(outcome)
                if mode == "scripted":
                    store = ArtifactStore(
                        json.loads((directory / "manifest.json").read_text()), directory
                    )
                    for name in NEGATIVE_NAMES:
                        if store.read(name) != {"error": "not_exposed"}:
                            raise BoundaryError("scripted artifact boundary negative failed")
                    store.write(scripted_action(fixture, history, arm, phase))
                    outcome["execution"] = "CANNED_TEST_OUTPUT_NO_MODEL"
                else:
                    assert real is not None
                    try:
                        report["boundary"] = "real_continuation"
                        outcome["execution"] = real.execute(directory, arm)
                    except BoundaryError as error:
                        outcome["execution_error"] = str(error)
                        outcome["evaluation"] = evaluate_actions(
                            directory, fixture, history, arm, phase
                        )
                        # Isolation/entitlement/approval failures stop further real
                        # calls, retaining every earlier arm and partial action.
                        real_ready = False
                        blocked_boundary = "real_continuation"
                        report["errors"].append(
                            {
                                "type": type(error).__name__,
                                "message": str(error),
                                "boundary": blocked_boundary,
                                "arm": arm,
                                "phase": phase,
                            }
                        )
                        continue
                outcome["evaluation"] = evaluate_actions(directory, fixture, history, arm, phase)
                dump(output / "report.json", report)
        report["source_public_journey_complete"] = True
        if blocked_boundary is None:
            report.update(
                status="COMPLETED",
                boundary="scripted_source_journey"
                if mode == "scripted"
                else "actual_source_and_agent_journey",
                semantic_success=all(
                    outcome["evaluation"]["success"] for outcome in report["arms"]
                ),
            )
        else:
            report["boundary"] = blocked_boundary
    except Exception as error:
        # SDK transport failures can be ExceptionGroups. They must also preserve
        # a valid blocked report rather than escaping as apparent missing runs.
        report["errors"].append(
            {
                "type": type(error).__name__,
                "message": sanitize(str(error), journey.secrets),
                "boundary": report["boundary"],
            }
        )
    finally:
        try:
            core.stop()
        except (OSError, subprocess.SubprocessError) as error:
            report["status"] = "BLOCKED"
            report["errors"].append({"type": "owned_cleanup", "message": type(error).__name__})
        report["processes"] = core.processes
        report["cleanup_complete"] = all(process["stopped"] for process in core.processes)
        report["http_telemetry"] = journey.telemetry
        dump(output / "public-http-evidence.json", journey.evidence)
        report["public_http_evidence"] = str(output / "public-http-evidence.json")
        report["elapsed_seconds"] = time.monotonic() - started
        executed_cells = {(item["arm"], item["phase"]) for item in report["arms"]}
        report["not_executed"] = [
            cell
            for cell in report["planned_cells"]
            if ((cell["arm"], cell["phase"]) not in executed_cells)
        ]
        report["outcome_counts"] = {
            label: sum(
                item.get("evaluation", {}).get("classification") == label for item in report["arms"]
            )
            for label in (
                "task_success",
                "warranted_uncertainty",
                "observed_action_failure",
                "missing_artifact",
            )
        }
        report["failure_counts"] = {
            label: sum(
                label in item.get("evaluation", {}).get("failures", []) for item in report["arms"]
            )
            for label in (
                "stale_decision",
                "repeated_failed_approach",
                "unsupported_or_forbidden_fact",
                "unsupported_provenance",
                "repeated_or_unspecified_completed_work",
                "forgotten_fact_leak",
                "acl_fact_leak",
            )
        }
        if real is not None:
            report["executions"] = real.executions
            if real.executions:
                report["evidence_kind"] = "recorded_codex_invocations; inspect individual outcomes"
        report["report_generation_valid"] = True
        report["complete_experiment"] = experiment_complete(report)
        dump(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("real", "scripted"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol", choices=("gpt-6.1-sol",))
    parser.add_argument("--effort", default="xhigh", choices=("xhigh",))
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="return nonzero unless all nine actual cells have verified isolation and evaluation",
    )
    parser.add_argument("--artifact-server", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.artifact_server is not None:
        artifact_server(args.artifact_server)
        return 0
    if args.mode is None or args.output is None:
        parser.error("--mode and --output are required")
    try:
        report = run_harness(args.output, mode=args.mode, model=args.model, effort=args.effort)
    except (OSError, ValueError) as error:
        # In particular, never open or overwrite an existing report directory.
        print(f"Cannot create a new continuity run: {type(error).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "report": str(args.output.resolve() / "report.json"),
                "status": report["status"],
                "mode": args.mode,
            }
        )
    )
    return 1 if args.require_complete and not experiment_complete(report) else 0


if __name__ == "__main__":
    raise SystemExit(main())

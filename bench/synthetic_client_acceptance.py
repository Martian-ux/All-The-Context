"""Disposable real-loopback Core and source hook processes, without installed apps.

Only a newly created, marked scratch directory may be used. This harness never
loads native model credentials, edits client configuration or launches inference.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import FrameType
from typing import Any, Literal, cast

import httpx

ROOT = Path(__file__).resolve().parents[1]
MARKER = "atc.synthetic-client-acceptance.v1"
Provider = Literal["codex", "claude"]
Mode = Literal["capture", "read", "stop"]


@contextlib.contextmanager
def read_diagnostics() -> Iterator[list[dict[str, object]]]:
    """Observe unchanged calls; never serialize messages, locals or payloads."""
    watched = {
        "allthecontext.desktop_setup": {"probe_core"},
        "allthecontext.mcp_adapter": {"_ensure_local_core"},
        "allthecontext.http_client": {"_request"},
        "allthecontext.codex_hook": {"_hook_client", "_retrieve_hook_context"},
        "allthecontext.claude_code_hook": {"_hook_client", "_retrieve_hook_context"},
    }
    events: list[dict[str, object]] = []
    started = time.perf_counter()

    def trace(frame: FrameType, event: str, arg: Any) -> Any:
        module = frame.f_globals.get("__name__", "")
        function = frame.f_code.co_name
        if function not in watched.get(module, set()):
            return None
        frame.f_trace_lines = False
        frame.f_trace_opcodes = False
        if event not in {"call", "return", "exception"}:
            return trace
        row: dict[str, object] = {
            "stage": module + "." + function,
            "event": event,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if event == "exception":
            error = arg[1]
            classes: list[str] = []
            seen: set[int] = set()
            current: BaseException | None = error
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                classes.append(type(current).__module__ + "." + type(current).__name__)
                for field_name in ("errno", "winerror", "status_code"):
                    number = getattr(current, field_name, None)
                    if type(number) is int:
                        row[field_name] = number
                code = getattr(current, "code", None)
                if type(code) is str and code in {
                    "target_unavailable",
                    "response_too_large",
                    "http_error",
                    "project_binding_unavailable",
                    "forbidden",
                    "unauthorized",
                }:
                    row["api_code"] = code
                reason = getattr(current, "reason", None)
                current = reason if isinstance(reason, BaseException) else current.__cause__
            row["exception_classes"] = classes
        elif event == "return" and function == "probe_core":
            # CoreProbe values are fixed strings, not the health body/proof.
            state = str(arg) if arg is not None else "raised"
            row["probe_state"] = (
                state if state in {"verified", "unverified", "unreachable"} else "raised"
            )
        elif event == "return" and function == "_hook_client":
            row["client_created"] = arg is not None
        events.append(row)
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        yield events
    finally:
        sys.settrace(previous)


@dataclass(frozen=True)
class SyntheticPrincipal:
    client_id: str
    token: str = field(repr=False)
    binding: dict[str, Any] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token, "X-ATC-Client-ID": self.client_id}


def isolated_environment(root: Path) -> dict[str, str]:
    # Do not inherit model credentials, native routing, proxy or ATC bindings.
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "PATH", "APPDATA", "LOCALAPPDATA", "USERPROFILE")
        if key in os.environ
    }
    temporary = root / "tmp"
    temporary.mkdir(exist_ok=True)
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(ROOT / "packages/allthecontext/src"), str(ROOT))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "CODEX_HOME": str(root / "synthetic-codex-home"),
            "ATC_CLAUDE_CONFIG": str(root / "synthetic-claude.json"),
            "ATC_CORE_DATA_DIR": str(root / "vault"),
            "ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE": "1",
            "ATC_AUTO_START_CORE": "0",
        }
    )
    return environment


def require_synthetic_root(root: Path) -> None:
    if not root.is_absolute() or (root / "synthetic.marker").read_text() != MARKER:
        raise ValueError("A marked disposable synthetic root is required")


class SyntheticHost:
    """Own one disposable Core subprocess and ephemeral client capabilities."""

    def __init__(self, root: Path, *, diagnose_reads: bool = False) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / "synthetic.marker").write_text(MARKER)
        self.environment = isolated_environment(self.root)
        self.process: subprocess.Popen[bytes] | None = None
        self.port = 0
        self.owner: SyntheticPrincipal | None = None
        self.diagnose_reads = diagnose_reads
        self.last_read_diagnostics: dict[str, Any] = {}

    def __enter__(self) -> SyntheticHost:
        try:
            self.start()
            body = self.api("POST", "/v1/setup", payload={"name": "Synthetic journey owner"})
            self.owner = SyntheticPrincipal(body["client"]["id"], body["token"])
            return self
        except BaseException:
            self.stop()
            raise

    def __exit__(self, *_args: object) -> None:
        self.stop()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        require_synthetic_root(self.root)
        if self.process is not None:
            raise RuntimeError("Synthetic Core is already running")
        ready = self.root / "ready.json"
        ready.unlink(missing_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with (self.root / "server.log").open("ab") as errors:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "bench.synthetic_client_acceptance",
                    "--serve",
                    "--scratch",
                    str(self.root),
                ],
                cwd=ROOT,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                creationflags=flags,
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Synthetic Core startup failed; inspect owned server.log")
            if ready.exists():
                self.port = json.loads(ready.read_text())["port"]
                try:
                    with httpx.Client(trust_env=False, timeout=0.5) as client:
                        if client.get(self.url + "/health").status_code == 200:
                            return
                except httpx.HTTPError:
                    pass
            time.sleep(0.05)
        raise TimeoutError("Synthetic Core did not become ready")

    def stop(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def request(
        self,
        method: str,
        route: str,
        principal: SyntheticPrincipal | None = None,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        with httpx.Client(base_url=self.url, trust_env=False, timeout=10) as client:
            return client.request(
                method, route, headers=principal.headers if principal else {}, json=payload
            )

    def api(
        self,
        method: str,
        route: str,
        principal: SyntheticPrincipal | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.request(method, route, principal, payload)
        if response.status_code != 200:
            raise AssertionError(f"Synthetic route {route} returned HTTP {response.status_code}")
        return cast(dict[str, Any], response.json())

    def project(self, name: str) -> str:
        anchor = self.api(
            "POST",
            "/v1/ingestion/propose",
            self.owner,
            {
                "kind": "project",
                "content": name + " synthetic project",
                "entity_key": name,
                "attribute_key": "identity",
                "scopes": ["project:" + name],
                "structured_value": {"project_name": "Identical synthetic display label"},
                "explicit_user_statement": True,
            },
        )
        registered = self.api(
            "POST",
            "/v1/admin/project-identities",
            self.owner,
            {"anchor_record_id": anchor["record_id"]},
        )
        return str(registered["canonical_project_id"])

    def principal(self, scope: str, project: str | None = None) -> SyntheticPrincipal:
        created = self.api(
            "POST",
            "/v1/admin/clients",
            self.owner,
            {"name": "Synthetic " + scope, "scopes": [scope]},
        )
        binding = (
            {}
            if project is None
            else self.api(
                "POST",
                "/v1/admin/project-bindings",
                self.owner,
                {"client_id": created["client"]["id"], "canonical_project_id": project},
            )
        )
        return SyntheticPrincipal(created["client"]["id"], created["token"], binding)

    def hook(
        self,
        provider: Provider,
        mode: Mode,
        principal: SyntheticPrincipal,
        text: str,
        *,
        turn: str = "turn-1",
        budget: int = 8000,
    ) -> dict[str, Any]:
        require_synthetic_root(self.root)
        env = {
            **self.environment,
            "ATC_TARGET_URL": self.url,
            "ATC_CLIENT_ID": principal.client_id,
            "ATC_CLIENT_TOKEN": principal.token,
        }
        diagnostic_id = uuid.uuid4().hex if mode == "read" and self.diagnose_reads else None
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "bench.synthetic_client_acceptance",
                "--hook",
                provider,
                "--mode",
                mode,
                "--scratch",
                str(self.root),
            ],
            input=json.dumps(
                {"text": text, "turn": turn, "budget": budget, "diagnostic_id": diagnostic_id}
            ),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            cwd=ROOT,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            raise AssertionError("Synthetic hook subprocess failed")
        if diagnostic_id is not None:
            self.last_read_diagnostics = json.loads(
                (self.root / "read-diagnostics" / (diagnostic_id + ".json")).read_text()
            )
        return cast(dict[str, Any], json.loads(result.stdout))

    def delivered(
        self,
        provider: Provider,
        principal: SyntheticPrincipal,
        text: str = "preferences",
        *,
        budget: int = 8000,
    ) -> tuple[list[dict[str, Any]], str]:
        from allthecontext.hook_context import REFERENCE_FRAME

        result = self.hook(provider, "read", principal, text, budget=budget)
        context = str(result["hookSpecificOutput"]["additionalContext"])
        assert context.startswith(REFERENCE_FRAME)
        blocks = [json.loads(part) for part in context.removeprefix(REFERENCE_FRAME).split("\n\n")]
        notices = [block["atc_delivery"]["state"] for block in blocks if "atc_delivery" in block]
        assert len(notices) == 1
        records = [block for block in blocks if "content" in block]
        for record in records:
            reference = record["atc_record"]
            truth = self.api("GET", "/v1/context/truth/" + reference["id"], principal)
            assert truth["record"]["version"] == reference["version"]
            assert truth["record"]["content"] == record["content"]
            assert truth["evidence"]
        return records, notices[0]

    def observations(self) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]], self.api("GET", "/v1/admin/observations", self.owner)["items"]
        )


def serve(root: Path) -> None:
    require_synthetic_root(root)
    import keyring
    import uvicorn
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from allthecontext.core.service import CoreService
    from keyring.backends.null import Keyring

    keyring.set_keyring(Keyring())  # type: ignore[no-untyped-call]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        config = replace(CoreConfig.in_directory(root / "vault", require_auth=True), port=port)
        with CoreService(config) as service:
            app = create_app(config, service=service)
            server = uvicorn.Server(
                uvicorn.Config(
                    app, host="127.0.0.1", port=port, log_level="warning", access_log=False
                )
            )
            (root / "ready.json").write_text(json.dumps({"port": port, "pid": os.getpid()}))
            server.run(sockets=[listener])


def run_hook(provider: Provider, mode: Mode, root: Path) -> None:
    require_synthetic_root(root)
    import keyring
    from allthecontext import claude_code_hook, codex_hook
    from keyring.backends.null import Keyring

    keyring.set_keyring(Keyring())  # type: ignore[no-untyped-call]
    payload = json.load(sys.stdin)
    text, turn = payload["text"], payload["turn"]
    context = cast(Any, None)
    diagnostic_id = payload.get("diagnostic_id")
    if diagnostic_id is not None and (
        type(diagnostic_id) is not str
        or len(diagnostic_id) != 32
        or any(character not in "0123456789abcdef" for character in diagnostic_id)
    ):
        raise ValueError("Invalid synthetic diagnostic ID")
    if mode == "read" and diagnostic_id is not None:
        with read_diagnostics() as events:
            if provider == "codex":
                output = codex_hook.codex_user_prompt_submit_read(
                    context, text, payload["budget"], "synthetic-native-session", turn
                )
            else:
                output = claude_code_hook.claude_code_user_prompt_submit_read(
                    context, text, str(root), "synthetic-native-session"
                )
        directory = root / "read-diagnostics"
        directory.mkdir(exist_ok=True)
        (directory / (diagnostic_id + ".json")).write_text(
            json.dumps({"provider": provider, "events": events}), encoding="utf-8"
        )
        print(json.dumps(output, ensure_ascii=True))
        return
    if provider == "codex":
        if mode == "read":
            output = codex_hook.codex_user_prompt_submit_read(
                context, text, payload["budget"], "synthetic-native-session", turn
            )
        else:
            output, _receipt = codex_hook.handle_codex_event(
                {
                    "hook_event_name": "UserPromptSubmit" if mode == "capture" else "Stop",
                    "prompt" if mode == "capture" else "last_assistant_message": text,
                    "session_id": "synthetic-native-session",
                    "turn_id": turn,
                }
            )
    else:
        if mode == "read":
            output = claude_code_hook.claude_code_user_prompt_submit_read(
                context, text, str(root), "synthetic-native-session"
            )
        elif mode == "capture":
            output = claude_code_hook.claude_code_user_prompt_submit_capture(
                context, text, str(root), "synthetic-native-session"
            )
        else:
            output = claude_code_hook.claude_code_stop_capture(
                context, text, str(root), "synthetic-native-session"
            )
    print(json.dumps(output, ensure_ascii=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve", action="store_true")
    group.add_argument("--hook", choices=("codex", "claude"))
    parser.add_argument("--mode", choices=("capture", "read", "stop"))
    args = parser.parse_args()
    if args.serve:
        serve(args.scratch)
    else:
        if args.mode is None:
            parser.error("--hook requires --mode")
        run_hook(args.hook, args.mode, args.scratch)


if __name__ == "__main__":
    main()

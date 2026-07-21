"""First-run setup orchestration for the desktop application."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .client_config import ClientConfigResult, configure_codex
from .config import CoreConfig
from .credentials import DevelopmentFileCredentialStore, KeyringCredentialStore
from .desktop_runtime import RuntimeCommand
from .models import ClientCreate
from .storage import CoreStore
from .user_startup import StartupResult, install_user_startup

DESKTOP_CLIENT_NAME = "All The Context Desktop"
DESKTOP_SCOPES = [
    "*",
    "admin",
    "context:ingest",
    "context:propose",
    "context:read",
    "context:status",
]
CORE_URL = "http://127.0.0.1:7337"
ProgressCallback = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class SetupOptions:
    vault_name: str = "My Context"
    timezone: str = "UTC"
    configure_codex: bool = True
    start_at_login: bool = True


@dataclass(frozen=True, slots=True)
class SetupResult:
    vault_id: str
    client_id: str
    dashboard_url: str
    core_url: str
    credential_storage: str
    codex: ClientConfigResult | None
    startup: StartupResult | None
    log_path: Path
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DesktopAccess:
    client_id: str
    token: str
    credential_storage: str


def core_is_healthy(url: str = CORE_URL, *, timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return bool(payload.get("status") == "ok" and payload.get("component") == "core")
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _log_path(config: CoreConfig) -> Path:
    path = config.data_dir / "logs" / "core.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def launch_core(
    runtime: RuntimeCommand,
    config: CoreConfig,
    *,
    wait_seconds: float = 10.0,
) -> Path:
    log_path = _log_path(config)
    if core_is_healthy(f"http://{config.host}:{config.port}"):
        return log_path

    environment = os.environ.copy()
    environment.update(
        {
            "ATC_CORE_DATA_DIR": str(config.data_dir),
            "ATC_CORE_HOST": config.host,
            "ATC_CORE_PORT": str(config.port),
        }
    )
    creation_flags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with log_path.open("ab") as log:
        subprocess.Popen(
            runtime.core(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            creationflags=creation_flags,
            start_new_session=start_new_session,
        )

    deadline = time.monotonic() + wait_seconds
    url = f"http://{config.host}:{config.port}"
    while time.monotonic() < deadline:
        if core_is_healthy(url):
            return log_path
        time.sleep(0.1)
    tail = ""
    with suppress(OSError):
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2_000:]
    detail = f" See {log_path}." if not tail else f" Last log output:\n{tail}"
    raise RuntimeError(f"Core did not become ready within {wait_seconds:g} seconds.{detail}")


def _fallback_store(config: CoreConfig) -> DevelopmentFileCredentialStore:
    return DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")


def _stored_token(client_id: str, config: CoreConfig) -> DesktopAccess | None:
    try:
        token = KeyringCredentialStore().get(f"client:{client_id}")
    except RuntimeError:
        token = None
    if token:
        return DesktopAccess(client_id, token, "operating-system credential store")

    try:
        token = _fallback_store(config).get(f"client:{client_id}")
    except (OSError, RuntimeError, ValueError):
        token = None
    if token:
        return DesktopAccess(client_id, token, "local app-data fallback")
    return None


def recover_desktop_access(config: CoreConfig | None = None) -> DesktopAccess | None:
    active_config = config or CoreConfig.default()
    if not active_config.database_path.is_file():
        return None
    store = CoreStore(active_config.database_path)
    try:
        clients = store.list_clients()
    except sqlite3.DatabaseError:
        return None
    for client in clients:
        if client["name"] != DESKTOP_CLIENT_NAME or client["revoked"]:
            continue
        client_id = str(client["id"])
        access = _stored_token(client_id, active_config)
        principal = store.authenticate(access.token) if access else None
        if principal is not None and principal.id == client_id:
            return access
    return None


def _desktop_client(store: CoreStore, config: CoreConfig) -> DesktopAccess:
    for client in store.list_clients():
        if client["name"] != DESKTOP_CLIENT_NAME or client["revoked"]:
            continue
        client_id = str(client["id"])
        access = _stored_token(client_id, config)
        principal = store.authenticate(access.token) if access else None
        if access is not None and principal is not None and principal.id == client_id:
            return access

    principal, token = store.create_client(
        ClientCreate(name=DESKTOP_CLIENT_NAME, scopes=DESKTOP_SCOPES)
    )
    try:
        credential_store = KeyringCredentialStore()
        credential_name = f"client:{principal.id}"
        credential_store.set(credential_name, token)
        if credential_store.get(credential_name) != token:
            raise RuntimeError("the operating-system credential store did not persist the value")
        return DesktopAccess(principal.id, token, "operating-system credential store")
    except RuntimeError:
        _fallback_store(config).set(f"client:{principal.id}", token)
        return DesktopAccess(principal.id, token, "local app-data fallback")


def authenticated_dashboard_url(config: CoreConfig, token: str) -> str:
    fragment = urllib.parse.urlencode({"atc_token": token})
    return f"http://{config.host}:{config.port}/#{fragment}"


def perform_setup(
    options: SetupOptions,
    runtime: RuntimeCommand | None = None,
    *,
    progress: ProgressCallback | None = None,
    config: CoreConfig | None = None,
) -> SetupResult:
    active_runtime = runtime or RuntimeCommand.current()
    active_config = config or CoreConfig.default()
    notify = progress or (lambda _step, _message: None)
    warnings: list[str] = []

    notify("vault", "Creating your private local Core")
    active_config.prepare()
    store = CoreStore(active_config.database_path)
    vault_id = store.initialize_vault(options.vault_name.strip() or "My Context", options.timezone)

    notify("credential", "Securing the desktop and MCP credential")
    access = _desktop_client(store, active_config)
    stored_in_keyring = access.credential_storage == "operating-system credential store"
    if not stored_in_keyring:
        warnings.append(
            "Your OS credential service was unavailable, so this first release used its "
            "local app-data fallback."
        )

    codex_result: ClientConfigResult | None = None
    if options.configure_codex:
        notify("client", "Connecting Codex to All The Context")
        try:
            codex_result = configure_codex(
                active_runtime,
                access.client_id,
                token=None if stored_in_keyring else access.token,
                target_url=f"http://{active_config.host}:{active_config.port}",
            )
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"Codex configuration was not changed: {exc}")

    startup_result: StartupResult | None = None
    if options.start_at_login:
        notify("startup", "Enabling private per-user startup")
        try:
            startup_result = install_user_startup(active_runtime)
        except OSError as exc:
            warnings.append(f"Automatic startup was not enabled: {exc}")

    notify("core", "Starting Core on this device")
    log_path = launch_core(active_runtime, active_config)
    dashboard_url = authenticated_dashboard_url(active_config, access.token)
    notify("complete", "All The Context is ready")
    return SetupResult(
        vault_id=vault_id,
        client_id=access.client_id,
        dashboard_url=dashboard_url,
        core_url=f"http://{active_config.host}:{active_config.port}",
        credential_storage=access.credential_storage,
        codex=codex_result,
        startup=startup_result,
        log_path=log_path,
        warnings=tuple(warnings),
    )


def open_dashboard(url: str) -> bool:
    return webbrowser.open(url, new=2)

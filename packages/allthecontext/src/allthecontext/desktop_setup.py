"""First-run setup orchestration for the desktop application."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from .capture_runtime import (
    authorize_local_workspace,
    compose_capture_coordinator,
)
from .capture_scheduler import CAPTURE_SCHEDULER_ENABLED_ENV
from .client_config import (
    ClientConfigResult,
    ManagedClientConfig,
    claude_config_path,
    codex_config_path,
    configure_claude,
    configure_codex,
    read_claude_config,
    read_codex_config,
)
from .config import CoreConfig
from .credentials import (
    FALLBACK_CREDENTIAL_STORAGE,
    OS_CREDENTIAL_STORAGE,
    DevelopmentFileCredentialStore,
    KeyringCredentialStore,
    development_file_credentials_enabled,
    require_development_file_credentials,
)
from .desktop_runtime import RuntimeCommand
from .instance_identity import ensure_instance_secret, proof_matches
from .models import ClientCreate
from .platform_compat import windows_creation_flags
from .storage import CoreStore, StorageError
from .user_startup import StartupResult, install_user_startup

DESKTOP_CLIENT_NAME = "All The Context Desktop"
CODEX_CLIENT_NAME = "Codex"
CLAUDE_CLIENT_NAME = "Claude Desktop"
SCHEDULER_SETUP_CLIENT_NAME = "All The Context one-time setup scheduler"
DESKTOP_SCOPES = [
    "*",
    "admin",
    "context:ingest",
    "context:propose",
    "context:read",
    "context:status",
]
AI_CLIENT_SCOPES = [
    "context:ingest",
    "context:propose",
    "context:read",
    "context:status",
    # Explicit local trust grant for A-09 / B-102. Authentication alone is not
    # enough; only ATC-configured same-device AI principals receive this class.
    "witness:explicit_user_statement",
]
ProgressCallback = Callable[[str, str], None]


def local_timezone() -> str:
    """Return the operating system's display timezone without asking the user."""
    timezone = datetime.now().astimezone().tzinfo
    return getattr(timezone, "key", None) or str(timezone or "UTC")


@dataclass(frozen=True, slots=True)
class SetupOptions:
    vault_name: str = "My Context"
    timezone: str = field(default_factory=local_timezone)
    configure_codex: bool = True
    configure_claude: bool = True
    start_at_login: bool = True
    workspace_root: Path | None = None
    workspace_local_only_acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class SetupResult:
    vault_id: str
    client_id: str
    dashboard_url: str
    core_url: str
    credential_storage: str
    codex: ClientConfigResult | None
    claude: ClientConfigResult | None
    startup: StartupResult | None
    log_path: Path
    warnings: tuple[str, ...] = ()
    workspace_source_id: str | None = None
    continuous_capture_enabled: bool = False


@dataclass(frozen=True, slots=True)
class DesktopAccess:
    client_id: str
    token: str
    credential_storage: str


class CoreProbe(StrEnum):
    VERIFIED = "verified"
    UNREACHABLE = "unreachable"
    UNVERIFIED = "unverified"


def probe_core(config: CoreConfig, *, timeout: float = 0.4) -> CoreProbe:
    """Identify this installation's Core without trusting a forgeable health body."""
    challenge = secrets.token_urlsafe(24)
    root = f"http://{config.host}:{config.port}"
    try:
        with urllib.request.urlopen(
            f"{root}/health?{urllib.parse.urlencode({'challenge': challenge})}",
            timeout=timeout,
        ) as response:
            if response.status != 200:
                return CoreProbe.UNVERIFIED
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                return CoreProbe.UNVERIFIED
            proof = payload.get("proof")
            if (
                payload.get("status") == "ok"
                and payload.get("component") == "core"
                and isinstance(proof, str)
                and proof_matches(config, challenge, proof)
            ):
                return CoreProbe.VERIFIED
            return CoreProbe.UNVERIFIED
    except urllib.error.HTTPError:
        return CoreProbe.UNVERIFIED
    except (OSError, ValueError, urllib.error.URLError):
        return CoreProbe.UNREACHABLE


def core_is_healthy(config: CoreConfig, *, timeout: float = 0.4) -> bool:
    return probe_core(config, timeout=timeout) is CoreProbe.VERIFIED


def _log_path(config: CoreConfig) -> Path:
    path = config.data_dir / "logs" / "core.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _wait_for_previous_core_exit(config: CoreConfig, *, wait_seconds: float) -> CoreProbe:
    """Wait for an unreachable Core to release its process lock after shutdown."""

    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            with FileLock(str(config.lock_path), timeout=0):
                return CoreProbe.UNREACHABLE
        except FileLockTimeout:
            state = probe_core(config)
            if state is not CoreProbe.UNREACHABLE:
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "The previous All The Context Core is still shutting down. Try again."
                ) from None
            time.sleep(min(0.1, remaining))


def launch_core(
    runtime: RuntimeCommand,
    config: CoreConfig,
    *,
    wait_seconds: float = 10.0,
) -> Path:
    ensure_instance_secret(config)
    log_path = _log_path(config)
    launch_lock = FileLock(str(config.data_dir / "core-launch.lock"), timeout=wait_seconds)
    try:
        with launch_lock:
            initial = probe_core(config)
            if initial is CoreProbe.VERIFIED:
                return log_path
            if initial is CoreProbe.UNVERIFIED:
                raise RuntimeError(
                    f"Port {config.port} is already used by a service that is not this "
                    "All The Context Core. Close that service or choose another Core port."
                )

            after_shutdown = _wait_for_previous_core_exit(
                config,
                wait_seconds=wait_seconds,
            )
            if after_shutdown is CoreProbe.VERIFIED:
                return log_path
            if after_shutdown is CoreProbe.UNVERIFIED:
                raise RuntimeError(
                    f"Port {config.port} was claimed by a service that is not this "
                    "All The Context Core."
                )

            environment = os.environ.copy()
            environment.update(
                {
                    "ATC_CORE_DATA_DIR": str(config.data_dir),
                    "ATC_CORE_HOST": config.host,
                    "ATC_CORE_PORT": str(config.port),
                    CAPTURE_SCHEDULER_ENABLED_ENV: "1",
                }
            )
            if getattr(sys, "frozen", False):
                # A long-lived Core spawned from a PyInstaller one-file application
                # must not reuse the caller's temporary extraction. Reuse can keep a
                # completed setup process alive until Core exits and can interfere
                # with one-file cleanup on Windows.
                environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            creation_flags = 0
            start_new_session = os.name != "nt"
            if os.name == "nt":
                creation_flags = windows_creation_flags(
                    "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"
                )

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
            while time.monotonic() < deadline:
                state = probe_core(config)
                if state is CoreProbe.VERIFIED:
                    return log_path
                if state is CoreProbe.UNVERIFIED:
                    raise RuntimeError(
                        f"Port {config.port} was claimed by a service that is not this "
                        "All The Context Core."
                    )
                time.sleep(0.1)
    except FileLockTimeout:
        if probe_core(config) is CoreProbe.VERIFIED:
            return log_path
        raise RuntimeError(
            "Another All The Context client is already restarting Core. Try again."
        ) from None

    tail = ""
    with suppress(OSError):
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2_000:]
    detail = f" See {log_path}." if not tail else f" Last log output:\n{tail}"
    raise RuntimeError(f"Core did not become ready within {wait_seconds:g} seconds.{detail}")


def _fallback_store(config: CoreConfig) -> DevelopmentFileCredentialStore:
    return DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")


def recover_client_access(client_id: str, config: CoreConfig) -> DesktopAccess | None:
    try:
        token = KeyringCredentialStore().get(f"client:{client_id}")
    except RuntimeError:
        token = None
    if token:
        return DesktopAccess(client_id, token, OS_CREDENTIAL_STORAGE)

    if development_file_credentials_enabled():
        try:
            token = _fallback_store(config).get(f"client:{client_id}")
        except (OSError, RuntimeError, ValueError):
            token = None
        if token:
            return DesktopAccess(client_id, token, FALLBACK_CREDENTIAL_STORAGE)
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
        access = recover_client_access(client_id, active_config)
        principal = store.authenticate(access.token) if access else None
        if principal is not None and principal.id == client_id:
            return access
    return None


def recover_administrator_access(config: CoreConfig | None = None) -> DesktopAccess | None:
    """Recover a valid administrator created by either desktop setup or the CLI."""
    active_config = config or CoreConfig.default()
    preferred = recover_desktop_access(active_config)
    if preferred is not None:
        return preferred
    if not active_config.database_path.is_file():
        return None
    store = CoreStore(active_config.database_path)
    try:
        clients = store.list_clients()
    except sqlite3.DatabaseError:
        return None
    for client in clients:
        scopes = set(client.get("scopes", []))
        if client["revoked"] or not ({"admin", "*"} & scopes):
            continue
        client_id = str(client["id"])
        access = recover_client_access(client_id, active_config)
        principal = store.authenticate(access.token) if access else None
        if principal is not None and principal.id == client_id:
            return access
    return None


def delete_client_credential(
    client_id: str,
    config: CoreConfig,
    *,
    strict_storage: str | None = None,
) -> None:
    name = f"client:{client_id}"
    keyring = KeyringCredentialStore()
    fallback = _fallback_store(config)
    if strict_storage not in {None, OS_CREDENTIAL_STORAGE, FALLBACK_CREDENTIAL_STORAGE}:
        raise ValueError("strict_storage must identify a supported credential store")

    if strict_storage == OS_CREDENTIAL_STORAGE:
        try:
            keyring.delete(name)
            if keyring.get(name) is not None:
                raise RuntimeError("the operating-system credential store retained the credential")
        except RuntimeError as exc:
            raise RuntimeError(
                "Could not verify removal from the operating-system credential store"
            ) from exc
        with suppress(OSError, RuntimeError, ValueError):
            fallback.delete(name)
        return

    if strict_storage == FALLBACK_CREDENTIAL_STORAGE:
        with suppress(RuntimeError):
            keyring.delete(name)
        try:
            fallback.delete(name)
            if fallback.get(name) is not None:
                raise RuntimeError("the local fallback retained the credential")
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(
                "Could not verify removal from the local credential fallback"
            ) from exc
        return

    # Once the database principal is revoked, a lingering secret is inert.
    # Cleanup therefore remains portable on Linux hosts with no Secret Service.
    with suppress(RuntimeError):
        keyring.delete(name)
    with suppress(OSError, RuntimeError, ValueError):
        fallback.delete(name)


def _persist_client_token(client_id: str, token: str, config: CoreConfig) -> DesktopAccess:
    credential_name = f"client:{client_id}"
    credential_store = KeyringCredentialStore()
    try:
        previous = credential_store.get(credential_name)
    except RuntimeError:
        require_development_file_credentials()
        _fallback_store(config).set(credential_name, token)
        return DesktopAccess(client_id, token, FALLBACK_CREDENTIAL_STORAGE)
    try:
        credential_store.set(credential_name, token)
        if credential_store.get(credential_name) != token:
            raise RuntimeError("the operating-system credential store did not persist the value")
        return DesktopAccess(client_id, token, OS_CREDENTIAL_STORAGE)
    except RuntimeError:
        try:
            if previous is None:
                credential_store.delete(credential_name)
                if credential_store.get(credential_name) is not None:
                    raise RuntimeError("the new operating-system credential still exists")
            else:
                credential_store.set(credential_name, previous)
                if credential_store.get(credential_name) != previous:
                    raise RuntimeError("the previous operating-system credential was not restored")
        except RuntimeError as rollback_error:
            raise RuntimeError(
                "The operating-system credential write failed and could not be rolled back. "
                "No fallback copy was created"
            ) from rollback_error
        require_development_file_credentials()
        _fallback_store(config).set(credential_name, token)
        return DesktopAccess(client_id, token, FALLBACK_CREDENTIAL_STORAGE)


def _create_client_access(
    store: CoreStore,
    config: CoreConfig,
    *,
    name: str,
    scopes: list[str],
) -> DesktopAccess:
    principal, token = store.create_client(ClientCreate(name=name, scopes=scopes))
    try:
        return _persist_client_token(principal.id, token, config)
    except BaseException:
        store.revoke_client(principal.id)
        delete_client_credential(principal.id, config)
        raise


def _ensure_client_access_with_state(
    store: CoreStore,
    config: CoreConfig,
    *,
    name: str,
    scopes: list[str],
) -> tuple[DesktopAccess, bool]:
    for client in store.list_clients():
        if client["name"] != name or client["revoked"]:
            continue
        client_id = str(client["id"])
        if set(client.get("scopes", [])) != set(scopes):
            continue
        access = recover_client_access(client_id, config)
        principal = store.authenticate(access.token) if access else None
        if access is not None and principal is not None and principal.id == client_id:
            return access, False
    return _create_client_access(store, config, name=name, scopes=scopes), True


def configure_client_access_transactionally(
    store: CoreStore,
    config: CoreConfig,
    *,
    name: str,
    scopes: list[str],
    configure: Callable[[DesktopAccess], ClientConfigResult],
) -> tuple[DesktopAccess, ClientConfigResult]:
    """Create/reuse a principal and remove a newly created one if configuration fails."""

    access, created = _ensure_client_access_with_state(
        store,
        config,
        name=name,
        scopes=scopes,
    )
    try:
        result = configure(access)
    except BaseException:
        if created:
            store.revoke_client(access.client_id)
            delete_client_credential(
                access.client_id,
                config,
                strict_storage=access.credential_storage,
            )
        raise
    return access, result


def retire_other_named_clients(
    store: CoreStore,
    config: CoreConfig,
    *,
    name: str,
    keep_id: str,
) -> None:
    """Revoke stale same-purpose credentials after their config was replaced."""
    for client in store.list_clients():
        client_id = str(client["id"])
        if client["name"] == name and client_id != keep_id and not client["revoked"]:
            store.revoke_client(client_id)
            delete_client_credential(client_id, config)


def ensure_client_access(
    store: CoreStore,
    config: CoreConfig,
    *,
    name: str,
    scopes: list[str],
) -> DesktopAccess:
    """Return a recoverable named client without sharing another app's credential."""
    access, _created = _ensure_client_access_with_state(
        store,
        config,
        name=name,
        scopes=scopes,
    )
    return access


def _desktop_client(store: CoreStore, config: CoreConfig) -> DesktopAccess:
    return ensure_client_access(
        store,
        config,
        name=DESKTOP_CLIENT_NAME,
        scopes=DESKTOP_SCOPES,
    )


def _configuration_uses_access(
    configured: ManagedClientConfig,
    access: DesktopAccess,
) -> bool:
    return (
        configured.env.get("ATC_CLIENT_ID") == access.client_id
        or configured.env.get("ATC_CLIENT_TOKEN") == access.token
    )


def migrate_existing_integrations(
    runtime: RuntimeCommand,
    config: CoreConfig,
    desktop_access: DesktopAccess,
) -> DesktopAccess:
    """Repair managed clients on every launch and retire legacy administrator reuse."""
    store = CoreStore(config.database_path)
    target_url = f"http://{config.host}:{config.port}"
    used_desktop_credential = False
    integrations = (
        (CODEX_CLIENT_NAME, codex_config_path, read_codex_config, configure_codex),
        (CLAUDE_CLIENT_NAME, claude_config_path, read_claude_config, configure_claude),
    )
    for name, config_path, read_config, configure in integrations:
        try:
            current = read_config()
        except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError):
            # Preserve an invalid user-owned config. The dashboard reports it as
            # degraded and its explicit Repair action will surface the parse error.
            with suppress(OSError):
                raw = config_path().read_text(encoding="utf-8")
                used_desktop_credential = used_desktop_credential or (
                    desktop_access.client_id in raw or desktop_access.token in raw
                )
            continue
        if current is None:
            continue
        legacy = _configuration_uses_access(current, desktop_access)
        access = ensure_client_access(store, config, name=name, scopes=AI_CLIENT_SCOPES)
        embedded_token = (
            None if access.credential_storage == OS_CREDENTIAL_STORAGE else access.token
        )
        try:
            configure(
                runtime,
                access.client_id,
                token=embedded_token,
                target_url=target_url,
                core_data_dir=config.data_dir,
            )
        except (OSError, ValueError):
            # If an old config exposed the desktop administrator, rotating it
            # below makes that stale config harmless even when the user-owned
            # config file cannot currently be repaired.
            used_desktop_credential = used_desktop_credential or legacy
            continue
        retire_other_named_clients(store, config, name=name, keep_id=access.client_id)
        used_desktop_credential = used_desktop_credential or legacy

    if not used_desktop_credential:
        return desktop_access

    replacement = _create_client_access(
        store,
        config,
        name=DESKTOP_CLIENT_NAME,
        scopes=DESKTOP_SCOPES,
    )
    store.revoke_client(desktop_access.client_id)
    delete_client_credential(desktop_access.client_id, config)
    return replacement


def authenticated_dashboard_url(
    config: CoreConfig,
    token: str,
    *,
    timeout: float = 3.0,
    landing_page: str | None = None,
) -> str:
    """Mint a one-use browser connection URL without placing credentials in the address bar."""

    dashboard_pages = {
        "sources",
        "context",
        "connections",
        "activity",
        "backup",
        "updates",
    }
    if landing_page is not None and landing_page not in dashboard_pages:
        raise ValueError("landing_page is not a recognized dashboard page")
    state = probe_core(config, timeout=min(timeout, 1.0))
    if state is CoreProbe.UNVERIFIED:
        raise RuntimeError(
            "The service on the Core port could not prove that it belongs to this installation; "
            "the administrator credential was not sent."
        )
    if state is CoreProbe.UNREACHABLE:
        raise RuntimeError("Core is not reachable on this device")
    root = f"http://{config.host}:{config.port}"
    request = urllib.request.Request(
        f"{root}/v1/admin/browser-session",
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    path = payload.get("connect_path") if isinstance(payload, dict) else None
    if not isinstance(path, str) or not path.startswith("/v1/browser/connect?ticket="):
        raise RuntimeError("Core returned an invalid browser connection link")
    if landing_page is not None:
        path = f"{path}&{urllib.parse.urlencode({'page': landing_page})}"
    return urllib.parse.urljoin(root, path)


def _validate_workspace_options(options: SetupOptions) -> None:
    if options.workspace_root is None:
        if options.workspace_local_only_acknowledged:
            raise ValueError("workspace local-only acknowledgement requires a workspace root")
        return
    if not options.workspace_local_only_acknowledged:
        raise ValueError("workspace local-only acknowledgement is required")


def _configure_workspace_capture(
    options: SetupOptions,
    store: CoreStore,
    config: CoreConfig,
    *,
    notify: ProgressCallback,
    warnings: list[str],
) -> tuple[str | None, bool]:
    root = options.workspace_root
    if root is None:
        return None, False

    notify("source", "Authorizing the selected local source")
    authorization = authorize_local_workspace(
        store,
        config,
        root,
        local_only_acknowledged=True,
    )
    source_id = authorization.get("id")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("workspace authorization did not return a source")

    coordinator = compose_capture_coordinator(store, config)
    source = coordinator.get_source(source_id)
    if source.lifecycle_state == "disabled":
        source = coordinator.enable(source_id)

    if source.lifecycle_state != "enabled":
        warnings.append(
            "Continuous capture remains disabled because the local source is not enabled."
        )
        return source_id, False

    return source_id, True


def _running_core_scheduler_request(
    config: CoreConfig,
    token: str,
    *,
    action: str | None = None,
    timeout: float = 3.0,
) -> dict[str, object] | None:
    """Call one scheduler route only after the listener proves this installation."""

    if probe_core(config, timeout=min(timeout, 1.0)) is not CoreProbe.VERIFIED:
        return None
    root = f"http://{config.host}:{config.port}"
    suffix = f"/{action}" if action is not None else ""
    method = "POST" if action is not None else "GET"
    request = urllib.request.Request(
        f"{root}/v1/admin/capture/scheduler{suffix}",
        data=b"" if method == "POST" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TypeError, ValueError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _scheduler_is_active(payload: dict[str, object] | None) -> bool:
    return bool(
        payload is not None
        and payload.get("config_valid") is True
        and payload.get("durable_enabled") is True
        and payload.get("enabled") is True
        and payload.get("running") is True
    )


def _enable_running_core_scheduler(
    config: CoreConfig,
    token: str,
    *,
    timeout: float = 3.0,
) -> bool:
    """Enable the live scheduler and reconcile an ambiguous activation response."""

    previous = _running_core_scheduler_request(config, token, timeout=timeout)
    if _scheduler_is_active(previous):
        return True

    activated = _running_core_scheduler_request(
        config,
        token,
        action="enable",
        timeout=timeout,
    )
    if _scheduler_is_active(activated):
        return True

    reconciled = _running_core_scheduler_request(config, token, timeout=timeout)
    if _scheduler_is_active(reconciled):
        return True

    if previous is not None and previous.get("durable_enabled") is False:
        _running_core_scheduler_request(
            config,
            token,
            action="disable",
            timeout=timeout,
        )
    return False


def _activate_running_core_scheduler(store: CoreStore, config: CoreConfig) -> bool:
    """Use a revocable one-time administrator rather than the durable desktop token."""

    principal, token = store.create_client(
        ClientCreate(name=SCHEDULER_SETUP_CLIENT_NAME, scopes=["admin"])
    )
    try:
        return _enable_running_core_scheduler(config, token)
    finally:
        with suppress(StorageError):
            store.revoke_client(principal.id)


def perform_setup(
    options: SetupOptions,
    runtime: RuntimeCommand | None = None,
    *,
    progress: ProgressCallback | None = None,
    config: CoreConfig | None = None,
) -> SetupResult:
    _validate_workspace_options(options)
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
    stored_in_keyring = access.credential_storage == OS_CREDENTIAL_STORAGE
    if not stored_in_keyring:
        warnings.append(
            "The explicitly enabled insecure development credential file is in use; "
            "credentials are stored as plaintext."
        )

    codex_result: ClientConfigResult | None = None
    if options.configure_codex:
        notify("client", "Connecting Codex to All The Context")
        try:
            codex_access, codex_result = configure_client_access_transactionally(
                store,
                active_config,
                name=CODEX_CLIENT_NAME,
                scopes=AI_CLIENT_SCOPES,
                configure=lambda client_access: configure_codex(
                    active_runtime,
                    client_access.client_id,
                    token=(
                        None
                        if client_access.credential_storage == OS_CREDENTIAL_STORAGE
                        else client_access.token
                    ),
                    target_url=f"http://{active_config.host}:{active_config.port}",
                    core_data_dir=active_config.data_dir,
                ),
            )
            retire_other_named_clients(
                store,
                active_config,
                name=CODEX_CLIENT_NAME,
                keep_id=codex_access.client_id,
            )
        except (OSError, RuntimeError, ValueError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"Codex configuration was not changed: {exc}")

    claude_result: ClientConfigResult | None = None
    if options.configure_claude:
        notify("client", "Connecting Claude Desktop to All The Context")
        try:
            claude_access, claude_result = configure_client_access_transactionally(
                store,
                active_config,
                name=CLAUDE_CLIENT_NAME,
                scopes=AI_CLIENT_SCOPES,
                configure=lambda client_access: configure_claude(
                    active_runtime,
                    client_access.client_id,
                    token=(
                        None
                        if client_access.credential_storage == OS_CREDENTIAL_STORAGE
                        else client_access.token
                    ),
                    target_url=f"http://{active_config.host}:{active_config.port}",
                    core_data_dir=active_config.data_dir,
                ),
            )
            retire_other_named_clients(
                store,
                active_config,
                name=CLAUDE_CLIENT_NAME,
                keep_id=claude_access.client_id,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Claude Desktop configuration was not changed: {exc}")

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

    workspace_source_id, workspace_capture_ready = _configure_workspace_capture(
        options,
        store,
        active_config,
        notify=notify,
        warnings=warnings,
    )
    continuous_capture_enabled = False
    if workspace_source_id is not None and workspace_capture_ready:
        continuous_capture_enabled = _activate_running_core_scheduler(store, active_config)
        if not continuous_capture_enabled:
            warnings.append("Continuous capture could not be activated in the running Core.")
    notify("complete", "All The Context is ready")
    return SetupResult(
        vault_id=vault_id,
        client_id=access.client_id,
        dashboard_url=dashboard_url,
        core_url=f"http://{active_config.host}:{active_config.port}",
        credential_storage=access.credential_storage,
        codex=codex_result,
        claude=claude_result,
        startup=startup_result,
        log_path=log_path,
        warnings=tuple(warnings),
        workspace_source_id=workspace_source_id,
        continuous_capture_enabled=continuous_capture_enabled,
    )


def open_dashboard(url: str) -> bool:
    return webbrowser.open(url, new=2)

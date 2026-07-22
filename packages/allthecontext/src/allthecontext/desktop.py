"""Single-entry desktop application, background Core, and packaged diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tkinter
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox
from typing import Any

from platformdirs import user_data_path

from .application_install import (
    install_application_entrypoints,
    remove_application_entrypoints,
)
from .client_config import apply_managed_client_cleanup, plan_managed_client_cleanup
from .config import CoreConfig
from .credentials import FALLBACK_CREDENTIAL_STORAGE
from .desktop_runtime import RuntimeCommand, mcp_helper_name
from .desktop_setup import (
    CLAUDE_CLIENT_NAME,
    CODEX_CLIENT_NAME,
    CoreProbe,
    SetupOptions,
    authenticated_dashboard_url,
    delete_client_credential,
    launch_core,
    local_timezone,
    migrate_existing_integrations,
    open_dashboard,
    perform_setup,
    probe_core,
    recover_desktop_access,
)
from .edge_connection import EdgeConnectionStore, decommission_edge_connection
from .instance_identity import IDENTITY_FILENAME
from .models import ClientCreate
from .storage import CoreStore, StorageError
from .user_startup import remove_user_startup

WINDOWS_APP_NAME = "AllTheContext.exe"


def _retire_installed_ai_clients(
    config: CoreConfig, configured_client_storages: dict[str, str]
) -> bool:
    """Revoke and remove every credential created for managed AI connections."""

    database_readable = True
    clients: list[dict[str, Any]] = []
    store: CoreStore | None = None
    if config.database_path.is_file():
        store = CoreStore(config.database_path)
        try:
            clients = store.list_clients()
        except sqlite3.DatabaseError:
            database_readable = False
    managed_names = {CODEX_CLIENT_NAME, CLAUDE_CLIENT_NAME}
    managed_client_ids = set(configured_client_storages)
    for client in clients:
        if client["name"] not in managed_names:
            continue
        client_id = str(client["id"])
        managed_client_ids.add(client_id)
        if not client["revoked"]:
            assert store is not None
            store.revoke_client(client_id)
    # Always retry deletion for an already-revoked row. A prior uninstall may
    # have revoked database access before the OS vault became writable. IDs
    # returned by config removal cover the active connection even when the
    # retained Core database is damaged and cannot be queried.
    for client_id in managed_client_ids:
        strict_storage = (
            None
            if store is not None and database_readable
            else configured_client_storages.get(client_id)
        )
        delete_client_credential(client_id, config, strict_storage=strict_storage)
    return database_readable


def _redact_failure_message(error: Exception) -> str:
    """Keep setup diagnostics useful without copying known credential forms."""

    message = str(error).strip() or type(error).__name__
    patterns = (
        (r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[redacted]"),
        (r"atc-edge-v1\.[A-Za-z0-9_-]+", "[redacted Edge enrollment]"),
        (
            r"(?i)((?:token|secret|recovery[_ -]?code)\s*[=:]\s*)[^\s,;]+",
            r"\1[redacted]",
        ),
    )
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message)
    return message[:2_000]


def _write_failure_diagnostics(error: Exception) -> Path | None:
    """Persist a small redacted report even when the windowed build has no console."""

    report: dict[str, Any] = {
        "application": "All The Context",
        "error_type": type(error).__name__,
        "error": _redact_failure_message(error),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with suppress(Exception):
        report["runtime"] = diagnostics()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    locations = (
        Path(user_data_path("AllTheContext", "AllTheContext", roaming=False)) / "logs",
        Path(tempfile.gettempdir()) / "AllTheContext" / "logs",
    )
    for directory in locations:
        target = directory / f"desktop-error-{stamp}.json"
        temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.atc-new")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
            return target
        except OSError:
            temporary.unlink(missing_ok=True)
    return None


def _offer_graphical_retry(error: Exception) -> bool:
    diagnostics_path = _write_failure_diagnostics(error)
    location = str(diagnostics_path) if diagnostics_path is not None else "unavailable"
    return messagebox.askretrycancel(
        "All The Context needs attention",
        "Setup could not finish, and no local context was deleted.\n\n"
        f"{_redact_failure_message(error)}\n\n"
        f"Diagnostics: {location}\n\n"
        "Choose Retry after correcting the problem, or Cancel to close safely.",
    )


def windows_install_directory() -> Path:
    configured = os.environ.get("ATC_INSTALL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).resolve() / "Programs" / "All The Context"
    data_path = Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
    return data_path.parent / "Programs" / "All The Context"


def _same_file(source: Path, target: Path) -> bool:
    if not target.is_file() or source.stat().st_size != target.stat().st_size:
        return False
    chunk_size = 1024 * 1024
    with source.open("rb") as source_stream, target.open("rb") as target_stream:
        while True:
            source_chunk = source_stream.read(chunk_size)
            if source_chunk != target_stream.read(chunk_size):
                return False
            if not source_chunk:
                return True


def _copy_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(source, target):
        return
    temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.atc-new")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _install_mcp_helper(source: Path, target: Path) -> Path:
    """Update the stable helper or install a content-addressed copy if an AI app holds it open."""
    try:
        _copy_atomically(source, target)
        return target
    except PermissionError:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()[:12]
        versioned = target.with_name(f"{target.stem}-{digest}{target.suffix}")
        _copy_atomically(source, versioned)
        return versioned


def _stop_installed_core_for_upgrade() -> None:
    """Release a running installed executable before replacing it on Windows."""
    config = CoreConfig.default()
    state = probe_core(config)
    if state is CoreProbe.UNREACHABLE:
        return
    # Builds predating instance proofs cannot answer the challenge. Permit that
    # one migration only when the installation has no identity file; a
    # present-but-invalid proof is treated as an impersonated service.
    if state is CoreProbe.UNVERIFIED and (config.data_dir / IDENTITY_FILENAME).exists():
        raise RuntimeError(
            f"Port {config.port} is occupied by an unverified service. The installer "
            "did not send it a credential."
        )
    core_url = f"http://{config.host}:{config.port}"
    if not config.database_path.is_file():
        raise RuntimeError(
            "A service is using the Core port, but no existing All The Context vault was found."
        )
    # Never expose the durable desktop administrator during an upgrade. Core
    # sees this short-lived credential directly from the shared SQLite vault;
    # it is revoked immediately even if the listener returns a forged response.
    store = CoreStore(config.database_path)
    upgrade_principal, upgrade_token = store.create_client(
        ClientCreate(name="All The Context one-time upgrade", scopes=["admin"])
    )
    request = urllib.request.Request(
        f"{core_url}/v1/admin/shutdown",
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {upgrade_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3):
            pass
    except urllib.error.URLError as exc:
        raise RuntimeError("The existing Core could not be stopped for the update") from exc
    finally:
        with suppress(StorageError):
            store.revoke_client(upgrade_principal.id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if probe_core(config) is not CoreProbe.VERIFIED:
            return
        time.sleep(0.1)
    raise RuntimeError("The existing Core did not stop in time for the update")


def prepare_installed_runtime(
    runtime: RuntimeCommand,
    *,
    relaunch_args: tuple[str, ...] | None,
) -> tuple[RuntimeCommand, bool]:
    """Install frozen Windows binaries per-user and optionally relaunch the stable copy."""
    if not getattr(sys, "frozen", False) or platform.system() != "Windows":
        return runtime, False

    helper_source = runtime.mcp_executable
    if helper_source is None or not helper_source.is_file():
        raise RuntimeError("The packaged MCP helper is missing. Download the installer again.")

    install_dir = windows_install_directory()
    app_target = install_dir / WINDOWS_APP_NAME
    helper_target = install_dir / mcp_helper_name()
    app_needs_update = not _same_file(runtime.executable, app_target)
    if runtime.executable != app_target and app_target.is_file() and app_needs_update:
        _stop_installed_core_for_upgrade()
    installed_helper = _install_mcp_helper(helper_source, helper_target)
    _copy_atomically(runtime.executable, app_target)
    if runtime.executable != app_target:
        install_application_entrypoints(app_target)
    installed = RuntimeCommand(app_target, mcp_executable=installed_helper)

    if runtime.executable != app_target and relaunch_args is not None:
        environment = os.environ.copy()
        # PyInstaller 6.9+ otherwise treats a same-executable child as a
        # worker sharing the current one-file extraction. A relaunched app
        # must outlive this setup process and own an independent extraction.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(
            (str(app_target), *relaunch_args),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return installed, True
    return installed, False


def diagnostics() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    core_migrations = package_root / "migrations" / "core"
    relay_migrations = package_root / "migrations" / "relay"
    runtime = RuntimeCommand.current()
    return {
        "application": "All The Context",
        "version": "0.1.0",
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.system(),
        "python": platform.python_version(),
        "tk": tkinter.TkVersion,
        "core_migrations": len(tuple(core_migrations.glob("*.sql"))),
        "relay_migrations": len(tuple(relay_migrations.glob("*.sql"))),
        "dashboard_bundled": (package_root / "web" / "index.html").is_file(),
        "update_keyring_bundled": (package_root / "update_keys.json").is_file(),
        "mcp_helper_bundled": runtime.mcp_executable is not None,
        "mcp_stdio_available": runtime.mcp_executable is not None or platform.system() == "Linux",
        "core_data_directory": str(CoreConfig.default().data_dir),
    }


def write_diagnostics(path: Path) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(json.dumps(diagnostics(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _headless_setup(args: argparse.Namespace, runtime: RuntimeCommand) -> int:
    installed, _ = prepare_installed_runtime(runtime, relaunch_args=None)
    result = perform_setup(
        SetupOptions(
            vault_name=args.vault_name,
            timezone=args.timezone or local_timezone(),
            configure_codex=not args.no_codex,
            configure_claude=not args.no_claude,
            start_at_login=not args.no_startup,
        ),
        installed,
    )
    report = asdict(result)
    report["log_path"] = str(result.log_path)
    report["codex"] = asdict(result.codex) if result.codex else None
    report["claude"] = asdict(result.claude) if result.claude else None
    report["startup"] = asdict(result.startup) if result.startup else None
    target = Path(args.headless_setup).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return 0


def _open_existing(runtime: RuntimeCommand) -> bool:
    config = CoreConfig.default()
    access = recover_desktop_access(config)
    if access is None:
        return False
    access = migrate_existing_integrations(runtime, config, access)
    launch_core(runtime, config)
    dashboard_url = authenticated_dashboard_url(config, access.token)
    if not open_dashboard(dashboard_url):
        _dashboard_launch_fallback(config, access.token, dashboard_url)
    return True


def _dashboard_launch_fallback(config: CoreConfig, token: str, initial_url: str) -> None:
    """Keep a discoverable retry/copy window when the default browser rejects launch."""

    root = tkinter.Tk()
    root.title("Open All The Context")
    root.geometry("620x230")
    root.resizable(False, False)
    frame = tkinter.Frame(root, padx=24, pady=22)
    frame.pack(fill="both", expand=True)
    tkinter.Label(
        frame,
        text="Your browser did not open automatically.",
        font=("Segoe UI", 13, "bold"),
        anchor="w",
    ).pack(fill="x")
    tkinter.Label(
        frame,
        text="Try again, or copy a fresh private sign-in link and paste it into your browser.",
        font=("Segoe UI", 9),
        anchor="w",
        pady=9,
    ).pack(fill="x")
    url_value = tkinter.StringVar(value=initial_url)
    entry = tkinter.Entry(frame, textvariable=url_value, state="readonly")
    entry.pack(fill="x", pady=(4, 14))
    actions = tkinter.Frame(frame)
    actions.pack(fill="x")

    def fresh_url() -> str:
        value = authenticated_dashboard_url(config, token)
        url_value.set(value)
        return value

    def retry() -> None:
        value = fresh_url()
        if open_dashboard(value):
            root.destroy()
        else:
            copy_link()
            messagebox.showinfo(
                "Link copied",
                "The browser still did not open, so a fresh sign-in link was copied.",
                parent=root,
            )

    def copy_link() -> None:
        value = fresh_url()
        root.clipboard_clear()
        root.clipboard_append(value)
        root.update()

    tkinter.Button(actions, text="Try browser again", command=retry, padx=14, pady=7).pack(
        side="left"
    )
    tkinter.Button(actions, text="Copy private link", command=copy_link, padx=14, pady=7).pack(
        side="left", padx=(10, 0)
    )
    root.mainloop()


def _schedule_windows_install_removal(install_dir: Path) -> None:
    target = install_dir.resolve(strict=True)
    expected = windows_install_directory().resolve(strict=True)
    if target != expected or len(target.parts) < 4:
        raise RuntimeError("refusing to remove an unexpected installation directory")
    environment = os.environ.copy()
    environment["ATC_UNINSTALL_DIR"] = str(target)
    environment["ATC_UNINSTALL_PID"] = str(os.getpid())
    script = (
        "$atcProcessId=[int]$env:ATC_UNINSTALL_PID;"
        "Wait-Process -Id $atcProcessId -ErrorAction SilentlyContinue;"
        # A frozen one-file executable has an outer bootloader process around
        # the Python child.  The child can be gone while the bootloader still
        # has the installed executable open, so one removal attempt can leave
        # only AllTheContext.exe behind.  Retry for a bounded period while the
        # final process and transient antivirus/indexer handles unwind.
        "for($atcAttempt=0;$atcAttempt -lt 300;$atcAttempt++){"
        "if(-not (Test-Path -LiteralPath $env:ATC_UNINSTALL_DIR)){exit 0};"
        "try{"
        "Remove-Item -LiteralPath $env:ATC_UNINSTALL_DIR -Recurse -Force "
        "-ErrorAction Stop;"
        "if(-not (Test-Path -LiteralPath $env:ATC_UNINSTALL_DIR)){exit 0}"
        "}catch{};"
        "Start-Sleep -Milliseconds 100"
        "};"
        "exit 1"
    )
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        # The real Start Menu uninstall shortcut starts inside install_dir.
        # A process cannot remove its own current directory on Windows, so the
        # detached cleanup helper must explicitly run from the stable parent.
        cwd=target.parent,
        env=environment,
    )


def _uninstall(runtime: RuntimeCommand, *, unattended: bool = False) -> int:
    if platform.system() != "Windows" or not getattr(sys, "frozen", False):
        raise RuntimeError("The graphical uninstaller is available in the Windows package")
    accepted = unattended or messagebox.askyesno(
        "Uninstall All The Context",
        "Remove the app, shortcuts, startup entry, and AI app connections?\n\n"
        "The hosted Edge will revoke access and remove active records before disconnecting. "
        "You must still delete its service, disk, and provider backups. Your local context "
        "data is kept.",
    )
    if not accepted:
        return 0
    config = CoreConfig.default()
    try:
        edge_connections = EdgeConnectionStore(config)
        # The credential and public state are stored independently. Always let
        # the decommission routine inspect both so an interrupted setup cannot
        # make uninstall silently skip an orphaned Edge credential.
        decommission_edge_connection(edge_connections)
    except Exception as exc:
        if unattended:
            raise RuntimeError(
                "Hosted Edge could not be verified and decommissioned; nothing was uninstalled"
            ) from exc
        forget_confirmed = messagebox.askyesno(
            "Hosted Edge could not be decommissioned",
            f"{exc}\n\n"
            "All The Context cannot prove that the hosted service removed its active "
            "data and access.\n\n"
            "Continue ONLY if you already deleted the hosted Edge service, its persistent "
            "disk, and any provider backups. This will forget the local recovery credential "
            "but cannot erase anything still held by the hosting provider.",
        )
        if not forget_confirmed:
            messagebox.showerror(
                "Nothing was uninstalled",
                "The app and local Edge recovery information were kept. Reconnect Edge, "
                "or delete the hosted service and rerun uninstall.",
            )
            return 1
        try:
            edge_connections.reset()
        except Exception as reset_error:
            if unattended:
                raise RuntimeError(
                    "Local Edge recovery information could not be cleared; nothing was uninstalled"
                ) from reset_error
            messagebox.showerror(
                "Nothing was uninstalled",
                f"The local Edge recovery information could not be cleared: {reset_error}",
            )
            return 1
    try:
        _stop_installed_core_for_upgrade()
        # These edits are part of uninstall's integrity boundary.  If a client
        # configuration is malformed or locked, keep the application in place
        # so the user can repair the file and retry instead of leaving a stale
        # MCP command that points at an executable we just removed.
        client_cleanup = plan_managed_client_cleanup()
        configured_client_storages: dict[str, str] = {}
        for cleanup in client_cleanup:
            client_id = cleanup.managed_client_id
            storage = cleanup.credential_storage
            if not client_id or not storage:
                continue
            previous = configured_client_storages.get(client_id)
            if previous is None or storage == FALLBACK_CREDENTIAL_STORAGE:
                configured_client_storages[client_id] = storage
        database_readable = _retire_installed_ai_clients(config, configured_client_storages)
        apply_managed_client_cleanup(client_cleanup)
        remove_user_startup()
        remove_application_entrypoints()
        _schedule_windows_install_removal(runtime.executable.parent)
    except Exception as exc:
        raise RuntimeError(
            "Local uninstall cleanup did not finish. The installed files and local vault "
            f"were kept so the operation can be retried. Cleanup error: {exc}"
        ) from exc
    if not unattended:
        messagebox.showinfo(
            "All The Context was uninstalled",
            f"Your local context remains in:\n{config.data_dir}"
            + (
                "\n\nThe retained Core database could not be read, so its internal AI client "
                "rows could not be revoked. Current Codex/Claude configuration and stored "
                "credentials were removed. Repair or delete the retained data before restoring it."
                if not database_readable
                else ""
            ),
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="All The Context")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--core", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--mcp-stdio", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--setup", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--diagnostics", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--headless-setup", metavar="REPORT_PATH", help=argparse.SUPPRESS)
    mode.add_argument(
        "--packaged-smoke-uninstall",
        metavar="REPORT_PATH",
        help=argparse.SUPPRESS,
    )
    mode.add_argument("--uninstall", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--vault-name", default="My Context", help=argparse.SUPPRESS)
    parser.add_argument("--timezone", help=argparse.SUPPRESS)
    parser.add_argument("--no-codex", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-claude", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)
    return parser


def _run_graphical(args: argparse.Namespace) -> int:
    runtime = RuntimeCommand.current()
    if args.uninstall:
        return _uninstall(runtime)

    # Relaunch normally: an existing vault opens immediately, while a true
    # first run naturally falls through to the setup wizard.
    installed, relaunched = prepare_installed_runtime(runtime, relaunch_args=())
    if relaunched:
        return 0

    if not args.setup and _open_existing(installed):
        return 0
    from .wizard import run_setup_wizard

    run_setup_wizard(installed)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.core:
        from .core.app import main as core_main

        core_main()
        return 0
    if args.mcp_stdio:
        from .mcp_adapter import main as mcp_main

        mcp_main()
        return 0
    if args.diagnostics:
        write_diagnostics(args.diagnostics)
        return 0
    if args.headless_setup:
        return _headless_setup(args, RuntimeCommand.current())
    if args.packaged_smoke_uninstall:
        if os.environ.get("ATC_PACKAGED_SMOKE") != "1":
            raise RuntimeError("Packaged smoke uninstall is disabled")
        report_path = Path(args.packaged_smoke_uninstall).expanduser().resolve()
        result = _uninstall(RuntimeCommand.current(), unattended=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"uninstalled": result == 0, "vault_preserved": True}) + "\n",
            encoding="utf-8",
        )
        return result

    while True:
        try:
            return _run_graphical(args)
        except Exception as exc:
            if not _offer_graphical_retry(exc):
                return 1


if __name__ == "__main__":
    raise SystemExit(main())

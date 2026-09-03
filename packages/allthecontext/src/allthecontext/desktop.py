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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox
from typing import Any, Literal

from platformdirs import user_data_path

from . import __version__
from .application_install import (
    install_application_entrypoints,
    remove_application_entrypoints,
)
from .client_config import apply_managed_client_cleanup, plan_managed_client_cleanup
from .config import CoreConfig
from .credentials import FALLBACK_CREDENTIAL_STORAGE, verify_isolated_os_credential_round_trip
from .desktop_runtime import RuntimeCommand
from .desktop_setup import (
    CLAUDE_CLIENT_NAME,
    CLAUDE_CODE_CAPTURE_CLIENT_NAME,
    CLAUDE_CODE_CLIENT_NAME,
    CODEX_CAPTURE_CLIENT_NAME,
    CODEX_CLIENT_NAME,
    CODEX_EXPLICIT_CLIENT_NAME,
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
from .hermes_config import HERMES_CAPTURE_CLIENT_NAME, HERMES_READ_CLIENT_NAME
from .instance_identity import IDENTITY_FILENAME
from .macos_bundle import MacOSBundleError, macos_bundle_fingerprint, validate_macos_bundle_links
from .models import ClientCreate
from .platform_compat import windows_creation_flags
from .storage import CoreStore, StorageError
from .user_startup import remove_user_startup

WINDOWS_APP_NAME = "AllTheContext.exe"
WINDOWS_MCP_NAME = "AllTheContextMCP.exe"
WINDOWS_RECOVERY_NAME = "AllTheContextRecovery.exe"
WINDOWS_UPDATE_HELPER_NAME = "AllTheContextUpdater.exe"
WINDOWS_INSTALL_REMOVAL_ATTEMPTS = 300
WINDOWS_INSTALL_REMOVAL_INTERVAL_MILLISECONDS = 100
WINDOWS_INSTALL_REMOVAL_TIMEOUT_SECONDS = (
    WINDOWS_INSTALL_REMOVAL_ATTEMPTS * WINDOWS_INSTALL_REMOVAL_INTERVAL_MILLISECONDS / 1000
)
MACOS_APP_NAME = "All The Context.app"


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
    managed_names = {
        CODEX_CLIENT_NAME,
        CODEX_CAPTURE_CLIENT_NAME,
        CODEX_EXPLICIT_CLIENT_NAME,
        CLAUDE_CLIENT_NAME,
        CLAUDE_CODE_CLIENT_NAME,
        CLAUDE_CODE_CAPTURE_CLIENT_NAME,
        HERMES_READ_CLIENT_NAME,
        HERMES_CAPTURE_CLIENT_NAME,
    }
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


def _redact_failure_message(error: Exception | str) -> str:
    """Keep setup diagnostics useful without copying known credential forms."""

    if isinstance(error, BaseException):
        message = str(error).strip() or type(error).__name__
    else:
        message = str(error).strip() or "error"
    patterns = (
        (r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[redacted]"),
        (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]"),
        (r"atc-edge-v1\.[A-Za-z0-9_-]+", "[redacted Edge enrollment]"),
        (
            r"(?i)((?:token|secret|recovery[_ -]?code|password|api[_-]?key|"
            r"atc_client_token|client_token)\s*[=:]\s*)[^\s,;\"']+",
            r"\1[redacted]",
        ),
        (r"(?i)([?&](?:ticket|atc_token|token)=)[^&\s\"']+", r"\1[redacted]"),
        (r"https?://[^\s\"']+", "[redacted url]"),
        (r"[A-Za-z]:\\[^\s\"']+", "[redacted path]"),
        (r"/(?:Users|home|tmp|var|opt|private)[^\s\"']*", "[redacted path]"),
        (
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            "[redacted id]",
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
        # Keep the spelling intact until the bootstrap transaction has
        # lstat-validated every existing parent.  Resolving here could turn a
        # junction or symlink into an apparently trusted install root.
        return Path(os.path.abspath(os.fspath(Path(configured).expanduser())))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (
            Path(os.path.abspath(os.fspath(Path(local_app_data).expanduser())))
            / "Programs"
            / "All The Context"
        )
    data_path = Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
    return data_path.parent / "Programs" / "All The Context"


def _macos_install_location() -> tuple[Path, Path]:
    configured = os.environ.get("ATC_INSTALL_DIR")
    if configured:
        configured_path = Path(os.path.abspath(Path(configured).expanduser()))
        configured_is_bundle = configured_path.suffix.casefold() == ".app"
        base = (configured_path.parent if configured_is_bundle else configured_path).resolve(
            strict=False
        )
        return base / (configured_path.name if configured_is_bundle else MACOS_APP_NAME), base
    home = Path.home().resolve(strict=True)
    return home / "Applications" / MACOS_APP_NAME, home


def macos_install_bundle() -> Path:
    return _macos_install_location()[0]


def _macos_source_bundle(executable: Path) -> Path:
    resolved = executable.resolve(strict=True)
    for candidate in (resolved, *resolved.parents):
        if candidate.suffix.casefold() == ".app" and candidate.is_dir():
            return candidate
    raise RuntimeError("The packaged macOS application bundle is incomplete")


def _validate_macos_install_target(target: Path, *, trusted_base: Path) -> None:
    """Refuse link redirection or non-directory components in the install path."""

    absolute = Path(os.path.abspath(target.expanduser()))
    base = Path(os.path.abspath(trusted_base.expanduser()))
    if base.is_symlink():
        raise RuntimeError("The macOS trusted install base must be resolved")
    try:
        relative = absolute.relative_to(base)
    except ValueError as exc:
        raise RuntimeError("The macOS install target is outside its trusted base") from exc
    if base.exists() and not base.is_dir():
        raise RuntimeError("The macOS install base is not a directory")
    current = base
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise RuntimeError("The macOS install path contains a symbolic link")
        if current.exists() and not current.is_dir():
            raise RuntimeError("The macOS install path contains a non-directory entry")


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


def _copy_macos_bundle_atomically(source: Path, target: Path, *, trusted_base: Path) -> None:
    """Replace a per-user app bundle while preserving the prior copy on failure."""

    source = source.resolve(strict=True)
    target = Path(os.path.abspath(target.expanduser()))
    _validate_macos_install_target(target, trusted_base=trusted_base)
    target.parent.mkdir(parents=True, exist_ok=True)
    _validate_macos_install_target(target, trusted_base=trusted_base)
    nonce = secrets.token_hex(6)
    staged = target.with_name(f"{target.name}.{nonce}.atc-new")
    backup = target.with_name(f"{target.name}.{nonce}.atc-old")
    try:
        # Preserve bundle-internal links exactly: changing their representation
        # can invalidate the structural macOS code seal. This is confined to
        # the Darwin .app format; Core data and authorization never depend on
        # link or POSIX permission semantics.
        validate_macos_bundle_links(source)
        if target.exists():
            validate_macos_bundle_links(target)
        shutil.copytree(source, staged, symlinks=True)
        validate_macos_bundle_links(staged)
        if target.exists():
            target.replace(backup)
        try:
            staged.replace(target)
        except OSError:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
        if backup.exists() and target.exists():
            shutil.rmtree(backup)


def _stop_installed_core_for_upgrade() -> None:
    """Release a running installed Core before replacing platform binaries."""
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


def _relaunch_installed_runtime(runtime: RuntimeCommand, relaunch_args: tuple[str, ...]) -> None:
    environment = os.environ.copy()
    # PyInstaller 6.9+ otherwise treats a same-executable child as a worker
    # sharing the current extraction. The relaunched app must own an
    # independent extraction and outlive this installer process.
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        (str(runtime.executable), *relaunch_args),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=windows_creation_flags("CREATE_NEW_PROCESS_GROUP"),
    )


def _prepare_macos_runtime(
    runtime: RuntimeCommand, *, relaunch_args: tuple[str, ...] | None
) -> tuple[RuntimeCommand, bool]:
    source_bundle = _macos_source_bundle(runtime.executable)
    target_bundle, trusted_base = _macos_install_location()
    _validate_macos_install_target(target_bundle, trusted_base=trusted_base)
    if source_bundle.resolve() == target_bundle.resolve():
        return runtime, False
    if runtime.mcp_executable is None or not runtime.mcp_executable.is_file():
        raise RuntimeError("The packaged MCP helper is missing. Download the app again.")
    if runtime.recovery_executable is None or not runtime.recovery_executable.is_file():
        raise RuntimeError("The packaged recovery helper is missing. Download the app again.")
    try:
        executable_relative = runtime.executable.resolve(strict=True).relative_to(source_bundle)
        helper_relative = runtime.mcp_executable.resolve(strict=True).relative_to(source_bundle)
        recovery_relative = runtime.recovery_executable.resolve(strict=True).relative_to(
            source_bundle
        )
    except ValueError as exc:
        raise RuntimeError("The packaged macOS helper is outside its application bundle") from exc

    target_executable = target_bundle / executable_relative
    target_helper = target_bundle / helper_relative
    target_recovery = target_bundle / recovery_relative
    current = False
    if target_executable.is_file() and target_helper.is_file() and target_recovery.is_file():
        try:
            current = macos_bundle_fingerprint(source_bundle) == macos_bundle_fingerprint(
                target_bundle
            )
        except (MacOSBundleError, OSError):
            current = False
    if not current:
        if target_executable.is_file():
            _stop_installed_core_for_upgrade()
        _copy_macos_bundle_atomically(source_bundle, target_bundle, trusted_base=trusted_base)
    if (
        not target_executable.is_file()
        or not target_helper.is_file()
        or not target_recovery.is_file()
    ):
        raise RuntimeError("The per-user macOS application copy is incomplete")
    installed = RuntimeCommand(
        target_executable,
        mcp_executable=target_helper,
        recovery_executable=target_recovery,
    )
    if relaunch_args is not None:
        _relaunch_installed_runtime(installed, relaunch_args)
        return installed, True
    return installed, False


def prepare_installed_runtime(
    runtime: RuntimeCommand,
    *,
    relaunch_args: tuple[str, ...] | None,
) -> tuple[RuntimeCommand, bool]:
    """Install frozen platform bundles per-user and optionally relaunch the stable copy."""
    if not getattr(sys, "frozen", False):
        return runtime, False
    system = platform.system()
    if system == "Darwin":
        return _prepare_macos_runtime(runtime, relaunch_args=relaunch_args)
    if system != "Windows":
        return runtime, False

    from .windows_bootstrap_install import (
        bootstrap_journal_root,
        canonical_targets,
        install_windows_components,
    )

    helper_source = runtime.mcp_executable
    if helper_source is None or not helper_source.is_file():
        raise RuntimeError("The packaged MCP helper is missing. Download the installer again.")
    recovery_source = runtime.recovery_executable
    if recovery_source is None or not recovery_source.is_file():
        raise RuntimeError("The packaged recovery helper is missing. Download the installer again.")
    update_source = runtime.update_executable
    if update_source is None or not update_source.is_file():
        raise RuntimeError("The packaged update helper is missing. Download the installer again.")

    install_dir = windows_install_directory()
    sources = {
        "main": runtime.executable,
        "mcp": helper_source,
        "recovery": recovery_source,
        "updater": update_source,
    }
    targets = canonical_targets(install_dir)

    # Probe only for lifecycle bookkeeping.  The stop routine independently
    # authenticates and waits for Core to exit before the first target replace.
    core_was_running = probe_core(CoreConfig.default()) is not CoreProbe.UNREACHABLE

    def restart_prior_core() -> None:
        prior_runtime = RuntimeCommand(
            targets["main"],
            mcp_executable=targets["mcp"],
            update_executable=targets["updater"],
            recovery_executable=targets["recovery"],
        )
        _relaunch_installed_runtime(prior_runtime, ("--core",))

    result = install_windows_components(
        sources,
        install_dir,
        core_was_running=core_was_running,
        stop_core=_stop_installed_core_for_upgrade,
        restart_core=restart_prior_core,
        journal_root=bootstrap_journal_root(install_dir),
    )
    app_target = result.targets["main"]
    helper_target = result.targets["mcp"]
    recovery_target = result.targets["recovery"]
    update_target = result.targets["updater"]
    if runtime.executable != app_target:
        # Shortcut/registry registration is intentionally outside the binary
        # transaction and runs only after its complete commit.
        install_application_entrypoints(app_target)
    installed = RuntimeCommand(
        app_target,
        mcp_executable=helper_target,
        update_executable=update_target,
        recovery_executable=recovery_target,
    )

    if runtime.executable != app_target and relaunch_args is not None:
        _relaunch_installed_runtime(installed, relaunch_args)
        return installed, True
    return installed, False


def _dashboard_exposes_import_operations(package_root: Path) -> bool:
    """True when committed packaged dashboard assets reference durable import ops."""

    web_root = package_root / "web"
    if not (web_root / "index.html").is_file():
        return False
    needles = ("import-operations", "importOperations", "startImportOperation")
    for path in web_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in {".js", ".html", ".css", ".map"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(needle in text for needle in needles):
            return True
    return False


def diagnostics() -> dict[str, Any]:
    from .updater import UpdateConfig

    package_root = Path(__file__).resolve().parent
    core_migrations = package_root / "migrations" / "core"
    relay_migrations = package_root / "migrations" / "relay"
    core_migration_names = sorted(path.name for path in core_migrations.glob("*.sql"))
    relay_migration_names = sorted(path.name for path in relay_migrations.glob("*.sql"))
    runtime = RuntimeCommand.current()
    update_config = UpdateConfig.default()
    system = platform.system()
    return {
        "application": "All The Context",
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "distribution_trust": (
            "unsigned-community" if getattr(sys, "frozen", False) else "source-development"
        ),
        "platform": system,
        "python": platform.python_version(),
        "tk": tkinter.TkVersion,
        "core_migrations": len(core_migration_names),
        "core_migration_names": core_migration_names,
        "relay_migrations": len(relay_migration_names),
        "import_operations_migration": "009_import_operations.sql" in core_migration_names,
        "dashboard_bundled": (package_root / "web" / "index.html").is_file(),
        "dashboard_import_operations": _dashboard_exposes_import_operations(package_root),
        "update_keyring_bundled": (package_root / "update_keys.json").is_file(),
        "update_helper_bundled": runtime.update_executable is not None,
        "update_channels": sorted(update_config.manifest_urls),
        "mcp_helper_bundled": runtime.mcp_executable is not None,
        "mcp_stdio_available": runtime.mcp_executable is not None or system == "Linux",
        "recovery_admin_mode": True,
        "recovery_helper_bundled": (
            runtime.recovery_executable is not None
            if system in {"Windows", "Darwin"}
            else True  # Linux recovery modes attach to the console main binary
        ),
        "recovery_console_helper": (
            runtime.recovery_executable.name
            if runtime.recovery_executable is not None
            else ("all-the-context" if system == "Linux" else None)
        ),
        "recovery_python_checkout_required": False,
        "core_data_directory": str(CoreConfig.default().data_dir),
    }


def _valid_update_operation() -> str:
    operation = os.environ.get("ATC_UPDATE_OPERATION", "")
    if len(operation) != 24 or any(character not in "0123456789abcdef" for character in operation):
        raise RuntimeError("The update transaction identity is invalid")
    return operation


def _update_report_path(value: str, operation: str, expected_name: str) -> Path:
    target = Path(value).expanduser().resolve()
    root = CoreConfig.default().data_dir / "updates" / "transactions" / operation
    expected = (root / expected_name).resolve()
    if target != expected:
        raise RuntimeError("The update report path is invalid for this transaction")
    return target


def _apply_packaged_update(report_value: str) -> int:
    if platform.system() != "Windows" or not getattr(sys, "frozen", False):
        raise RuntimeError("Packaged update application is available only on Windows")
    operation = _valid_update_operation()
    report_path = _update_report_path(report_value, operation, "apply-report.json")
    installed, _ = prepare_installed_runtime(RuntimeCommand.current(), relaunch_args=None)
    install_application_entrypoints(installed.executable)
    app_digest, app_size = _file_digest(installed.executable)
    helper = installed.mcp_executable
    if helper is None or not helper.is_file():
        raise RuntimeError("The installed MCP helper is unavailable after update")
    helper_digest, helper_size = _file_digest(helper)
    update_helper = installed.update_executable
    if update_helper is None or not update_helper.is_file():
        raise RuntimeError("The installed update helper is unavailable after update")
    update_helper_digest, update_helper_size = _file_digest(update_helper)
    recovery = installed.recovery_executable
    if recovery is None or not recovery.is_file():
        raise RuntimeError("The installed recovery helper is unavailable after update")
    recovery_digest, recovery_size = _file_digest(recovery)
    payload = {
        "status": "installed",
        "version": __version__,
        "application": str(installed.executable),
        "application_sha256": app_digest,
        "application_size": app_size,
        "mcp": str(helper),
        "mcp_sha256": helper_digest,
        "mcp_size": helper_size,
        "recovery": str(recovery),
        "recovery_sha256": recovery_digest,
        "recovery_size": recovery_size,
        "update_helper": str(update_helper),
        "update_helper_sha256": update_helper_digest,
        "update_helper_size": update_helper_size,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f"{report_path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(report_path)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def write_diagnostics(path: Path) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(json.dumps(diagnostics(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _run_silent_internal_mode(operation: Callable[[], int | None]) -> int:
    """Keep windowed updater child failures on the helper's exit-code boundary."""

    try:
        result = operation()
    except Exception:
        # PyInstaller's windowed bootloader otherwise presents a blocking
        # "Unhandled exception" dialog. The updater owns the user-facing,
        # content-free failure state and rollback decision.
        return 1
    return 0 if result is None else result


def _run_packaged_update_health_check(report_value: str) -> int:
    operation = _valid_update_operation()
    report_path = _update_report_path(report_value, operation, "health.json")
    from .core.app import run_update_health_check

    return run_update_health_check(report_path)


def _packaged_credential_acceptance(report_value: str) -> int:
    if (
        os.environ.get("ATC_PACKAGED_SMOKE") != "1"
        or not getattr(sys, "frozen", False)
        or platform.system() not in {"Windows", "Darwin"}
    ):
        raise RuntimeError("Packaged OS credential acceptance is disabled")
    verify_isolated_os_credential_round_trip()
    report = Path(report_value).expanduser().resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "platform": platform.system(),
                "os_credential": "round-trip-and-delete-passed",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def _packaged_provider_acceptance(args: argparse.Namespace) -> int:
    """Run a real provider import inside the shipped desktop binary."""

    smoke_ok = os.environ.get("ATC_PACKAGED_SMOKE") == "1"
    if not getattr(sys, "frozen", False) and not smoke_ok:
        raise RuntimeError("Packaged provider acceptance requires a frozen package")
    if args.provider_accept_provider is None or args.provider_accept_export is None:
        raise RuntimeError("provider and export are required for packaged provider acceptance")
    from .packaged_provider_acceptance import run_packaged_provider_acceptance

    data_dir_value = os.environ.get("ATC_CORE_DATA_DIR")
    data_dir = Path(data_dir_value).expanduser() if data_dir_value else None
    return run_packaged_provider_acceptance(
        report_path=Path(args.packaged_provider_acceptance),
        export_path=args.provider_accept_export,
        provider=args.provider_accept_provider,
        data_dir=data_dir,
    )


def _headless_setup_error_code(error: Exception) -> str:
    """Map arbitrary setup failures to the closed automation diagnostic vocabulary."""

    message = str(error).casefold()
    if "credential store" in message or "credential storage" in message:
        return "credential_store_unavailable"
    if isinstance(error, OSError):
        return "setup_io_error"
    if isinstance(error, ValueError):
        return "setup_invalid_value"
    return "setup_failed"


def _write_headless_setup_failure_report(target: Path, error: Exception) -> Path | None:
    """Write a redacted headless failure report when the windowed app has no console."""

    error_code = _headless_setup_error_code(error)
    # The general graphical diagnostic path accepts a human-facing exception
    # message. Headless setup is automation-facing, so persist only a closed
    # code even if a lower layer accidentally embeds a token, path, or imported
    # text in its exception.
    diagnostics_path = _write_failure_diagnostics(RuntimeError(error_code))
    report: dict[str, Any] = {
        "setup": "failed",
        "error_type": type(error).__name__
        if type(error).__name__ in {"RuntimeError", "OSError", "ValueError"}
        else "Exception",
        "error_code": error_code,
        # Never embed absolute developer paths; only a presence/basename signal.
        "diagnostics_written": diagnostics_path is not None,
        "diagnostics_name": diagnostics_path.name if diagnostics_path is not None else None,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.atc-new")
        try:
            temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
    except OSError:
        return None


def _headless_claude_code_result(result: object) -> dict[str, Any] | None:
    """Project the Claude Code result without returning its user-file paths."""

    configured = getattr(result, "claude_code", None)
    if configured is None:
        return None
    payload = {
        "client": "Claude Code",
        "changed": bool(getattr(configured, "changed", False)),
        "mcp_changed": bool(getattr(configured, "mcp_changed", False)),
        "settings_changed": bool(getattr(configured, "settings_changed", False)),
    }
    if hasattr(result, "continuous_capture_clients"):
        payload["continuous_capture"] = "Claude Code" in getattr(
            result, "continuous_capture_clients", ()
        )
    return payload


def _headless_claude_code_explicit_result(result: object) -> dict[str, Any] | None:
    """Project explicit-command setup without returning user-file paths."""

    configured = getattr(result, "claude_code_explicit", None)
    if configured is None:
        return None
    return {
        "client": "Claude Code explicit commands",
        "changed": bool(getattr(configured, "changed", False)),
        "mcp_changed": bool(getattr(configured, "mcp_changed", False)),
        "settings_changed": bool(getattr(configured, "settings_changed", False)),
        "skill_changed": bool(getattr(configured, "skill_changed", False)),
    }


def _headless_setup(args: argparse.Namespace, runtime: RuntimeCommand) -> int:
    target = Path(args.headless_setup).expanduser().resolve()
    try:
        installed, _ = prepare_installed_runtime(runtime, relaunch_args=None)
        setup_kwargs: dict[str, Any] = {
            "vault_name": args.vault_name,
            "timezone": args.timezone or local_timezone(),
            "configure_codex": not args.no_codex,
            "configure_claude": not args.no_claude,
            "configure_claude_code": args.configure_claude_code,
            "start_at_login": not args.no_startup,
            "workspace_root": args.workspace_root,
            "workspace_local_only_acknowledged": args.acknowledge_local_workspace,
        }
        if args.configure_claude_code_explicit_commands:
            setup_kwargs["configure_claude_code_explicit_commands"] = True
        if getattr(args, "configure_codex_continuous_capture", False):
            setup_kwargs["configure_codex_continuous_capture"] = True
        if getattr(args, "configure_codex_explicit_commands", False):
            setup_kwargs["configure_codex_explicit_commands"] = True
        if getattr(args, "configure_claude_code_continuous_capture", False):
            setup_kwargs["configure_claude_code_continuous_capture"] = True
        if getattr(args, "configure_hermes", False):
            setup_kwargs["configure_hermes"] = True
        if getattr(args, "configure_hermes_continuous_capture", False):
            setup_kwargs["configure_hermes_continuous_capture"] = True
        if getattr(args, "hermes_profile", None):
            setup_kwargs["hermes_profile"] = args.hermes_profile
        result = perform_setup(SetupOptions(**setup_kwargs), installed)
        report = asdict(result)
        for field_name in (
            "workspace_root",
            "workspace_local_only_acknowledged",
            "acknowledge_local_workspace",
        ):
            report.pop(field_name, None)
        report["setup"] = "passed"
        report["log_path"] = str(result.log_path)
        report["codex"] = asdict(result.codex) if result.codex else None
        report["claude"] = asdict(result.claude) if result.claude else None
        report["claude_code"] = _headless_claude_code_result(result)
        report["claude_code_explicit"] = _headless_claude_code_explicit_result(result)
        hermes_result = getattr(result, "hermes", None)
        report["hermes"] = asdict(hermes_result) if hermes_result else None
        report["startup"] = asdict(result.startup) if result.startup else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        # Windowed Windows packages have no console; persist a redacted report so
        # packaged smoke and operators can diagnose fail-closed setup without
        # relying on hidden stderr.
        report_path = _write_headless_setup_failure_report(target, exc)
        error_code = _headless_setup_error_code(exc)
        if report_path is not None:
            print(
                f"Headless setup failed: {error_code}\nReport: {report_path.name}",
                file=sys.stderr,
            )
        else:
            print(f"Headless setup failed: {error_code}", file=sys.stderr)
        return 1


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
        f"for($atcAttempt=0;$atcAttempt -lt {WINDOWS_INSTALL_REMOVAL_ATTEMPTS};"
        "$atcAttempt++){"
        "if(-not (Test-Path -LiteralPath $env:ATC_UNINSTALL_DIR)){exit 0};"
        "try{"
        "Remove-Item -LiteralPath $env:ATC_UNINSTALL_DIR -Recurse -Force "
        "-ErrorAction Stop;"
        "if(-not (Test-Path -LiteralPath $env:ATC_UNINSTALL_DIR)){exit 0}"
        "}catch{};"
        f"Start-Sleep -Milliseconds {WINDOWS_INSTALL_REMOVAL_INTERVAL_MILLISECONDS}"
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
        creationflags=windows_creation_flags("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"),
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
    mode.add_argument("--hermes-hook", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--setup", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--diagnostics", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--headless-setup", metavar="REPORT_PATH", help=argparse.SUPPRESS)
    mode.add_argument(
        "--packaged-credential-acceptance",
        metavar="REPORT_PATH",
        help=argparse.SUPPRESS,
    )
    mode.add_argument(
        "--packaged-provider-acceptance",
        metavar="REPORT_PATH",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--provider-accept-provider",
        choices=["chatgpt", "claude", "grok"],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--provider-accept-export",
        type=Path,
        help=argparse.SUPPRESS,
    )
    mode.add_argument("--apply-update", metavar="REPORT_PATH", help=argparse.SUPPRESS)
    mode.add_argument("--update-health-check", metavar="REPORT_PATH", help=argparse.SUPPRESS)
    mode.add_argument(
        "--packaged-smoke-uninstall",
        metavar="REPORT_PATH",
        help=argparse.SUPPRESS,
    )
    mode.add_argument("--uninstall", action="store_true", help=argparse.SUPPRESS)
    # Packaged recovery/admin (B-109): deliberately hidden native modes.
    mode.add_argument("--recovery-help", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--recovery-export", metavar="DESTINATION", help=argparse.SUPPRESS)
    mode.add_argument("--recovery-restore", metavar="SOURCE", help=argparse.SUPPRESS)
    mode.add_argument("--recovery-rollback", metavar="ROLLBACK_DIR", help=argparse.SUPPRESS)
    mode.add_argument(
        "--recovery-purge",
        nargs=2,
        metavar=("TYPE", "ID"),
        help=argparse.SUPPRESS,
    )
    mode.add_argument("--recovery-purge-resume", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--recovery-doctor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--vault-name", default="My Context", help=argparse.SUPPRESS)
    parser.add_argument("--timezone", help=argparse.SUPPRESS)
    parser.add_argument(
        "--hermes-role",
        choices=("read", "capture"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--hermes-client-id", help=argparse.SUPPRESS)
    parser.add_argument("--hermes-target-url", help=argparse.SUPPRESS)
    parser.add_argument("--hermes-core-data-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--hermes-core-command", help=argparse.SUPPRESS)
    parser.add_argument("--no-codex", action="store_true", help=argparse.SUPPRESS)
    codex_capture = parser.add_mutually_exclusive_group()
    codex_capture.add_argument(
        "--codex-continuous-capture",
        dest="configure_codex_continuous_capture",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    codex_capture.add_argument(
        "--no-codex-continuous-capture",
        dest="configure_codex_continuous_capture",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_codex_continuous_capture=False)
    codex_explicit = parser.add_mutually_exclusive_group()
    codex_explicit.add_argument(
        "--codex-explicit-commands",
        dest="configure_codex_explicit_commands",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    codex_explicit.add_argument(
        "--no-codex-explicit-commands",
        dest="configure_codex_explicit_commands",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_codex_explicit_commands=False)
    parser.add_argument("--no-claude", action="store_true", help=argparse.SUPPRESS)
    claude_code = parser.add_mutually_exclusive_group()
    claude_code.add_argument(
        "--claude-code",
        dest="configure_claude_code",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    claude_code.add_argument(
        "--no-claude-code",
        dest="configure_claude_code",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_claude_code=False)
    claude_code_capture = parser.add_mutually_exclusive_group()
    claude_code_capture.add_argument(
        "--claude-code-continuous-capture",
        dest="configure_claude_code_continuous_capture",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    claude_code_capture.add_argument(
        "--no-claude-code-continuous-capture",
        dest="configure_claude_code_continuous_capture",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_claude_code_continuous_capture=False)
    claude_code_explicit = parser.add_mutually_exclusive_group()
    claude_code_explicit.add_argument(
        "--claude-code-explicit-commands",
        dest="configure_claude_code_explicit_commands",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    claude_code_explicit.add_argument(
        "--no-claude-code-explicit-commands",
        dest="configure_claude_code_explicit_commands",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_claude_code_explicit_commands=False)
    hermes = parser.add_mutually_exclusive_group()
    hermes.add_argument(
        "--hermes",
        dest="configure_hermes",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    hermes.add_argument(
        "--no-hermes",
        dest="configure_hermes",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_hermes=False)
    hermes_capture = parser.add_mutually_exclusive_group()
    hermes_capture.add_argument(
        "--hermes-continuous-capture",
        dest="configure_hermes_continuous_capture",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    hermes_capture.add_argument(
        "--no-hermes-continuous-capture",
        dest="configure_hermes_continuous_capture",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(configure_hermes_continuous_capture=False)
    parser.add_argument("--hermes-profile", help=argparse.SUPPRESS)
    parser.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--acknowledge-local-workspace", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--recovery-data-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--recovery-destination", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--recovery-rollback-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--recovery-confirmation", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-cutover", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery-no-compact", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--recovery-passphrase-env",
        default="ATC_EXPORT_PASSPHRASE",
        help=argparse.SUPPRESS,
    )
    return parser


def _run_recovery(args: argparse.Namespace) -> int:
    from .recovery_admin import (
        RecoveryError,
        doctor,
        dump_json,
        export_active_vault,
        passphrase_from_env,
        purge_target,
        recovery_help_text,
        restore_isolated,
        resume_purge_jobs,
        rollback_active_vault,
    )

    data_dir = args.recovery_data_dir
    try:
        if args.recovery_help:
            print(recovery_help_text())
            return 0
        if args.recovery_doctor:
            dump_json(doctor(data_dir=data_dir))
            return 0
        if args.recovery_export:
            dump_json(
                export_active_vault(
                    Path(args.recovery_export),
                    data_dir=data_dir,
                    passphrase=passphrase_from_env(args.recovery_passphrase_env),
                )
            )
            return 0
        if args.recovery_restore:
            dump_json(
                restore_isolated(
                    Path(args.recovery_restore),
                    data_dir=data_dir,
                    destination=args.recovery_destination,
                    passphrase=passphrase_from_env(args.recovery_passphrase_env),
                    dry_run=args.recovery_dry_run,
                    cutover=args.recovery_cutover,
                    rollback_path=args.recovery_rollback_path,
                )
            )
            return 0
        if args.recovery_rollback:
            dump_json(rollback_active_vault(Path(args.recovery_rollback), data_dir=data_dir))
            return 0
        if args.recovery_purge is not None:
            raw_type, target_id = args.recovery_purge
            if raw_type not in {"record", "source"}:
                raise RecoveryError("purge type must be record or source")
            target_type: Literal["record", "source"] = (
                "record" if raw_type == "record" else "source"
            )
            if not args.recovery_confirmation:
                raise RecoveryError("--recovery-confirmation is required for purge")
            dump_json(
                purge_target(
                    target_type,
                    target_id,
                    confirmation=args.recovery_confirmation,
                    data_dir=data_dir,
                    compact=not args.recovery_no_compact,
                )
            )
            return 0
        if args.recovery_purge_resume:
            dump_json(resume_purge_jobs(data_dir=data_dir))
            return 0
    except RecoveryError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - defensive packaging boundary
        print(f"Recovery failed: {error}", file=sys.stderr)
        return 1
    return 1


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
    if args.hermes_hook:
        from .hermes_hook import main as hermes_hook_main

        return hermes_hook_main(
            role=args.hermes_role,
            client_id=args.hermes_client_id,
            target_url=args.hermes_target_url,
            core_data_dir=args.hermes_core_data_dir,
            core_command=args.hermes_core_command,
        )
    if args.core:
        from .release_manifest import ManifestError
        from .windows_update_helper import (
            ensure_recovery_before_core,
            record_startup_recovery_parser_failure,
        )

        try:
            recovery_allowed = ensure_recovery_before_core()
        except ManifestError:
            record_startup_recovery_parser_failure()
            return 0
        if not recovery_allowed:
            return 0
        from .core.app import main as core_main

        core_main()
        return 0
    if args.mcp_stdio:
        from .mcp_adapter import main as mcp_main

        mcp_main()
        return 0
    if args.diagnostics:
        return _run_silent_internal_mode(lambda: write_diagnostics(args.diagnostics))
    if (
        args.recovery_help
        or args.recovery_export
        or args.recovery_restore
        or args.recovery_rollback
        or args.recovery_purge is not None
        or args.recovery_purge_resume
        or args.recovery_doctor
    ):
        return _run_recovery(args)
    if args.headless_setup:
        return _headless_setup(args, RuntimeCommand.current())
    if args.packaged_credential_acceptance:
        return _packaged_credential_acceptance(args.packaged_credential_acceptance)
    if args.packaged_provider_acceptance:
        return _packaged_provider_acceptance(args)
    if args.apply_update:
        return _run_silent_internal_mode(lambda: _apply_packaged_update(args.apply_update))
    if args.update_health_check:
        return _run_silent_internal_mode(
            lambda: _run_packaged_update_health_check(args.update_health_check)
        )
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

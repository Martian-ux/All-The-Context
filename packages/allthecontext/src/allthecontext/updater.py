"""Fail-closed native update checking, staging, handoff, and recovery.

Release metadata is untrusted until :mod:`allthecontext.release_manifest` has
verified its exact schema, trust key, signature, and version policy.  This
module deliberately keeps transport and installation behind small protocols so
the transaction can be exercised without a network or a real installation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast
from urllib.parse import urljoin, urlsplit

from platformdirs import user_data_path

from . import __version__
from .desktop_runtime import RuntimeCommand
from .release_manifest import (
    ManifestError,
    ReleaseVersion,
    load_keyring,
    sha256_file,
    verify_manifest,
)
from .windows_update_helper import (
    HelperError,
    HelperPhase,
    UpdateJournal,
    launch_recovery_helper,
    register_recovery,
    request_rollback,
    transaction_outcome,
    unregister_recovery,
)

CURRENT_VERSION = __version__
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 1
CHECK_INTERVAL = timedelta(hours=24)
MAX_CLEANUP_ENTRIES = 32
DEFAULT_BETA_MANIFEST_URL = (
    "https://martian-ux.github.io/All-The-Context/beta/windows/x86_64/manifest-v1.json"
)

Channel = Literal["stable", "beta"]


class UpdateError(RuntimeError):
    """A safe, operator-facing update failure without sensitive detail."""


class UpdateBusyError(UpdateError):
    """Another check, download, or install owns the transaction."""


class UpdatePhase(StrEnum):
    IDLE = "idle"
    DISABLED = "disabled"
    CHECKING = "checking"
    CURRENT = "current"
    AVAILABLE = "available"
    DEFERRED = "deferred"
    DOWNLOADING = "downloading"
    READY = "ready"
    INSTALLING = "installing"
    RESTART_REQUIRED = "restart_required"
    INSTALLED = "installed"
    ROLLED_BACK = "rolled_back"
    MANUAL_REQUIRED = "manual_required"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class UpdatePreferences:
    enabled: bool = True
    channel: Channel = "stable"
    deferred_version: str | None = None


@dataclass(slots=True)
class UpdateState:
    phase: UpdatePhase = UpdatePhase.IDLE
    current_version: str = CURRENT_VERSION
    offered_version: str | None = None
    mandatory: bool = False
    release_notes_url: str | None = None
    downloaded_path: str | None = None
    backup_path: str | None = None
    last_checked_at: str | None = None
    last_error: str | None = None
    operation_id: str | None = None
    transaction_path: str | None = None
    recovery_attempts: int = 0


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    path: Path
    filename: str
    size: int


@dataclass(frozen=True, slots=True)
class InstallPlan:
    artifact: Path
    target_version: str
    current_version: str
    operation_id: str
    operation_dir: Path
    transaction_dir: Path
    database_path: Path
    database_backup_path: Path
    state_path: Path
    core_host: str
    core_port: int


@dataclass(frozen=True, slots=True)
class UpdateConfig:
    data_dir: Path
    keyring_path: Path
    manifest_urls: Mapping[Channel, str]
    current_version: str = CURRENT_VERSION
    platform_name: str = field(default_factory=lambda: current_platform()[0])
    architecture: str = field(default_factory=lambda: current_platform()[1])

    @classmethod
    def default(cls) -> UpdateConfig:
        data_dir = Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
        package_keyring = Path(__file__).resolve().with_name("update_keys.json")
        platform_name, architecture = current_platform()
        urls: dict[Channel, str] = {}
        if (
            _packaged_update_runtime(platform_name)
            and platform_name == "windows"
            and architecture == "x86_64"
        ):
            try:
                keyring = load_keyring(package_keyring)
            except (ManifestError, OSError, ValueError, TypeError, json.JSONDecodeError):
                keyring = {"keys": []}
            keys = keyring.get("keys")
            if isinstance(keys, list) and any(
                isinstance(key, dict)
                and key.get("status") == "active"
                and isinstance(key.get("channels"), list)
                and "beta" in key["channels"]
                for key in keys
            ):
                urls["beta"] = DEFAULT_BETA_MANIFEST_URL
        stable = os.environ.get("ATC_UPDATE_STABLE_URL")
        beta = os.environ.get("ATC_UPDATE_BETA_URL")
        if stable:
            urls["stable"] = stable
        if beta:
            urls["beta"] = beta
        return cls(
            data_dir / "updates",
            package_keyring,
            urls,
            platform_name=platform_name,
            architecture=architecture,
        )


def _packaged_update_runtime(platform_name: str) -> bool:
    if bool(getattr(sys, "frozen", False)):
        return True
    if platform_name != "windows":
        return False
    try:
        executable = Path(sys.executable).resolve()
        helper = executable.with_name("AllTheContextUpdater.exe")
        return (
            executable.name.casefold() == "allthecontext.exe"
            and executable.is_file()
            and helper.is_file()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def current_platform() -> tuple[str, str]:
    system = platform.system()
    try:
        platform_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[system]
    except KeyError as exc:
        raise UpdateError("Automatic update checks do not support this operating system") from exc
    if struct.calcsize("P") * 8 != 64:
        raise UpdateError("Automatic update checks require a 64-bit application runtime")
    machine = platform.machine().casefold()
    if machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    elif machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    else:
        raise UpdateError("Automatic update checks do not support this CPU architecture")
    return platform_name, architecture


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class UpdateTransport(Protocol):
    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes: ...

    def stream(
        self,
        url: str,
        target: Path,
        *,
        expected_bytes: int,
        cancelled: Callable[[], bool],
    ) -> tuple[str, int]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class HttpsTransport:
    """HTTPS-only transport with bounded bodies and a pinned release-asset redirect."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _request(url: str, *, redirected_release_asset: bool = False) -> urllib.request.Request:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise UpdateError("Update endpoint has an invalid network port") from exc
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise UpdateError("Update endpoint must be HTTPS without embedded credentials")
        if redirected_release_asset:
            if (
                parsed.hostname != "release-assets.githubusercontent.com"
                or port not in {None, 443}
                or not parsed.path.startswith("/github-production-release-asset/")
                or not parsed.query
                or parsed.fragment
            ):
                raise UpdateError("Release download redirect was refused")
            return urllib.request.Request(url, headers={"User-Agent": "AllTheContext-Updater/1"})
        lowered_path = parsed.path.casefold()
        if (
            parsed.query
            or parsed.fragment
            or "/main/" in lowered_path
            or "/latest/" in lowered_path
        ):
            raise UpdateError(
                "Update endpoint must be immutable and cannot reference main or latest"
            )
        return urllib.request.Request(url, headers={"User-Agent": "AllTheContext-Updater/1"})

    @staticmethod
    def _release_asset_redirect(source_url: str, location: str | None) -> str:
        source = urlsplit(source_url)
        try:
            source_port = source.port
        except ValueError as exc:
            raise UpdateError("Release download redirect was refused") from exc
        parts = source.path.split("/")
        if (
            source.scheme != "https"
            or source.hostname != "github.com"
            or source_port not in {None, 443}
            or source.query
            or source.fragment
            or len(parts) != 7
            or parts[0] != ""
            or parts[3:5] != ["releases", "download"]
            or any(not part for part in (parts[1], parts[2], parts[5], parts[6]))
            or parts[5].casefold() == "latest"
            or not location
        ):
            raise UpdateError("Release download redirect was refused")
        redirected = urljoin(source_url, location)
        try:
            HttpsTransport._request(redirected, redirected_release_asset=True)
        except UpdateError as exc:
            raise UpdateError("Release download redirect was refused") from exc
        return redirected

    def _open(self, url: str, *, allow_release_redirect: bool = False) -> Any:
        current_url = url
        redirect_count = 0
        while True:
            try:
                response = self._opener.open(
                    self._request(
                        current_url,
                        redirected_release_asset=redirect_count > 0,
                    ),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
                # urllib uses the socket timeout for subsequent reads as well.
                raw = getattr(response, "fp", None)
                socket = getattr(getattr(raw, "raw", None), "_sock", None)
                if socket is not None:
                    socket.settimeout(READ_TIMEOUT_SECONDS)
                return response
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    if (
                        not allow_release_redirect
                        or exc.code not in {302, 307, 308}
                        or redirect_count >= MAX_REDIRECTS
                    ):
                        raise UpdateError("Update endpoint redirect was refused") from exc
                    headers = exc.headers
                    current_url = self._release_asset_redirect(
                        url, headers.get("Location") if headers is not None else None
                    )
                    redirect_count += 1
                    continue
                raise UpdateError(f"Update endpoint returned HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise UpdateError(
                    "Update endpoint could not be reached within the time limit"
                ) from exc

    @staticmethod
    def _content_length(headers: Any) -> int | None:
        declared = headers.get("Content-Length")
        if declared is None:
            return None
        try:
            value = int(declared)
        except (TypeError, ValueError, OverflowError) as exc:
            raise UpdateError("Update endpoint returned an invalid Content-Length") from exc
        if value < 0:
            raise UpdateError("Update endpoint returned an invalid Content-Length")
        return value

    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes:
        with self._open(url) as response:
            declared = self._content_length(response.headers)
            if declared is not None and declared > maximum_bytes:
                raise UpdateError("Update metadata exceeds the size limit")
            value = response.read(maximum_bytes + 1)
        if len(value) > maximum_bytes:
            raise UpdateError("Update metadata exceeds the size limit")
        return cast(bytes, value)

    def stream(
        self,
        url: str,
        target: Path,
        *,
        expected_bytes: int,
        cancelled: Callable[[], bool],
    ) -> tuple[str, int]:
        if expected_bytes <= 0 or expected_bytes > MAX_ARTIFACT_BYTES:
            raise UpdateError("Release artifact declares an unsupported size")
        digest = hashlib.sha256()
        received = 0
        try:
            with (
                self._open(url, allow_release_redirect=True) as response,
                target.open("xb") as output,
            ):
                declared = self._content_length(response.headers)
                if declared is not None and declared != expected_bytes:
                    raise UpdateError("Release download length differs from signed metadata")
                while True:
                    if cancelled():
                        raise UpdateError("Update download was cancelled")
                    chunk = response.read(min(1024 * 1024, expected_bytes - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_bytes:
                        raise UpdateError("Release download exceeded its signed size")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if received != expected_bytes:
                raise UpdateError("Release download was truncated")
            return digest.hexdigest(), received
        except BaseException:
            target.unlink(missing_ok=True)
            raise


class Installer(Protocol):
    @property
    def supported(self) -> bool: ...

    @property
    def unsupported_reason(self) -> str: ...

    def preflight(self, artifact: Path, required_bytes: int) -> None: ...

    def handoff(self, plan: InstallPlan) -> None: ...

    def rollback(self, state: UpdateState) -> None: ...

    def recovery_outcome(self, state: UpdateState) -> str | None: ...


class HealthProbe(Protocol):
    def healthy(self) -> bool: ...


class LoopbackHealthProbe:
    """Bounded post-restart proof that the local Core is serving again."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7337) -> None:
        self.url = f"http://{host}:{port}/health"

    def healthy(self) -> bool:
        try:
            request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                if cast(int, response.status) != 200:
                    return False
                value = json.loads(response.read(4097).decode("utf-8"))
            return bool(value == {"status": "ok", "component": "core"})
        except (OSError, ValueError, UnicodeError, urllib.error.URLError, json.JSONDecodeError):
            return False


class PlatformInstaller:
    """Native handoff for the artifact forms the project can safely apply."""

    def __init__(
        self,
        *,
        system: str | None = None,
        frozen: bool | None = None,
        application_path: Path | None = None,
        helper_path: Path | None = None,
        mcp_path: Path | None = None,
    ) -> None:
        self.system = system or platform.system()
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        runtime = RuntimeCommand.current()
        self.application_path = (application_path or runtime.executable).resolve()
        self.helper_path = helper_path or runtime.update_executable
        self.mcp_path = mcp_path or self.application_path.with_name("AllTheContextMCP.exe")
        self.stable_update_helper_path = self.application_path.with_name("AllTheContextUpdater.exe")

    @property
    def supported(self) -> bool:
        return bool(
            self.system == "Windows"
            and self.frozen
            and self.application_path.is_file()
            and self.helper_path is not None
            and self.helper_path.is_file()
            and self.stable_update_helper_path.is_file()
        )

    @property
    def unsupported_reason(self) -> str:
        if self.system == "Windows":
            if not self.frozen:
                return "Automatic Windows updates require the installed desktop application"
            return (
                "The installed Windows recovery helper is unavailable; reinstall the current "
                "desktop package before applying updates"
            )
        if self.system == "Darwin":
            return (
                "The verified macOS update requires a manual app replacement; this community "
                "build is not notarized"
            )
        if self.system == "Linux":
            return (
                "The verified Linux update requires a distribution-specific manual package install"
            )
        return "This platform has no safe automatic installer handoff"

    def preflight(self, artifact: Path, required_bytes: int) -> None:
        free = shutil.disk_usage(artifact.parent).free
        # Keep enough room for archive, extraction, and a retained recovery copy.
        if free < required_bytes * 3:
            raise UpdateError("Insufficient disk space to stage and recover this update")
        if self.supported and not zipfile.is_zipfile(artifact):
            raise UpdateError("The Windows release artifact is not a valid ZIP archive")

    @staticmethod
    def _extract_windows_setup(archive: Path, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=False)
        setup: Path | None = None
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            expanded = 0
            for entry in entries:
                if "\\" in entry.filename or ":" in entry.filename:
                    raise UpdateError("Release archive contains an unsafe path")
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or entry.is_dir():
                    if entry.is_dir():
                        continue
                    raise UpdateError("Release archive contains an unsafe path")
                expanded += entry.file_size
                if expanded > MAX_ARTIFACT_BYTES * 2:
                    raise UpdateError("Release archive expands beyond the safety limit")
                destination = target.joinpath(*name.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if destination.name.casefold() == "allthecontextsetup.exe":
                    if setup is not None:
                        raise UpdateError(
                            "Release archive contains multiple Windows setup programs"
                        )
                    setup = destination
        if setup is None:
            raise UpdateError("Release archive does not contain AllTheContextSetup.exe")
        return setup

    @staticmethod
    def _copy_verified(source: Path, target: Path) -> tuple[str, int]:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.atc-new")
        temporary.unlink(missing_ok=True)
        try:
            with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            digest, size = sha256_file(temporary)
            temporary.replace(target)
            return digest, size
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def handoff(self, plan: InstallPlan) -> None:
        if not self.supported:
            raise UpdateError(self.unsupported_reason)
        assert self.helper_path is not None
        recovery_registered = False
        try:
            plan.transaction_dir.mkdir(parents=True, exist_ok=False)
            setup = self._extract_windows_setup(plan.artifact, plan.operation_dir / "extracted")
            replacement = plan.transaction_dir / "replacement" / "AllTheContextSetup.exe"
            replacement_digest, replacement_size = self._copy_verified(setup, replacement)
            rollback_application = plan.transaction_dir / "rollback" / "AllTheContext.exe"
            rollback_digest, rollback_size = self._copy_verified(
                self.application_path, rollback_application
            )
            rollback_mcp: Path | None = None
            rollback_mcp_digest: str | None = None
            rollback_mcp_size: int | None = None
            if self.mcp_path.is_file():
                rollback_mcp = plan.transaction_dir / "rollback" / "AllTheContextMCP.exe"
                rollback_mcp_digest, rollback_mcp_size = self._copy_verified(
                    self.mcp_path, rollback_mcp
                )
            rollback_update_helper = plan.transaction_dir / "rollback" / "AllTheContextUpdater.exe"
            rollback_update_digest, rollback_update_size = self._copy_verified(
                self.stable_update_helper_path, rollback_update_helper
            )
            copied_helper = plan.transaction_dir / "AllTheContextUpdater.exe"
            self._copy_verified(self.helper_path, copied_helper)
            backup_digest, backup_size = sha256_file(plan.database_backup_path)
            journal_path = plan.transaction_dir / "journal.json"
            now = _utc_now()
            journal = UpdateJournal(
                operation_id=plan.operation_id,
                phase=HelperPhase.PREPARED,
                current_version=plan.current_version,
                target_version=plan.target_version,
                parent_pid=os.getpid(),
                application_path=str(self.application_path),
                replacement_path=str(replacement),
                replacement_sha256=replacement_digest,
                replacement_size=replacement_size,
                rollback_application_path=str(rollback_application),
                rollback_application_sha256=rollback_digest,
                rollback_application_size=rollback_size,
                mcp_path=str(self.mcp_path),
                rollback_mcp_path=str(rollback_mcp) if rollback_mcp else None,
                rollback_mcp_sha256=rollback_mcp_digest,
                rollback_mcp_size=rollback_mcp_size,
                stable_update_helper_path=str(self.stable_update_helper_path),
                rollback_update_helper_path=str(rollback_update_helper),
                rollback_update_helper_sha256=rollback_update_digest,
                rollback_update_helper_size=rollback_update_size,
                database_path=str(plan.database_path),
                database_backup_path=str(plan.database_backup_path),
                database_backup_sha256=backup_digest,
                database_backup_size=backup_size,
                state_path=str(plan.state_path),
                helper_path=str(copied_helper),
                core_host=plan.core_host,
                core_port=plan.core_port,
                created_at=now,
                updated_at=now,
            )
            journal.validate(journal_path)
            journal.save(journal_path)
            register_recovery(copied_helper, journal_path, plan.operation_id)
            recovery_registered = True
            launch_recovery_helper(copied_helper, journal_path)
        except (HelperError, OSError, zipfile.BadZipFile) as exc:
            if recovery_registered:
                with suppress(HelperError, OSError):
                    unregister_recovery(plan.operation_id)
            raise UpdateError("The Windows recovery transaction could not be prepared") from exc

    def rollback(self, state: UpdateState) -> None:
        if state.transaction_path is None:
            raise UpdateError("The independent Windows recovery journal is unavailable")
        try:
            request_rollback(Path(state.transaction_path))
        except (HelperError, OSError) as exc:
            raise UpdateError("The independent Windows rollback could not be requested") from exc

    def recovery_outcome(self, state: UpdateState) -> str | None:
        if state.transaction_path is None:
            return None
        return transaction_outcome(Path(state.transaction_path))


class DatabaseBackup(Protocol):
    def create(self, source: Path, target: Path) -> None: ...


class SQLiteBackup:
    """Consistent, verified SQLite backup taken before native cutover."""

    def create(self, source: Path, target: Path) -> None:
        if not source.is_file():
            raise UpdateError("Core database is unavailable for the required pre-update backup")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".atc-new")
        temporary.unlink(missing_ok=True)
        try:
            source_connection = sqlite3.connect(source, timeout=10)
            destination = sqlite3.connect(temporary)
            try:
                source_connection.execute("PRAGMA busy_timeout=10000")
                source_connection.backup(destination)
                result = destination.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise UpdateError("Pre-update database backup failed integrity verification")
            finally:
                destination.close()
                source_connection.close()
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


class UpdateManager:
    """One serialized, durable update transaction."""

    def __init__(
        self,
        config: UpdateConfig,
        *,
        database_path: Path,
        transport: UpdateTransport | None = None,
        installer: Installer | None = None,
        backup: DatabaseBackup | None = None,
        health_probe: HealthProbe | None = None,
    ) -> None:
        self.config = config
        self.database_path = database_path
        self.transport = transport or HttpsTransport()
        self.installer = installer or PlatformInstaller()
        self.backup = backup or SQLiteBackup()
        self.health_probe = health_probe or LoopbackHealthProbe()
        self._operation_gate = threading.Lock()
        self._operation_lock = threading.RLock()
        self._cancel = threading.Event()
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.preferences_path = self.config.data_dir / "preferences.json"
        self.state_path = self.config.data_dir / "state.json"
        with self._operation_lock:
            self.preferences = self._load_preferences()
            self.state = self._load_state()
            self.state.current_version = config.current_version
            self._validate_internal_state()
            self._recover_interrupted()
            self._prune_directory(self.config.data_dir / "staging", keep=self.state.operation_id)
            active_transaction = (
                Path(self.state.transaction_path).parent.name
                if self.state.transaction_path is not None
                else self.state.operation_id
            )
            self._prune_directory(self.config.data_dir / "transactions", keep=active_transaction)
            self._prune_directory(self.config.data_dir / "exports", keep=None)
            self._atomic_json(self.preferences_path, asdict(self.preferences))
            self._save()

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{path.name}.", suffix=".atc-new", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _load_preferences(self) -> UpdatePreferences:
        default_preferences = self._default_preferences()
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("enabled", True), bool):
                raise ValueError("invalid preferences")
            channel = value.get("channel")
            if channel not in {"stable", "beta"}:
                raise ValueError("invalid channel")
            deferred = value.get("deferred_version")
            if deferred is not None:
                if not isinstance(deferred, str):
                    raise ValueError("invalid deferred version")
                ReleaseVersion.parse(deferred)
            selected_channel = cast(Channel, channel)
            if (
                selected_channel not in self.config.manifest_urls
                and default_preferences.channel in self.config.manifest_urls
            ):
                selected_channel = default_preferences.channel
                deferred = None
            return UpdatePreferences(
                enabled=value.get("enabled", True),
                channel=selected_channel,
                deferred_version=deferred,
            )
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return default_preferences

    def _default_preferences(self) -> UpdatePreferences:
        version = ReleaseVersion.parse(self.config.current_version)
        if version.stability == 0 and "beta" in self.config.manifest_urls:
            return UpdatePreferences(channel="beta")
        return UpdatePreferences()

    def _load_state(self) -> UpdateState:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid state")
            value["phase"] = UpdatePhase(value["phase"])
            allowed = set(UpdateState.__dataclass_fields__)
            state = UpdateState(**{key: item for key, item in value.items() if key in allowed})
            optional_strings = (
                state.offered_version,
                state.release_notes_url,
                state.downloaded_path,
                state.backup_path,
                state.last_checked_at,
                state.last_error,
                state.operation_id,
                state.transaction_path,
            )
            if any(item is not None and not isinstance(item, str) for item in optional_strings):
                raise ValueError("invalid state string")
            if not isinstance(state.current_version, str):
                raise ValueError("invalid current version")
            ReleaseVersion.parse(state.current_version)
            if state.offered_version is not None:
                ReleaseVersion.parse(state.offered_version)
            if not isinstance(state.mandatory, bool):
                raise ValueError("invalid mandatory flag")
            if (
                isinstance(state.recovery_attempts, bool)
                or not isinstance(state.recovery_attempts, int)
                or state.recovery_attempts < 0
            ):
                raise ValueError("invalid recovery attempts")
            if state.operation_id is not None and (
                len(state.operation_id) != 24
                or any(character not in "0123456789abcdef" for character in state.operation_id)
            ):
                raise ValueError("invalid operation ID")
            return state
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            if self.state_path.exists():
                return UpdateState(
                    phase=UpdatePhase.ERROR,
                    current_version=self.config.current_version,
                    last_error="Persisted update state was corrupt and was reset safely",
                )
            return UpdateState(current_version=self.config.current_version)

    def _validate_internal_state(self) -> None:
        invalid = False
        operation = self.state.operation_id
        expected_artifact = (
            self.config.data_dir / "staging" / operation / "artifact.zip"
            if operation is not None
            else None
        )
        if self.state.downloaded_path is not None:
            try:
                downloaded_path_valid = (
                    expected_artifact is not None
                    and Path(self.state.downloaded_path).resolve() == expected_artifact.resolve()
                )
            except OSError:
                downloaded_path_valid = False
            if not downloaded_path_valid:
                self.state.downloaded_path = None
                invalid = True
        if self.state.backup_path is not None:
            backup_root = (self.config.data_dir / "backups").resolve()
            try:
                Path(self.state.backup_path).resolve().relative_to(backup_root)
            except (OSError, ValueError):
                self.state.backup_path = None
                invalid = True
        if self.state.transaction_path is not None:
            transaction_root = (self.config.data_dir / "transactions").resolve()
            expected_transaction = (
                transaction_root / operation / "journal.json" if operation is not None else None
            )
            try:
                transaction_valid = (
                    expected_transaction is not None
                    and Path(self.state.transaction_path).resolve()
                    == expected_transaction.resolve()
                )
            except OSError:
                transaction_valid = False
            if not transaction_valid:
                self.state.transaction_path = None
                invalid = True
        if invalid:
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = "Persisted update paths were invalid and were reset safely"

    def _save(self) -> None:
        value = asdict(self.state)
        value["phase"] = self.state.phase.value
        self._atomic_json(self.state_path, value)

    def _require_no_active_handoff(self) -> None:
        if self.state.transaction_path is not None and self.state.phase in {
            UpdatePhase.INSTALLING,
            UpdatePhase.RESTART_REQUIRED,
        }:
            raise UpdateBusyError("The Windows recovery helper owns the active update")

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._operation_gate.acquire(blocking=False):
            raise UpdateBusyError("Another update operation is already in progress")
        try:
            with self._operation_lock:
                yield
        finally:
            self._operation_gate.release()

    def _recover_interrupted(self) -> None:
        if self.state.phase in {UpdatePhase.CHECKING, UpdatePhase.DOWNLOADING}:
            self._clean_operation()
            self.state.phase = UpdatePhase.CANCELLED
            self.state.last_error = "The interrupted update operation was safely cancelled"
            self._save()

    def recover_after_restart(self) -> dict[str, Any]:
        """Resolve a persisted handoff exactly once after the replacement starts."""

        with self._exclusive():
            if self.state.phase not in {
                UpdatePhase.INSTALLING,
                UpdatePhase.RESTART_REQUIRED,
            }:
                return self.public_status()
            recovery_outcome = self.installer.recovery_outcome(self.state)
            if recovery_outcome == "pending":
                return self.public_status()
            if recovery_outcome == "installed":
                self.state.phase = UpdatePhase.INSTALLED
                self.state.last_error = None
                self.state.transaction_path = None
                self._clean_operation()
                self._save()
                return self.public_status()
            if recovery_outcome == "rolled_back":
                self.state.phase = UpdatePhase.ROLLED_BACK
                self.state.last_error = (
                    "The update did not become healthy; the previous app and vault were restored"
                )
                self.state.transaction_path = None
                self._clean_operation()
                self._save()
                return self.public_status()
            if recovery_outcome == "failed":
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = "The Windows update recovery journal was invalid"
                self._save()
                return self.public_status()
            offered = self.state.offered_version
            try:
                version_advanced = offered is not None and (
                    ReleaseVersion.parse(self.config.current_version)
                    >= ReleaseVersion.parse(offered)
                )
            except ManifestError:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = (
                    "Persisted update recovery metadata was invalid and was reset safely"
                )
                self._save()
                return self.public_status()
            if version_advanced and self.health_probe.healthy():
                self.state.phase = UpdatePhase.INSTALLED
                self.state.last_error = None
                self.state.transaction_path = None
                self._clean_operation()
                self._save()
                return self.public_status()
            try:
                self.installer.rollback(self.state)
                self.state.phase = UpdatePhase.ROLLED_BACK
                self.state.last_error = (
                    "The new version failed its health check and was rolled back"
                )
                self.state.transaction_path = None
            except UpdateError as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = (
                    "The new version did not become healthy and automatic rollback failed: "
                    + str(exc)
                )[:500]
            self._save()
            return self.public_status()

    def _operation_directory(self) -> Path:
        operation = self.state.operation_id or "pending"
        return self.config.data_dir / "staging" / operation

    def _clean_operation(self) -> None:
        operation = self._operation_directory()
        if operation.exists():
            shutil.rmtree(operation, ignore_errors=True)
        self.state.downloaded_path = None

    @staticmethod
    def _prune_directory(root: Path, *, keep: str | None) -> None:
        """Remove at most a bounded number of private orphan entries."""

        if not root.is_dir():
            return
        removed = 0
        try:
            entries = root.iterdir()
            for entry in entries:
                if removed >= MAX_CLEANUP_ENTRIES:
                    break
                if keep is not None and entry.name == keep:
                    continue
                try:
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink(missing_ok=True)
                except OSError:
                    continue
                removed += 1
        except OSError:
            return

    def public_status(self) -> dict[str, Any]:
        with self._operation_lock:
            result = asdict(self.state)
            result["phase"] = self.state.phase.value
            result.update(
                {
                    "enabled": self.preferences.enabled,
                    "channel": self.preferences.channel,
                    "deferred_version": self.preferences.deferred_version,
                    "automatic_install_supported": self.installer.supported,
                    "verified_artifact_available": self.state.downloaded_path is not None
                    and self.state.phase in {UpdatePhase.READY, UpdatePhase.MANUAL_REQUIRED},
                    "installer_detail": (
                        "Packaged update can restart into the verified installer"
                        if self.installer.supported
                        else self.installer.unsupported_reason
                    ),
                    "configured": self.preferences.channel in self.config.manifest_urls,
                    "available_channels": sorted(self.config.manifest_urls),
                }
            )
            # Private staging and backup paths are intentionally not exposed.
            result.pop("downloaded_path", None)
            result.pop("backup_path", None)
            result.pop("operation_id", None)
            result.pop("transaction_path", None)
            return result

    def configure(self, *, enabled: bool, channel: Channel) -> dict[str, Any]:
        if channel not in {"stable", "beta"}:
            raise UpdateError("Update channel must be stable or beta")
        with self._exclusive():
            self._require_no_active_handoff()
            channel_changed = channel != self.preferences.channel
            self.preferences = UpdatePreferences(enabled, channel, None)
            self._atomic_json(self.preferences_path, asdict(self.preferences))
            if channel_changed:
                self._clean_operation()
                self._prune_directory(self.config.data_dir / "staging", keep=None)
                self.state.offered_version = None
                self.state.mandatory = False
                self.state.release_notes_url = None
                self.state.operation_id = None
                self.state.transaction_path = None
            if not enabled:
                self._cancel.set()
                self.state.phase = UpdatePhase.DISABLED
                self.state.last_error = None
            elif channel_changed or self.state.phase == UpdatePhase.DISABLED:
                self.state.phase = UpdatePhase.IDLE
                self.state.last_error = None
            self._save()
            return self.public_status()

    def defer(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            if self.state.offered_version is None:
                raise UpdateError("There is no available update to defer")
            if self.state.mandatory:
                raise UpdateError("This compatibility or security update cannot be deferred")
            self.preferences = UpdatePreferences(
                self.preferences.enabled, self.preferences.channel, self.state.offered_version
            )
            self._atomic_json(self.preferences_path, asdict(self.preferences))
            self.state.phase = UpdatePhase.DEFERRED
            self._save()
            return self.public_status()

    def clear_error(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            self.state.last_error = None
            if self.state.phase in {UpdatePhase.ERROR, UpdatePhase.CANCELLED}:
                self.state.phase = UpdatePhase.IDLE
            self._save()
            return self.public_status()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        with self._operation_gate, self._operation_lock:
            return self.public_status()

    def scheduled_check(self) -> dict[str, Any]:
        with self._operation_lock:
            last = _parse_time(self.state.last_checked_at)
            if not self.preferences.enabled or (
                last is not None and datetime.now(UTC) - last < CHECK_INTERVAL
            ):
                return self.public_status()
        return self.check(respect_defer=True)

    def check(self, *, respect_defer: bool = False) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            if not self.preferences.enabled:
                self.state.phase = UpdatePhase.DISABLED
                self._save()
                return self.public_status()
            url = self.config.manifest_urls.get(self.preferences.channel)
            if url is None:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = (
                    "No HTTPS metadata endpoint is configured for this update channel"
                )
                self._save()
                return self.public_status()
            self._cancel.clear()
            self.state.phase = UpdatePhase.CHECKING
            self.state.last_error = None
            self._save()
            try:
                raw = self.transport.get_bytes(url, maximum_bytes=MAX_MANIFEST_BYTES)
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise UpdateError("Update metadata must be a JSON object")
                manifest = cast(dict[str, Any], value)
                keyring = load_keyring(self.config.keyring_path)
                verify_manifest(
                    manifest,
                    keyring,
                    current_version=self.config.current_version,
                    expected_channel=self.preferences.channel,
                )
                if manifest["platform"] != self.config.platform_name:
                    raise UpdateError("Signed update metadata targets a different platform")
                if manifest["architecture"] != self.config.architecture:
                    raise UpdateError("Signed update metadata targets a different architecture")
                offered = cast(str, manifest["version"])
                operation_id = hashlib.sha256(raw).hexdigest()[:24]
                self._prune_directory(self.config.data_dir / "staging", keep=operation_id)
                self.state.last_checked_at = _utc_now()
                self.state.offered_version = offered
                self.state.mandatory = cast(bool, manifest["mandatory"])
                self.state.release_notes_url = cast(str, manifest["release_notes_url"])
                self.state.operation_id = operation_id
                self.state.downloaded_path = None
                self._atomic_json(self._operation_directory() / "manifest.json", manifest)
                if ReleaseVersion.parse(offered) == ReleaseVersion.parse(
                    self.config.current_version
                ):
                    self.state.phase = UpdatePhase.CURRENT
                elif (
                    respect_defer
                    and not self.state.mandatory
                    and offered == self.preferences.deferred_version
                ):
                    self.state.phase = UpdatePhase.DEFERRED
                else:
                    self.state.phase = UpdatePhase.AVAILABLE
                self._save()
                return self.public_status()
            except (
                ManifestError,
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
                UpdateError,
            ) as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = str(exc)[:500]
                self.state.last_checked_at = _utc_now()
                self._save()
                return self.public_status()

    def download(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            if self.state.phase not in {UpdatePhase.AVAILABLE, UpdatePhase.CANCELLED}:
                raise UpdateError("A verified available update is required before download")
            manifest_path = self._operation_directory() / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise UpdateError(
                    "Verified update metadata is no longer available; check again"
                ) from exc
            self._cancel.clear()
            self.state.phase = UpdatePhase.DOWNLOADING
            self.state.last_error = None
            self._save()
            target = self._operation_directory() / "artifact.zip"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            try:
                digest, received = self.transport.stream(
                    cast(str, manifest["url"]),
                    target,
                    expected_bytes=cast(int, manifest["size"]),
                    cancelled=self._cancel.is_set,
                )
                if received != manifest["size"] or digest != manifest["sha256"]:
                    raise UpdateError("Release artifact checksum does not match signed metadata")
                self.state.downloaded_path = str(target)
                self.state.phase = (
                    UpdatePhase.READY if self.installer.supported else UpdatePhase.MANUAL_REQUIRED
                )
                if not self.installer.supported:
                    self.state.last_error = self.installer.unsupported_reason
                self._save()
                return self.public_status()
            except (OSError, UpdateError) as exc:
                target.unlink(missing_ok=True)
                self.state.downloaded_path = None
                self.state.phase = (
                    UpdatePhase.CANCELLED if "cancel" in str(exc).casefold() else UpdatePhase.ERROR
                )
                self.state.last_error = str(exc)[:500]
                self._save()
                return self.public_status()

    def prepare_artifact_export(self) -> PreparedArtifact:
        """Copy a freshly re-verified staged artifact for one authenticated response."""

        with self._exclusive():
            self._require_no_active_handoff()
            if (
                self.state.phase
                not in {
                    UpdatePhase.READY,
                    UpdatePhase.MANUAL_REQUIRED,
                }
                or self.state.downloaded_path is None
            ):
                raise UpdateError("A completely verified update must be ready before saving")
            try:
                raw_manifest = json.loads(
                    (self._operation_directory() / "manifest.json").read_text(encoding="utf-8")
                )
                if not isinstance(raw_manifest, dict):
                    raise UpdateError("Verified update metadata is invalid; check again")
                manifest = cast(dict[str, Any], raw_manifest)
                verify_manifest(
                    manifest,
                    load_keyring(self.config.keyring_path),
                    current_version=self.config.current_version,
                    expected_channel=self.preferences.channel,
                )
                if manifest["platform"] != self.config.platform_name:
                    raise UpdateError("Signed update metadata targets a different platform")
                if manifest["architecture"] != self.config.architecture:
                    raise UpdateError("Signed update metadata targets a different architecture")
                if manifest["version"] != self.state.offered_version:
                    raise UpdateError("Verified update state no longer matches its metadata")
                source = Path(self.state.downloaded_path)
                expected = self._operation_directory() / "artifact.zip"
                if source.resolve() != expected.resolve() or not source.is_file():
                    raise UpdateError(
                        "Verified update artifact is no longer available; download again"
                    )
                source_digest, source_size = sha256_file(source)
                if source_size != manifest["size"] or source_digest != manifest["sha256"]:
                    raise UpdateError("Saved update artifact failed signed checksum verification")
            except (ManifestError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise UpdateError(
                    "Verified update artifact could not be re-verified; check again"
                ) from exc

            export_root = self.config.data_dir / "exports"
            export_root.mkdir(parents=True, exist_ok=True)
            descriptor, export_name = tempfile.mkstemp(suffix=".zip", dir=export_root)
            export_path = Path(export_name)
            try:
                digest = hashlib.sha256()
                copied = 0
                with (
                    source.open("rb") as input_stream,
                    os.fdopen(descriptor, "wb") as output_stream,
                ):
                    while chunk := input_stream.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > cast(int, manifest["size"]):
                            raise UpdateError("Saved update artifact exceeded its signed size")
                        digest.update(chunk)
                        output_stream.write(chunk)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                if copied != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
                    raise UpdateError("Saved update artifact failed signed checksum verification")
            except BaseException:
                with suppress(OSError):
                    os.close(descriptor)
                export_path.unlink(missing_ok=True)
                raise
            filename = (
                f"all-the-context-{manifest['version']}-{manifest['platform']}-"
                f"{manifest['architecture']}.zip"
            )
            return PreparedArtifact(export_path, filename, copied)

    def install(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            if self.state.phase != UpdatePhase.READY or self.state.downloaded_path is None:
                raise UpdateError("A completely verified update must be ready before install")
            artifact = Path(self.state.downloaded_path)
            try:
                manifest = json.loads(
                    (self._operation_directory() / "manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise UpdateError(
                    "Verified update metadata is no longer available; check again"
                ) from exc
            self._cancel.clear()
            self.state.phase = UpdatePhase.INSTALLING
            self.state.last_error = None
            self._save()
            try:
                self.installer.preflight(artifact, cast(int, manifest["size"]))
                backup_path = (
                    self.config.data_dir
                    / "backups"
                    / (f"core-{self.config.current_version}-before-{manifest['version']}.sqlite3")
                )
                self.backup.create(self.database_path, backup_path)
                self.state.backup_path = str(backup_path)
                self.state.recovery_attempts += 1
                operation_id = self.state.operation_id
                if operation_id is None:
                    raise UpdateError("Verified update transaction identity is unavailable")
                transaction_dir = self.config.data_dir / "transactions" / operation_id
                self.state.transaction_path = str(transaction_dir / "journal.json")
                self.state.phase = UpdatePhase.RESTART_REQUIRED
                self._save()
                core_host = os.environ.get("ATC_CORE_HOST", "127.0.0.1")
                try:
                    core_port = int(os.environ.get("ATC_CORE_PORT", "7337"))
                except ValueError as exc:
                    raise UpdateError("The Core port is invalid for update recovery") from exc
                self.installer.handoff(
                    InstallPlan(
                        artifact=artifact,
                        target_version=cast(str, manifest["version"]),
                        current_version=self.config.current_version,
                        operation_id=operation_id,
                        operation_dir=self._operation_directory(),
                        transaction_dir=transaction_dir,
                        database_path=self.database_path,
                        database_backup_path=backup_path,
                        state_path=self.state_path,
                        core_host=core_host,
                        core_port=core_port,
                    )
                )
                return self.public_status()
            except (OSError, ValueError, UpdateError) as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = str(exc)[:500]
                self.state.transaction_path = None
                self._save()
                return self.public_status()

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
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from platformdirs import user_data_path

from .release_manifest import ManifestError, ReleaseVersion, load_keyring, verify_manifest

CURRENT_VERSION = "0.1.0"
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 0
CHECK_INTERVAL = timedelta(hours=24)

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
    recovery_attempts: int = 0


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
        urls: dict[Channel, str] = {}
        stable = os.environ.get("ATC_UPDATE_STABLE_URL")
        beta = os.environ.get("ATC_UPDATE_BETA_URL")
        if stable:
            urls["stable"] = stable
        if beta:
            urls["beta"] = beta
        return cls(data_dir / "updates", package_keyring, urls)


def current_platform() -> tuple[str, str]:
    system = platform.system()
    platform_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        system, system.casefold()
    )
    machine = platform.machine().casefold()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
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
    """HTTPS-only transport with bounded bodies, timeouts, and no redirects."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _request(url: str) -> urllib.request.Request:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise UpdateError("Update endpoint must be HTTPS without embedded credentials")
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

    def _open(self, url: str) -> Any:
        try:
            response = self._opener.open(self._request(url), timeout=CONNECT_TIMEOUT_SECONDS)
            # urllib uses the socket timeout for subsequent reads as well.
            raw = getattr(response, "fp", None)
            socket = getattr(getattr(raw, "raw", None), "_sock", None)
            if socket is not None:
                socket.settimeout(READ_TIMEOUT_SECONDS)
            return response
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise UpdateError("Update endpoint redirect was refused") from exc
            raise UpdateError(f"Update endpoint returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError("Update endpoint could not be reached within the time limit") from exc

    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes:
        with self._open(url) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > maximum_bytes:
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
            with self._open(url) as response, target.open("xb") as output:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) != expected_bytes:
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

    def handoff(self, artifact: Path, version: str, operation_dir: Path) -> None: ...

    def rollback(self, state: UpdateState) -> None: ...


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

    def __init__(self, *, system: str | None = None, frozen: bool | None = None) -> None:
        self.system = system or platform.system()
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen

    @property
    def supported(self) -> bool:
        # The current Windows setup executable can replace a stopped app, but
        # it has no independent journaled helper capable of restoring both the
        # prior executable and database after a failed post-migration health
        # check.  Do not expose one-click install until that recovery boundary
        # exists and has been exercised as a real packaged transaction.
        return False

    @property
    def unsupported_reason(self) -> str:
        if self.system == "Windows":
            return (
                "The verified Windows update requires manual installation until the packaged "
                "installer has independent binary-and-database rollback"
            )
        if self.system == "Darwin":
            return (
                "The verified macOS update requires a signed and notarized manual app replacement"
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

    def handoff(self, artifact: Path, version: str, operation_dir: Path) -> None:
        del version
        if not self.supported:
            raise UpdateError(self.unsupported_reason)
        setup = self._extract_windows_setup(artifact, operation_dir / "extracted")
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(
            (str(setup),),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

    def rollback(self, state: UpdateState) -> None:
        del state
        raise UpdateError(
            "Automatic binary rollback is unavailable; the verified database backup is preserved"
        )


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
        self._operation_lock = threading.Lock()
        self._cancel = threading.Event()
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.preferences_path = self.config.data_dir / "preferences.json"
        self.state_path = self.config.data_dir / "state.json"
        self.preferences = self._load_preferences()
        self.state = self._load_state()
        self.state.current_version = config.current_version
        self._recover_interrupted()

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.atc-new")
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _load_preferences(self) -> UpdatePreferences:
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            channel = value.get("channel")
            if channel not in {"stable", "beta"}:
                raise ValueError("invalid channel")
            return UpdatePreferences(
                enabled=bool(value.get("enabled", True)),
                channel=cast(Channel, channel),
                deferred_version=value.get("deferred_version"),
            )
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return UpdatePreferences()

    def _load_state(self) -> UpdateState:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            value["phase"] = UpdatePhase(value["phase"])
            allowed = set(UpdateState.__dataclass_fields__)
            return UpdateState(**{key: item for key, item in value.items() if key in allowed})
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return UpdateState(current_version=self.config.current_version)

    def _save(self) -> None:
        value = asdict(self.state)
        value["phase"] = self.state.phase.value
        self._atomic_json(self.state_path, value)

    def _recover_interrupted(self) -> None:
        if self.state.phase in {UpdatePhase.CHECKING, UpdatePhase.DOWNLOADING}:
            self._clean_operation()
            self.state.phase = UpdatePhase.CANCELLED
            self.state.last_error = "The interrupted update operation was safely cancelled"
            self._save()

    def recover_after_restart(self) -> dict[str, Any]:
        """Resolve a persisted handoff exactly once after the replacement starts."""

        if self.state.phase not in {UpdatePhase.INSTALLING, UpdatePhase.RESTART_REQUIRED}:
            return self.public_status()
        offered = self.state.offered_version
        version_advanced = offered is not None and (
            ReleaseVersion.parse(self.config.current_version) >= ReleaseVersion.parse(offered)
        )
        if version_advanced and self.health_probe.healthy():
            self.state.phase = UpdatePhase.INSTALLED
            self.state.last_error = None
            self._clean_operation()
            self._save()
            return self.public_status()
        try:
            self.installer.rollback(self.state)
            self.state.phase = UpdatePhase.ROLLED_BACK
            self.state.last_error = "The new version failed its health check and was rolled back"
        except UpdateError as exc:
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = (
                "The new version did not become healthy and automatic rollback failed: " + str(exc)
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

    def public_status(self) -> dict[str, Any]:
        result = asdict(self.state)
        result["phase"] = self.state.phase.value
        result.update(
            {
                "enabled": self.preferences.enabled,
                "channel": self.preferences.channel,
                "deferred_version": self.preferences.deferred_version,
                "automatic_install_supported": self.installer.supported,
                "installer_detail": (
                    "Packaged Windows update can restart into the verified installer"
                    if self.installer.supported
                    else self.installer.unsupported_reason
                ),
                "configured": self.preferences.channel in self.config.manifest_urls,
            }
        )
        # Private staging and backup paths are intentionally not exposed to the dashboard.
        result.pop("downloaded_path", None)
        result.pop("backup_path", None)
        result.pop("operation_id", None)
        return result

    def configure(self, *, enabled: bool, channel: Channel) -> dict[str, Any]:
        if channel not in {"stable", "beta"}:
            raise UpdateError("Update channel must be stable or beta")
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateBusyError("Another update operation is already in progress")
        try:
            channel_changed = channel != self.preferences.channel
            self.preferences = UpdatePreferences(enabled, channel, None)
            self._atomic_json(self.preferences_path, asdict(self.preferences))
            if channel_changed:
                self._clean_operation()
                self.state.offered_version = None
                self.state.mandatory = False
                self.state.release_notes_url = None
                self.state.operation_id = None
            if not enabled:
                self._cancel.set()
                self.state.phase = UpdatePhase.DISABLED
                self.state.last_error = None
            elif channel_changed or self.state.phase == UpdatePhase.DISABLED:
                self.state.phase = UpdatePhase.IDLE
                self.state.last_error = None
            self._save()
            return self.public_status()
        finally:
            self._operation_lock.release()

    def defer(self) -> dict[str, Any]:
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
        self.state.last_error = None
        if self.state.phase in {UpdatePhase.ERROR, UpdatePhase.CANCELLED}:
            self.state.phase = UpdatePhase.IDLE
        self._save()
        return self.public_status()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        return self.public_status()

    def scheduled_check(self) -> dict[str, Any]:
        last = _parse_time(self.state.last_checked_at)
        if not self.preferences.enabled or (
            last is not None and datetime.now(UTC) - last < CHECK_INTERVAL
        ):
            return self.public_status()
        return self.check(respect_defer=True)

    def _begin(self, phase: UpdatePhase) -> None:
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateBusyError("Another update operation is already in progress")
        self._cancel.clear()
        self.state.phase = phase
        self.state.last_error = None
        self._save()

    def _finish(self) -> None:
        self._operation_lock.release()

    def check(self, *, respect_defer: bool = False) -> dict[str, Any]:
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
        self._begin(UpdatePhase.CHECKING)
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
            self.state.last_checked_at = _utc_now()
            self.state.offered_version = offered
            self.state.mandatory = cast(bool, manifest["mandatory"])
            self.state.release_notes_url = cast(str, manifest["release_notes_url"])
            self.state.operation_id = hashlib.sha256(raw).hexdigest()[:24]
            self._atomic_json(self._operation_directory() / "manifest.json", manifest)
            if ReleaseVersion.parse(offered) == ReleaseVersion.parse(self.config.current_version):
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
        finally:
            self._finish()

    def download(self) -> dict[str, Any]:
        if self.state.phase not in {UpdatePhase.AVAILABLE, UpdatePhase.CANCELLED}:
            raise UpdateError("A verified available update is required before download")
        manifest_path = self._operation_directory() / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise UpdateError(
                "Verified update metadata is no longer available; check again"
            ) from exc
        self._begin(UpdatePhase.DOWNLOADING)
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
        finally:
            self._finish()

    def install(self) -> dict[str, Any]:
        if self.state.phase != UpdatePhase.READY or self.state.downloaded_path is None:
            raise UpdateError("A completely verified update must be ready before install")
        artifact = Path(self.state.downloaded_path)
        manifest = json.loads(
            (self._operation_directory() / "manifest.json").read_text(encoding="utf-8")
        )
        self._begin(UpdatePhase.INSTALLING)
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
            self._save()
            self.installer.handoff(
                artifact, cast(str, manifest["version"]), self._operation_directory()
            )
            self.state.phase = UpdatePhase.RESTART_REQUIRED
            self._save()
            return self.public_status()
        except (OSError, ValueError, UpdateError) as exc:
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = str(exc)[:500]
            self._save()
            return self.public_status()
        finally:
            self._finish()

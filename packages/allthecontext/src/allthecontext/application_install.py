"""Per-user application registration behind platform-specific adapters."""

from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import hmac
import json
import os
import platform
import secrets
import stat
import subprocess
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from platformdirs import user_data_path

from . import __version__
from .platform_compat import windows_creation_flags, windows_dll, windows_registry
from .release_manifest import ManifestError, ReleaseVersion

WINDOWS_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AllTheContext"
WINDOWS_APP_ID = "AllTheContext"
WINDOWS_APP_NAME = "AllTheContext.exe"
WINDOWS_USER_SHELL_FOLDERS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
)
PACKAGED_SMOKE_FLAG = "ATC_PACKAGED_SMOKE"

_MAX_SHORTCUT_BYTES = 1024 * 1024
_MAX_REGISTRY_DATA_BYTES = 64 * 1024
_MAX_STATUS_ITEMS = 16
_REPARSE_POINT = 0x400
_REGISTRATION_JOURNAL_SCHEMA = 2
_REGISTRATION_JOURNAL_NAME = "registration-v1.json"
_REGISTRATION_JOURNAL_DIRECTORY = ".atc-registration"
_MAX_REGISTRATION_JOURNAL_BYTES = 8 * 1024 * 1024

ShortcutName = Literal["launcher", "desktop", "uninstall"]
RegistrationName = Literal[
    "launcher",
    "desktop",
    "uninstall",
    "DisplayName",
    "DisplayVersion",
    "Publisher",
    "InstallLocation",
    "DisplayIcon",
    "UninstallString",
    "NoModify",
    "NoRepair",
]
RegistryData = str | int | bytes | tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ApplicationRegistration:
    platform: str
    launcher: Path
    desktop_shortcut: Path | None
    uninstall_registered: bool


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    links: int
    attributes: int


@dataclass(frozen=True, slots=True)
class WindowsShortcutSnapshot:
    """Bounded preimage for one fixed ATC-owned shortcut."""

    name: ShortcutName
    present: bool
    data: bytes | None = field(repr=False)
    identity: _FileIdentity | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class WindowsRegistryValueSnapshot:
    """Exact presence, type, and bounded data for one ATC-owned value."""

    name: str
    present: bool
    value_type: int | None
    data: RegistryData = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class WindowsApplicationRegistrationSnapshot:
    """Complete bounded preimage for the application-registration surface."""

    plan_token: str = field(repr=False)
    uninstall_key_present: bool
    shortcuts: tuple[WindowsShortcutSnapshot, ...]
    registry_values: tuple[WindowsRegistryValueSnapshot, ...]


@dataclass(frozen=True, slots=True)
class WindowsRegistrationRestoreStatus:
    """Content-free, bounded status retained when compensation needs a retry."""

    complete: bool
    retryable: bool
    restored_count: int
    pending: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WindowsRegistrationApplyResult:
    snapshot: WindowsApplicationRegistrationSnapshot
    changed_entries: tuple[RegistrationName, ...]


@dataclass(slots=True)
class _RegistrationJournal:
    """Durable registration evidence and recovery state for one plan."""

    install_root: str
    executable: str
    start_menu: str
    desktop: str | None
    uninstall_key: str
    snapshot: WindowsApplicationRegistrationSnapshot
    desired_shortcuts: dict[ShortcutName, bytes]
    desired_shortcut_identities: dict[ShortcutName, _FileIdentity]
    desired_registry: dict[str, tuple[int, RegistryData]]
    registry_before: dict[str, WindowsRegistryValueSnapshot]
    phase: str
    active: tuple[RegistrationName, ...]
    legacy: bool = field(default=False, repr=False)
    registry_key_created: bool = field(default=False, repr=False)


class WindowsRegistrationError(OSError):
    """Safe registration failure with no path, credential, or user-context text."""

    def __init__(
        self,
        code: str,
        *,
        transaction: WindowsApplicationRegistrationTransaction | None = None,
        status: WindowsRegistrationRestoreStatus | None = None,
    ) -> None:
        self.code = code
        self.transaction = transaction
        self.status = status
        super().__init__(code)


class WindowsRegistrationCompensationError(WindowsRegistrationError):
    """Registration failed and at least one prior mutation remains retryable."""


@dataclass(slots=True)
class _ShortcutMutation:
    name: ShortcutName
    path: Path = field(repr=False)
    before: WindowsShortcutSnapshot
    after_data: bytes
    after_identity: _FileIdentity | None = None
    remove: bool = False


@dataclass(slots=True)
class _RegistryMutation:
    name: str
    before: WindowsRegistryValueSnapshot
    after: WindowsRegistryValueSnapshot
    remove: bool = False


@dataclass(frozen=True, slots=True)
class _ShortcutPlan:
    name: ShortcutName
    path: Path = field(repr=False)
    arguments: str
    description: str


def _absolute_path(path: Path) -> Path:
    """Normalize spelling without resolving links or changing object identity."""

    candidate = Path(path).expanduser()
    return Path(os.path.abspath(os.fspath(candidate)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_absolute_path(left))) == os.path.normcase(
        os.fspath(_absolute_path(right))
    )


def _windows_install_root() -> Path:
    """Return the configured canonical root while preserving reparse spelling."""

    configured = os.environ.get("ATC_INSTALL_DIR")
    if configured:
        return _absolute_path(Path(configured))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return _absolute_path(Path(local_app_data)) / "Programs" / "All The Context"
    data_path = Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
    return _absolute_path(data_path).parent / "Programs" / "All The Context"


def _registration_journal_path(install_root: Path) -> Path:
    root = _absolute_path(install_root)
    return root / _REGISTRATION_JOURNAL_DIRECTORY / _REGISTRATION_JOURNAL_NAME


def _json_object_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate journal key")
        result[key] = value
    return result


def _encode_registry_data(data: RegistryData) -> object:
    if data is None:
        return None
    if isinstance(data, str):
        return {"kind": "str", "value": data}
    if isinstance(data, bytes):
        return {
            "kind": "bytes",
            "value": base64.b64encode(data).decode("ascii"),
        }
    if isinstance(data, int) and not isinstance(data, bool):
        return {"kind": "int", "value": data}
    if isinstance(data, tuple) and all(isinstance(item, str) for item in data):
        return {"kind": "multi", "value": list(data)}
    raise WindowsRegistrationError("registration_journal_invalid")


def _decode_registry_data(value: object) -> RegistryData:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        raise WindowsRegistrationError("registration_journal_invalid")
    kind = value.get("kind")
    raw = value.get("value")
    try:
        if kind == "str" and isinstance(raw, str):
            data: RegistryData = raw
        elif kind == "bytes" and isinstance(raw, str):
            data = base64.b64decode(raw.encode("ascii"), validate=True)
        elif kind == "int" and isinstance(raw, int) and not isinstance(raw, bool):
            data = int(raw)
        elif (
            kind == "multi" and isinstance(raw, list) and all(isinstance(item, str) for item in raw)
        ):
            data = tuple(raw)
        else:
            raise WindowsRegistrationError("registration_journal_invalid")
    except (ValueError, UnicodeError, WindowsRegistrationError):
        raise WindowsRegistrationError("registration_journal_invalid") from None
    try:
        return _registry_data_copy(data)
    except WindowsRegistrationError as exc:
        raise WindowsRegistrationError("registration_journal_invalid") from exc


def _encode_identity(identity: _FileIdentity | None) -> object:
    if identity is None:
        return None
    return {
        "device": identity.device,
        "inode": identity.inode,
        "size": identity.size,
        "modified_ns": identity.modified_ns,
        "links": identity.links,
        "attributes": identity.attributes,
    }


def _decode_identity(value: object) -> _FileIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WindowsRegistrationError("registration_journal_invalid")
    fields = ("device", "inode", "size", "modified_ns", "links", "attributes")
    if set(value) != set(fields) or any(
        not isinstance(value[field], int) or isinstance(value[field], bool) or value[field] < 0
        for field in fields
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    return _FileIdentity(*(int(value[field]) for field in fields))


def _encode_registry_snapshot(snapshot: WindowsRegistryValueSnapshot) -> dict[str, object]:
    return {
        "name": snapshot.name,
        "present": snapshot.present,
        "value_type": snapshot.value_type,
        "data": _encode_registry_data(snapshot.data),
    }


def _decode_registry_snapshot(value: object) -> WindowsRegistryValueSnapshot:
    if not isinstance(value, dict):
        raise WindowsRegistrationError("registration_journal_invalid")
    name = value.get("name")
    present = value.get("present")
    value_type = value.get("value_type")
    if not isinstance(name, str) or not isinstance(present, bool):
        raise WindowsRegistrationError("registration_journal_invalid")
    if value_type is not None and (not isinstance(value_type, int) or isinstance(value_type, bool)):
        raise WindowsRegistrationError("registration_journal_invalid")
    data = _decode_registry_data(value.get("data"))
    if present != (value_type is not None) or (not present and data is not None):
        raise WindowsRegistrationError("registration_journal_invalid")
    return WindowsRegistryValueSnapshot(name, present, value_type, data)


class _WindowsDataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    )


_JOURNAL_TEST_KEY = secrets.token_bytes(32)
_JOURNAL_PROTECTION_DESCRIPTION = "All The Context registration journal"


def _protect_registration_payload(payload: bytes) -> bytes:
    """Protect journal bytes with the current Windows user's DPAPI boundary.

    DPAPI authenticates the journal against the Windows user profile and detects
    ordinary file tampering. A same-user process with the ability to run code
    as this account can still use that account's DPAPI authority; canonical
    path/component checks below remain mandatory and this limit is intentional.
    The non-Windows branch exists only for tests that emulate Windows APIs on a
    non-Windows host and is deliberately process-local, never a production
    security boundary.
    """

    if os.name != "nt":
        return hmac.new(_JOURNAL_TEST_KEY, payload, hashlib.sha256).digest() + payload
    try:
        crypt32 = windows_dll("crypt32")
        kernel32 = windows_dll("kernel32")
        protect = crypt32.CryptProtectData
        protect.argtypes = (
            ctypes.POINTER(_WindowsDataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_WindowsDataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_WindowsDataBlob),
        )
        protect.restype = wintypes.BOOL
        free = kernel32.LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(payload)
        source = _WindowsDataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        protected = _WindowsDataBlob()
        if not protect(
            ctypes.byref(source),
            _JOURNAL_PROTECTION_DESCRIPTION,
            None,
            None,
            None,
            0x1,
            ctypes.byref(protected),
        ):
            raise OSError("CryptProtectData failed")
        try:
            return ctypes.string_at(protected.pbData, protected.cbData)
        finally:
            free(ctypes.cast(protected.pbData, ctypes.c_void_p))
    except WindowsRegistrationError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise WindowsRegistrationError("registration_journal_auth_unavailable") from exc


def _unprotect_registration_payload(protected: bytes) -> bytes:
    if os.name != "nt":
        if len(protected) < hashlib.sha256().digest_size:
            raise WindowsRegistrationError("registration_journal_auth_invalid")
        expected = hmac.new(_JOURNAL_TEST_KEY, protected[32:], hashlib.sha256).digest()
        if not hmac.compare_digest(protected[:32], expected):
            raise WindowsRegistrationError("registration_journal_auth_invalid")
        return protected[32:]
    try:
        crypt32 = windows_dll("crypt32")
        kernel32 = windows_dll("kernel32")
        unprotect = crypt32.CryptUnprotectData
        unprotect.argtypes = (
            ctypes.POINTER(_WindowsDataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_WindowsDataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_WindowsDataBlob),
        )
        unprotect.restype = wintypes.BOOL
        free = kernel32.LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(protected)
        source = _WindowsDataBlob(
            len(protected), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        description = wintypes.LPWSTR()
        plaintext = _WindowsDataBlob()
        if not unprotect(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            0x1,
            ctypes.byref(plaintext),
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(plaintext.pbData, plaintext.cbData)
        finally:
            if description:
                free(ctypes.cast(description, ctypes.c_void_p))
            free(ctypes.cast(plaintext.pbData, ctypes.c_void_p))
    except WindowsRegistrationError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise WindowsRegistrationError("registration_journal_auth_invalid") from exc


def _encode_snapshot(snapshot: WindowsApplicationRegistrationSnapshot) -> dict[str, object]:
    return {
        "plan_token": snapshot.plan_token,
        "uninstall_key_present": snapshot.uninstall_key_present,
        "shortcuts": [
            {
                "name": shortcut.name,
                "present": shortcut.present,
                "data": (
                    base64.b64encode(shortcut.data).decode("ascii")
                    if shortcut.data is not None
                    else None
                ),
                "identity": _encode_identity(shortcut.identity),
            }
            for shortcut in snapshot.shortcuts
        ],
        "registry_values": [
            {
                "name": value.name,
                "present": value.present,
                "value_type": value.value_type,
                "data": _encode_registry_data(value.data),
            }
            for value in snapshot.registry_values
        ],
    }


def _decode_snapshot(value: object) -> WindowsApplicationRegistrationSnapshot:
    if not isinstance(value, dict):
        raise WindowsRegistrationError("registration_journal_invalid")
    plan_token = value.get("plan_token")
    uninstall_key_present = value.get("uninstall_key_present")
    shortcuts_raw = value.get("shortcuts")
    registry_raw = value.get("registry_values")
    if (
        not isinstance(plan_token, str)
        or len(plan_token) != 64
        or not isinstance(uninstall_key_present, bool)
        or not isinstance(shortcuts_raw, list)
        or not isinstance(registry_raw, list)
        or len(shortcuts_raw) > _MAX_STATUS_ITEMS
        or len(registry_raw) > _MAX_STATUS_ITEMS
    ):
        raise WindowsRegistrationError("registration_journal_invalid")

    shortcuts: list[WindowsShortcutSnapshot] = []
    try:
        for item in shortcuts_raw:
            if not isinstance(item, dict):
                raise ValueError
            name = item.get("name")
            present = item.get("present")
            data_raw = item.get("data")
            if (
                not isinstance(name, str)
                or name
                not in {
                    "launcher",
                    "desktop",
                    "uninstall",
                }
                or not isinstance(present, bool)
            ):
                raise ValueError
            shortcut_name = cast(ShortcutName, name)
            if data_raw is None:
                data = None
            elif isinstance(data_raw, str):
                data = base64.b64decode(data_raw.encode("ascii"), validate=True)
                if len(data) > _MAX_SHORTCUT_BYTES:
                    raise ValueError
            else:
                raise ValueError
            identity = _decode_identity(item.get("identity"))
            if present != (data is not None) or (present and identity is None):
                raise ValueError
            shortcuts.append(WindowsShortcutSnapshot(shortcut_name, present, data, identity))
        registry_values: list[WindowsRegistryValueSnapshot] = []
        for item in registry_raw:
            if not isinstance(item, dict):
                raise ValueError
            name = item.get("name")
            present = item.get("present")
            value_type = item.get("value_type")
            if not isinstance(name, str) or not isinstance(present, bool):
                raise ValueError
            if value_type is not None and (
                not isinstance(value_type, int) or isinstance(value_type, bool)
            ):
                raise ValueError
            registry_data = _decode_registry_data(item.get("data"))
            if present != (value_type is not None) or (not present and registry_data is not None):
                raise ValueError
            registry_values.append(
                WindowsRegistryValueSnapshot(name, present, value_type, registry_data)
            )
    except (TypeError, RecursionError, ValueError, UnicodeError, WindowsRegistrationError):
        raise WindowsRegistrationError("registration_journal_invalid") from None
    return WindowsApplicationRegistrationSnapshot(
        plan_token,
        uninstall_key_present,
        tuple(shortcuts),
        tuple(registry_values),
    )


def _resolve_known_folder(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path(os.path.abspath(candidate))
    _validate_directory_chain(candidate)
    return candidate.resolve()


def _windows_known_folder(name: str, *, fallback: Path | None = None) -> Path | None:
    """Resolve a per-user Shell folder, including OneDrive/enterprise redirection."""

    winreg = windows_registry()

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_USER_SHELL_FOLDERS) as key:
            raw, _value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return _resolve_known_folder(fallback) if fallback is not None else None
    if not isinstance(raw, str) or not raw.strip():
        return _resolve_known_folder(fallback) if fallback is not None else None
    return _resolve_known_folder(Path(os.path.expandvars(raw)))


def _windows_locations() -> tuple[Path, Path | None]:
    if os.environ.get(PACKAGED_SMOKE_FLAG) == "1":
        smoke_programs = os.environ.get("ATC_SMOKE_PROGRAMS_DIR")
        smoke_desktop = os.environ.get("ATC_SMOKE_DESKTOP_DIR")
        if not smoke_programs or not smoke_desktop:
            raise OSError("Packaged smoke Windows folders are not configured")
        return (
            _resolve_known_folder(Path(smoke_programs)) / "All The Context",
            _resolve_known_folder(Path(smoke_desktop)),
        )
    app_data = os.environ.get("APPDATA")
    programs_fallback = (
        Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" if app_data else None
    )
    programs = _windows_known_folder("Programs", fallback=programs_fallback)
    if programs is None:
        raise OSError("Windows Programs folder is unavailable")
    user_profile = os.environ.get("USERPROFILE")
    desktop_fallback = Path(user_profile) / "Desktop" if user_profile else None
    desktop = _windows_known_folder("Desktop", fallback=desktop_fallback)
    return programs / "All The Context", desktop


def _is_safe_uninstall_key(key_name: str) -> bool:
    if key_name == WINDOWS_UNINSTALL_KEY:
        return True
    prefix = "Software\\AllTheContext\\Smoke\\"
    if not key_name.startswith(prefix):
        return False
    suffix = key_name[len(prefix) :]
    return bool(suffix) and all(part and part not in {".", ".."} for part in suffix.split("\\"))


def _windows_uninstall_key() -> str:
    override = os.environ.get("ATC_SMOKE_UNINSTALL_KEY")
    if override is None:
        return WINDOWS_UNINSTALL_KEY
    if os.environ.get(PACKAGED_SMOKE_FLAG) != "1" or not _is_safe_uninstall_key(override):
        raise OSError("Refusing an unsafe uninstall-registry override")
    return override


def _is_reparse_or_link(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return bool(metadata.st_mode and stat.S_ISLNK(metadata.st_mode)) or bool(
        attributes & _REPARSE_POINT
    )


def _file_identity(metadata: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        size=int(metadata.st_size),
        modified_ns=int(metadata.st_mtime_ns),
        links=int(metadata.st_nlink),
        attributes=int(getattr(metadata, "st_file_attributes", 0)),
    )


def _same_file_identity(left: _FileIdentity, right: _FileIdentity) -> bool:
    """Compare an object identity without treating hard-link count as identity."""

    return (
        left.device == right.device
        and left.inode == right.inode
        and left.size == right.size
        and left.modified_ns == right.modified_ns
        and left.attributes == right.attributes
    )


def _safe_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WindowsRegistrationError("registration_target_unreadable") from exc


def _validate_directory_chain(path: Path) -> None:
    if not path.is_absolute() or not path.anchor:
        raise WindowsRegistrationError("registration_path_not_absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        metadata = _safe_lstat(current)
        if metadata is None:
            continue
        if _is_reparse_or_link(metadata):
            raise WindowsRegistrationError("registration_reparse_path")
        if not stat.S_ISDIR(metadata.st_mode):
            raise WindowsRegistrationError("registration_parent_not_directory")


def _validate_file_path(path: Path, *, allow_missing: bool = True) -> os.stat_result | None:
    _validate_directory_chain(path.parent)
    metadata = _safe_lstat(path)
    if metadata is None:
        if allow_missing:
            return None
        raise WindowsRegistrationError("registration_file_missing")
    if _is_reparse_or_link(metadata):
        raise WindowsRegistrationError("registration_reparse_path")
    if not stat.S_ISREG(metadata.st_mode):
        raise WindowsRegistrationError("registration_target_not_regular")
    if int(metadata.st_nlink) != 1:
        raise WindowsRegistrationError("registration_hardlink_path")
    return metadata


def _read_bounded_bytes(path: Path, metadata: os.stat_result, maximum: int) -> bytes:
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(opened) != _file_identity(metadata):
                raise WindowsRegistrationError("registration_target_changed")
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise WindowsRegistrationError("registration_file_too_large")
            after = os.fstat(stream.fileno())
        current = _safe_lstat(path)
        if current is None or _file_identity(current) != _file_identity(after):
            raise WindowsRegistrationError("registration_target_changed")
        return data
    except WindowsRegistrationError:
        raise
    except OSError as exc:
        raise WindowsRegistrationError("registration_file_unreadable") from exc


def _read_bounded_file(path: Path, metadata: os.stat_result) -> bytes:
    return _read_bounded_bytes(path, metadata, _MAX_SHORTCUT_BYTES)


def _shortcut_quarantine_path(path: Path) -> Path:
    """Return the one fixed sibling used to remove a known shortcut safely."""

    return path.with_name(f".{path.name}.atc-quarantine")


def _restore_quarantined_shortcut(
    quarantine: Path,
    original: Path,
    expected_identity: _FileIdentity,
    expected_data: bytes,
) -> None:
    """Put a quarantined file back without replacing a concurrent target."""

    quarantine_metadata = _safe_lstat(quarantine)
    if quarantine_metadata is None or _is_reparse_or_link(quarantine_metadata):
        raise WindowsRegistrationError("registration_restore_cleanup_ambiguous")
    if not _same_file_identity(_file_identity(quarantine_metadata), expected_identity):
        raise WindowsRegistrationError("registration_restore_cleanup_ambiguous")
    if _read_bounded_file(quarantine, quarantine_metadata) != expected_data:
        raise WindowsRegistrationError("registration_restore_cleanup_ambiguous")
    if _safe_lstat(original) is not None:
        raise WindowsRegistrationError("registration_restore_target_changed")
    try:
        os.link(quarantine, original, follow_symlinks=False)
    except FileExistsError as exc:
        raise WindowsRegistrationError("registration_restore_target_changed") from exc
    except OSError as exc:
        raise WindowsRegistrationError("registration_restore_failed") from exc
    restored_metadata = _safe_lstat(original)
    if (
        restored_metadata is None
        or not _same_file_identity(_file_identity(restored_metadata), expected_identity)
        or _read_bounded_file(original, restored_metadata) != expected_data
    ):
        raise WindowsRegistrationError("registration_restore_unverified")
    try:
        quarantine.unlink()
    except OSError as exc:
        raise WindowsRegistrationError("registration_restore_cleanup_failed") from exc


def _quarantine_and_remove_shortcut(
    path: Path,
    expected_identity: _FileIdentity,
    expected_data: bytes,
) -> None:
    """Remove one expected shortcut while preserving a swapped path entry.

    The target is first moved to a fixed, collision-checked sibling.  If the
    object moved was not the independently verified expected object, it is
    returned with a no-clobber hard-link publication and no data is deleted.
    The final unlink is performed only on the quarantined, verified object, so
    a concurrent replacement at the canonical target cannot be deleted.
    """

    metadata = _safe_lstat(path)
    if metadata is None:
        return
    if _is_reparse_or_link(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise WindowsRegistrationError("registration_restore_target_changed")
    observed_identity = _file_identity(metadata)
    if not _same_file_identity(observed_identity, expected_identity):
        raise WindowsRegistrationError("registration_restore_target_changed")
    observed_data = _read_bounded_file(path, metadata)
    if observed_data != expected_data:
        raise WindowsRegistrationError("registration_restore_target_changed")

    quarantine = _shortcut_quarantine_path(path)
    if _safe_lstat(quarantine) is not None:
        raise WindowsRegistrationError("registration_restore_cleanup_ambiguous")
    try:
        os.replace(path, quarantine)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WindowsRegistrationError("registration_restore_delete_failed") from exc

    moved_metadata = _safe_lstat(quarantine)
    if moved_metadata is None or _is_reparse_or_link(moved_metadata):
        raise WindowsRegistrationError("registration_restore_cleanup_ambiguous")
    moved_identity = _file_identity(moved_metadata)
    moved_data = _read_bounded_file(quarantine, moved_metadata)
    if not _same_file_identity(moved_identity, expected_identity) or moved_data != expected_data:
        _restore_quarantined_shortcut(quarantine, path, moved_identity, moved_data)
        raise WindowsRegistrationError("registration_restore_target_changed")
    try:
        quarantine.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WindowsRegistrationError("registration_restore_delete_failed") from exc
    if _safe_lstat(quarantine) is not None:
        raise WindowsRegistrationError("registration_restore_unverified")


def _shortcut_state(name: ShortcutName, path: Path) -> WindowsShortcutSnapshot:
    metadata = _validate_file_path(path)
    if metadata is None:
        return WindowsShortcutSnapshot(name, False, None)
    return WindowsShortcutSnapshot(
        name, True, _read_bounded_file(path, metadata), _file_identity(metadata)
    )


def _registry_data_copy(data: object) -> RegistryData:
    if data is None:
        return None
    if isinstance(data, str):
        copied: RegistryData = str(data)
    elif isinstance(data, bytes):
        copied = bytes(data)
    elif isinstance(data, int) and not isinstance(data, bool):
        copied = int(data)
    elif isinstance(data, (list, tuple)) and all(isinstance(item, str) for item in data):
        copied = tuple(str(item) for item in data)
    else:
        raise WindowsRegistrationError("registration_value_type_unsupported")
    if _registry_data_size(copied) > _MAX_REGISTRY_DATA_BYTES:
        raise WindowsRegistrationError("registration_value_too_large")
    return copy.deepcopy(copied)


def _registry_data_size(data: RegistryData) -> int:
    if data is None:
        return 0
    if isinstance(data, str):
        return len(data)
    if isinstance(data, bytes):
        return len(data)
    if isinstance(data, int):
        return 8
    return sum(len(item) for item in data)


def _registry_value_snapshot(
    winreg: Any,
    key: Any,
    name: str,
) -> WindowsRegistryValueSnapshot:
    try:
        data, value_type = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return WindowsRegistryValueSnapshot(name, False, None)
    except OSError as exc:
        raise WindowsRegistrationError("registration_value_unreadable") from exc
    if not isinstance(value_type, int) or isinstance(value_type, bool):
        raise WindowsRegistrationError("registration_value_type_invalid")
    return WindowsRegistryValueSnapshot(name, True, int(value_type), _registry_data_copy(data))


def _read_registry_state(
    winreg: Any,
    key_name: str,
    value_names: tuple[str, ...],
) -> tuple[bool, tuple[WindowsRegistryValueSnapshot, ...]]:
    key_read = int(getattr(winreg, "KEY_READ", 0x20019))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, key_read) as key:
            values = tuple(_registry_value_snapshot(winreg, key, name) for name in value_names)
    except FileNotFoundError:
        values = tuple(WindowsRegistryValueSnapshot(name, False, None) for name in value_names)
        return False, values
    except WindowsRegistrationError:
        raise
    except OSError as exc:
        raise WindowsRegistrationError("registration_key_unreadable") from exc
    return True, values


def _registry_data_for_set(data: RegistryData) -> object:
    if isinstance(data, tuple):
        return list(data)
    return copy.deepcopy(data)


def _registry_snapshot_equal(
    left: WindowsRegistryValueSnapshot,
    right: WindowsRegistryValueSnapshot,
) -> bool:
    return (
        left.name == right.name
        and left.present == right.present
        and left.value_type == right.value_type
        and left.data == right.data
    )


def _delete_registry_value_if_unchanged(
    winreg: Any,
    key: Any,
    expected: WindowsRegistryValueSnapshot,
) -> None:
    """Delete only an exact value, using an adapter CAS when available."""

    confirmed = _registry_value_snapshot(winreg, key, expected.name)
    if not _registry_snapshot_equal(confirmed, expected):
        raise WindowsRegistrationError("registration_restore_target_changed")
    conditional = getattr(winreg, "DeleteValueIfUnchanged", None)
    if callable(conditional):
        try:
            result = conditional(key, expected.name, expected)
        except FileNotFoundError:
            return
        if not isinstance(result, bool) or not result:
            raise WindowsRegistrationError("registration_restore_target_changed")
        return
    # The stdlib winreg API has no compare-and-delete primitive.  Leaving the
    # value and the journal for retry is the only safe portable behavior when
    # the platform adapter cannot supply one.
    raise WindowsRegistrationError("registration_restore_atomicity_unavailable")


def _set_registry_value_if_unchanged(
    winreg: Any,
    key: Any,
    expected: WindowsRegistryValueSnapshot,
    value_type: int,
    data: RegistryData,
) -> None:
    """Set only an exact value, using an adapter CAS when available."""

    confirmed = _registry_value_snapshot(winreg, key, expected.name)
    if not _registry_snapshot_equal(confirmed, expected):
        raise WindowsRegistrationError("registration_target_changed")
    conditional = getattr(winreg, "SetValueIfUnchanged", None)
    if callable(conditional):
        try:
            result = conditional(
                key,
                expected.name,
                expected,
                value_type,
                _registry_data_for_set(data),
            )
        except FileNotFoundError:
            raise WindowsRegistrationError("registration_target_changed") from None
        if not isinstance(result, bool) or not result:
            raise WindowsRegistrationError("registration_target_changed")
        return
    # A second read cannot make a plain SetValueEx compare-and-set.  Leaving
    # the journal for an adapter that can perform the atomic operation is the
    # only safe behavior when the platform API cannot provide one.
    del key, value_type, data
    raise WindowsRegistrationError("registration_restore_atomicity_unavailable")


def _create_registry_key_if_absent(winreg: Any, name: str) -> tuple[Any, bool | None]:
    """Create a key and return ownership only from an atomic adapter result."""

    conditional = getattr(winreg, "CreateKeyIfAbsent", None)
    if callable(conditional):
        try:
            result = conditional(winreg.HKEY_CURRENT_USER, name)
        except FileNotFoundError:
            raise WindowsRegistrationError("registration_key_create_failed") from None
        if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[1], bool):
            raise WindowsRegistrationError("registration_key_creation_unverified")
        return result[0], result[1]
    # winreg.CreateKey does not expose RegCreateKeyEx's disposition.  It can
    # still create the key for forward progress, but None cannot prove
    # exclusive ownership for a later destructive cleanup.
    try:
        return winreg.CreateKey(winreg.HKEY_CURRENT_USER, name), None
    except FileNotFoundError:
        raise WindowsRegistrationError("registration_key_create_failed") from None


def _delete_registry_key_if_unchanged(
    winreg: Any,
    name: str,
    expected: tuple[WindowsRegistryValueSnapshot, ...],
) -> None:
    """Delete a key only through an adapter compare-delete primitive."""

    conditional = getattr(winreg, "DeleteKeyIfUnchanged", None)
    if not callable(conditional):
        raise WindowsRegistrationError("registration_restore_atomicity_unavailable")
    try:
        result = conditional(winreg.HKEY_CURRENT_USER, name, expected)
    except FileNotFoundError:
        return
    if not isinstance(result, bool) or not result:
        raise WindowsRegistrationError("registration_restore_target_changed")


def _plan_token(
    executable: Path,
    start_menu: Path,
    desktop: Path | None,
    uninstall_key: str,
) -> str:
    digest = hashlib.sha256()
    for value in (
        str(executable),
        str(start_menu),
        str(desktop) if desktop is not None else "",
        uninstall_key,
    ):
        encoded = value.encode("utf-8", "surrogatepass")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _safe_error_code(error: BaseException) -> str:
    if isinstance(error, WindowsRegistrationError):
        return error.code
    if isinstance(error, RecursionError):
        return "registration_journal_invalid"
    if isinstance(error, PermissionError):
        return "registration_permission_denied"
    if isinstance(error, TimeoutError):
        return "registration_timeout"
    return "registration_step_failed"


def _load_registration_json(raw: bytes, *, code: str) -> object:
    """Parse bounded journal JSON without leaking parser/recursion failures."""

    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=_json_object_no_duplicates)
    except WindowsRegistrationError:
        raise
    except RecursionError:
        raise WindowsRegistrationError(code) from None
    except (UnicodeError, ValueError, TypeError):
        raise WindowsRegistrationError(code) from None


def _decode_legacy_journal(decoded: object) -> _RegistrationJournal:
    """Decode only the exact plaintext schema written by the verified parent."""

    if not isinstance(decoded, dict):
        raise WindowsRegistrationError("registration_journal_invalid")
    required = {
        "schema",
        "install_root",
        "executable",
        "start_menu",
        "desktop",
        "uninstall_key",
        "phase",
        "active",
        "snapshot",
        "desired_shortcuts",
        "desired_registry",
    }
    if (
        set(decoded) != required
        or not isinstance(decoded.get("schema"), int)
        or isinstance(decoded.get("schema"), bool)
        or decoded.get("schema") != 1
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    text_fields = ("install_root", "executable", "start_menu", "uninstall_key")
    if any(
        not isinstance(decoded.get(name), str) or len(decoded[name]) > 4096 for name in text_fields
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    desktop = decoded.get("desktop")
    if desktop is not None and (not isinstance(desktop, str) or len(desktop) > 4096):
        raise WindowsRegistrationError("registration_journal_invalid")
    phase = decoded.get("phase")
    active_raw = decoded.get("active")
    desired_shortcuts_raw = decoded.get("desired_shortcuts")
    desired_registry_raw = decoded.get("desired_registry")
    if (
        not isinstance(phase, str)
        or phase not in {"applying", "installed", "restoring", "uninstalling"}
        or not isinstance(active_raw, list)
        or len(active_raw) > _MAX_STATUS_ITEMS
        or any(not isinstance(name, str) for name in active_raw)
        or len(set(active_raw)) != len(active_raw)
        or not isinstance(desired_shortcuts_raw, dict)
        or not isinstance(desired_registry_raw, dict)
        or len(desired_shortcuts_raw) > _MAX_STATUS_ITEMS
        or len(desired_registry_raw) > _MAX_STATUS_ITEMS
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    try:
        desired_shortcuts: dict[ShortcutName, bytes] = {}
        for name, data_raw in desired_shortcuts_raw.items():
            if name not in {"launcher", "desktop", "uninstall"} or not isinstance(data_raw, str):
                raise ValueError
            data = base64.b64decode(data_raw.encode("ascii"), validate=True)
            if len(data) > _MAX_SHORTCUT_BYTES:
                raise ValueError
            desired_shortcuts[name] = data
        desired_registry: dict[str, tuple[int, RegistryData]] = {}
        for name, item in desired_registry_raw.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                raise ValueError
            if set(item) != {"value_type", "data"}:
                raise ValueError
            value_type = item.get("value_type")
            if not isinstance(value_type, int) or isinstance(value_type, bool):
                raise ValueError
            desired_registry[name] = (int(value_type), _decode_registry_data(item.get("data")))
        snapshot = _decode_snapshot(decoded["snapshot"])
    except (TypeError, ValueError, UnicodeError, RecursionError, WindowsRegistrationError):
        raise WindowsRegistrationError("registration_journal_invalid") from None
    return _RegistrationJournal(
        decoded["install_root"],
        decoded["executable"],
        decoded["start_menu"],
        desktop,
        decoded["uninstall_key"],
        snapshot,
        desired_shortcuts,
        {},
        desired_registry,
        {},
        phase,
        tuple(active_raw),
        True,
    )


def _encode_journal(journal: _RegistrationJournal, *, phase: str, active: tuple[str, ...]) -> bytes:
    payload = {
        "schema": _REGISTRATION_JOURNAL_SCHEMA,
        "install_root": journal.install_root,
        "executable": journal.executable,
        "start_menu": journal.start_menu,
        "desktop": journal.desktop,
        "uninstall_key": journal.uninstall_key,
        "phase": phase,
        "active": list(active),
        "registry_key_created": journal.registry_key_created,
        "snapshot": _encode_snapshot(journal.snapshot),
        "desired_shortcuts": {
            name: base64.b64encode(data).decode("ascii")
            for name, data in journal.desired_shortcuts.items()
        },
        "desired_shortcut_identities": {
            name: _encode_identity(identity)
            for name, identity in journal.desired_shortcut_identities.items()
        },
        "desired_registry": {
            name: {"value_type": value_type, "data": _encode_registry_data(data)}
            for name, (value_type, data) in journal.desired_registry.items()
        },
        "registry_before": {
            name: _encode_registry_snapshot(snapshot)
            for name, snapshot in journal.registry_before.items()
        },
    }
    try:
        payload_bytes = (
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        protected = base64.b64encode(_protect_registration_payload(payload_bytes)).decode("ascii")
        encoded = (
            json.dumps(
                {"schema": _REGISTRATION_JOURNAL_SCHEMA, "protected": protected},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise WindowsRegistrationError("registration_journal_invalid") from exc
    if len(encoded) > _MAX_REGISTRATION_JOURNAL_BYTES:
        raise WindowsRegistrationError("registration_journal_too_large")
    return encoded


def _read_registration_journal(path: Path) -> _RegistrationJournal | None:
    metadata = _safe_lstat(path)
    if metadata is None:
        return None
    checked = _validate_file_path(path, allow_missing=False)
    if checked is None:
        raise WindowsRegistrationError("registration_journal_invalid")
    try:
        raw = _read_bounded_bytes(path, checked, _MAX_REGISTRATION_JOURNAL_BYTES)
    except WindowsRegistrationError:
        raise
    except OSError as exc:
        raise WindowsRegistrationError("registration_journal_invalid") from exc
    decoded = _load_registration_json(raw, code="registration_journal_invalid")
    if not isinstance(decoded, dict):
        raise WindowsRegistrationError("registration_journal_invalid")
    if (
        isinstance(decoded.get("schema"), int)
        and not isinstance(decoded.get("schema"), bool)
        and decoded.get("schema") == 1
    ):
        try:
            return _decode_legacy_journal(decoded)
        except RecursionError:
            raise WindowsRegistrationError("registration_journal_invalid") from None
    if (
        set(decoded) != {"schema", "protected"}
        or not isinstance(decoded.get("schema"), int)
        or isinstance(decoded.get("schema"), bool)
        or decoded.get("schema") != _REGISTRATION_JOURNAL_SCHEMA
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    protected_raw = decoded.get("protected")
    if not isinstance(protected_raw, str):
        raise WindowsRegistrationError("registration_journal_invalid")
    try:
        payload_bytes = base64.b64decode(protected_raw.encode("ascii"), validate=True)
        if len(payload_bytes) > _MAX_REGISTRATION_JOURNAL_BYTES:
            raise WindowsRegistrationError("registration_journal_too_large")
        plaintext = _unprotect_registration_payload(payload_bytes)
        if len(plaintext) > _MAX_REGISTRATION_JOURNAL_BYTES:
            raise WindowsRegistrationError("registration_journal_too_large")
    except WindowsRegistrationError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise WindowsRegistrationError("registration_journal_auth_invalid") from exc
    decoded = _load_registration_json(plaintext, code="registration_journal_auth_invalid")
    if not isinstance(decoded, dict):
        raise WindowsRegistrationError("registration_journal_invalid")
    required = {
        "schema",
        "install_root",
        "executable",
        "start_menu",
        "desktop",
        "uninstall_key",
        "phase",
        "active",
        "snapshot",
        "desired_shortcuts",
        "desired_shortcut_identities",
        "desired_registry",
        "registry_before",
        "registry_key_created",
    }
    if set(decoded) != required or decoded.get("schema") != _REGISTRATION_JOURNAL_SCHEMA:
        raise WindowsRegistrationError("registration_journal_invalid")
    text_fields = ("install_root", "executable", "start_menu", "uninstall_key")
    if any(
        not isinstance(decoded.get(name), str) or len(decoded[name]) > 4096 for name in text_fields
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    desktop = decoded.get("desktop")
    if desktop is not None and (not isinstance(desktop, str) or len(desktop) > 4096):
        raise WindowsRegistrationError("registration_journal_invalid")
    phase = decoded.get("phase")
    active_raw = decoded.get("active")
    desired_shortcuts_raw = decoded.get("desired_shortcuts")
    desired_shortcut_identities_raw = decoded.get("desired_shortcut_identities")
    desired_registry_raw = decoded.get("desired_registry")
    registry_before_raw = decoded.get("registry_before")
    registry_key_created = decoded.get("registry_key_created")
    if (
        not isinstance(phase, str)
        or phase not in {"applying", "migrating", "installed", "restoring", "uninstalling"}
        or not isinstance(active_raw, list)
        or not isinstance(registry_key_created, bool)
        or len(active_raw) > _MAX_STATUS_ITEMS
        or any(not isinstance(name, str) for name in active_raw)
        or len(set(active_raw)) != len(active_raw)
        or not isinstance(desired_shortcuts_raw, dict)
        or not isinstance(desired_shortcut_identities_raw, dict)
        or not isinstance(desired_registry_raw, dict)
        or not isinstance(registry_before_raw, dict)
        or len(desired_shortcuts_raw) > _MAX_STATUS_ITEMS
        or len(desired_shortcut_identities_raw) > _MAX_STATUS_ITEMS
        or len(desired_registry_raw) > _MAX_STATUS_ITEMS
        or len(registry_before_raw) > _MAX_STATUS_ITEMS
    ):
        raise WindowsRegistrationError("registration_journal_invalid")
    try:
        desired_shortcuts: dict[ShortcutName, bytes] = {}
        for name, data_raw in desired_shortcuts_raw.items():
            if name not in {"launcher", "desktop", "uninstall"} or not isinstance(data_raw, str):
                raise ValueError
            data = base64.b64decode(data_raw.encode("ascii"), validate=True)
            if len(data) > _MAX_SHORTCUT_BYTES:
                raise ValueError
            desired_shortcuts[name] = data
        desired_shortcut_identities: dict[ShortcutName, _FileIdentity] = {}
        for name, identity_raw in desired_shortcut_identities_raw.items():
            if name not in {"launcher", "desktop", "uninstall"}:
                raise ValueError
            identity = _decode_identity(identity_raw)
            if identity is None:
                raise ValueError
            desired_shortcut_identities[name] = identity
        desired_registry: dict[str, tuple[int, RegistryData]] = {}
        for name, item in desired_registry_raw.items():
            if not isinstance(name, str) or not isinstance(item, dict):
                raise ValueError
            value_type = item.get("value_type")
            if not isinstance(value_type, int) or isinstance(value_type, bool):
                raise ValueError
            desired_registry[name] = (int(value_type), _decode_registry_data(item.get("data")))
        registry_before: dict[str, WindowsRegistryValueSnapshot] = {}
        for name, snapshot_raw in registry_before_raw.items():
            if not isinstance(name, str):
                raise ValueError
            registry_snapshot = _decode_registry_snapshot(snapshot_raw)
            if registry_snapshot.name != name:
                raise ValueError
            registry_before[name] = registry_snapshot
        snapshot = _decode_snapshot(decoded["snapshot"])
    except (TypeError, ValueError, UnicodeError, RecursionError, WindowsRegistrationError):
        raise WindowsRegistrationError("registration_journal_invalid") from None
    return _RegistrationJournal(
        decoded["install_root"],
        decoded["executable"],
        decoded["start_menu"],
        desktop,
        decoded["uninstall_key"],
        snapshot,
        desired_shortcuts,
        desired_shortcut_identities,
        desired_registry,
        registry_before,
        phase,
        tuple(active_raw),
        False,
        registry_key_created,
    )


def _write_registration_journal(
    path: Path,
    journal: _RegistrationJournal,
    *,
    phase: str,
    active: tuple[RegistrationName, ...] | None = None,
) -> None:
    effective_active = journal.active if active is None else active
    encoded = _encode_journal(journal, phase=phase, active=effective_active)
    parent = path.parent
    _validate_directory_chain(parent)
    try:
        parent.mkdir(parents=True, exist_ok=True)
        _validate_directory_chain(parent)
        existing = _safe_lstat(path)
        if existing is not None:
            _validate_file_path(path, allow_missing=False)
        temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.atc-new")
        if _safe_lstat(temporary) is not None:
            raise WindowsRegistrationError("registration_journal_temporary_exists")
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _validate_file_path(temporary, allow_missing=False)
            os.replace(temporary, path)
            _validate_file_path(path, allow_missing=False)
        finally:
            if _safe_lstat(temporary) is not None:
                with suppress(OSError):
                    temporary.unlink()
    except WindowsRegistrationError:
        raise
    except OSError as exc:
        raise WindowsRegistrationError("registration_journal_write_failed") from exc


def _remove_registration_journal(path: Path) -> None:
    metadata = _safe_lstat(path)
    if metadata is None:
        return
    _validate_file_path(path, allow_missing=False)
    try:
        path.unlink()
        if _safe_lstat(path) is not None:
            raise WindowsRegistrationError("registration_journal_cleanup_failed")
    except WindowsRegistrationError:
        raise
    except OSError as exc:
        raise WindowsRegistrationError("registration_journal_cleanup_failed") from exc
    with suppress(OSError):
        path.parent.rmdir()


def _create_windows_shortcut(
    shortcut: Path,
    executable: Path,
    *,
    arguments: str = "",
    description: str,
) -> None:
    _validate_directory_chain(shortcut.parent)
    try:
        shortcut.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WindowsRegistrationError("registration_directory_create_failed") from exc
    environment = os.environ.copy()
    environment.update(
        {
            "ATC_SHORTCUT_PATH": str(shortcut),
            "ATC_SHORTCUT_TARGET": str(executable),
            "ATC_SHORTCUT_ARGUMENTS": arguments,
            "ATC_SHORTCUT_DESCRIPTION": description,
            "ATC_SHORTCUT_WORKDIR": str(executable.parent),
        }
    )
    script = (
        "$shell=New-Object -ComObject WScript.Shell;"
        "$link=$shell.CreateShortcut($env:ATC_SHORTCUT_PATH);"
        "$link.TargetPath=$env:ATC_SHORTCUT_TARGET;"
        "$link.Arguments=$env:ATC_SHORTCUT_ARGUMENTS;"
        "$link.Description=$env:ATC_SHORTCUT_DESCRIPTION;"
        "$link.WorkingDirectory=$env:ATC_SHORTCUT_WORKDIR;"
        "$link.IconLocation=$env:ATC_SHORTCUT_TARGET + ',0';"
        "$link.Save()"
    )
    try:
        completed = subprocess.run(
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
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=environment,
            creationflags=windows_creation_flags("CREATE_NO_WINDOW"),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowsRegistrationError("registration_shortcut_create_failed") from exc
    if completed.returncode != 0:
        raise WindowsRegistrationError("registration_shortcut_create_failed")
    _validate_file_path(shortcut, allow_missing=False)


class WindowsApplicationRegistrationTransaction:
    """Reversible transaction for only the ATC-owned Windows registrations."""

    _REGISTRY_NAMES: tuple[str, ...] = (
        "DisplayName",
        "DisplayVersion",
        "Publisher",
        "InstallLocation",
        "DisplayIcon",
        "UninstallString",
        "NoModify",
        "NoRepair",
    )

    def __init__(
        self,
        executable: Path,
        *,
        start_menu: Path,
        desktop: Path | None,
        uninstall_key: str = WINDOWS_UNINSTALL_KEY,
        registry: Any | None = None,
        install_root: Path | None = None,
        journal_path: Path | None = None,
    ) -> None:
        self._executable = _absolute_path(Path(executable))
        self._start_menu = _absolute_path(Path(start_menu))
        self._desktop = _absolute_path(Path(desktop)) if desktop is not None else None
        self._uninstall_key = uninstall_key
        self._registry = registry
        self._install_root = (
            _absolute_path(Path(install_root))
            if install_root is not None
            else _windows_install_root()
        )
        self._journal_path = (
            _absolute_path(Path(journal_path))
            if journal_path is not None
            else _registration_journal_path(self._install_root)
        )
        self._journal: _RegistrationJournal | None = None
        self._snapshot: WindowsApplicationRegistrationSnapshot | None = None
        self._mutations: list[_ShortcutMutation | _RegistryMutation] = []
        self._key_created = False
        self._applied = False
        self._canonical_shortcut_cache: dict[ShortcutName, bytes] = {}

    def _check_platform_and_plan(self) -> Any:
        if platform.system() != "Windows":
            raise WindowsRegistrationError("registration_windows_only")
        if not _is_safe_uninstall_key(self._uninstall_key):
            raise WindowsRegistrationError("registration_key_path_invalid")
        if self._start_menu.name.casefold() != "all the context":
            raise WindowsRegistrationError("registration_path_unexpected")
        if self._desktop is not None and _same_path(self._desktop, self._start_menu):
            raise WindowsRegistrationError("registration_path_unexpected")
        if not _same_path(self._install_root, _windows_install_root()):
            raise WindowsRegistrationError("registration_install_root_mismatch")
        expected_executable = self._install_root / WINDOWS_APP_NAME
        if not _same_path(self._executable, expected_executable):
            raise WindowsRegistrationError("registration_executable_mismatch")
        expected_journal = _registration_journal_path(self._install_root)
        if not _same_path(self._journal_path, expected_journal):
            raise WindowsRegistrationError("registration_journal_path_invalid")
        _validate_directory_chain(self._install_root)
        _validate_directory_chain(self._start_menu)
        if self._desktop is not None:
            _validate_directory_chain(self._desktop)
        executable_metadata = _validate_file_path(self._executable, allow_missing=False)
        if executable_metadata is None:
            raise WindowsRegistrationError("registration_executable_missing")
        winreg = self._registry if self._registry is not None else windows_registry()
        return winreg

    def _shortcut_plans(self) -> tuple[_ShortcutPlan, ...]:
        plans: list[_ShortcutPlan] = [
            _ShortcutPlan(
                "launcher",
                self._start_menu / "All The Context.lnk",
                "",
                "Open your local All The Context Core",
            )
        ]
        if self._desktop is not None:
            plans.append(
                _ShortcutPlan(
                    "desktop",
                    self._desktop / "All The Context.lnk",
                    "",
                    "Open your local All The Context Core",
                )
            )
        plans.append(
            _ShortcutPlan(
                "uninstall",
                self._start_menu / "Uninstall All The Context.lnk",
                "--uninstall",
                "Uninstall All The Context (your context data is kept)",
            )
        )
        return tuple(plans)

    def _desired_registry(self) -> tuple[tuple[str, int, RegistryData], ...]:
        target = self._executable
        string_values: tuple[tuple[str, int, RegistryData], ...] = (
            ("DisplayName", 1, "All The Context"),
            ("DisplayVersion", 1, __version__),
            ("Publisher", 1, "All The Context"),
            ("InstallLocation", 1, str(target.parent)),
            ("DisplayIcon", 1, str(target)),
            ("UninstallString", 1, subprocess.list2cmdline([str(target), "--uninstall"])),
        )
        winreg = self._registry if self._registry is not None else windows_registry()
        return (
            *string_values,
            ("NoModify", int(winreg.REG_DWORD), 1),
            ("NoRepair", int(winreg.REG_DWORD), 1),
        )

    def _plan_token(self) -> str:
        return _plan_token(self._executable, self._start_menu, self._desktop, self._uninstall_key)

    def _owned_names(self) -> tuple[RegistrationName, ...]:
        names: list[RegistrationName] = []
        for plan in self._shortcut_plans():
            names.append(cast(RegistrationName, plan.name))
        names.extend(cast(tuple[RegistrationName, ...], self._REGISTRY_NAMES))
        return tuple(names)

    def _validate_journal(self, journal: _RegistrationJournal) -> None:
        if journal.phase not in {
            "applying",
            "migrating",
            "installed",
            "restoring",
            "uninstalling",
        }:
            raise WindowsRegistrationError("registration_journal_invalid")
        if not _same_path(Path(journal.install_root), self._install_root):
            raise WindowsRegistrationError("registration_journal_mismatch")
        if not _same_path(Path(journal.executable), self._executable):
            raise WindowsRegistrationError("registration_journal_mismatch")
        if not _same_path(Path(journal.start_menu), self._start_menu):
            raise WindowsRegistrationError("registration_journal_mismatch")
        if (journal.desktop is None) != (self._desktop is None) or (
            journal.desktop is not None
            and self._desktop is not None
            and not _same_path(Path(journal.desktop), self._desktop)
        ):
            raise WindowsRegistrationError("registration_journal_mismatch")
        if journal.uninstall_key != self._uninstall_key:
            raise WindowsRegistrationError("registration_journal_mismatch")
        if journal.snapshot.plan_token != self._plan_token():
            raise WindowsRegistrationError("registration_journal_mismatch")
        if journal.registry_key_created and journal.snapshot.uninstall_key_present:
            raise WindowsRegistrationError("registration_journal_invalid")
        owned = self._owned_names()
        if set(journal.active) - set(owned) or len(set(journal.active)) != len(journal.active):
            raise WindowsRegistrationError("registration_journal_invalid")
        shortcut_names = tuple(plan.name for plan in self._shortcut_plans())
        snapshot_shortcuts = tuple(item.name for item in journal.snapshot.shortcuts)
        if snapshot_shortcuts != shortcut_names:
            raise WindowsRegistrationError("registration_journal_invalid")
        snapshot_registry = tuple(item.name for item in journal.snapshot.registry_values)
        if snapshot_registry != self._REGISTRY_NAMES:
            raise WindowsRegistrationError("registration_journal_invalid")
        if set(journal.desired_shortcuts) - set(shortcut_names):
            raise WindowsRegistrationError("registration_journal_invalid")
        if set(journal.desired_shortcut_identities) - set(shortcut_names):
            raise WindowsRegistrationError("registration_journal_invalid")
        if set(journal.desired_shortcut_identities) - set(journal.desired_shortcuts):
            raise WindowsRegistrationError("registration_journal_invalid")
        for shortcut_name, data in journal.desired_shortcuts.items():
            if data != self._canonical_shortcut_for_name(shortcut_name):
                raise WindowsRegistrationError("registration_journal_mismatch")
        if set(journal.registry_before) - set(self._REGISTRY_NAMES):
            raise WindowsRegistrationError("registration_journal_invalid")
        for registry_name, snapshot in journal.registry_before.items():
            if snapshot.name != registry_name:
                raise WindowsRegistrationError("registration_journal_invalid")
        desired_registry = {
            name: (value_type, data) for name, value_type, data in self._desired_registry()
        }
        if set(journal.desired_registry) != set(desired_registry):
            raise WindowsRegistrationError("registration_journal_mismatch")
        for name, expected in desired_registry.items():
            observed = journal.desired_registry[name]
            if name != "DisplayVersion":
                if observed != expected:
                    raise WindowsRegistrationError("registration_journal_mismatch")
                continue
            if observed[0] != expected[0] or not isinstance(observed[1], str):
                raise WindowsRegistrationError("registration_journal_mismatch")
            try:
                ReleaseVersion.parse(observed[1])
            except ManifestError:
                raise WindowsRegistrationError("registration_journal_invalid") from None
        if journal.phase == "installed" and set(journal.active) != set(owned):
            raise WindowsRegistrationError("registration_journal_invalid")
        if journal.phase == "installed" and set(journal.desired_shortcuts) != set(shortcut_names):
            raise WindowsRegistrationError("registration_journal_invalid")
        if journal.phase == "installed" and set(journal.desired_shortcut_identities) != set(
            shortcut_names
        ):
            raise WindowsRegistrationError("registration_journal_invalid")
        if journal.phase == "installed" and journal.registry_before:
            raise WindowsRegistrationError("registration_journal_invalid")
        if journal.phase == "migrating" and journal.active != ("DisplayVersion",):
            raise WindowsRegistrationError("registration_journal_invalid")
        if journal.phase == "migrating" and set(journal.registry_before) != {"DisplayVersion"}:
            raise WindowsRegistrationError("registration_journal_invalid")
        for active_name in journal.active:
            if active_name in shortcut_names and active_name not in journal.desired_shortcuts:
                raise WindowsRegistrationError("registration_journal_invalid")
            if (
                active_name in shortcut_names
                and active_name not in journal.desired_shortcut_identities
            ):
                raise WindowsRegistrationError("registration_journal_invalid")
            if (
                active_name == "DisplayVersion"
                and journal.phase in {"applying", "migrating", "restoring"}
                and active_name not in journal.registry_before
            ):
                raise WindowsRegistrationError("registration_journal_invalid")

    def _load_journal(self) -> _RegistrationJournal | None:
        journal = _read_registration_journal(self._journal_path)
        if journal is not None:
            if journal.legacy:
                journal = self._upgrade_legacy_journal(journal)
            self._validate_journal(journal)
            self._journal = journal
        return journal

    def _upgrade_legacy_journal(self, journal: _RegistrationJournal) -> _RegistrationJournal:
        """Reject schema 1 because it contains no authenticated ownership proof.

        Schema 1 was plaintext and did not persist the post-publication shortcut
        identities or an independently verifiable key-creation proof.  A
        same-user rewrite can therefore make a live canonical-looking surface
        appear owned, including an empty pre-existing uninstall key.  Keeping
        the journal in place gives the operator a recoverable artifact without
        authorizing any cleanup.
        """

        del journal
        raise WindowsRegistrationError("registration_journal_invalid")

    def _persist_journal(
        self,
        phase: str,
        active: tuple[RegistrationName, ...],
    ) -> None:
        journal = self._journal
        if journal is None:
            raise WindowsRegistrationError("registration_journal_missing", transaction=self)
        _write_registration_journal(
            self._journal_path,
            journal,
            phase=phase,
            active=active,
        )
        journal.phase = phase
        journal.active = active

    def _prepare_journal(self, snapshot: WindowsApplicationRegistrationSnapshot) -> None:
        if self._journal is not None:
            self._validate_journal(self._journal)
            return
        desired_registry = {
            name: (value_type, data) for name, value_type, data in self._desired_registry()
        }
        journal = _RegistrationJournal(
            str(self._install_root),
            str(self._executable),
            str(self._start_menu),
            str(self._desktop) if self._desktop is not None else None,
            self._uninstall_key,
            snapshot,
            {},
            {},
            desired_registry,
            {},
            "applying",
            (),
        )
        _write_registration_journal(self._journal_path, journal, phase="applying", active=())
        self._journal = journal

    def _clear_journal(self) -> None:
        _remove_registration_journal(self._journal_path)
        self._journal = None

    def _journal_mutations(
        self,
        journal: _RegistrationJournal,
        names: tuple[RegistrationName, ...],
        *,
        remove: bool = False,
    ) -> list[_ShortcutMutation | _RegistryMutation]:
        before_shortcuts = {item.name: item for item in journal.snapshot.shortcuts}
        before_registry = {item.name: item for item in journal.snapshot.registry_values}
        mutations: list[_ShortcutMutation | _RegistryMutation] = []
        shortcut_names = {"launcher", "desktop", "uninstall"}
        for name in names:
            if name in shortcut_names:
                shortcut_name = cast(ShortcutName, name)
                shortcut_before = before_shortcuts[shortcut_name]
                after_data = self._canonical_shortcut_for_name(shortcut_name)
                after_identity = journal.desired_shortcut_identities.get(shortcut_name)
                if after_identity is None:
                    raise WindowsRegistrationError("registration_journal_invalid")
                mutations.append(
                    _ShortcutMutation(
                        shortcut_name,
                        self._shortcut_path(shortcut_name),
                        shortcut_before,
                        after_data,
                        after_identity,
                        remove,
                    )
                )
            else:
                registry_before = (
                    journal.registry_before.get(name) if journal.phase != "installed" else None
                ) or before_registry.get(name)
                if registry_before is None:
                    raise WindowsRegistrationError("registration_journal_invalid")
                desired = {
                    item_name: (value_type, data)
                    for item_name, value_type, data in self._desired_registry()
                }.get(name)
                if desired is None:
                    raise WindowsRegistrationError("registration_journal_invalid")
                value_type, data = desired
                after = WindowsRegistryValueSnapshot(name, True, value_type, data)
                mutations.append(_RegistryMutation(name, registry_before, after, remove))
        return mutations

    def _active_with(self, name: RegistrationName) -> tuple[RegistrationName, ...]:
        if self._journal is None:
            raise WindowsRegistrationError("registration_journal_missing", transaction=self)
        return tuple(dict.fromkeys((*self._journal.active, name)))

    def _shortcut_path(self, name: ShortcutName) -> Path:
        for plan in self._shortcut_plans():
            if plan.name == name:
                return plan.path
        raise WindowsRegistrationError("registration_journal_invalid")

    def _canonical_shortcut_for_name(self, name: ShortcutName) -> bytes:
        for plan in self._shortcut_plans():
            if plan.name == name:
                return self._canonical_shortcut_data(plan)
        raise WindowsRegistrationError("registration_journal_invalid")

    def snapshot(self) -> WindowsApplicationRegistrationSnapshot:
        """Capture the exact bounded preimage before any registration mutation."""

        if self._mutations:
            raise WindowsRegistrationError("registration_restore_required", transaction=self)
        winreg = self._check_platform_and_plan()
        journal = self._load_journal()
        if journal is not None:
            if journal.phase != "installed":
                raise WindowsRegistrationError("registration_recovery_required", transaction=self)
            self._snapshot = journal.snapshot
            return journal.snapshot
        shortcuts = tuple(_shortcut_state(plan.name, plan.path) for plan in self._shortcut_plans())
        key_present, registry_values = _read_registry_state(
            winreg, self._uninstall_key, self._REGISTRY_NAMES
        )
        snapshot = WindowsApplicationRegistrationSnapshot(
            self._plan_token(), key_present, shortcuts, registry_values
        )
        self._snapshot = snapshot
        return snapshot

    def _current_snapshot(self) -> WindowsApplicationRegistrationSnapshot:
        winreg = self._check_platform_and_plan()
        shortcuts = tuple(_shortcut_state(plan.name, plan.path) for plan in self._shortcut_plans())
        key_present, registry_values = _read_registry_state(
            winreg, self._uninstall_key, self._REGISTRY_NAMES
        )
        return WindowsApplicationRegistrationSnapshot(
            self._plan_token(), key_present, shortcuts, registry_values
        )

    def _require_snapshot(
        self, snapshot: WindowsApplicationRegistrationSnapshot | None
    ) -> WindowsApplicationRegistrationSnapshot:
        selected = snapshot or self._snapshot or self.snapshot()
        if not isinstance(selected, WindowsApplicationRegistrationSnapshot):
            raise WindowsRegistrationError("registration_snapshot_invalid", transaction=self)
        if selected.plan_token != self._plan_token():
            raise WindowsRegistrationError("registration_snapshot_mismatch", transaction=self)
        return selected

    def _assert_snapshot_unchanged(self, expected: WindowsApplicationRegistrationSnapshot) -> None:
        current = self._current_snapshot()
        if current.plan_token != expected.plan_token:
            raise WindowsRegistrationError("registration_snapshot_mismatch", transaction=self)
        if current.uninstall_key_present != expected.uninstall_key_present:
            raise WindowsRegistrationError("registration_target_changed", transaction=self)
        if len(current.shortcuts) != len(expected.shortcuts) or any(
            left != right for left, right in zip(current.shortcuts, expected.shortcuts, strict=True)
        ):
            raise WindowsRegistrationError("registration_target_changed", transaction=self)
        if len(current.registry_values) != len(expected.registry_values) or any(
            not _registry_snapshot_equal(left, right)
            for left, right in zip(current.registry_values, expected.registry_values, strict=True)
        ):
            raise WindowsRegistrationError("registration_target_changed", transaction=self)

    def _canonical_shortcut_data(self, plan: _ShortcutPlan) -> bytes:
        cached = self._canonical_shortcut_cache.get(plan.name)
        if cached is not None:
            return cached
        # Recovery may be inspecting the publication temporary itself.  Keep
        # canonical generation on a distinct fixed sibling so that validation
        # never has to guess which temporary object is authoritative.
        temporary = self._canonical_temporary_path(plan.path)
        generated: bytes | None = None
        try:
            generated = self._desired_shortcut_data(plan, temporary)
            self._canonical_shortcut_cache[plan.name] = generated
            return generated
        finally:
            self._cleanup_temporary(temporary, generated)

    def _canonical_shortcuts(self) -> dict[ShortcutName, bytes]:
        return {plan.name: self._canonical_shortcut_data(plan) for plan in self._shortcut_plans()}

    @staticmethod
    def _registry_values_by_name(
        snapshot: WindowsApplicationRegistrationSnapshot,
    ) -> dict[str, WindowsRegistryValueSnapshot]:
        return {value.name: value for value in snapshot.registry_values}

    def _canonical_registry(self) -> dict[str, WindowsRegistryValueSnapshot]:
        return {
            name: WindowsRegistryValueSnapshot(name, True, value_type, data)
            for name, value_type, data in self._desired_registry()
        }

    @staticmethod
    def _is_valid_display_version(value: WindowsRegistryValueSnapshot) -> bool:
        if not value.present or not isinstance(value.data, str):
            return False
        try:
            ReleaseVersion.parse(value.data)
        except (ManifestError, RecursionError):
            return False
        return True

    def _canonical_registry_except_version(
        self,
        current: WindowsApplicationRegistrationSnapshot,
        *,
        version: WindowsRegistryValueSnapshot | None = None,
    ) -> bool:
        observed = self._registry_values_by_name(current)
        canonical = self._canonical_registry()
        for name, expected in canonical.items():
            if name == "DisplayVersion" and version is not None:
                if not _registry_snapshot_equal(observed[name], version):
                    return False
                continue
            if not _registry_snapshot_equal(observed[name], expected):
                return False
        return True

    def _assert_fresh_registration_surface(
        self, snapshot: WindowsApplicationRegistrationSnapshot
    ) -> None:
        """Refuse to overwrite an unproven pre-existing registration surface."""

        if any(shortcut.present for shortcut in snapshot.shortcuts) or any(
            value.present for value in snapshot.registry_values
        ):
            raise WindowsRegistrationError("registration_target_changed", transaction=self)

    def _registration_matches_journal(
        self,
        journal: _RegistrationJournal,
        current: WindowsApplicationRegistrationSnapshot | None = None,
    ) -> bool:
        """Validate a prior installed surface using only canonical state."""

        observed = current or self._current_snapshot()
        if not observed.uninstall_key_present:
            return False
        current_registry = self._registry_values_by_name(observed)
        canonical_registry = self._canonical_registry()
        for name, expected in canonical_registry.items():
            if name == "DisplayVersion":
                journal_version = journal.desired_registry.get(name)
                if journal_version is None:
                    return False
                journal_expected = WindowsRegistryValueSnapshot(
                    name, True, journal_version[0], journal_version[1]
                )
                if not self._is_valid_display_version(journal_expected):
                    return False
                if not _registry_snapshot_equal(current_registry[name], journal_expected):
                    return False
                continue
            if not _registry_snapshot_equal(current_registry[name], expected):
                return False
        canonical_shortcuts = self._canonical_shortcuts()
        current_shortcuts = {shortcut.name: shortcut for shortcut in observed.shortcuts}
        for plan in self._shortcut_plans():
            expected_data = journal.desired_shortcuts.get(plan.name)
            expected_identity = journal.desired_shortcut_identities.get(plan.name)
            current_shortcut = current_shortcuts[plan.name]
            if (
                expected_data is None
                or expected_identity is None
                or expected_data != canonical_shortcuts[plan.name]
                or not current_shortcut.present
                or current_shortcut.data != expected_data
                or current_shortcut.identity != expected_identity
            ):
                return False
        return True

    def _registration_surface_requires_journal(
        self,
        current: WindowsApplicationRegistrationSnapshot,
    ) -> bool:
        """Detect canonical remnants without treating vendor preimages as ATC state."""

        if any(shortcut.present for shortcut in current.shortcuts):
            canonical_shortcuts = self._canonical_shortcuts()
            if any(
                shortcut.present and shortcut.data == canonical_shortcuts[shortcut.name]
                for shortcut in current.shortcuts
            ):
                return True
        expected_registry = {
            name: WindowsRegistryValueSnapshot(name, True, value_type, data)
            for name, value_type, data in self._desired_registry()
        }
        for value in current.registry_values:
            if not value.present:
                continue
            if value.name == "DisplayVersion":
                if isinstance(value.data, str):
                    try:
                        ReleaseVersion.parse(value.data)
                    except ManifestError:
                        continue
                    return True
            elif _registry_snapshot_equal(value, expected_registry[value.name]):
                return True
        return False

    def _complete_version_migration(self, journal: _RegistrationJournal) -> None:
        """Finish a durable forward-only DisplayVersion migration."""

        self._journal = journal
        self._snapshot = journal.snapshot
        desired_tuple = journal.desired_registry.get("DisplayVersion")
        canonical = self._canonical_registry()
        if desired_tuple != (
            canonical["DisplayVersion"].value_type,
            canonical["DisplayVersion"].data,
        ):
            raise WindowsRegistrationError("registration_journal_invalid", transaction=self)
        value_type, data = desired_tuple
        desired = WindowsRegistryValueSnapshot("DisplayVersion", True, value_type, data)
        current = self._current_snapshot()
        current_registry = self._registry_values_by_name(current)
        current_version = current_registry["DisplayVersion"]
        if _registry_snapshot_equal(current_version, desired):
            journal.registry_before.clear()
            self._persist_journal("installed", self._owned_names())
            return
        if not self._is_valid_display_version(current_version) or not (
            self._canonical_registry_except_version(current, version=current_version)
        ):
            raise WindowsRegistrationError("registration_target_changed", transaction=self)
        winreg = self._registry if self._registry is not None else windows_registry()
        key_read_set = int(getattr(winreg, "KEY_READ", 0x20019)) | int(
            getattr(winreg, "KEY_SET_VALUE", 0x0002)
        )
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_read_set
            ) as key:
                current_value = _registry_value_snapshot(winreg, key, "DisplayVersion")
                if not _registry_snapshot_equal(current_value, current_version):
                    raise WindowsRegistrationError("registration_target_changed", transaction=self)
                confirmed_value = _registry_value_snapshot(winreg, key, "DisplayVersion")
                if not _registry_snapshot_equal(confirmed_value, current_version):
                    raise WindowsRegistrationError("registration_target_changed", transaction=self)
                try:
                    _set_registry_value_if_unchanged(
                        winreg,
                        key,
                        current_value,
                        value_type,
                        data,
                    )
                except OSError as exc:
                    raise WindowsRegistrationError(
                        "registration_value_write_failed", transaction=self
                    ) from exc
                after = _registry_value_snapshot(winreg, key, "DisplayVersion")
                if not _registry_snapshot_equal(after, desired):
                    raise WindowsRegistrationError(
                        "registration_value_write_unverified", transaction=self
                    )
        except WindowsRegistrationError:
            raise
        except FileNotFoundError:
            raise WindowsRegistrationError(
                "registration_key_unreadable", transaction=self
            ) from None
        except OSError as exc:
            raise WindowsRegistrationError(
                "registration_value_write_failed", transaction=self
            ) from exc
        journal.registry_before.clear()
        self._persist_journal("installed", self._owned_names())

    def _migrate_installed_journal(self, journal: _RegistrationJournal) -> None:
        desired_registry = {
            name: (value_type, data) for name, value_type, data in self._desired_registry()
        }
        if journal.desired_registry == desired_registry:
            return
        current = self._current_snapshot()
        if not self._registration_matches_journal(journal, current):
            raise WindowsRegistrationError("registration_target_changed", transaction=self)
        old_version = journal.desired_registry.get("DisplayVersion")
        new_version = desired_registry.get("DisplayVersion")
        if old_version is None or new_version is None or old_version == new_version:
            raise WindowsRegistrationError("registration_journal_mismatch", transaction=self)
        journal.desired_registry = desired_registry
        journal.registry_before = {
            "DisplayVersion": self._registry_values_by_name(current)["DisplayVersion"]
        }
        self._journal = journal
        self._persist_journal("migrating", ("DisplayVersion",))
        self._complete_version_migration(journal)

    def _desired_shortcut_data(self, plan: _ShortcutPlan, temporary: Path) -> bytes:
        if _safe_lstat(temporary) is not None:
            raise WindowsRegistrationError("registration_temporary_exists", transaction=self)
        _create_windows_shortcut(
            temporary,
            self._executable,
            arguments=plan.arguments,
            description=plan.description,
        )
        metadata = _validate_file_path(temporary, allow_missing=False)
        if metadata is None:
            raise WindowsRegistrationError("registration_shortcut_create_failed", transaction=self)
        data = _read_bounded_file(temporary, metadata)
        final_metadata = _validate_file_path(temporary, allow_missing=False)
        if final_metadata is None or _file_identity(final_metadata) != _file_identity(metadata):
            raise WindowsRegistrationError("registration_temporary_changed", transaction=self)
        return data

    @staticmethod
    def _temporary_path(path: Path) -> Path:
        # A fixed sibling is recoverable after a process crash and is not an
        # attacker-selected path read from the journal.
        return path.with_name(f".{path.name}.atc-new")

    @staticmethod
    def _canonical_temporary_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.atc-canonical")

    @staticmethod
    def _cleanup_temporary(path: Path, data: bytes | None) -> None:
        metadata = _safe_lstat(path)
        if metadata is None:
            return
        if _is_reparse_or_link(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise WindowsRegistrationError("registration_temporary_unsafe")
        observed_data = _read_bounded_file(path, metadata)
        if data is not None and observed_data != data:
            raise WindowsRegistrationError("registration_temporary_changed")
        _quarantine_and_remove_shortcut(path, _file_identity(metadata), observed_data)

    @staticmethod
    def _publish_new_shortcut(temporary: Path, target: Path, data: bytes | None = None) -> None:
        temporary_metadata = _validate_file_path(temporary, allow_missing=False)
        if temporary_metadata is None:
            raise WindowsRegistrationError("registration_temporary_changed")
        temporary_identity = _file_identity(temporary_metadata)
        temporary_data = _read_bounded_file(temporary, temporary_metadata)
        if data is not None and data != temporary_data:
            raise WindowsRegistrationError("registration_temporary_changed")
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise WindowsRegistrationError("registration_target_changed") from exc
        except OSError as exc:
            raise WindowsRegistrationError("registration_shortcut_publish_failed") from exc
        try:
            _quarantine_and_remove_shortcut(temporary, temporary_identity, temporary_data)
        except WindowsRegistrationError:
            # The target link is intentionally left in place.  The durable
            # journal records its identity and recovery can converge the
            # target/temporary nlink==2 state without guessing a path.
            raise

    @staticmethod
    def _publish_existing_shortcut(
        temporary: Path, target: Path, before: WindowsShortcutSnapshot
    ) -> None:
        del temporary, target, before
        # There is no cross-platform compare-and-replace primitive for a
        # directory entry.  Refusing this branch is the no-clobber transition;
        # a recoverable journal remains preferable to overwriting a swapped
        # user/vendor shortcut.
        raise WindowsRegistrationError("registration_shortcut_replace_unsupported")

    def _apply_shortcut(
        self,
        plan: _ShortcutPlan,
        before: WindowsShortcutSnapshot,
        changed_entries: list[RegistrationName],
    ) -> None:
        current = _shortcut_state(plan.name, plan.path)
        if current != before:
            raise WindowsRegistrationError("registration_target_changed")
        temporary = self._temporary_path(plan.path)
        temporary_owned = _safe_lstat(temporary) is None
        generated: bytes | None = None
        try:
            generated = self._desired_shortcut_data(plan, temporary)
            temporary_metadata = _validate_file_path(temporary, allow_missing=False)
            if temporary_metadata is None:
                raise WindowsRegistrationError(
                    "registration_shortcut_create_failed", transaction=self
                )
            temporary_identity = _file_identity(temporary_metadata)
            self._canonical_shortcut_cache[plan.name] = generated
            current = _shortcut_state(plan.name, plan.path)
            if current != before:
                raise WindowsRegistrationError("registration_target_changed")
            if before.present and before.data == generated:
                if self._journal is None:
                    raise WindowsRegistrationError("registration_journal_missing", transaction=self)
                self._journal.desired_shortcuts[plan.name] = generated
                if before.identity is None:
                    raise WindowsRegistrationError("registration_target_changed", transaction=self)
                self._journal.desired_shortcut_identities[plan.name] = before.identity
                self._persist_journal("applying", self._journal.active)
                return
            if self._journal is None:
                raise WindowsRegistrationError("registration_journal_missing", transaction=self)
            self._journal.desired_shortcuts[plan.name] = generated
            self._journal.desired_shortcut_identities[plan.name] = temporary_identity
            self._persist_journal("applying", self._active_with(plan.name))
            mutation = _ShortcutMutation(
                plan.name,
                plan.path,
                before,
                generated,
                temporary_identity,
            )
            self._mutations.append(mutation)
            if before.present:
                self._publish_existing_shortcut(temporary, plan.path, before)
            else:
                self._publish_new_shortcut(temporary, plan.path, generated)
            after = _shortcut_state(plan.name, plan.path)
            if not after.present or after.data != generated or after.identity != temporary_identity:
                raise WindowsRegistrationError("registration_shortcut_publish_unverified")
            mutation.after_identity = after.identity
            if after.identity is None:
                raise WindowsRegistrationError("registration_shortcut_publish_unverified")
            self._journal.desired_shortcut_identities[plan.name] = after.identity
            self._persist_journal("applying", self._journal.active)
            changed_entries.append(plan.name)
        finally:
            if temporary_owned:
                self._cleanup_temporary(temporary, generated)

    def _apply_registry(
        self,
        expected: WindowsApplicationRegistrationSnapshot,
        changed_entries: list[RegistrationName],
    ) -> None:
        winreg = self._registry if self._registry is not None else windows_registry()
        key_read_set = int(getattr(winreg, "KEY_READ", 0x20019)) | int(
            getattr(winreg, "KEY_SET_VALUE", 0x0002)
        )
        key: Any | None = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_read_set)
        except FileNotFoundError:
            try:
                key, created = _create_registry_key_if_absent(winreg, self._uninstall_key)
            except WindowsRegistrationError:
                raise
            except OSError as exc:
                raise WindowsRegistrationError("registration_key_create_failed") from exc
            if created is False or (created is None and expected.uninstall_key_present):
                raise WindowsRegistrationError("registration_target_changed") from None
            self._key_created = created is True
            if self._key_created and self._journal is not None:
                self._journal.registry_key_created = True
                self._persist_journal("applying", self._journal.active)
        except OSError as exc:
            raise WindowsRegistrationError("registration_key_open_failed") from exc

        desired = self._desired_registry()
        before_by_name = {value.name: value for value in expected.registry_values}
        try:
            for name, value_type, data in desired:
                before = before_by_name[name]
                current = _registry_value_snapshot(winreg, key, name)
                if not _registry_snapshot_equal(current, before):
                    raise WindowsRegistrationError("registration_target_changed")
                confirmed = _registry_value_snapshot(winreg, key, name)
                if not _registry_snapshot_equal(confirmed, before):
                    raise WindowsRegistrationError("registration_target_changed")
                desired_value = WindowsRegistryValueSnapshot(name, True, value_type, data)
                if _registry_snapshot_equal(current, desired_value):
                    continue
                mutation = _RegistryMutation(name, before, desired_value)
                if self._journal is None:
                    raise WindowsRegistrationError("registration_journal_missing", transaction=self)
                self._journal.registry_before[name] = before
                self._persist_journal("applying", self._active_with(cast(RegistrationName, name)))
                self._mutations.append(mutation)
                try:
                    _set_registry_value_if_unchanged(
                        winreg,
                        key,
                        before,
                        value_type,
                        data,
                    )
                except OSError as exc:
                    raise WindowsRegistrationError("registration_value_write_failed") from exc
                after = _registry_value_snapshot(winreg, key, name)
                if not _registry_snapshot_equal(after, desired_value):
                    raise WindowsRegistrationError("registration_value_write_unverified")
                changed_entries.append(name)  # type: ignore[arg-type]
        finally:
            if key is not None:
                with suppress(OSError):
                    key.Close()

    def apply(
        self,
        snapshot: WindowsApplicationRegistrationSnapshot | None = None,
    ) -> WindowsRegistrationApplyResult:
        """Apply registration and compensate every mutation if any step fails."""

        if self._applied:
            try:
                current = self._current_snapshot()
            except BaseException as exc:
                raise WindowsRegistrationError(_safe_error_code(exc), transaction=self) from exc
            if not self._registration_is_desired(current):
                raise WindowsRegistrationError("registration_target_changed", transaction=self)
            return WindowsRegistrationApplyResult(self._snapshot or current, ())
        if self._mutations:
            raise WindowsRegistrationError("registration_restore_required", transaction=self)

        selected = self._require_snapshot(snapshot)
        self._snapshot = selected
        journal = self._journal or self._load_journal()
        if journal is not None:
            if journal.phase == "installed":
                current = self._current_snapshot()
                if not self._registration_is_desired(current):
                    self._migrate_installed_journal(journal)
                if not self._registration_is_desired(self._current_snapshot()):
                    raise WindowsRegistrationError("registration_target_changed", transaction=self)
                self._applied = True
                return WindowsRegistrationApplyResult(selected, ())
            raise WindowsRegistrationError("registration_recovery_required", transaction=self)
        changed_entries: list[RegistrationName] = []
        try:
            self._assert_snapshot_unchanged(selected)
            self._assert_fresh_registration_surface(selected)
            self._prepare_journal(selected)
            by_name = {shortcut.name: shortcut for shortcut in selected.shortcuts}
            for plan in self._shortcut_plans():
                self._apply_shortcut(plan, by_name[plan.name], changed_entries)
            self._apply_registry(selected, changed_entries)
            if self._journal is None:
                raise WindowsRegistrationError("registration_journal_missing", transaction=self)
            self._journal.registry_before.clear()
            self._persist_journal("installed", self._owned_names())
        except BaseException as exc:
            status = self.restore()
            if not status.complete:
                raise WindowsRegistrationCompensationError(
                    "registration_compensation_required",
                    transaction=self,
                    status=status,
                ) from exc
            raise WindowsRegistrationError(_safe_error_code(exc), transaction=self) from exc
        self._applied = True
        return WindowsRegistrationApplyResult(selected, tuple(changed_entries))

    def _registration_is_desired(self, current: WindowsApplicationRegistrationSnapshot) -> bool:
        return self._registration_is_desired_checked(current)

    def _registration_is_desired_checked(
        self,
        current: WindowsApplicationRegistrationSnapshot,
        *,
        verify_canonical_shortcuts: bool = False,
    ) -> bool:
        del verify_canonical_shortcuts
        if not current.uninstall_key_present:
            return False
        registry = self._canonical_registry()
        if any(
            not _registry_snapshot_equal(value, registry[value.name])
            for value in current.registry_values
        ):
            return False
        canonical_shortcuts = self._canonical_shortcuts()
        shortcut_by_name = {shortcut.name: shortcut for shortcut in current.shortcuts}
        for plan in self._shortcut_plans():
            shortcut = shortcut_by_name[plan.name]
            expected_data = canonical_shortcuts[plan.name]
            if not shortcut.present or shortcut.data != expected_data:
                return False
            expected_identity: _FileIdentity | None = None
            if self._journal is not None:
                expected_identity = self._journal.desired_shortcut_identities.get(plan.name)
            if expected_identity is None:
                for mutation in self._mutations:
                    if isinstance(mutation, _ShortcutMutation) and mutation.name == plan.name:
                        expected_identity = mutation.after_identity
                        break
            if expected_identity is None or shortcut.identity != expected_identity:
                return False
        return True

    def _restore_shortcut(self, mutation: _ShortcutMutation) -> None:
        before = mutation.before
        if mutation.remove and before.present:
            raise WindowsRegistrationError("registration_restore_target_changed")
        expected_identity = mutation.after_identity
        if expected_identity is None:
            raise WindowsRegistrationError("registration_restore_identity_unavailable")
        canonical = self._canonical_shortcut_for_name(mutation.name)
        if mutation.after_data != canonical:
            raise WindowsRegistrationError("registration_restore_target_changed")

        temporary = self._temporary_path(mutation.path)
        temporary_metadata = _safe_lstat(temporary)
        target_metadata = _safe_lstat(mutation.path)
        if temporary_metadata is not None:
            if _is_reparse_or_link(temporary_metadata) or not stat.S_ISREG(
                temporary_metadata.st_mode
            ):
                raise WindowsRegistrationError("registration_restore_target_changed")
            temporary_identity = _file_identity(temporary_metadata)
            temporary_data = _read_bounded_file(temporary, temporary_metadata)
            if (
                target_metadata is not None
                and int(target_metadata.st_nlink) == 2
                and int(temporary_metadata.st_nlink) == 2
                and not _is_reparse_or_link(target_metadata)
                and stat.S_ISREG(target_metadata.st_mode)
                and _same_file_identity(_file_identity(target_metadata), temporary_identity)
                and _same_file_identity(temporary_identity, expected_identity)
                and temporary_data == canonical
                and _read_bounded_file(mutation.path, target_metadata) == canonical
            ):
                # This is the exact crash window after os.link and before the
                # temporary hardlink cleanup.  Removing the fixed temporary
                # entry leaves the canonical target at nlink==1.
                _quarantine_and_remove_shortcut(temporary, expected_identity, temporary_data)
            elif _same_file_identity(temporary_identity, expected_identity) and (
                temporary_data == canonical
            ):
                _quarantine_and_remove_shortcut(temporary, expected_identity, temporary_data)
            else:
                raise WindowsRegistrationError("registration_restore_target_changed")

        # A failed quarantine cleanup is itself recoverable.  Revisit the
        # deterministic artifact before touching the canonical target.
        target_quarantine = _shortcut_quarantine_path(mutation.path)
        target_quarantine_metadata = _safe_lstat(target_quarantine)
        if target_quarantine_metadata is not None:
            if _is_reparse_or_link(target_quarantine_metadata) or not stat.S_ISREG(
                target_quarantine_metadata.st_mode
            ):
                raise WindowsRegistrationError("registration_restore_target_changed")
            _quarantine_and_remove_shortcut(
                target_quarantine,
                expected_identity,
                _read_bounded_file(target_quarantine, target_quarantine_metadata),
            )

        current = _shortcut_state(mutation.name, mutation.path)
        if not mutation.remove and before.present and current == before:
            return
        if not current.present:
            if not before.present:
                return
            raise WindowsRegistrationError("registration_restore_identity_unavailable")
        if (
            current.data != canonical
            or current.identity is None
            or not _same_file_identity(current.identity, expected_identity)
        ):
            raise WindowsRegistrationError("registration_restore_target_changed")
        _quarantine_and_remove_shortcut(mutation.path, expected_identity, canonical)

    def _restore_registry(self, mutation: _RegistryMutation) -> None:
        winreg = self._registry if self._registry is not None else windows_registry()
        key_read_set = int(getattr(winreg, "KEY_READ", 0x20019)) | int(
            getattr(winreg, "KEY_SET_VALUE", 0x0002)
        )
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_read_set
            ) as key:
                current = _registry_value_snapshot(winreg, key, mutation.name)
                if not mutation.remove and _registry_snapshot_equal(current, mutation.before):
                    return
                if mutation.remove and not current.present:
                    return
                canonical = self._canonical_registry().get(mutation.name)
                if canonical is None or not _registry_snapshot_equal(current, canonical):
                    raise WindowsRegistrationError("registration_restore_target_changed")
                _delete_registry_value_if_unchanged(winreg, key, canonical)
                restored = _registry_value_snapshot(winreg, key, mutation.name)
                if restored.present:
                    raise WindowsRegistrationError("registration_restore_unverified")
        except FileNotFoundError:
            if not mutation.before.present:
                return
            raise WindowsRegistrationError("registration_restore_key_missing") from None
        except WindowsRegistrationError:
            raise
        except OSError as exc:
            raise WindowsRegistrationError("registration_restore_value_failed") from exc

    def _delete_created_registry_key(self) -> bool:
        """Delete an exclusively ATC-created key only when its whole surface is exact."""

        if not self._key_created:
            return False
        winreg = self._registry if self._registry is not None else windows_registry()
        key_read = int(getattr(winreg, "KEY_READ", 0x20019))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_read) as key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
                if int(subkeys) != 0:
                    return False
                canonical = self._canonical_registry()
                observed = tuple(
                    _registry_value_snapshot(winreg, key, name) for name in self._REGISTRY_NAMES
                )
                if any(
                    value.present and not _registry_snapshot_equal(value, canonical[value.name])
                    for value in observed
                ):
                    return False
                if int(values) != sum(value.present for value in observed):
                    return False
        except FileNotFoundError:
            self._key_created = False
            return True
        except WindowsRegistrationError:
            raise
        except OSError as exc:
            raise WindowsRegistrationError("registration_restore_key_unreadable") from exc
        _delete_registry_key_if_unchanged(winreg, self._uninstall_key, observed)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_read):
                pass
        except FileNotFoundError:
            self._key_created = False
            return True
        except OSError as exc:
            raise WindowsRegistrationError("registration_restore_key_unverified") from exc
        raise WindowsRegistrationError("registration_restore_key_unverified")

    def _restore_created_key(self) -> None:
        if not self._key_created:
            return
        self._delete_created_registry_key()

    def _recover_journal(self, journal: _RegistrationJournal) -> WindowsRegistrationRestoreStatus:
        if journal.phase == "migrating":
            self._complete_version_migration(journal)
            return WindowsRegistrationRestoreStatus(True, False, 0)
        self._snapshot = journal.snapshot
        self._journal = journal
        # The journal records whether CreateKey reported a new key, but that
        # same-user-readable record is not sufficient to authorize a later
        # path-based DeleteKey.  Only the live transaction may delete a key it
        # created; recovery removes owned values and preserves the key.
        self._key_created = False
        self._mutations = self._journal_mutations(journal, journal.active)
        return self.restore(journal.snapshot)

    def uninstall(self) -> WindowsRegistrationRestoreStatus:
        """Remove only the registrations owned by the durable installed record."""

        self._check_platform_and_plan()
        journal = self._load_journal()
        if journal is None:
            current = self._current_snapshot()
            if self._registration_surface_requires_journal(current):
                raise WindowsRegistrationError("registration_journal_missing", transaction=self)
            return WindowsRegistrationRestoreStatus(True, False, 0)
        if journal.phase != "installed":
            raise WindowsRegistrationError("registration_recovery_required", transaction=self)
        current = self._current_snapshot()
        if not self._registration_is_desired_checked(current, verify_canonical_shortcuts=True):
            self._migrate_installed_journal(journal)
            current = self._current_snapshot()
        if not self._registration_is_desired_checked(current, verify_canonical_shortcuts=True):
            raise WindowsRegistrationError("registration_target_changed", transaction=self)
        self._snapshot = journal.snapshot
        # Do not turn same-user-rewritable journal metadata into a destructive
        # DeleteKey authorization.  This transaction did not create the key;
        # value cleanup can proceed while the key itself remains recoverable.
        self._key_created = False
        owned = self._owned_names()
        self._mutations = self._journal_mutations(journal, owned, remove=True)
        self._persist_journal("uninstalling", owned)
        return self.restore(journal.snapshot)

    def restore(
        self,
        snapshot: WindowsApplicationRegistrationSnapshot | None = None,
    ) -> WindowsRegistrationRestoreStatus:
        """Restore the saved preimage; keep unresolved mutations for bounded retry."""

        selected = snapshot or self._snapshot
        if selected is not None and selected.plan_token != self._plan_token():
            raise WindowsRegistrationError("registration_snapshot_mismatch", transaction=self)
        if selected is None and not self._mutations and not self._key_created:
            self._applied = False
            return WindowsRegistrationRestoreStatus(True, False, 0)

        restored_count = 0
        errors: list[str] = []
        active_before = tuple(
            dict.fromkeys(cast(RegistrationName, mutation.name) for mutation in self._mutations)
        )
        if self._journal is not None and active_before:
            try:
                self._persist_journal("restoring", active_before)
            except BaseException as exc:
                errors.append(_safe_error_code(exc))
        if self._key_created:
            try:
                if self._delete_created_registry_key():
                    registry_mutations = {
                        mutation.name
                        for mutation in self._mutations
                        if isinstance(mutation, _RegistryMutation)
                    }
                    removed = len(registry_mutations)
                    self._mutations = [
                        mutation
                        for mutation in self._mutations
                        if not isinstance(mutation, _RegistryMutation)
                    ]
                    restored_count += removed
            except BaseException as exc:
                errors.append(_safe_error_code(exc))
        for mutation in tuple(reversed(self._mutations)):
            try:
                if isinstance(mutation, _ShortcutMutation):
                    self._restore_shortcut(mutation)
                else:
                    self._restore_registry(mutation)
            except BaseException as exc:
                errors.append(_safe_error_code(exc))
                continue
            with suppress(ValueError):
                self._mutations.remove(mutation)
            restored_count += 1
            if self._journal is not None:
                remaining = tuple(
                    dict.fromkeys(cast(RegistrationName, item.name) for item in self._mutations)
                )
                try:
                    self._persist_journal("restoring", remaining)
                except BaseException as exc:
                    errors.append(_safe_error_code(exc))
        if not self._mutations:
            try:
                self._restore_created_key()
            except BaseException as exc:
                errors.append(_safe_error_code(exc))
        pending: list[str] = []
        for mutation in self._mutations:
            if mutation.name not in pending:
                pending.append(mutation.name)
        if self._key_created and "uninstall_key" not in pending:
            pending.append("uninstall_key")
        if not self._mutations and not self._key_created and self._journal is not None:
            try:
                self._clear_journal()
            except BaseException as exc:
                errors.append(_safe_error_code(exc))
                pending.append("journal")
        bounded_pending = tuple(pending[:_MAX_STATUS_ITEMS])
        bounded_errors = tuple(errors[:_MAX_STATUS_ITEMS])
        complete = not self._mutations and not self._key_created and self._journal is None
        self._applied = False if complete else self._applied
        return WindowsRegistrationRestoreStatus(
            complete,
            not complete,
            restored_count,
            bounded_pending,
            bounded_errors,
        )


def install_application_entrypoints(executable: Path) -> ApplicationRegistration | None:
    """Register a discoverable launcher and per-user uninstaller."""

    if platform.system() != "Windows":
        return None
    target = _absolute_path(executable)
    start_menu, desktop = _windows_locations()
    transaction = WindowsApplicationRegistrationTransaction(
        target,
        start_menu=start_menu,
        desktop=desktop,
        uninstall_key=_windows_uninstall_key(),
        install_root=_windows_install_root(),
    )
    transaction._check_platform_and_plan()
    journal = transaction._load_journal()
    if journal is not None and journal.phase != "installed":
        status = transaction._recover_journal(journal)
        if not status.complete:
            raise WindowsRegistrationCompensationError(
                "registration_recovery_required",
                transaction=transaction,
                status=status,
            )
    transaction.apply(transaction.snapshot())
    launcher = start_menu / "All The Context.lnk"
    desktop_shortcut = desktop / "All The Context.lnk" if desktop else None
    return ApplicationRegistration("Windows", launcher, desktop_shortcut, True)


def recover_application_entrypoints() -> WindowsRegistrationRestoreStatus | None:
    """Replay durable registration evidence after an interrupted operation."""

    if platform.system() != "Windows":
        return None
    start_menu, desktop = _windows_locations()
    transaction = WindowsApplicationRegistrationTransaction(
        _windows_install_root() / WINDOWS_APP_NAME,
        start_menu=start_menu,
        desktop=desktop,
        uninstall_key=_windows_uninstall_key(),
        install_root=_windows_install_root(),
    )
    transaction._check_platform_and_plan()
    journal = transaction._load_journal()
    if journal is None:
        return None
    if journal.phase == "installed":
        transaction._snapshot = journal.snapshot
        current = transaction._current_snapshot()
        if not transaction._registration_is_desired_checked(
            current, verify_canonical_shortcuts=True
        ):
            transaction._migrate_installed_journal(journal)
            current = transaction._current_snapshot()
        if not transaction._registration_is_desired_checked(
            current, verify_canonical_shortcuts=True
        ):
            raise WindowsRegistrationError("registration_target_changed", transaction=transaction)
        return WindowsRegistrationRestoreStatus(True, False, 0)
    status = transaction._recover_journal(journal)
    if not status.complete:
        raise WindowsRegistrationCompensationError(
            "registration_recovery_required",
            transaction=transaction,
            status=status,
        )
    return status


def remove_application_entrypoints() -> None:
    """Remove launchers and uninstall registration without deleting user data."""

    if platform.system() != "Windows":
        return
    start_menu, desktop = _windows_locations()
    transaction = WindowsApplicationRegistrationTransaction(
        _windows_install_root() / WINDOWS_APP_NAME,
        start_menu=start_menu,
        desktop=desktop,
        uninstall_key=_windows_uninstall_key(),
        install_root=_windows_install_root(),
    )
    transaction._check_platform_and_plan()
    journal = transaction._load_journal()
    if journal is None:
        # No durable ownership record means this may be a legacy or shared
        # registration.  Preserve it, but report stale fixed-surface entries
        # instead of silently claiming that uninstall succeeded.
        status = transaction.uninstall()
        if not status.complete:
            raise WindowsRegistrationCompensationError(
                "registration_uninstall_required",
                transaction=transaction,
                status=status,
            )
        return
    if journal.phase != "installed":
        status = transaction._recover_journal(journal)
        if not status.complete:
            raise WindowsRegistrationCompensationError(
                "registration_recovery_required",
                transaction=transaction,
                status=status,
            )
        journal = transaction._load_journal()
        if journal is None:
            return
        if journal.phase != "installed":
            raise WindowsRegistrationError(
                "registration_recovery_required", transaction=transaction
            )
    status = transaction.uninstall()
    if not status.complete:
        raise WindowsRegistrationCompensationError(
            "registration_uninstall_required",
            transaction=transaction,
            status=status,
        )
    with suppress(OSError):
        start_menu.rmdir()

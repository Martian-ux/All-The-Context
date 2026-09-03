"""Per-user application registration behind platform-specific adapters."""

from __future__ import annotations

import copy
import hashlib
import os
import platform
import stat
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import __version__
from .platform_compat import windows_creation_flags, windows_registry

WINDOWS_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AllTheContext"
WINDOWS_APP_ID = "AllTheContext"
WINDOWS_USER_SHELL_FOLDERS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
)
PACKAGED_SMOKE_FLAG = "ATC_PACKAGED_SMOKE"

_MAX_SHORTCUT_BYTES = 1024 * 1024
_MAX_REGISTRY_DATA_BYTES = 64 * 1024
_MAX_STATUS_ITEMS = 16
_REPARSE_POINT = 0x400

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


@dataclass(slots=True)
class _RegistryMutation:
    name: str
    before: WindowsRegistryValueSnapshot
    after: WindowsRegistryValueSnapshot


@dataclass(frozen=True, slots=True)
class _ShortcutPlan:
    name: ShortcutName
    path: Path = field(repr=False)
    arguments: str
    description: str


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


def _read_bounded_file(path: Path, metadata: os.stat_result) -> bytes:
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(opened) != _file_identity(metadata):
                raise WindowsRegistrationError("registration_target_changed")
            data = stream.read(_MAX_SHORTCUT_BYTES + 1)
            if len(data) > _MAX_SHORTCUT_BYTES:
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
    if isinstance(error, PermissionError):
        return "registration_permission_denied"
    if isinstance(error, TimeoutError):
        return "registration_timeout"
    return "registration_step_failed"


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
    ) -> None:
        self._executable = Path(executable)
        self._start_menu = Path(start_menu)
        self._desktop = Path(desktop) if desktop is not None else None
        self._uninstall_key = uninstall_key
        self._registry = registry
        self._snapshot: WindowsApplicationRegistrationSnapshot | None = None
        self._mutations: list[_ShortcutMutation | _RegistryMutation] = []
        self._key_created = False
        self._applied = False

    def _check_platform_and_plan(self) -> Any:
        if platform.system() != "Windows":
            raise WindowsRegistrationError("registration_windows_only")
        if not _is_safe_uninstall_key(self._uninstall_key):
            raise WindowsRegistrationError("registration_key_path_invalid")
        if self._start_menu.name != "All The Context":
            raise WindowsRegistrationError("registration_path_unexpected")
        if self._desktop is not None and self._desktop == self._start_menu:
            raise WindowsRegistrationError("registration_path_unexpected")
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

    def snapshot(self) -> WindowsApplicationRegistrationSnapshot:
        """Capture the exact bounded preimage before any registration mutation."""

        if self._mutations:
            raise WindowsRegistrationError("registration_restore_required", transaction=self)
        winreg = self._check_platform_and_plan()
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
        return _read_bounded_file(temporary, metadata)

    @staticmethod
    def _temporary_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.{os.urandom(8).hex()}.atc-new")

    @staticmethod
    def _cleanup_temporary(path: Path, data: bytes) -> None:
        metadata = _safe_lstat(path)
        if metadata is None:
            return
        if _is_reparse_or_link(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise WindowsRegistrationError("registration_temporary_unsafe")
        if int(metadata.st_nlink) != 1 or _read_bounded_file(path, metadata) != data:
            raise WindowsRegistrationError("registration_temporary_changed")
        try:
            path.unlink()
        except OSError as exc:
            raise WindowsRegistrationError("registration_temporary_cleanup_failed") from exc

    @staticmethod
    def _publish_new_shortcut(temporary: Path, target: Path) -> None:
        try:
            os.link(temporary, target, follow_symlinks=False)
            temporary.unlink()
        except FileExistsError as exc:
            raise WindowsRegistrationError("registration_target_changed") from exc
        except OSError as exc:
            raise WindowsRegistrationError("registration_shortcut_publish_failed") from exc

    @staticmethod
    def _publish_existing_shortcut(
        temporary: Path, target: Path, before: WindowsShortcutSnapshot
    ) -> None:
        current = _shortcut_state(before.name, target)
        if current != before:
            raise WindowsRegistrationError("registration_target_changed")
        try:
            os.replace(temporary, target)
        except OSError as exc:
            raise WindowsRegistrationError("registration_shortcut_publish_failed") from exc

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
        generated: bytes | None = None
        try:
            generated = self._desired_shortcut_data(plan, temporary)
            current = _shortcut_state(plan.name, plan.path)
            if current != before:
                raise WindowsRegistrationError("registration_target_changed")
            if before.present and before.data == generated:
                return
            mutation = _ShortcutMutation(plan.name, plan.path, before, generated)
            self._mutations.append(mutation)
            if before.present:
                self._publish_existing_shortcut(temporary, plan.path, before)
            else:
                self._publish_new_shortcut(temporary, plan.path)
            after = _shortcut_state(plan.name, plan.path)
            if not after.present or after.data != generated:
                raise WindowsRegistrationError("registration_shortcut_publish_unverified")
            mutation.after_identity = after.identity
            changed_entries.append(plan.name)
        finally:
            if generated is not None:
                self._cleanup_temporary(temporary, generated)

    def _apply_registry(
        self,
        expected: WindowsApplicationRegistrationSnapshot,
        changed_entries: list[RegistrationName],
    ) -> None:
        winreg = self._registry if self._registry is not None else windows_registry()
        key_set_value = int(getattr(winreg, "KEY_SET_VALUE", 0x0002))
        key: Any | None = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_set_value)
        except FileNotFoundError:
            self._key_created = not expected.uninstall_key_present
            try:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._uninstall_key)
            except OSError as exc:
                raise WindowsRegistrationError("registration_key_create_failed") from exc
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
                desired_value = WindowsRegistryValueSnapshot(name, True, value_type, data)
                if _registry_snapshot_equal(current, desired_value):
                    continue
                mutation = _RegistryMutation(name, before, desired_value)
                self._mutations.append(mutation)
                try:
                    winreg.SetValueEx(key, name, 0, value_type, _registry_data_for_set(data))
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
        changed_entries: list[RegistrationName] = []
        try:
            self._assert_snapshot_unchanged(selected)
            by_name = {shortcut.name: shortcut for shortcut in selected.shortcuts}
            for plan in self._shortcut_plans():
                self._apply_shortcut(plan, by_name[plan.name], changed_entries)
            self._apply_registry(selected, changed_entries)
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
        if not current.uninstall_key_present:
            return False
        desired = self._desired_registry()
        registry = {
            name: WindowsRegistryValueSnapshot(name, True, value_type, data)
            for name, value_type, data in desired
        }
        if any(
            not _registry_snapshot_equal(value, registry[value.name])
            for value in current.registry_values
        ):
            return False
        before_by_name = {
            shortcut.name: shortcut
            for shortcut in (self._snapshot.shortcuts if self._snapshot else ())
        }
        after_by_name = {
            mutation.name: mutation
            for mutation in self._mutations
            if isinstance(mutation, _ShortcutMutation)
        }
        for shortcut in current.shortcuts:
            mutation = after_by_name.get(shortcut.name)
            expected_data = (
                mutation.after_data if mutation is not None else before_by_name[shortcut.name].data
            )
            expected_identity = (
                mutation.after_identity
                if mutation is not None
                else before_by_name[shortcut.name].identity
            )
            if (
                not shortcut.present
                or shortcut.data != expected_data
                or shortcut.identity != expected_identity
            ):
                return False
        return True

    def _restore_shortcut(self, mutation: _ShortcutMutation) -> None:
        current = _shortcut_state(mutation.name, mutation.path)
        before = mutation.before
        if before.present and current.present and current.data == before.data:
            return
        if not before.present and not current.present:
            return
        if mutation.after_identity is None or not current.present:
            raise WindowsRegistrationError("registration_restore_identity_unavailable")
        if current.identity != mutation.after_identity or current.data != mutation.after_data:
            raise WindowsRegistrationError("registration_restore_target_changed")
        if before.present:
            temporary = self._temporary_path(mutation.path)
            try:
                try:
                    with temporary.open("xb") as stream:
                        stream.write(before.data or b"")
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError as exc:
                    raise WindowsRegistrationError("registration_restore_write_failed") from exc
                metadata = _validate_file_path(temporary, allow_missing=False)
                if metadata is None:
                    raise WindowsRegistrationError("registration_restore_write_failed")
                self._publish_existing_shortcut(temporary, mutation.path, current)
                restored = _shortcut_state(mutation.name, mutation.path)
                if not restored.present or restored.data != before.data:
                    raise WindowsRegistrationError("registration_restore_unverified")
            finally:
                if _safe_lstat(temporary) is not None:
                    with suppress(OSError):
                        temporary.unlink()
        else:
            try:
                mutation.path.unlink()
            except FileNotFoundError:
                return
            except OSError as exc:
                raise WindowsRegistrationError("registration_restore_delete_failed") from exc
            if _safe_lstat(mutation.path) is not None:
                raise WindowsRegistrationError("registration_restore_unverified")

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
                if _registry_snapshot_equal(current, mutation.before):
                    return
                if not _registry_snapshot_equal(current, mutation.after):
                    raise WindowsRegistrationError("registration_restore_target_changed")
                if mutation.before.present:
                    winreg.SetValueEx(
                        key,
                        mutation.name,
                        0,
                        int(mutation.before.value_type or 0),
                        _registry_data_for_set(mutation.before.data),
                    )
                else:
                    try:
                        winreg.DeleteValue(key, mutation.name)
                    except FileNotFoundError:
                        return
                restored = _registry_value_snapshot(winreg, key, mutation.name)
                if not _registry_snapshot_equal(restored, mutation.before):
                    raise WindowsRegistrationError("registration_restore_unverified")
        except FileNotFoundError:
            if not mutation.before.present:
                return
            raise WindowsRegistrationError("registration_restore_key_missing") from None
        except WindowsRegistrationError:
            raise
        except OSError as exc:
            raise WindowsRegistrationError("registration_restore_value_failed") from exc

    def _restore_created_key(self) -> None:
        if not self._key_created:
            return
        winreg = self._registry if self._registry is not None else windows_registry()
        key_read = int(getattr(winreg, "KEY_READ", 0x20019))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._uninstall_key, 0, key_read) as key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
        except FileNotFoundError:
            self._key_created = False
            return
        except OSError as exc:
            raise WindowsRegistrationError("registration_restore_key_unreadable") from exc
        if int(subkeys) != 0 or int(values) != 0:
            # A concurrent or unrelated value owns the key now; preserve it.
            self._key_created = False
            return
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self._uninstall_key)
        except FileNotFoundError:
            self._key_created = False
        except OSError as exc:
            raise WindowsRegistrationError("registration_restore_key_delete_failed") from exc
        else:
            self._key_created = False

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
        bounded_pending = tuple(pending[:_MAX_STATUS_ITEMS])
        bounded_errors = tuple(errors[:_MAX_STATUS_ITEMS])
        complete = not self._mutations and not self._key_created
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
    try:
        target = executable.resolve(strict=True)
    except OSError as exc:
        raise WindowsRegistrationError("registration_executable_unavailable") from exc
    start_menu, desktop = _windows_locations()
    transaction = WindowsApplicationRegistrationTransaction(
        target,
        start_menu=start_menu,
        desktop=desktop,
        uninstall_key=_windows_uninstall_key(),
    )
    transaction.apply(transaction.snapshot())
    launcher = start_menu / "All The Context.lnk"
    desktop_shortcut = desktop / "All The Context.lnk" if desktop else None
    return ApplicationRegistration("Windows", launcher, desktop_shortcut, True)


def remove_application_entrypoints() -> None:
    """Remove launchers and uninstall registration without deleting user data."""

    if platform.system() != "Windows":
        return
    start_menu, desktop = _windows_locations()
    (start_menu / "All The Context.lnk").unlink(missing_ok=True)
    (start_menu / "Uninstall All The Context.lnk").unlink(missing_ok=True)
    with suppress(OSError):
        start_menu.rmdir()
    if desktop is not None:
        (desktop / "All The Context.lnk").unlink(missing_ok=True)

    winreg = windows_registry()

    with suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _windows_uninstall_key())

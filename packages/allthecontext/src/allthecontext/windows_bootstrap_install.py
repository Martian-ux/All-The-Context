"""Transactional frozen-Windows bootstrap installation.

The normal updater has a separate recovery journal and helper because its
installer must outlive the executable being replaced.  Bootstrap runs before
the per-user copy exists (or when that copy is incomplete), so it has a small,
independent journal with a deliberately different schema and lifecycle.

Only the four canonical executable roles are owned here.  Shortcuts,
registry values, client configuration, credentials, and the Core database are
outside this transaction boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout

BOOTSTRAP_JOURNAL_SCHEMA_VERSION = 1
BOOTSTRAP_JOURNAL_NAME = "bootstrap-install-v1.json"
BOOTSTRAP_LOCK_NAME = "bootstrap-install-v1.lock"
BOOTSTRAP_MAX_JOURNAL_BYTES = 64 * 1024
BOOTSTRAP_MAX_COMPONENT_BYTES = 512 * 1024 * 1024
BOOTSTRAP_MAX_TOTAL_COMPONENT_BYTES = 2 * 1024 * 1024 * 1024
BOOTSTRAP_MAX_TRANSACTION_ENTRIES = 32
WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

CANONICAL_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("main", "AllTheContext.exe"),
    ("mcp", "AllTheContextMCP.exe"),
    ("recovery", "AllTheContextRecovery.exe"),
    ("updater", "AllTheContextUpdater.exe"),
)
_ROLES = frozenset(role for role, _name in CANONICAL_COMPONENTS)
_NAMES = frozenset(name.casefold() for _role, name in CANONICAL_COMPONENTS)
_HEX = frozenset("0123456789abcdef")


class BootstrapInstallError(RuntimeError):
    """A bounded bootstrap failure that is safe to show to an operator."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class BootstrapInstallAmbiguity(BootstrapInstallError):
    """The filesystem no longer matches an identity the journal can explain."""

    def __init__(self, code: str = "bootstrap_retry_required") -> None:
        super().__init__(code)


class _OwnedDirectoryCreateFailure(Exception):
    """A directory was created, then its create operation reported failure."""

    def __init__(
        self,
        path: Path,
        created_stat: os.stat_result,
        cause: BaseException,
    ) -> None:
        self.path = path
        self.created_stat = created_stat
        self.cause = cause
        super().__init__(str(cause))


class BootstrapPhase(StrEnum):
    STAGING = "staging"
    STAGED = "staged"
    STOPPING_CORE = "stopping_core"
    CORE_STOPPED = "core_stopped"
    CUTOVER = "cutover"
    VERIFYING = "verifying"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    RETRY_REQUIRED = "retry_required"


_ACTIVE_PHASES = frozenset(
    {
        BootstrapPhase.STAGING,
        BootstrapPhase.STAGED,
        BootstrapPhase.STOPPING_CORE,
        BootstrapPhase.CORE_STOPPED,
        BootstrapPhase.CUTOVER,
        BootstrapPhase.VERIFYING,
        BootstrapPhase.ROLLING_BACK,
        BootstrapPhase.RETRY_REQUIRED,
    }
)
_CLEANUP_PHASES = frozenset(
    {
        BootstrapPhase.COMMITTED,
        BootstrapPhase.ROLLED_BACK,
    }
)


@dataclass(frozen=True, slots=True)
class ComponentIdentity:
    sha256: str
    size: int


@dataclass(slots=True)
class BootstrapComponent:
    role: str
    filename: str
    source_path: str
    target_path: str
    staged_path: str
    source_sha256: str | None = None
    source_size: int | None = None
    staged_sha256: str | None = None
    staged_size: int | None = None
    prior_present: bool = False
    prior_sha256: str | None = None
    prior_size: int | None = None
    backup_path: str | None = None
    backup_sha256: str | None = None
    backup_size: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "filename": self.filename,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "staged_path": self.staged_path,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "staged_sha256": self.staged_sha256,
            "staged_size": self.staged_size,
            "prior_present": self.prior_present,
            "prior_sha256": self.prior_sha256,
            "prior_size": self.prior_size,
            "backup_path": self.backup_path,
            "backup_sha256": self.backup_sha256,
            "backup_size": self.backup_size,
        }

    @classmethod
    def from_dict(cls, value: object) -> BootstrapComponent:
        if not isinstance(value, dict) or set(value) != {
            "role",
            "filename",
            "source_path",
            "target_path",
            "staged_path",
            "source_sha256",
            "source_size",
            "staged_sha256",
            "staged_size",
            "prior_present",
            "prior_sha256",
            "prior_size",
            "backup_path",
            "backup_sha256",
            "backup_size",
        }:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        try:
            component = cls(**cast(dict[str, Any], value))
        except (TypeError, ValueError) as exc:
            raise BootstrapInstallError("bootstrap_journal_invalid") from exc
        return component


@dataclass(slots=True)
class BootstrapInstallJournal:
    operation_id: str
    install_root: str
    transaction_dir: str
    core_was_running: bool
    core_stop_complete: bool
    phase: BootstrapPhase
    cutover_index: int
    core_restart_complete: bool
    components: list[BootstrapComponent]
    created_at: str
    updated_at: str
    last_error_code: str | None = None
    schema_version: int = BOOTSTRAP_JOURNAL_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "install_root": self.install_root,
            "transaction_dir": self.transaction_dir,
            "core_was_running": self.core_was_running,
            "core_stop_complete": self.core_stop_complete,
            "phase": self.phase.value,
            "cutover_index": self.cutover_index,
            "core_restart_complete": self.core_restart_complete,
            "components": [component.as_dict() for component in self.components],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error_code": self.last_error_code,
        }

    def validate(self, path: Path) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != BOOTSTRAP_JOURNAL_SCHEMA_VERSION
        ):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not isinstance(self.operation_id, str) or not _valid_operation_id(self.operation_id):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if path.name != BOOTSTRAP_JOURNAL_NAME:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not isinstance(self.install_root, str) or not self.install_root:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not isinstance(self.transaction_dir, str) or not self.transaction_dir:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not isinstance(self.phase, BootstrapPhase):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not path.is_absolute() or not Path(self.install_root).is_absolute():
            raise BootstrapInstallError("bootstrap_journal_invalid")
        transaction = Path(self.transaction_dir)
        if not transaction.is_absolute() or transaction.name != self.operation_id:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not _same_path(transaction.parent, path.parent):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        install_root = _absolute(Path(self.install_root))
        if (
            not isinstance(self.core_was_running, bool)
            or not isinstance(self.core_stop_complete, bool)
            or not isinstance(self.core_restart_complete, bool)
        ):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if isinstance(self.cutover_index, bool) or not isinstance(self.cutover_index, int):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not 0 <= self.cutover_index <= len(CANONICAL_COMPONENTS):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if not isinstance(self.components, list) or len(self.components) != len(
            CANONICAL_COMPONENTS
        ):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        seen_roles: set[str] = set()
        seen_names: set[str] = set()
        for component, (role, filename) in zip(self.components, CANONICAL_COMPONENTS, strict=True):
            if (
                component.role != role
                or component.filename != filename
                or role in seen_roles
                or filename.casefold() in seen_names
            ):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            seen_roles.add(role)
            seen_names.add(filename.casefold())
            paths = (
                component.source_path,
                component.target_path,
                component.staged_path,
                component.backup_path,
            )
            if any(not isinstance(value, str) or not value for value in paths if value is not None):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            if any(not Path(value).is_absolute() for value in paths if value is not None):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            if not _same_path(Path(component.target_path), install_root / filename):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            if not _same_path(
                Path(component.staged_path),
                transaction / "staged" / filename,
            ):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            if component.backup_path is not None and not _same_path(
                Path(component.backup_path),
                transaction / "backups" / filename,
            ):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            for digest, size in (
                (component.source_sha256, component.source_size),
                (component.staged_sha256, component.staged_size),
                (component.prior_sha256, component.prior_size),
                (component.backup_sha256, component.backup_size),
            ):
                if (digest is None) != (size is None):
                    raise BootstrapInstallError("bootstrap_journal_invalid")
                if digest is not None and not _valid_identity(digest, size):
                    raise BootstrapInstallError("bootstrap_journal_invalid")
            if not isinstance(component.prior_present, bool):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            if component.prior_present != (component.prior_sha256 is not None):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            if component.prior_present != (component.backup_path is not None):
                raise BootstrapInstallError("bootstrap_journal_invalid")
        if self.core_restart_complete and not self.core_was_running:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if self.core_stop_complete and not self.core_was_running:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        if self.core_restart_complete and self.phase not in {
            BootstrapPhase.COMMITTED,
            BootstrapPhase.ROLLED_BACK,
        }:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        for timestamp in (self.created_at, self.updated_at):
            if not isinstance(timestamp, str):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise BootstrapInstallError("bootstrap_journal_invalid") from exc
            if parsed.tzinfo is None:
                raise BootstrapInstallError("bootstrap_journal_invalid")
        if self.last_error_code is not None and (
            not isinstance(self.last_error_code, str)
            or len(self.last_error_code) > 64
            or not self.last_error_code.replace("_", "").isalnum()
        ):
            raise BootstrapInstallError("bootstrap_journal_invalid")

    def save(self, path: Path) -> None:
        self.updated_at = _utc_now()
        self.validate(path)
        _atomic_json(path, self.as_dict())

    @classmethod
    def load(cls, path: Path) -> BootstrapInstallJournal:
        try:
            raw = _read_bounded(path, BOOTSTRAP_MAX_JOURNAL_BYTES)
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except BootstrapInstallError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise BootstrapInstallError("bootstrap_journal_invalid") from exc
        required_fields = {
            "schema_version",
            "operation_id",
            "install_root",
            "transaction_dir",
            "core_was_running",
            "phase",
            "cutover_index",
            "core_restart_complete",
            "components",
            "created_at",
            "updated_at",
            "last_error_code",
        }
        fields = set(value) if isinstance(value, dict) else None
        if fields not in (required_fields, required_fields | {"core_stop_complete"}):
            raise BootstrapInstallError("bootstrap_journal_invalid")
        try:
            phase = BootstrapPhase(value["phase"])
            raw_components = value["components"]
            if not isinstance(raw_components, list):
                raise BootstrapInstallError("bootstrap_journal_invalid")
            journal = cls(
                operation_id=value["operation_id"],
                install_root=value["install_root"],
                transaction_dir=value["transaction_dir"],
                core_was_running=value["core_was_running"],
                core_stop_complete=value.get(
                    "core_stop_complete",
                    bool(
                        value["core_was_running"]
                        and phase
                        in {
                            BootstrapPhase.CORE_STOPPED,
                            BootstrapPhase.CUTOVER,
                            BootstrapPhase.VERIFYING,
                            BootstrapPhase.ROLLED_BACK,
                        }
                    ),
                ),
                phase=phase,
                cutover_index=value["cutover_index"],
                core_restart_complete=value["core_restart_complete"],
                components=[BootstrapComponent.from_dict(item) for item in raw_components],
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                last_error_code=value["last_error_code"],
                schema_version=value["schema_version"],
            )
        except (BootstrapInstallError, TypeError, ValueError) as exc:
            if isinstance(exc, BootstrapInstallError):
                raise
            raise BootstrapInstallError("bootstrap_journal_invalid") from exc
        journal.validate(path)
        return journal


@dataclass(frozen=True, slots=True)
class BootstrapInstallResult:
    install_root: Path
    targets: dict[str, Path]
    recovered: bool = False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_absolute(left))) == os.path.normcase(
        os.fspath(_absolute(right))
    )


def _valid_operation_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 24 and set(value) <= _HEX


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _valid_identity(digest: object, size: object) -> bool:
    return (
        _valid_digest(digest)
        and isinstance(size, int)
        and not isinstance(size, bool)
        and 0 < size <= BOOTSTRAP_MAX_COMPONENT_BYTES
    )


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)


def _same_stat(expected: os.stat_result, observed: os.stat_result) -> bool:
    try:
        same = os.path.samestat(expected, observed)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(
        same
        and stat.S_ISREG(observed.st_mode)
        and not _is_reparse(observed)
        and getattr(observed, "st_nlink", 1) == 1
        and expected.st_size == observed.st_size
        and getattr(expected, "st_mtime_ns", None) == getattr(observed, "st_mtime_ns", None)
    )


def _same_directory_stat(expected: os.stat_result, observed: os.stat_result) -> bool:
    try:
        same = os.path.samestat(expected, observed)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(same and stat.S_ISDIR(observed.st_mode) and not _is_reparse(observed))


def _plain_directory_stat(path: Path, code: str) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        raise BootstrapInstallError(code) from exc
    if _is_reparse(value) or not stat.S_ISDIR(value.st_mode):
        raise BootstrapInstallError(code)
    return value


def _plain_directory_chain(path: Path, code: str) -> None:
    for directory in reversed((_absolute(path), *_absolute(path).parents)):
        _plain_directory_stat(directory, code)


def _plain_directory_chain_if_present(path: Path, code: str) -> bool:
    for directory in reversed((_absolute(path), *_absolute(path).parents)):
        try:
            value = directory.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BootstrapInstallError(code) from exc
        if _is_reparse(value) or not stat.S_ISDIR(value.st_mode):
            raise BootstrapInstallError(code)
    return True


def _plain_directory_chain_stat_if_present(path: Path, code: str) -> os.stat_result | None:
    if not _plain_directory_chain_if_present(path, code):
        return None
    return _plain_directory_stat(path, code)


def _ensure_plain_directory(path: Path, code: str) -> None:
    absolute = _absolute(path)
    missing: list[Path] = []
    current = absolute
    while True:
        try:
            value = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            if current.parent == current:
                raise BootstrapInstallError(code) from None
            current = current.parent
            continue
        except OSError as exc:
            raise BootstrapInstallError(code) from exc
        if _is_reparse(value) or not stat.S_ISDIR(value.st_mode):
            raise BootstrapInstallError(code)
        _plain_directory_chain(current, code)
        break
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise BootstrapInstallError(code) from exc
        _plain_directory_stat(directory, code)
    _plain_directory_chain(absolute, code)


def _create_owned_directory(
    path: Path,
    *,
    parent_expected: os.stat_result,
    code: str,
) -> os.stat_result:
    """Create one new directory and retain its identity for later cleanup."""

    absolute = _absolute(path)
    parent = _plain_directory_stat(absolute.parent, code)
    if not _same_directory_stat(parent_expected, parent):
        raise BootstrapInstallAmbiguity(code)
    try:
        absolute.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise BootstrapInstallError(code) from exc
    else:
        raise BootstrapInstallAmbiguity(code)
    try:
        absolute.mkdir()
    except FileExistsError as exc:
        raise BootstrapInstallAmbiguity(code) from exc
    except BaseException as exc:
        try:
            created = _capture_owned_directory_stat(
                absolute,
                parent_expected=parent_expected,
                code=code,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BootstrapInstallError:
            raise
        except BaseException as inspect_exc:
            raise BootstrapInstallError(code) from inspect_exc
        raise _OwnedDirectoryCreateFailure(absolute, created, exc) from exc
    return _capture_owned_directory_stat(
        absolute,
        parent_expected=parent_expected,
        code=code,
    )


def _capture_owned_directory_stat(
    path: Path,
    *,
    parent_expected: os.stat_result,
    code: str,
) -> os.stat_result:
    """Capture a stable plain directory identity under its expected parent."""

    created = _plain_directory_stat(path, code)
    if not _same_directory_stat(
        parent_expected,
        _plain_directory_stat(path.parent, code),
    ):
        raise BootstrapInstallAmbiguity(code)
    current = _plain_directory_stat(path, code)
    if not _same_directory_stat(created, current):
        raise BootstrapInstallAmbiguity(code)
    if not _same_directory_stat(
        parent_expected,
        _plain_directory_stat(path.parent, code),
    ):
        raise BootstrapInstallAmbiguity(code)
    return created


def _plain_file_stat_if_present(path: Path, code: str) -> os.stat_result | None:
    if not _plain_directory_chain_if_present(path.parent, code):
        return None
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BootstrapInstallError(code) from exc
    if (
        _is_reparse(value)
        or stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or getattr(value, "st_nlink", 1) != 1
    ):
        raise BootstrapInstallError(code)
    return value


def _plain_file_stat(path: Path, code: str) -> os.stat_result:
    _plain_directory_chain(path.parent, code)
    value = _plain_file_stat_if_present(path, code)
    if value is None:
        raise BootstrapInstallError(code)
    return value


def _validate_root(root: Path) -> Path:
    root = _absolute(root)
    _ensure_plain_directory(root, "bootstrap_install_root_untrusted")
    return root


def _validate_target(path: Path, root: Path) -> None:
    path = _absolute(path)
    root = _absolute(root)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BootstrapInstallError("bootstrap_target_invalid") from exc
    _ensure_plain_directory(root, "bootstrap_target_invalid")
    _ensure_plain_directory(path.parent, "bootstrap_target_invalid")
    _plain_file_stat_if_present(path, "bootstrap_target_invalid")


def _read_identity(path: Path, code: str) -> ComponentIdentity:
    before = _plain_file_stat(path, code)
    if before.st_size <= 0 or before.st_size > BOOTSTRAP_MAX_COMPONENT_BYTES:
        raise BootstrapInstallError(code)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            if not _same_stat(before, os.fstat(stream.fileno())):
                raise BootstrapInstallAmbiguity("bootstrap_source_changed")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > BOOTSTRAP_MAX_COMPONENT_BYTES:
                    raise BootstrapInstallError(code)
                digest.update(chunk)
            if not _same_stat(before, os.fstat(stream.fileno())):
                raise BootstrapInstallAmbiguity("bootstrap_source_changed")
    except BootstrapInstallError:
        raise
    except OSError as exc:
        raise BootstrapInstallError(code) from exc
    after = _plain_file_stat(path, code)
    if not _same_stat(before, after) or size <= 0:
        raise BootstrapInstallAmbiguity("bootstrap_source_changed")
    return ComponentIdentity(digest.hexdigest(), size)


def _optional_identity(path: Path, code: str) -> ComponentIdentity | None:
    if _plain_file_stat_if_present(path, code) is None:
        return None
    return _read_identity(path, code)


def _identity_matches(path: Path, identity: ComponentIdentity | None, code: str) -> bool:
    observed = _optional_identity(path, code)
    return observed == identity


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapInstallError("bootstrap_journal_invalid")
        result[key] = value
    return result


def _read_bounded(path: Path, maximum: int) -> bytes:
    before = _plain_file_stat(path, "bootstrap_journal_invalid")
    try:
        with path.open("rb") as stream:
            if not _same_stat(before, os.fstat(stream.fileno())):
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
            raw = stream.read(maximum + 1)
            if not _same_stat(before, os.fstat(stream.fileno())):
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
    except OSError as exc:
        raise BootstrapInstallError("bootstrap_journal_invalid") from exc
    if len(raw) > maximum:
        raise BootstrapInstallError("bootstrap_journal_invalid")
    if not _same_stat(before, _plain_file_stat(path, "bootstrap_journal_invalid")):
        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
    return raw


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _ensure_plain_directory(path.parent, "bootstrap_journal_untrusted")
    parent_before = _plain_directory_stat(path.parent, "bootstrap_journal_untrusted")
    _plain_file_stat_if_present(path, "bootstrap_journal_untrusted")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".atc-new", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode(
            "utf-8"
        )
        if len(raw) > BOOTSTRAP_MAX_JOURNAL_BYTES:
            raise BootstrapInstallError("bootstrap_journal_too_large")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if not _same_directory_stat(
            parent_before,
            _plain_directory_stat(path.parent, "bootstrap_journal_untrusted"),
        ):
            raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
        _plain_file_stat(temporary, "bootstrap_journal_untrusted")
        _plain_file_stat_if_present(path, "bootstrap_journal_untrusted")
        temporary.replace(path)
    except BaseException:
        try:
            if _plain_file_stat_if_present(temporary, "bootstrap_journal_untrusted") is not None:
                temporary.unlink()
        except (BootstrapInstallError, OSError):
            pass
        raise


def _copy_verified(
    source: Path,
    target: Path,
    expected: ComponentIdentity | None,
    *,
    source_code: str,
    target_code: str,
    target_root: Path,
) -> ComponentIdentity:
    source_before = _plain_file_stat(source, source_code)
    _ensure_plain_directory(target.parent, target_code)
    if _plain_file_stat_if_present(target, target_code) is not None:
        raise BootstrapInstallAmbiguity(target_code)
    temporary = target.with_name(f"{target.name}.{secrets.token_hex(6)}.atc-new")
    if _plain_file_stat_if_present(temporary, target_code) is not None:
        raise BootstrapInstallAmbiguity(target_code)
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            if not _same_stat(source_before, os.fstat(input_stream.fileno())):
                raise BootstrapInstallAmbiguity("bootstrap_source_changed")
            while chunk := input_stream.read(1024 * 1024):
                size += len(chunk)
                if size > BOOTSTRAP_MAX_COMPONENT_BYTES:
                    raise BootstrapInstallError("bootstrap_component_too_large")
                digest.update(chunk)
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
            if not _same_stat(source_before, os.fstat(input_stream.fileno())):
                raise BootstrapInstallAmbiguity("bootstrap_source_changed")
        source_after = _plain_file_stat(source, source_code)
        if not _same_stat(source_before, source_after):
            raise BootstrapInstallAmbiguity("bootstrap_source_changed")
        identity = ComponentIdentity(digest.hexdigest(), size)
        if expected is not None and identity != expected:
            raise BootstrapInstallAmbiguity("bootstrap_identity_changed")
        if _read_identity(temporary, target_code) != identity:
            raise BootstrapInstallAmbiguity("bootstrap_copy_invalid")
        _validate_target(target, target_root)
        if _plain_file_stat_if_present(target, target_code) is not None:
            raise BootstrapInstallAmbiguity(target_code)
        temporary.replace(target)
        if _read_identity(target, target_code) != identity:
            raise BootstrapInstallAmbiguity("bootstrap_copy_invalid")
        return identity
    except BootstrapInstallError:
        raise
    except OSError as exc:
        raise BootstrapInstallError(target_code) from exc
    finally:
        try:
            if _plain_file_stat_if_present(temporary, target_code) is not None:
                temporary.unlink()
        except (BootstrapInstallError, OSError):
            pass


def _component_identity(component: BootstrapComponent, kind: str) -> ComponentIdentity | None:
    if kind == "prior":
        if not component.prior_present:
            return None
        return ComponentIdentity(cast(str, component.prior_sha256), cast(int, component.prior_size))
    if kind == "staged":
        if component.staged_sha256 is None:
            return None
        return ComponentIdentity(component.staged_sha256, cast(int, component.staged_size))
    if kind == "backup":
        if component.backup_sha256 is None:
            return None
        return ComponentIdentity(component.backup_sha256, cast(int, component.backup_size))
    raise ValueError(kind)


def _component_identity_from_fields(
    digest: str | None,
    size: int | None,
) -> ComponentIdentity | None:
    if digest is None or size is None:
        return None
    return ComponentIdentity(digest, size)


def _role_targets(install_root: Path) -> dict[str, Path]:
    return {role: install_root / filename for role, filename in CANONICAL_COMPONENTS}


def _journal_root_entries(root: Path, journal_path: Path) -> list[Path]:
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise BootstrapInstallError("bootstrap_journal_untrusted") from exc
    allowed = {BOOTSTRAP_JOURNAL_NAME.casefold(), BOOTSTRAP_LOCK_NAME.casefold()}
    for entry in entries:
        if entry.name.casefold() in allowed:
            continue
        value = entry.lstat()
        if (
            not stat.S_ISDIR(value.st_mode)
            or _is_reparse(value)
            or not _valid_operation_id(entry.name)
        ):
            raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
    return entries


def _expected_transaction_entries(journal: BootstrapInstallJournal) -> set[str]:
    transaction = Path(journal.transaction_dir)
    expected = {"staged", "backups"}
    for component in journal.components:
        expected.add(_relative_name(Path(component.staged_path), transaction))
        if component.backup_path is not None:
            expected.add(_relative_name(Path(component.backup_path), transaction))
    return expected


def _expected_transaction_paths(journal: BootstrapInstallJournal) -> set[Path]:
    transaction = _absolute(Path(journal.transaction_dir))
    expected = {transaction, transaction / "staged", transaction / "backups"}
    for component in journal.components:
        expected.add(_absolute(Path(component.staged_path)))
        if component.backup_path is not None:
            expected.add(_absolute(Path(component.backup_path)))
    return expected


def _relative_name(path: Path, root: Path) -> str:
    try:
        relative = _absolute(path).relative_to(_absolute(root))
    except ValueError as exc:
        raise BootstrapInstallError("bootstrap_journal_invalid") from exc
    return str(relative).replace("\\", "/")


def _transaction_file_identities(
    journal: BootstrapInstallJournal,
) -> dict[str, ComponentIdentity | None]:
    transaction = Path(journal.transaction_dir)
    expected: dict[str, ComponentIdentity | None] = {}
    for component in journal.components:
        for path, kind in (
            (component.staged_path, "staged"),
            (component.backup_path, "backup"),
        ):
            if path is None:
                continue
            key = _relative_name(Path(path), transaction).casefold()
            if key in expected:
                raise BootstrapInstallError("bootstrap_journal_invalid")
            expected[key] = _component_identity(component, kind)
    return expected


def _validate_transaction_tree(
    journal: BootstrapInstallJournal,
) -> dict[Path, os.stat_result]:
    transaction = _absolute(Path(journal.transaction_dir))
    _plain_directory_chain(transaction, "bootstrap_journal_untrusted")
    expected = _expected_transaction_entries(journal)
    expected_files = _transaction_file_identities(journal)
    bindings: dict[Path, os.stat_result] = {
        transaction: _plain_directory_stat(transaction, "bootstrap_journal_untrusted")
    }
    stack: list[tuple[Path, str]] = [(transaction, "")]
    count = 0
    while stack:
        directory, prefix = stack.pop()
        for entry in directory.iterdir():
            count += 1
            if count > BOOTSTRAP_MAX_TRANSACTION_ENTRIES:
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
            value = entry.lstat()
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if _is_reparse(value):
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
            if stat.S_ISDIR(value.st_mode):
                if relative not in expected:
                    raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
                _plain_directory_stat(entry, "bootstrap_journal_untrusted")
                bindings[_absolute(entry)] = value
                stack.append((entry, relative))
            elif stat.S_ISREG(value.st_mode) and getattr(value, "st_nlink", 1) == 1:
                if relative not in expected:
                    raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
                identity = expected_files.get(relative.casefold())
                if identity is None:
                    raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
                try:
                    if not _identity_matches(
                        entry,
                        identity,
                        "bootstrap_journal_untrusted",
                    ):
                        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
                except BootstrapInstallError as exc:
                    raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted") from exc
                bindings[_absolute(entry)] = value
            else:
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
    return bindings


def _verify_bound_file(
    path: Path,
    expected_stat: os.stat_result,
    expected_identity: ComponentIdentity,
    parent_expected: os.stat_result,
    code: str,
) -> None:
    parent_before = _plain_directory_stat(path.parent, code)
    file_before = _plain_file_stat(path, code)
    if not _same_directory_stat(parent_expected, parent_before) or not _same_stat(
        expected_stat,
        file_before,
    ):
        raise BootstrapInstallAmbiguity(code)
    if not _identity_matches(path, expected_identity, code):
        raise BootstrapInstallAmbiguity(code)
    parent_after = _plain_directory_stat(path.parent, code)
    file_after = _plain_file_stat(path, code)
    if not _same_directory_stat(parent_expected, parent_after) or not _same_stat(
        expected_stat,
        file_after,
    ):
        raise BootstrapInstallAmbiguity(code)


def _verify_bound_directory(
    path: Path,
    expected_stat: os.stat_result,
    parent_expected: os.stat_result,
    code: str,
) -> None:
    parent_before = _plain_directory_stat(path.parent, code)
    directory_before = _plain_directory_stat(path, code)
    if not _same_directory_stat(parent_expected, parent_before) or not _same_directory_stat(
        expected_stat,
        directory_before,
    ):
        raise BootstrapInstallAmbiguity(code)
    parent_after = _plain_directory_stat(path.parent, code)
    directory_after = _plain_directory_stat(path, code)
    if not _same_directory_stat(parent_expected, parent_after) or not _same_directory_stat(
        expected_stat,
        directory_after,
    ):
        raise BootstrapInstallAmbiguity(code)


def _remove_empty_owned_directory(
    path: Path,
    *,
    expected_stat: os.stat_result,
    parent_expected: os.stat_result,
    code: str,
) -> None:
    _verify_bound_directory(path, expected_stat, parent_expected, code)
    try:
        if any(path.iterdir()):
            raise BootstrapInstallAmbiguity(code)
    except OSError as exc:
        raise BootstrapInstallError(code) from exc
    _verify_bound_directory(path, expected_stat, parent_expected, code)
    path.rmdir()


def _remove_transaction_tree(
    journal: BootstrapInstallJournal,
    *,
    expected_transaction: os.stat_result | None = None,
    expected_entries: Mapping[Path, os.stat_result] | None = None,
) -> None:
    transaction = _absolute(Path(journal.transaction_dir))
    parent_expected = _plain_directory_stat(transaction.parent, "bootstrap_cleanup_untrusted")
    bindings = (
        dict(expected_entries)
        if expected_entries is not None
        else _validate_transaction_tree(journal)
    )
    transaction_stat = expected_transaction or bindings.get(transaction)
    if transaction_stat is None:
        raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
    if not _same_directory_stat(
        transaction_stat,
        _plain_directory_stat(transaction, "bootstrap_cleanup_untrusted"),
    ):
        raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
    current_bindings = _validate_transaction_tree(journal)
    expected_paths = _expected_transaction_paths(journal)
    missing = set(bindings) - set(current_bindings)
    if expected_entries is None:
        missing = expected_paths - set(current_bindings)
    missing_children: set[Path] = set()
    for name in ("staged", "backups"):
        child = _absolute(transaction / name)
        if child not in current_bindings:
            missing_children.add(child)
    if missing - expected_paths or set(current_bindings) - expected_paths:
        raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
    if missing and journal.phase not in _CLEANUP_PHASES:
        raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
    if any(
        path not in bindings
        or (
            not _same_stat(bindings[path], current_bindings[path])
            if stat.S_ISREG(bindings[path].st_mode)
            else not _same_directory_stat(bindings[path], current_bindings[path])
        )
        for path in current_bindings
    ):
        raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
    # The tree is bounded and has already been checked against the journal;
    # remove only named owned files/directories, never with recursive deletion.
    for component in journal.components:
        for value in (component.staged_path, component.backup_path):
            if value is None:
                continue
            path = Path(value)
            absolute = _absolute(path)
            if _absolute(path.parent) in missing_children:
                continue
            expected = bindings.get(absolute)
            current = _plain_file_stat_if_present(path, "bootstrap_cleanup_untrusted")
            if current is None and absolute in missing:
                continue
            if expected is None:
                if current is not None:
                    raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
                continue
            identity = _component_identity(
                component,
                "staged" if value == component.staged_path else "backup",
            )
            if identity is None:
                raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
            parent = bindings.get(_absolute(path.parent))
            if parent is None:
                raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
            _verify_bound_file(path, expected, identity, parent, "bootstrap_cleanup_untrusted")
            path.unlink()
            if _plain_file_stat_if_present(path, "bootstrap_cleanup_untrusted") is not None:
                raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
    for name in ("staged", "backups"):
        path = transaction / name
        if _absolute(path) in missing_children:
            continue
        expected = bindings.get(_absolute(path))
        if expected is None:
            raise BootstrapInstallAmbiguity("bootstrap_cleanup_untrusted")
        _remove_empty_owned_directory(
            path,
            expected_stat=expected,
            parent_expected=transaction_stat,
            code="bootstrap_cleanup_untrusted",
        )
    _remove_empty_owned_directory(
        transaction,
        expected_stat=transaction_stat,
        parent_expected=parent_expected,
        code="bootstrap_cleanup_untrusted",
    )


def _capture_journal_binding(path: Path) -> tuple[os.stat_result, os.stat_result]:
    return (
        _plain_directory_stat(path.parent, "bootstrap_journal_untrusted"),
        _plain_file_stat(path, "bootstrap_journal_untrusted"),
    )


def _verify_journal_binding(
    path: Path,
    *,
    parent_expected: os.stat_result,
    file_expected: os.stat_result,
    code: str,
) -> None:
    parent_before = _plain_directory_stat(path.parent, code)
    file_before = _plain_file_stat(path, code)
    if not _same_directory_stat(parent_expected, parent_before) or not _same_stat(
        file_expected,
        file_before,
    ):
        raise BootstrapInstallAmbiguity(code)
    parent_after = _plain_directory_stat(path.parent, code)
    file_after = _plain_file_stat(path, code)
    if not _same_directory_stat(parent_expected, parent_after) or not _same_stat(
        file_expected,
        file_after,
    ):
        raise BootstrapInstallAmbiguity(code)


def _remove_journal(
    path: Path,
    *,
    parent_expected: os.stat_result | None = None,
    file_expected: os.stat_result | None = None,
) -> None:
    if parent_expected is None or file_expected is None:
        parent_expected, file_expected = _capture_journal_binding(path)
    _verify_journal_binding(
        path,
        parent_expected=parent_expected,
        file_expected=file_expected,
        code="bootstrap_journal_untrusted",
    )
    _verify_journal_binding(
        path,
        parent_expected=parent_expected,
        file_expected=file_expected,
        code="bootstrap_journal_untrusted",
    )
    path.unlink()
    if _plain_file_stat_if_present(path, "bootstrap_journal_untrusted") is not None:
        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")


def _remove_unjournaled_transaction(
    transaction: Path,
    *,
    transaction_expected: os.stat_result,
    staged_expected: os.stat_result | None,
    backups_expected: os.stat_result | None,
    parent_expected: os.stat_result,
) -> None:
    """Remove only the empty tree created before initial journal authority."""

    for path, expected in (
        (transaction / "staged", staged_expected),
        (transaction / "backups", backups_expected),
    ):
        if expected is None:
            if _plain_directory_chain_if_present(path, "bootstrap_journal_untrusted"):
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
            continue
        _remove_empty_owned_directory(
            path,
            expected_stat=expected,
            parent_expected=transaction_expected,
            code="bootstrap_journal_untrusted",
        )
    _remove_empty_owned_directory(
        transaction,
        expected_stat=transaction_expected,
        parent_expected=parent_expected,
        code="bootstrap_journal_untrusted",
    )


def _prior_identity(component: BootstrapComponent) -> ComponentIdentity | None:
    return _component_identity(component, "prior")


def _prior_core_can_restart(journal: BootstrapInstallJournal) -> bool:
    """Only relaunch a prior Core when the prior canonical main exists."""

    return bool(
        journal.core_stop_complete
        and journal.core_was_running
        and journal.components[0].prior_present
    )


def _verify_set(journal: BootstrapInstallJournal, *, prior: bool) -> None:
    for component in journal.components:
        target = Path(component.target_path)
        expected = _prior_identity(component) if prior else _component_identity(component, "staged")
        observed = _optional_identity(target, "bootstrap_target_invalid")
        if observed != expected:
            raise BootstrapInstallAmbiguity("bootstrap_target_invalid")


def _safe_current_for_rollback(
    component: BootstrapComponent,
) -> tuple[Path, ComponentIdentity | None, ComponentIdentity | None]:
    target = Path(component.target_path)
    current = _optional_identity(target, "bootstrap_target_invalid")
    return target, current, _prior_identity(component)


def _restore_component(component: BootstrapComponent) -> None:
    target, current, prior = _safe_current_for_rollback(component)
    staged = _component_identity(component, "staged")
    if current not in {prior, staged, None}:
        raise BootstrapInstallAmbiguity("bootstrap_target_substituted")
    if prior is None:
        if current is None:
            return
        if staged is None or current != staged:
            raise BootstrapInstallAmbiguity("bootstrap_target_substituted")
        parent = _plain_directory_stat(target.parent, "bootstrap_target_invalid")
        if not _same_directory_stat(
            parent,
            _plain_directory_stat(target.parent, "bootstrap_target_invalid"),
        ):
            raise BootstrapInstallAmbiguity("bootstrap_target_invalid")
        if _optional_identity(target, "bootstrap_target_invalid") != staged:
            raise BootstrapInstallAmbiguity("bootstrap_target_substituted")
        target.unlink()
        if _optional_identity(target, "bootstrap_target_invalid") is not None:
            raise BootstrapInstallAmbiguity("bootstrap_target_invalid")
        return
    if current == prior:
        return
    backup = Path(cast(str, component.backup_path))
    backup_identity = _component_identity(component, "backup")
    if backup_identity != prior or not _identity_matches(
        backup, backup_identity, "bootstrap_backup_invalid"
    ):
        raise BootstrapInstallAmbiguity("bootstrap_backup_invalid")
    _copy_verified(
        backup,
        target,
        prior,
        source_code="bootstrap_backup_invalid",
        target_code="bootstrap_target_invalid",
        target_root=target.parent,
    )
    if not _identity_matches(target, prior, "bootstrap_target_invalid"):
        raise BootstrapInstallAmbiguity("bootstrap_target_invalid")


def _rollback(
    journal: BootstrapInstallJournal,
    journal_path: Path,
    *,
    restart_core: Callable[[], None] | None,
    transaction_expected: os.stat_result | None = None,
    transaction_entries: Mapping[Path, os.stat_result] | None = None,
) -> None:
    journal.phase = BootstrapPhase.ROLLING_BACK
    journal.last_error_code = "bootstrap_rollback_requested"
    transaction_removed = False
    try:
        journal.save(journal_path)
        if transaction_entries is None:
            transaction_entries = _validate_transaction_tree(journal)
        for component in journal.components:
            _restore_component(component)
        _verify_set(journal, prior=True)
        journal.phase = BootstrapPhase.ROLLED_BACK
        journal.last_error_code = None
        journal.save(journal_path)
        if _prior_core_can_restart(journal) and not journal.core_restart_complete:
            if restart_core is None:
                raise BootstrapInstallError("core_restart_required")
            restart_core()
            journal.core_restart_complete = True
            journal.save(journal_path)
        journal_parent, journal_file = _capture_journal_binding(journal_path)
        _remove_transaction_tree(
            journal,
            expected_transaction=transaction_expected,
            expected_entries=transaction_entries,
        )
        transaction_removed = True
        _remove_journal(
            journal_path,
            parent_expected=journal_parent,
            file_expected=journal_file,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if transaction_removed:
            # ROLLED_BACK was already durably published.  The transaction is
            # gone, so do not replace that valid terminal journal with a
            # retry_required phase that would falsely require the tree.
            raise BootstrapInstallError("bootstrap_retry_required") from exc
        journal.phase = BootstrapPhase.RETRY_REQUIRED
        journal.last_error_code = "bootstrap_retry_required"
        with suppress(BaseException):
            journal.save(journal_path)
        raise BootstrapInstallError("bootstrap_retry_required") from exc


def _finish_committed(
    journal: BootstrapInstallJournal,
    journal_path: Path,
    *,
    restart_core: Callable[[], None] | None,
    transaction_expected: os.stat_result | None = None,
    transaction_entries: Mapping[Path, os.stat_result] | None = None,
) -> None:
    _verify_set(journal, prior=False)
    if journal.core_was_running and not journal.core_restart_complete:
        if restart_core is None:
            raise BootstrapInstallError("core_restart_required")
        restart_core()
        journal.core_restart_complete = True
        journal.save(journal_path)
    journal_parent, journal_file = _capture_journal_binding(journal_path)
    if _plain_directory_chain_if_present(
        Path(journal.transaction_dir),
        "bootstrap_cleanup_untrusted",
    ):
        _remove_transaction_tree(
            journal,
            expected_transaction=transaction_expected,
            expected_entries=transaction_entries,
        )
    _remove_journal(
        journal_path,
        parent_expected=journal_parent,
        file_expected=journal_file,
    )


def _recover_existing(
    journal_path: Path,
    install_root: Path,
    *,
    stop_core: Callable[[], None] | None,
    restart_core: Callable[[], None] | None,
) -> bool:
    journal_parent = _plain_directory_stat(journal_path.parent, "bootstrap_journal_untrusted")
    journal_file = _plain_file_stat_if_present(journal_path, "bootstrap_journal_untrusted")
    if journal_file is None:
        return False
    journal = BootstrapInstallJournal.load(journal_path)
    _verify_journal_binding(
        journal_path,
        parent_expected=journal_parent,
        file_expected=journal_file,
        code="bootstrap_journal_untrusted",
    )
    transaction_expected = _plain_directory_chain_stat_if_present(
        Path(journal.transaction_dir),
        "bootstrap_journal_untrusted",
    )
    transaction_entries: Mapping[Path, os.stat_result] | None = None
    if transaction_expected is not None:
        transaction_entries = _validate_transaction_tree(journal)
    if transaction_expected is None and journal.phase not in {
        BootstrapPhase.COMMITTED,
        BootstrapPhase.ROLLED_BACK,
    }:
        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
    if not _same_path(Path(journal.install_root), install_root):
        raise BootstrapInstallAmbiguity("bootstrap_install_root_changed")
    if journal.phase is BootstrapPhase.COMMITTED:
        try:
            _finish_committed(
                journal,
                journal_path,
                restart_core=restart_core,
                transaction_expected=transaction_expected,
                transaction_entries=transaction_entries,
            )
        except (BootstrapInstallError, OSError) as exc:
            # A committed intended set is never sent through the old-set
            # rollback path.  Cleanup or restart is a terminal follow-up;
            # preserve COMMITTED evidence so a later start retries that work.
            journal.phase = BootstrapPhase.COMMITTED
            journal.last_error_code = (
                exc.code if isinstance(exc, BootstrapInstallError) else "bootstrap_retry_required"
            )
            journal.save(journal_path)
            raise
        return True
    # A pre-cutover journal can be safely reverted only after the complete
    # target set still matches its recorded preimage.  Cutover journals use
    # the same rollback routine, which accepts only prior or intended identities.
    if journal.phase in _ACTIVE_PHASES or journal.phase is BootstrapPhase.ROLLED_BACK:
        if journal.phase in {BootstrapPhase.STAGING, BootstrapPhase.STAGED}:
            _verify_set(journal, prior=True)
            journal.phase = BootstrapPhase.ROLLED_BACK
            journal.last_error_code = None
            journal.save(journal_path)
            journal_parent, journal_file = _capture_journal_binding(journal_path)
            if transaction_expected is not None:
                _remove_transaction_tree(
                    journal,
                    expected_transaction=transaction_expected,
                    expected_entries=transaction_entries,
                )
            _remove_journal(
                journal_path,
                parent_expected=journal_parent,
                file_expected=journal_file,
            )
            return True
        if journal.core_was_running and not journal.core_stop_complete and stop_core is None:
            raise BootstrapInstallError("core_stop_required")
        if stop_core is not None and journal.core_was_running and not journal.core_stop_complete:
            journal.phase = BootstrapPhase.STOPPING_CORE
            journal.save(journal_path)
            stop_core()
            journal.core_stop_complete = journal.core_was_running
            journal.phase = BootstrapPhase.CORE_STOPPED
            journal.save(journal_path)
        if journal.phase is BootstrapPhase.ROLLED_BACK:
            _verify_set(journal, prior=True)
            if _prior_core_can_restart(journal) and not journal.core_restart_complete:
                if restart_core is None:
                    raise BootstrapInstallError("core_restart_required")
                restart_core()
                journal.core_restart_complete = True
                journal.save(journal_path)
                journal_parent, journal_file = _capture_journal_binding(journal_path)
            if transaction_expected is not None:
                _remove_transaction_tree(
                    journal,
                    expected_transaction=transaction_expected,
                    expected_entries=transaction_entries,
                )
            _remove_journal(
                journal_path,
                parent_expected=journal_parent,
                file_expected=journal_file,
            )
            return True
        _rollback(
            journal,
            journal_path,
            restart_core=restart_core,
            transaction_expected=transaction_expected,
            transaction_entries=transaction_entries,
        )
        return True
    raise BootstrapInstallAmbiguity("bootstrap_journal_invalid")


def bootstrap_journal_root(install_root: Path) -> Path:
    """Return the private adjacent root used for bootstrap recovery evidence."""

    return _absolute(install_root).parent / ".atc-bootstrap"


def canonical_targets(install_root: Path) -> dict[str, Path]:
    root = _absolute(install_root)
    return _role_targets(root)


def is_complete_install(sources: Mapping[str, Path], install_root: Path) -> bool:
    """Check all four canonical files without mutating the install root."""

    try:
        root = _absolute(install_root)
        targets = _role_targets(root)
        if set(sources) != _ROLES:
            return False
        for role, _filename in CANONICAL_COMPONENTS:
            source = _absolute(Path(sources[role]))
            target = targets[role]
            source_identity = _read_identity(source, "bootstrap_source_invalid")
            if _optional_identity(target, "bootstrap_target_invalid") != source_identity:
                return False
        return True
    except (BootstrapInstallError, OSError):
        return False


def install_windows_components(
    sources: Mapping[str, Path],
    install_root: Path,
    *,
    core_was_running: bool,
    stop_core: Callable[[], None] | None,
    restart_core: Callable[[], None] | None,
    journal_root: Path | None = None,
) -> BootstrapInstallResult:
    """Install the four frozen Windows components as one recoverable unit."""

    if set(sources) != _ROLES:
        raise BootstrapInstallError("bootstrap_component_set_invalid")
    root = _validate_root(install_root)
    targets = _role_targets(root)
    for role, _filename in CANONICAL_COMPONENTS:
        _validate_target(targets[role], root)
        _plain_file_stat(Path(sources[role]), "bootstrap_source_invalid")

    evidence_root = _absolute(journal_root or bootstrap_journal_root(root))
    _ensure_plain_directory(evidence_root, "bootstrap_journal_untrusted")
    journal_path = evidence_root / BOOTSTRAP_JOURNAL_NAME
    lock_path = evidence_root / BOOTSTRAP_LOCK_NAME
    _journal_root_entries(evidence_root, journal_path)
    _plain_file_stat_if_present(lock_path, "bootstrap_journal_untrusted")
    try:
        lock = FileLock(str(lock_path))
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise BootstrapInstallError("bootstrap_busy") from exc
    try:
        recovered = _recover_existing(
            journal_path,
            root,
            stop_core=stop_core,
            restart_core=restart_core,
        )
        if recovered:
            _journal_root_entries(evidence_root, journal_path)
        else:
            entries = _journal_root_entries(evidence_root, journal_path)
            if any(
                entry.name.casefold()
                not in {
                    BOOTSTRAP_JOURNAL_NAME.casefold(),
                    BOOTSTRAP_LOCK_NAME.casefold(),
                }
                for entry in entries
            ):
                raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted")
        if _plain_file_stat_if_present(journal_path, "bootstrap_journal_untrusted") is not None:
            raise BootstrapInstallError("bootstrap_retry_required")
        if is_complete_install(sources, root):
            return BootstrapInstallResult(root, targets, recovered=recovered)
        prior_identities = [
            _optional_identity(targets[role], "bootstrap_target_invalid")
            for role, _filename in CANONICAL_COMPONENTS
        ]
        operation_id = secrets.token_hex(12)
        transaction = evidence_root / operation_id
        evidence_stat = _plain_directory_stat(evidence_root, "bootstrap_journal_untrusted")
        transaction_stat: os.stat_result | None = None
        staged_stat: os.stat_result | None = None
        backups_stat: os.stat_result | None = None
        staged_root = transaction / "staged"
        backups_root = transaction / "backups"
        try:
            transaction_stat = _create_owned_directory(
                transaction,
                parent_expected=evidence_stat,
                code="bootstrap_journal_untrusted",
            )
            staged_stat = _create_owned_directory(
                staged_root,
                parent_expected=transaction_stat,
                code="bootstrap_journal_untrusted",
            )
            backups_stat = _create_owned_directory(
                backups_root,
                parent_expected=transaction_stat,
                code="bootstrap_journal_untrusted",
            )
        except BaseException as exc:
            if isinstance(exc, _OwnedDirectoryCreateFailure):
                created_path = _absolute(exc.path)
                if _same_path(created_path, transaction):
                    if (
                        transaction_stat is not None
                        or staged_stat is not None
                        or backups_stat is not None
                    ):
                        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted") from exc
                    transaction_stat = exc.created_stat
                elif _same_path(created_path, staged_root):
                    if (
                        transaction_stat is None
                        or staged_stat is not None
                        or backups_stat is not None
                    ):
                        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted") from exc
                    staged_stat = exc.created_stat
                elif _same_path(created_path, backups_root):
                    if transaction_stat is None or staged_stat is None or backups_stat is not None:
                        raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted") from exc
                    backups_stat = exc.created_stat
                else:
                    raise BootstrapInstallAmbiguity("bootstrap_journal_untrusted") from exc
            if transaction_stat is not None:
                try:
                    _remove_unjournaled_transaction(
                        transaction,
                        transaction_expected=transaction_stat,
                        staged_expected=staged_stat,
                        backups_expected=backups_stat,
                        parent_expected=evidence_stat,
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as cleanup_exc:
                    raise BootstrapInstallError("bootstrap_journal_untrusted") from cleanup_exc
            cause = exc.cause if isinstance(exc, _OwnedDirectoryCreateFailure) else exc
            if isinstance(cause, (KeyboardInterrupt, SystemExit)):
                raise cause from None
            if isinstance(cause, BootstrapInstallError):
                raise cause from None
            raise BootstrapInstallError("bootstrap_journal_untrusted") from cause
        now = _utc_now()
        components = [
            BootstrapComponent(
                role=role,
                filename=filename,
                source_path=str(_absolute(Path(sources[role]))),
                target_path=str(targets[role]),
                staged_path=str(staged_root / filename),
                prior_present=prior is not None,
                prior_sha256=prior.sha256 if prior else None,
                prior_size=prior.size if prior else None,
                backup_path=str(backups_root / filename) if prior else None,
            )
            for (role, filename), prior in zip(
                CANONICAL_COMPONENTS,
                prior_identities,
                strict=True,
            )
        ]
        journal = BootstrapInstallJournal(
            operation_id=operation_id,
            install_root=str(root),
            transaction_dir=str(transaction),
            core_was_running=core_was_running,
            core_stop_complete=False,
            phase=BootstrapPhase.STAGING,
            cutover_index=0,
            core_restart_complete=False,
            components=components,
            created_at=now,
            updated_at=now,
        )
        # Persist every exact preimage before staging can complete or any
        # target can be touched.  A restart can therefore distinguish a
        # deliberate absence from an unexplained replacement.
        try:
            journal.save(journal_path)
        except BaseException as exc:
            journal_present = False
            with suppress(BaseException):
                journal_present = (
                    _plain_file_stat_if_present(
                        journal_path,
                        "bootstrap_journal_untrusted",
                    )
                    is not None
                )
            if not journal_present:
                _remove_unjournaled_transaction(
                    transaction,
                    transaction_expected=transaction_stat,
                    staged_expected=staged_stat,
                    backups_expected=backups_stat,
                    parent_expected=evidence_stat,
                )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, BootstrapInstallError):
                raise
            raise BootstrapInstallError("bootstrap_journal_write_failed") from exc
        try:
            total = 0
            for component in journal.components:
                target = Path(component.target_path)
                prior = _optional_identity(target, "bootstrap_target_invalid")
                if prior != _prior_identity(component):
                    raise BootstrapInstallAmbiguity("bootstrap_target_substituted")
                identity = _copy_verified(
                    Path(component.source_path),
                    Path(component.staged_path),
                    None,
                    source_code="bootstrap_source_invalid",
                    target_code="bootstrap_stage_invalid",
                    target_root=transaction,
                )
                component.source_sha256 = identity.sha256
                component.source_size = identity.size
                component.staged_sha256 = identity.sha256
                component.staged_size = identity.size
                total += identity.size
                if total > BOOTSTRAP_MAX_TOTAL_COMPONENT_BYTES:
                    raise BootstrapInstallError("bootstrap_component_set_too_large")
                journal.save(journal_path)
            for component in journal.components:
                prior = _prior_identity(component)
                if prior is None:
                    continue
                backup = Path(cast(str, component.backup_path))
                backup_identity = _copy_verified(
                    Path(component.target_path),
                    backup,
                    prior,
                    source_code="bootstrap_backup_source_invalid",
                    target_code="bootstrap_backup_invalid",
                    target_root=transaction,
                )
                component.backup_sha256 = backup_identity.sha256
                component.backup_size = backup_identity.size
                journal.save(journal_path)
            _verify_set(journal, prior=True)
            for component in journal.components:
                source_identity = _component_identity_from_fields(
                    component.source_sha256,
                    component.source_size,
                )
                if (
                    source_identity is None
                    or _read_identity(
                        Path(component.source_path),
                        "bootstrap_source_changed",
                    )
                    != source_identity
                ):
                    raise BootstrapInstallAmbiguity("bootstrap_source_changed")
            journal.phase = BootstrapPhase.STAGED
            journal.save(journal_path)
            if any(component.prior_present for component in journal.components):
                journal.phase = BootstrapPhase.STOPPING_CORE
                journal.save(journal_path)
                if stop_core is None:
                    raise BootstrapInstallError("core_stop_required")
                stop_core()
                journal.core_stop_complete = journal.core_was_running
                journal.phase = BootstrapPhase.CORE_STOPPED
                journal.save(journal_path)
            journal.phase = BootstrapPhase.CUTOVER
            journal.save(journal_path)
            for index, component in enumerate(journal.components):
                target = Path(component.target_path)
                prior = _prior_identity(component)
                if _optional_identity(target, "bootstrap_target_invalid") != prior:
                    raise BootstrapInstallAmbiguity("bootstrap_target_substituted")
                staged = Path(component.staged_path)
                staged_identity = _component_identity(component, "staged")
                if staged_identity is None or not _identity_matches(
                    staged, staged_identity, "bootstrap_stage_invalid"
                ):
                    raise BootstrapInstallAmbiguity("bootstrap_stage_invalid")
                _validate_target(target, root)
                staged.replace(target)
                if not _identity_matches(target, staged_identity, "bootstrap_target_invalid"):
                    raise BootstrapInstallAmbiguity("bootstrap_target_invalid")
                journal.cutover_index = index + 1
                journal.save(journal_path)
            journal.phase = BootstrapPhase.VERIFYING
            journal.save(journal_path)
            _verify_set(journal, prior=False)
            journal.phase = BootstrapPhase.COMMITTED
            journal.last_error_code = None
            journal.save(journal_path)
            journal_entries = _validate_transaction_tree(journal)
            journal_parent, journal_file = _capture_journal_binding(journal_path)
            _remove_transaction_tree(
                journal,
                expected_transaction=transaction_stat,
                expected_entries=journal_entries,
            )
            _remove_journal(
                journal_path,
                parent_expected=journal_parent,
                file_expected=journal_file,
            )
            return BootstrapInstallResult(root, targets, recovered=recovered)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BootstrapInstallError as exc:
            if journal.phase is BootstrapPhase.COMMITTED:
                journal.last_error_code = exc.code
                with suppress(BaseException):
                    journal.save(journal_path)
                raise
            journal.last_error_code = exc.code
            with suppress(BaseException):
                journal.save(journal_path)
            try:
                _rollback(
                    journal,
                    journal_path,
                    restart_core=restart_core,
                    transaction_expected=transaction_stat,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BootstrapInstallError:
                raise
            raise
        except (OSError, shutil.Error) as exc:
            if journal.phase is BootstrapPhase.COMMITTED:
                journal.last_error_code = "bootstrap_retry_required"
                with suppress(BaseException):
                    journal.save(journal_path)
                raise BootstrapInstallError("bootstrap_retry_required") from exc
            journal.last_error_code = "bootstrap_cutover_failed"
            with suppress(BaseException):
                journal.save(journal_path)
            try:
                _rollback(
                    journal,
                    journal_path,
                    restart_core=restart_core,
                    transaction_expected=transaction_stat,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BootstrapInstallError:
                raise
            raise BootstrapInstallError("bootstrap_cutover_failed") from exc
        except Exception as exc:
            if journal.phase is BootstrapPhase.COMMITTED:
                journal.last_error_code = "bootstrap_retry_required"
                with suppress(BaseException):
                    journal.save(journal_path)
                raise BootstrapInstallError("bootstrap_retry_required") from exc
            journal.last_error_code = "bootstrap_cutover_failed"
            with suppress(BaseException):
                journal.save(journal_path)
            try:
                _rollback(
                    journal,
                    journal_path,
                    restart_core=restart_core,
                    transaction_expected=transaction_stat,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BootstrapInstallError:
                raise
            raise BootstrapInstallError("bootstrap_cutover_failed") from exc
    finally:
        lock.release()

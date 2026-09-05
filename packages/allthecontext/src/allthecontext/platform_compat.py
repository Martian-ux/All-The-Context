"""Late-bound platform APIs with cross-platform static types."""

from __future__ import annotations

import ctypes
import importlib
import os
import subprocess
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_SUCCESS = 0
_ERROR_ALREADY_EXISTS = 183
_REG_CREATED_NEW_KEY = 1
_REG_OPENED_EXISTING_KEY = 2
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_REGISTRY_OWNERSHIP_VALUE = "ATCRegistrationGeneration"
_REGISTRY_IDENTITY_VALUE = "ATCRegistrationJournal"
_REGISTRY_IDENTITY_HEX_LENGTH = 32
_KEY_SET_VALUE = 0x0002
_KEY_CREATE_SUB_KEY = 0x0004
_KEY_READ = 0x20019
_KEY_ALL_ACCESS = 0xF003F
_DELETE = 0x00010000
# RegDeleteKeyTransactedW's samDesired is not a normal access mask.  It only
# accepts zero or one of these view selectors.  The rest of this adapter uses
# the process/default registry view, so keep that choice explicit and shared.
_KEY_WOW64_64KEY = 0x0100
_KEY_WOW64_32KEY = 0x0200
_REGISTRY_VIEW_MASK = 0
_FILE_READ_ATTRIBUTES = 0x00000080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_DISPOSITION_INFO = 4
_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008

_REGISTRY_MUTATION_LOCK = threading.RLock()


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = (("DeleteFile", ctypes.c_int),)


def _identity_tuple(value: object) -> tuple[int, int, int, int, int, int]:
    fields = ("device", "inode", "size", "modified_ns", "links", "attributes")
    result = tuple(getattr(value, field) for field in fields)
    if any(not isinstance(item, int) or isinstance(item, bool) for item in result):
        raise ValueError("invalid file identity")
    return result


def _attribute(value: object, name: str) -> Any:
    return getattr(value, name)


def _same_identity(left: os.stat_result, expected: tuple[int, int, int, int, int, int]) -> bool:
    observed = (
        int(left.st_dev),
        int(left.st_ino),
        int(left.st_size),
        int(left.st_mtime_ns),
        int(left.st_nlink),
        int(getattr(left, "st_file_attributes", 0)),
    )
    return (
        observed[0] == expected[0]
        and observed[1] == expected[1]
        and observed[2] == expected[2]
        and observed[3] == expected[3]
        and observed[5] == expected[5]
    )


def _raise_windows_error(code: int, message: str) -> None:
    if code in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
        raise FileNotFoundError(code, message)
    raise OSError(code, message)


def _windows_last_error(provider: object | None = None) -> int:
    """Read a native or explicitly modeled Win32 thread error.

    ``ctypes.get_last_error`` is only present on Windows.  A fake DLL used by
    the cross-platform tests may instead expose ``get_last_error`` itself;
    requiring that explicit seam prevents a Linux test double from silently
    turning a modeled native failure into error code zero.
    """

    if provider is not None:
        getter = getattr(provider, "get_last_error", None)
        if not callable(getter):
            raise OSError("Windows provider last-error API is unavailable")
        return int(cast(Callable[[], int], getter)())

    if os.name != "nt":
        return 0
    getter = getattr(ctypes, "get_last_error", None)
    if not callable(getter):
        raise OSError("Windows last-error API is unavailable")
    return int(cast(Callable[[], int], getter)())


def _delete_file_by_windows_handle(
    path: Path,
    expected: tuple[int, int, int, int, int, int],
) -> None:
    """Delete the object opened for the checked path, never a later path entry."""

    kernel32 = windows_dll("kernel32")
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    set_file_information.restype = ctypes.c_int
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        os.fspath(path),
        _DELETE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == invalid_handle or handle is None:
        _raise_windows_error(_windows_last_error(), "unable to open file")
    try:
        msvcrt = importlib.import_module("msvcrt")
        fd = int(msvcrt.open_osfhandle(int(handle), os.O_RDONLY))
        handle = None
        try:
            opened = os.fstat(fd)
            if not _same_identity(opened, expected):
                raise OSError("file identity changed")
            disposition = _FileDispositionInfo(1)
            if not set_file_information(
                msvcrt.get_osfhandle(fd),
                _FILE_DISPOSITION_INFO,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                _raise_windows_error(_windows_last_error(), "unable to delete file")
        finally:
            os.close(fd)
    finally:
        if handle not in {None, invalid_handle}:
            close_handle(handle)


def delete_file_by_identity(path: Path, identity: object) -> None:
    """Delete one already-validated file object, or fail closed.

    Windows has a handle-bound delete primitive.  Linux ``unlinkat`` is
    pathname-based and does not support ``AT_EMPTY_PATH`` for deleting an
    already-open file, so Linux deliberately has no path-based fallback here.
    """

    expected = _identity_tuple(identity)
    if os.name == "nt":
        _delete_file_by_windows_handle(Path(path), expected)
    else:
        raise OSError("identity-bound file deletion is unavailable")


def replace_file_durably(source: Path, destination: Path) -> None:
    """Atomically publish a fully-written sibling file at ``destination``.

    Windows has no portable directory-fsync equivalent.  ``MoveFileExW`` with
    ``MOVEFILE_WRITE_THROUGH`` is the native durable publication primitive for
    a same-volume replacement.  POSIX hosts additionally sync the containing
    directory so a restart observes either the old complete file or the new
    complete file, never a truncate-in-place intermediate.
    """

    source = Path(source)
    destination = Path(destination)
    if os.name == "nt":
        kernel32 = windows_dll("kernel32")
        move_file = kernel32.MoveFileExW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move_file.restype = ctypes.c_int
        if not move_file(
            os.fspath(source),
            os.fspath(destination),
            _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH,
        ):
            _raise_windows_error(_windows_last_error(), "unable to publish file")
        return

    os.replace(source, destination)
    if os.name != "posix":
        return
    flags = int(getattr(os, "O_DIRECTORY", 0)) | os.O_RDONLY
    directory_fd = os.open(destination.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class _RegistryTransactionState(StrEnum):
    """Observable states for the owner of one native KTM handle."""

    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    COMMIT_FAILED = "commit_failed"
    ROLLBACK_FAILED = "rollback_failed"
    CLOSED = "closed"
    CLOSE_FAILED = "close_failed"


class _RegistryTransaction:
    """Own one native KTM handle with single-attempt terminal operations.

    A failed native cleanup call is retained as evidence and never retried by
    this owner.  Retrying a close or rollback after an uncertain native result
    can target a reused handle, so callers must retain their durable recovery
    evidence instead of trying to make this handle look clean.
    """

    def __init__(
        self,
        handle: int,
        commit: Any,
        rollback: Any,
        close: Any,
        *,
        last_error_provider: object | None = None,
    ) -> None:
        self.handle = handle
        self._commit = commit
        self._rollback = rollback
        self._close = close
        self._last_error_provider = last_error_provider
        self.state = _RegistryTransactionState.ACTIVE
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.commit_attempted = False
        self.rollback_attempted = False
        self.close_attempted = False
        self.commit_error: BaseException | None = None
        self.rollback_error: BaseException | None = None
        self.close_error: BaseException | None = None

    def _raise_inactive(self) -> None:
        raise OSError("registry transaction is no longer active")

    def _last_error(self) -> int:
        return _windows_last_error(self._last_error_provider)

    def commit(self) -> None:
        if self.committed:
            return
        if self.commit_attempted:
            if self.commit_error is not None:
                raise self.commit_error
            return
        if self.state is _RegistryTransactionState.CLOSED:
            self._raise_inactive()
        if self.close_attempted:
            if self.close_error is not None:
                raise self.close_error
            self._raise_inactive()
        if self.rollback_attempted:
            if self.rollback_error is not None:
                raise self.rollback_error
            self._raise_inactive()
        self.commit_attempted = True
        try:
            result = int(self._commit(ctypes.c_void_p(self.handle)))
            if not result:
                _raise_windows_error(self._last_error(), "unable to commit registry transaction")
        except BaseException as exc:
            self.commit_error = exc
            self.state = _RegistryTransactionState.COMMIT_FAILED
            raise
        self.committed = True
        self.state = _RegistryTransactionState.COMMITTED

    def rollback(self) -> None:
        if self.committed or self.rolled_back:
            return
        if self.rollback_attempted:
            if self.rollback_error is not None:
                raise self.rollback_error
            return
        if self.state is _RegistryTransactionState.CLOSED:
            self._raise_inactive()
        if self.close_attempted:
            if self.close_error is not None:
                raise self.close_error
            self._raise_inactive()
        self.rollback_attempted = True
        try:
            result = int(self._rollback(ctypes.c_void_p(self.handle)))
            if not result:
                _raise_windows_error(self._last_error(), "unable to roll back registry transaction")
        except BaseException as exc:
            self.rollback_error = exc
            self.state = _RegistryTransactionState.ROLLBACK_FAILED
            raise
        self.rolled_back = True
        self.state = _RegistryTransactionState.ROLLED_BACK

    def close(self) -> None:
        if self.closed:
            return
        if self.close_attempted:
            if self.close_error is not None:
                raise self.close_error
            return
        self.close_attempted = True
        try:
            result = int(self._close(ctypes.c_void_p(self.handle)))
            if not result:
                _raise_windows_error(self._last_error(), "unable to close registry transaction")
        except BaseException as exc:
            self.close_error = exc
            self.state = _RegistryTransactionState.CLOSE_FAILED
            raise
        self.closed = True
        self.state = _RegistryTransactionState.CLOSED


class WindowsRegistryAdapter:
    """Standard ``winreg`` compatibility plus bounded registry primitives.

    The lower-case mutation methods below use native KTM registry transactions
    for generation-bound publication/deletion.  The public winreg-shaped
    methods remain delegated for ordinary forward writes and read-only
    consumers elsewhere in the desktop bootstrap.
    """

    def __init__(self, module: Any) -> None:
        self._module = module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    @property
    def _current_user(self) -> Any:
        return self._module.HKEY_CURRENT_USER

    def _open(self, name: str, access: int) -> Any:
        return self._module.OpenKey(self._current_user, name, 0, access)

    @property
    def atomic_mutations_available(self) -> bool:
        """Whether the wrapped runtime supplies real compare-and-set calls.

        The stdlib ``winreg`` module has no conditional value or key mutation
        primitive.  A process-local lock cannot close that inter-process race,
        so destructive cleanup remains disabled unless a native host supplies
        all three explicitly atomic operations.  Stock forward registration
        instead uses a complete transacted key and never writes an
        observed-absent value in place.
        """

        return all(
            callable(getattr(self._module, name, None))
            for name in (
                "SetValueIfUnchanged",
                "DeleteValueIfUnchanged",
                "DeleteKeyIfUnchanged",
            )
        )

    @property
    def native_registry_publication_available(self) -> bool:
        """Whether this winreg surface can own a transacted native key handle.

        CPython exposes registry handles as ``HKEYType`` instances, but does
        not expose a constructible ``PyHKEY`` wrapper for a raw handle on the
        hosted Python versions used by the desktop workflow.  Without that
        wrapper, the KTM publication path cannot safely bind its handles and
        must use the stock forward-only install path instead.
        """

        return callable(getattr(self._module, "PyHKEY", None))

    @property
    def path_bound_registry_handles(self) -> bool:
        """Stock winreg handles remain bound to the path used to open them."""

        return True

    def _native_key_handle(
        self,
        name: str,
        *,
        access: int = _KEY_ALL_ACCESS,
    ) -> tuple[Any, bool]:
        """Create/open a native key and transfer its handle to ``PyHKEY``.

        A stock ``winreg`` key is a ``PyHKEY`` without ``name`` or ``path``.
        Callers therefore receive the canonical path separately and must never
        infer it from the handle.  Once ownership is transferred to ``PyHKEY``
        its ``Close``/context-manager path owns the native handle; the raw
        ``RegCreateKeyExW`` handle is closed directly only when conversion
        fails.
        """

        advapi32 = windows_dll("advapi32")
        create_key = advapi32.RegCreateKeyExW
        create_key.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint32),
        )
        create_key.restype = ctypes.c_long
        close_key = advapi32.RegCloseKey
        close_key.argtypes = (ctypes.c_void_p,)
        close_key.restype = ctypes.c_long
        result_handle = ctypes.c_void_p()
        disposition = ctypes.c_uint32()
        root = ctypes.c_void_p(int(self._current_user) & 0xFFFFFFFFFFFFFFFF)
        result = int(
            create_key(
                root,
                name,
                0,
                None,
                0,
                access,
                None,
                ctypes.byref(result_handle),
                ctypes.byref(disposition),
            )
        )
        if result != _ERROR_SUCCESS:
            _raise_windows_error(result, "unable to create registry key")
        raw_handle = result_handle.value
        if not raw_handle:
            raise OSError("RegCreateKeyExW returned no handle")
        created = int(disposition.value) == 1
        transferred = False
        try:
            py_hkey = getattr(self._module, "PyHKEY", None)
            if callable(py_hkey):
                key = py_hkey(raw_handle)
                transferred = True
                return key, created
        finally:
            if not transferred:
                close_key(ctypes.c_void_p(raw_handle))
        return self._module.OpenKey(self._current_user, name, 0, access), created

    def _create_key_native(self, name: str) -> bool:
        key, created = self._native_key_handle(name)
        try:
            return created
        finally:
            key.Close()

    def create_key_if_absent(self, name: str) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            return self._create_key_native(name)

    @staticmethod
    def _split_key_name(name: str) -> tuple[str, str]:
        parent, leaf = name.rsplit("\\", 1) if "\\" in name else ("", name)
        if not leaf or leaf in {".", ".."}:
            raise ValueError("invalid registry key name")
        return parent, leaf

    @staticmethod
    def _key_path(parent: str, leaf: str) -> str:
        return f"{parent}\\{leaf}" if parent else leaf

    def _set_staged_values(
        self,
        key: Any,
        values: tuple[tuple[str, int, object], ...],
    ) -> None:
        setter = getattr(self._module, "SetValueEx", None)
        if not callable(setter):
            raise OSError("winreg.SetValueEx is unavailable")
        for name, value_type, data in values:
            setter(key, name, 0, int(value_type), data)

    def _staged_values_match(
        self,
        key: Any,
        values: tuple[tuple[str, int, object], ...],
    ) -> bool:
        query_info = getattr(self._module, "QueryInfoKey", None)
        query_value = getattr(self._module, "QueryValueEx", None)
        if not callable(query_info) or not callable(query_value):
            raise OSError("registry query primitives are unavailable")
        subkeys, value_count, _ = query_info(key)
        if int(subkeys) != 0 or int(value_count) != len(values):
            return False
        for name, value_type, data in values:
            try:
                observed, observed_type = query_value(key, name)
            except FileNotFoundError:
                return False
            if int(observed_type) != int(value_type) or observed != data:
                return False
        return True

    @staticmethod
    def _complete_identity_shape(
        values: tuple[tuple[str, int, object], ...],
        generation: str,
        ownership_value: str,
        identity_value: str,
    ) -> bool:
        """Require both journal markers and a closed, duplicate-free shape."""

        if (
            len(values) != len({name for name, _type, _data in values})
            or not isinstance(generation, str)
            or len(generation) != _REGISTRY_IDENTITY_HEX_LENGTH
            or any(char not in "0123456789abcdef" for char in generation)
        ):
            return False
        ownership_markers = [data for name, _type, data in values if name == ownership_value]
        identity_markers = [data for name, _type, data in values if name == identity_value]
        return (
            len(ownership_markers) == 1
            and ownership_markers[0] == generation
            and len(identity_markers) == 1
            and isinstance(identity_markers[0], str)
            and len(identity_markers[0]) == _REGISTRY_IDENTITY_HEX_LENGTH
            and all(char in "0123456789abcdef" for char in identity_markers[0])
        )

    def _transacted_key_handle(
        self,
        name: str,
        transaction: _RegistryTransaction,
        *,
        create: bool,
        access: int,
    ) -> tuple[Any, int]:
        """Open a registry key through the KTM-bound Win32 API.

        The returned ``PyHKEY`` owns the native key handle.  This deliberately
        requires stock ``winreg.PyHKEY`` support; a provider that cannot
        transfer and close the handle is not an atomic registry provider.
        """

        advapi32 = windows_dll("advapi32")
        function_name = "RegCreateKeyTransactedW" if create else "RegOpenKeyTransactedW"
        function = getattr(advapi32, function_name, None)
        py_hkey = getattr(self._module, "PyHKEY", None)
        close_key = getattr(advapi32, "RegCloseKey", None)
        if not callable(function) or not callable(py_hkey) or not callable(close_key):
            raise OSError("transactional registry handles are unavailable")
        result_handle = ctypes.c_void_p()
        disposition = ctypes.c_uint32(_REG_OPENED_EXISTING_KEY)
        root = ctypes.c_void_p(int(self._current_user) & 0xFFFFFFFFFFFFFFFF)
        requested_access = access | _REGISTRY_VIEW_MASK
        if create:
            function.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            function.restype = ctypes.c_long
            result = int(
                function(
                    root,
                    name,
                    0,
                    None,
                    0,
                    requested_access,
                    None,
                    ctypes.byref(result_handle),
                    ctypes.byref(disposition),
                    ctypes.c_void_p(transaction.handle),
                    None,
                )
            )
        else:
            function.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            function.restype = ctypes.c_long
            result = int(
                function(
                    root,
                    name,
                    0,
                    requested_access,
                    ctypes.byref(result_handle),
                    ctypes.c_void_p(transaction.handle),
                    None,
                )
            )
        if result in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            raise FileNotFoundError(name)
        if result != _ERROR_SUCCESS:
            operation = "create" if create else "open"
            _raise_windows_error(result, f"unable to {operation} transacted key")
        raw_handle = result_handle.value
        if raw_handle in {None, _INVALID_HANDLE_VALUE}:
            raise OSError("transactional registry API returned no handle")
        transferred = False
        try:
            key = py_hkey(raw_handle)
            transferred = True
            return key, int(disposition.value)
        finally:
            if not transferred:
                close_key(ctypes.c_void_p(raw_handle))

    def _set_transacted_values(
        self,
        key: Any,
        values: tuple[tuple[str, int, object], ...],
    ) -> None:
        setter = getattr(self._module, "SetValueEx", None)
        if not callable(setter):
            raise OSError("winreg.SetValueEx is unavailable")
        for name, value_type, data in values:
            setter(key, name, 0, int(value_type), data)

    def _delete_transacted_key(
        self,
        name: str,
        transaction: _RegistryTransaction,
    ) -> bool:
        """Delete a key by ``RegDeleteKeyTransactedW`` inside KTM."""

        advapi32 = windows_dll("advapi32")
        function = getattr(advapi32, "RegDeleteKeyTransactedW", None)
        if not callable(function):
            raise OSError("transactional registry deletion is unavailable")
        function.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        function.restype = ctypes.c_long
        root = ctypes.c_void_p(int(self._current_user) & 0xFFFFFFFFFFFFFFFF)
        result = int(
            function(
                root,
                name,
                _REGISTRY_VIEW_MASK,
                0,
                ctypes.c_void_p(transaction.handle),
                None,
            )
        )
        if result in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            return False
        if result != _ERROR_SUCCESS:
            _raise_windows_error(result, "unable to delete transacted registry key")
        return True

    def _transacted_key_is_absent(
        self,
        name: str,
        transaction: _RegistryTransaction,
    ) -> bool:
        try:
            key, _disposition = self._transacted_key_handle(
                name,
                transaction,
                create=False,
                access=_KEY_READ,
            )
        except FileNotFoundError:
            return True
        try:
            return False
        finally:
            key.Close()

    def _begin_registry_transaction(self) -> _RegistryTransaction:
        """Begin a local KTM transaction or fail closed on unsupported hosts."""

        ktmw32 = windows_dll("ktmw32")
        create = cast(Any, getattr(ktmw32, "CreateTransaction", None))
        commit = cast(Any, getattr(ktmw32, "CommitTransaction", None))
        rollback = cast(Any, getattr(ktmw32, "RollbackTransaction", None))
        close = cast(Any, getattr(ktmw32, "CloseHandle", None))
        if not callable(close):
            close = cast(Any, getattr(windows_dll("kernel32"), "CloseHandle", None))
        if not all(callable(function) for function in (create, commit, rollback, close)):
            raise OSError("KTM registry transactions are unavailable")
        create.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
        )
        create.restype = ctypes.c_void_p
        commit.argtypes = (ctypes.c_void_p,)
        commit.restype = ctypes.c_int
        rollback.argtypes = (ctypes.c_void_p,)
        rollback.restype = ctypes.c_int
        close.argtypes = (ctypes.c_void_p,)
        close.restype = ctypes.c_int
        raw_handle = create(None, None, 0, 0, 0, 0, None)
        raw_value = getattr(raw_handle, "value", raw_handle)
        if raw_value in {None, _INVALID_HANDLE_VALUE}:
            _raise_windows_error(
                _windows_last_error(ktmw32 if os.name != "nt" else None),
                "unable to create registry transaction",
            )
        return _RegistryTransaction(
            int(raw_value),
            commit,
            rollback,
            close,
            last_error_provider=ktmw32 if os.name != "nt" else None,
        )

    def _run_registry_transaction(self, operation: Any) -> Any:
        transaction = self._begin_registry_transaction()
        primary_error: BaseException | None = None
        try:
            result = operation(transaction)
            if result is False:
                transaction.rollback()
                return result
            transaction.commit()
            return result
        except BaseException as exc:
            primary_error = exc
            if not transaction.rollback_attempted:
                try:
                    transaction.rollback()
                except BaseException:
                    # The original failure remains the operation's public
                    # error, but its notes retain that native cleanup is
                    # ambiguous and durable recovery evidence is required.
                    exc.add_note("registry transaction rollback failed; cleanup is ambiguous")
            raise
        finally:
            try:
                transaction.close()
            except BaseException:
                if primary_error is not None:
                    primary_error.add_note(
                        "registry transaction close failed; cleanup is ambiguous"
                    )
                else:
                    raise

    def publish_key_if_absent(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        generation: str,
        ownership_value: str,
        identity_value: str = _REGISTRY_IDENTITY_VALUE,
    ) -> bool:
        """Atomically materialize the exact key and remove its stage.

        KTM gives the stage read, canonical create, value materialization, and
        stage removal one commit point.  A rename of a mutable staged key is
        intentionally not used: it cannot bind the content check to the
        publication syscall.  Windows Vista and later expose these APIs, but
        a missing/disabled API or a commit conflict is an unsupported or
        ambiguous provider and never falls back to path-based mutation.
        """

        if not self._complete_identity_shape(values, generation, ownership_value, identity_value):
            raise ValueError("invalid registry generation")
        parent, canonical_leaf = self._split_key_name(name)
        stage_leaf = f"{canonical_leaf}.atc-stage-{generation}"
        stage_name = self._key_path(parent, stage_leaf)

        def publish(transaction: _RegistryTransaction) -> bool:
            stage_key, stage_disposition = self._transacted_key_handle(
                stage_name,
                transaction,
                create=True,
                access=_KEY_READ | _KEY_SET_VALUE | _KEY_CREATE_SUB_KEY,
            )
            try:
                if stage_disposition == _REG_CREATED_NEW_KEY:
                    self._set_transacted_values(stage_key, values)
                elif stage_disposition != _REG_OPENED_EXISTING_KEY:
                    raise OSError("invalid registry stage disposition")
                if not self._staged_values_match(stage_key, values):
                    raise OSError("registry staging key changed")
            finally:
                stage_key.Close()

            canonical_key, canonical_disposition = self._transacted_key_handle(
                name,
                transaction,
                create=True,
                access=_KEY_READ | _KEY_SET_VALUE | _KEY_CREATE_SUB_KEY,
            )
            try:
                if canonical_disposition != _REG_CREATED_NEW_KEY:
                    return False
                self._set_transacted_values(canonical_key, values)
                if not self._staged_values_match(canonical_key, values):
                    raise OSError("registry canonical key changed")
            finally:
                canonical_key.Close()

            if not self._delete_transacted_key(stage_name, transaction):
                raise OSError("registry staging key disappeared")
            if not self._transacted_key_is_absent(stage_name, transaction):
                raise OSError("registry staging key remains")
            canonical_key, _disposition = self._transacted_key_handle(
                name,
                transaction,
                create=False,
                access=_KEY_READ,
            )
            try:
                if not self._staged_values_match(canonical_key, values):
                    raise OSError("registry canonical key changed")
            finally:
                canonical_key.Close()
            return True

        with _REGISTRY_MUTATION_LOCK:
            published = self._run_registry_transaction(publish)
        if not isinstance(published, bool) or not published:
            return False
        # The commit is atomic, but a post-commit observer can still report a
        # vendor mutation.  Do not turn that ambiguity into a success.
        return self.registry_key_matches_generation(
            name, values, generation, ownership_value, identity_value
        ) and not self.staging_key_exists(name, generation)

    def registry_key_matches_generation(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        generation: str,
        ownership_value: str,
        identity_value: str = _REGISTRY_IDENTITY_VALUE,
    ) -> bool:
        """Verify the exact published generation through the canonical path."""

        if not self._complete_identity_shape(values, generation, ownership_value, identity_value):
            return False
        try:
            with self._module.OpenKey(self._current_user, name, 0, _KEY_READ) as key:
                return self._staged_values_match(key, values)
        except FileNotFoundError:
            return False

    def staging_key_exists(self, name: str, generation: str) -> bool:
        """Report whether the journal-bound private staging key remains."""

        if not generation:
            return False
        parent, canonical_leaf = self._split_key_name(name)
        stage_name = self._key_path(parent, f"{canonical_leaf}.atc-stage-{generation}")
        for candidate in (
            stage_name,
            self._key_path(parent, f"{canonical_leaf}.atc-stage-{generation}.atc-tombstone"),
        ):
            try:
                with self._module.OpenKey(self._current_user, candidate, 0, _KEY_READ):
                    return True
            except FileNotFoundError:
                continue
        return False

    def delete_staging_if_owned(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        generation: str,
    ) -> bool:
        """Retry only an exact, journal-bound staging residual."""

        if not self._complete_identity_shape(
            values, generation, _REGISTRY_OWNERSHIP_VALUE, _REGISTRY_IDENTITY_VALUE
        ):
            return False
        parent, canonical_leaf = self._split_key_name(name)
        stage_leaf = f"{canonical_leaf}.atc-stage-{generation}"
        stage_name = self._key_path(parent, stage_leaf)
        tombstone_name = self._key_path(parent, f"{stage_leaf}.atc-tombstone")

        def cleanup(transaction: _RegistryTransaction) -> bool:
            for candidate in (tombstone_name, stage_name):
                try:
                    key, _disposition = self._transacted_key_handle(
                        candidate,
                        transaction,
                        create=False,
                        access=_KEY_READ,
                    )
                except FileNotFoundError:
                    continue
                try:
                    if not self._staged_values_match(key, values):
                        return False
                finally:
                    key.Close()
                if not self._delete_transacted_key(candidate, transaction):
                    return False
                return self._transacted_key_is_absent(candidate, transaction)
            return True

        with _REGISTRY_MUTATION_LOCK:
            cleaned = self._run_registry_transaction(cleanup)
        if not isinstance(cleaned, bool) or not cleaned:
            return False
        return not self.registry_residual_exists(name, generation)

    def registry_residual_exists(self, name: str, generation: str) -> bool:
        """Report any operation-shaped private key without claiming ownership."""

        if not generation:
            return False
        parent, canonical_leaf = self._split_key_name(name)
        candidates = (
            f"{canonical_leaf}.atc-stage-{generation}",
            f"{canonical_leaf}.atc-stage-{generation}.atc-tombstone",
            f"{canonical_leaf}.atc-backup-{generation}",
            f"{canonical_leaf}.atc-backup-{generation}.atc-tombstone",
        )
        for leaf in candidates:
            try:
                with self._module.OpenKey(
                    self._current_user, self._key_path(parent, leaf), 0, _KEY_READ
                ):
                    return True
            except FileNotFoundError:
                continue
        return False

    def delete_key_if_generation(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        generation: str,
        ownership_value: str,
        identity_value: str = _REGISTRY_IDENTITY_VALUE,
    ) -> bool:
        """Delete an exact generation-bound key at one KTM commit point.

        The transacted open, complete-value check, ``RegDeleteKeyTransactedW``
        call, and commit are one OS transaction.  A concurrent value/key
        mutation therefore conflicts with the transaction instead of being
        deleted by a later handle syscall and reported as success.
        """

        if not self._complete_identity_shape(values, generation, ownership_value, identity_value):
            return False
        self._split_key_name(name)

        def delete(transaction: _RegistryTransaction) -> bool:
            try:
                key, _disposition = self._transacted_key_handle(
                    name,
                    transaction,
                    create=False,
                    access=_KEY_READ,
                )
            except FileNotFoundError:
                return True
            try:
                if not self._staged_values_match(key, values):
                    return False
            finally:
                key.Close()
            if not self._delete_transacted_key(name, transaction):
                return False
            return self._transacted_key_is_absent(name, transaction)

        with _REGISTRY_MUTATION_LOCK:
            deleted = self._run_registry_transaction(delete)
        if not isinstance(deleted, bool) or not deleted:
            return False
        return self._key_is_absent(name) and not self.registry_residual_exists(name, generation)

    def _key_is_absent(self, name: str) -> bool:
        """Prove that a registry path is absent after a committed operation."""

        try:
            with self._module.OpenKey(self._current_user, name, 0, _KEY_READ):
                return False
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def set_value_if_unchanged(
        self,
        name: str,
        expected: object,
        value_type: int,
        data: object,
    ) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            conditional = getattr(self._module, "SetValueIfUnchanged", None)
            if not callable(conditional):
                raise OSError("registry compare-and-set unavailable")
            try:
                with self._open(name, _KEY_READ | _KEY_SET_VALUE) as key:
                    value_name = str(_attribute(expected, "name"))
                    result = conditional(key, value_name, expected, value_type, data)
                    if not isinstance(result, bool):
                        raise OSError("registry compare-and-set result invalid")
                    return result
            except FileNotFoundError:
                return False

    def delete_value_if_unchanged(self, name: str, expected: object) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            conditional = getattr(self._module, "DeleteValueIfUnchanged", None)
            if not callable(conditional):
                raise OSError("registry compare-and-delete unavailable")
            try:
                with self._open(name, _KEY_READ | _KEY_SET_VALUE) as key:
                    value_name = str(_attribute(expected, "name"))
                    result = conditional(key, value_name, expected)
                    if not isinstance(result, bool):
                        raise OSError("registry compare-and-delete result invalid")
                    return result
            except FileNotFoundError:
                return False

    def delete_key_if_unchanged(self, name: str, expected: tuple[object, ...]) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            conditional = getattr(self._module, "DeleteKeyIfUnchanged", None)
            if not callable(conditional):
                raise OSError("registry compare-and-delete unavailable")
            try:
                result = conditional(self._current_user, name, expected)
            except FileNotFoundError:
                return False
            if not isinstance(result, bool):
                raise OSError("registry compare-and-delete result invalid")
            return result

    # Compatibility for focused fakes and callers that use the historical
    # adapter names.  Registration transaction code prefers the path methods.
    def CreateKeyIfAbsent(self, root: Any, name: str) -> tuple[Any, bool]:
        created = self.create_key_if_absent(name)
        return self._module.OpenKey(root, name, 0, _KEY_ALL_ACCESS), created

    def SetValueIfUnchanged(
        self, key: Any, value_name: str, expected: object, value_type: int, data: object
    ) -> bool:
        key_name = getattr(key, "name", getattr(key, "path", None))
        if not isinstance(key_name, str):
            raise OSError("registry key path is unavailable")
        return self.set_value_if_unchanged(key_name, expected, value_type, data)

    def DeleteValueIfUnchanged(self, key: Any, value_name: str, expected: object) -> bool:
        del value_name
        key_name = getattr(key, "name", getattr(key, "path", None))
        if not isinstance(key_name, str):
            raise OSError("registry key path is unavailable")
        return self.delete_value_if_unchanged(key_name, expected)

    def DeleteKeyIfUnchanged(self, root: Any, name: str, expected: tuple[object, ...]) -> bool:
        del root
        return self.delete_key_if_unchanged(name, expected)


def windows_registry() -> Any:
    """Load the production registry adapter only after a runtime Windows guard."""

    if os.name != "nt":
        raise OSError("Windows registry is unavailable on this platform")
    return WindowsRegistryAdapter(importlib.import_module("winreg"))


def windows_creation_flags(*names: str) -> int:
    """Resolve Windows-only subprocess flags without exposing platform stubs."""

    flags = 0
    for name in names:
        flags |= int(getattr(subprocess, name, 0))
    return flags


def windows_dll(name: str) -> Any:
    """Load a Windows DLL only after a runtime Windows guard has passed."""

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Windows DLL loading is unavailable on this platform")
    return loader(name, use_last_error=True)

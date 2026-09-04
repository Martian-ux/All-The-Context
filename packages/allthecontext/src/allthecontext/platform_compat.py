"""Late-bound platform APIs with cross-platform static types."""

from __future__ import annotations

import ctypes
import importlib
import os
import secrets
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_SUCCESS = 0
_ERROR_ALREADY_EXISTS = 183
_REGISTRY_OWNERSHIP_VALUE = "ATCRegistrationGeneration"
_REGISTRY_IDENTITY_VALUE = "ATCRegistrationJournal"
_REGISTRY_IDENTITY_HEX_LENGTH = 32
_KEY_SET_VALUE = 0x0002
_KEY_CREATE_SUB_KEY = 0x0004
_KEY_READ = 0x20019
_KEY_ALL_ACCESS = 0xF003F
_DELETE = 0x00010000
_FILE_READ_ATTRIBUTES = 0x00000080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_DISPOSITION_INFO = 4
_AT_EMPTY_PATH = 0x1000
_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_PATH = getattr(os, "O_PATH", 0)

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
        _raise_windows_error(ctypes.get_last_error(), "unable to open file")
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
                _raise_windows_error(ctypes.get_last_error(), "unable to delete file")
        finally:
            os.close(fd)
    finally:
        if handle not in {None, invalid_handle}:
            close_handle(handle)


def _delete_file_by_linux_fd(
    path: Path,
    expected: tuple[int, int, int, int, int, int],
) -> None:
    """Use Linux's fd-bound unlinkat where the host provides it."""

    flags = _O_PATH | _O_NOFOLLOW
    if not flags:
        raise OSError("fd-bound deletion is unavailable")
    fd = os.open(path, flags)
    try:
        if not _same_identity(os.fstat(fd), expected):
            raise OSError("file identity changed")
        libc = ctypes.CDLL(None, use_errno=True)
        unlinkat = libc.unlinkat
        unlinkat.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
        unlinkat.restype = ctypes.c_int
        if unlinkat(fd, b"", _AT_EMPTY_PATH) != 0:
            error = ctypes.get_errno()
            raise OSError(error, "unable to delete file")
    finally:
        os.close(fd)


def delete_file_by_identity(path: Path, identity: object) -> None:
    """Delete one already-validated file object, or fail closed."""

    expected = _identity_tuple(identity)
    if os.name == "nt":
        _delete_file_by_windows_handle(Path(path), expected)
    elif os.name == "posix" and sys.platform.startswith("linux"):
        _delete_file_by_linux_fd(Path(path), expected)
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
            _raise_windows_error(ctypes.get_last_error(), "unable to publish file")
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


class WindowsRegistryAdapter:
    """Standard ``winreg`` compatibility plus bounded registry primitives.

    The lower-case mutation methods below are used only when the wrapped
    provider supplies the matching native compare operations.  They open the
    requested HKCU path inside this adapter, compare the requested preimage,
    and mutate that same path without returning a key handle to the caller.
    The public winreg-shaped methods remain delegated for ordinary forward
    writes and read-only consumers elsewhere in the desktop bootstrap.
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
        instead uses a fully-populated private sibling and native rename
        publication; it never writes an observed-absent value in place.
        """

        return all(
            callable(getattr(self._module, name, None))
            for name in (
                "SetValueIfUnchanged",
                "DeleteValueIfUnchanged",
                "DeleteKeyIfUnchanged",
            )
        )

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

    def _delete_exact_key(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        *,
        tombstone_name: str | None = None,
        ownership_value: str = _REGISTRY_OWNERSHIP_VALUE,
        identity_value: str = _REGISTRY_IDENTITY_VALUE,
    ) -> bool:
        """Isolate and remove an exact key through its native key handle.

        ``winreg.DeleteKey`` takes a parent and a path.  Verifying a key,
        closing that key, and then deleting the path is therefore a
        verify-close-delete race: another process can replace the path between
        the verification and delete.  ``RegRenameKey`` accepts the key handle
        itself when its subkey argument is ``NULL``.  Move the already-open,
        fully-validated key to a private tombstone, validate that same handle
        again, and use the native handle-bound delete.  Any ambiguity leaves
        the tombstone in place for bounded retry; it is never restored or
        removed by a later path-based delete.
        """

        ownership_markers = [
            data for key_name, _type, data in values if key_name == ownership_value
        ]
        if (
            len(ownership_markers) != 1
            or not isinstance(ownership_markers[0], str)
            or not self._complete_identity_shape(
                values, ownership_markers[0], ownership_value, identity_value
            )
        ):
            return False
        source_parent, _source_leaf = self._split_key_name(name)
        tombstone = tombstone_name or self._key_path(
            source_parent, f".atc-tombstone-{secrets.token_hex(8)}"
        )
        tombstone_parent, tombstone_leaf = self._split_key_name(tombstone)
        if tombstone_parent != source_parent or tombstone_leaf in {".", ".."}:
            raise ValueError("invalid registry tombstone name")
        access = _KEY_READ | _KEY_SET_VALUE | _KEY_CREATE_SUB_KEY | _DELETE
        try:
            with self._module.OpenKey(self._current_user, tombstone, 0, _KEY_READ):
                return False
        except FileNotFoundError:
            pass
        key: Any | None = None
        try:
            key = self._module.OpenKey(self._current_user, name, 0, access)
        except FileNotFoundError:
            return True
        try:
            return self._isolate_and_delete_open_key(
                key,
                values,
                tombstone,
                tombstone_leaf,
            )
        finally:
            if key is not None:
                with suppress(OSError):
                    key.Close()

    def _isolate_and_delete_open_key(
        self,
        key: Any,
        values: tuple[tuple[str, int, object], ...],
        tombstone: str,
        tombstone_leaf: str,
    ) -> bool:
        """Isolate and remove one already-open key without reopening its path."""

        if not self._staged_values_match(key, values):
            return False
        self._rename_open_key(key, tombstone_leaf)
        if not self._staged_values_match(key, values):
            # The key was atomically isolated, but it no longer has the
            # complete owned shape.  Preserve it under the tombstone.
            return False
        if not self._delete_open_key(key, values):
            return False
        return self._key_is_absent(tombstone)

    def _delete_isolated_key(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        *,
        ownership_value: str = _REGISTRY_OWNERSHIP_VALUE,
        identity_value: str = _REGISTRY_IDENTITY_VALUE,
    ) -> bool:
        """Delete an already-isolated tombstone through its native handle."""

        ownership_markers = [
            data for key_name, _type, data in values if key_name == ownership_value
        ]
        if (
            len(ownership_markers) != 1
            or not isinstance(ownership_markers[0], str)
            or not self._complete_identity_shape(
                values, ownership_markers[0], ownership_value, identity_value
            )
        ):
            return False
        access = _KEY_READ | _KEY_SET_VALUE | _KEY_CREATE_SUB_KEY | _DELETE
        key: Any | None = None
        try:
            key = self._module.OpenKey(self._current_user, name, 0, access)
        except FileNotFoundError:
            return True
        try:
            if not self._staged_values_match(key, values):
                return False
            if not self._delete_open_key(key, values):
                return False
            return self._key_is_absent(name)
        finally:
            if key is not None:
                with suppress(OSError):
                    key.Close()

    def _rename_open_key(self, key: Any, new_leaf: str) -> None:
        """Atomically rename the exact key represented by ``key``."""

        advapi32 = windows_dll("advapi32")
        rename_key = advapi32.RegRenameKey
        rename_key.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p)
        rename_key.restype = ctypes.c_long
        try:
            native_key = ctypes.c_void_p(int(key))
        except (TypeError, ValueError):
            native_key = key
        result = int(rename_key(native_key, None, new_leaf))
        if result == _ERROR_ALREADY_EXISTS:
            raise FileExistsError(new_leaf)
        if result != _ERROR_SUCCESS:
            _raise_windows_error(result, "unable to isolate registry key")

    def _delete_open_key(
        self,
        key: Any,
        values: tuple[tuple[str, int, object], ...],
    ) -> bool:
        """Delete one isolated key by handle after a final exact check."""

        # The caller validates immediately after isolation.  Revalidate here
        # while the same native handle is still live, so a mutation between
        # that validation and the destructive syscall cannot be counted as a
        # successful cleanup.  The handle also prevents a path replacement
        # from redirecting the syscall to a different key object.
        if not self._staged_values_match(key, values):
            return False

        ntdll = windows_dll("ntdll")
        delete_key = getattr(ntdll, "NtDeleteKey", None)
        close_handle = getattr(ntdll, "NtClose", None)
        if not callable(delete_key) or not callable(close_handle):
            raise OSError("native registry handle deletion is unavailable")
        delete_key.argtypes = (ctypes.c_void_p,)
        delete_key.restype = ctypes.c_long
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_long

        detach = getattr(key, "Detach", None)
        if callable(detach):
            raw_handle = int(detach())
            try:
                result = int(delete_key(ctypes.c_void_p(raw_handle)))
            finally:
                close_handle(ctypes.c_void_p(raw_handle))
        else:
            result = int(delete_key(key))
        if result != _ERROR_SUCCESS:
            raise OSError(result, "unable to delete isolated registry key")
        return True

    def _key_is_absent(self, name: str) -> bool:
        """Prove that the exact private name is absent after native deletion."""

        try:
            with self._module.OpenKey(self._current_user, name, 0, _KEY_READ):
                return False
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def publish_key_if_absent(
        self,
        name: str,
        values: tuple[tuple[str, int, object], ...],
        generation: str,
        ownership_value: str,
        identity_value: str = _REGISTRY_IDENTITY_VALUE,
    ) -> bool:
        """Publish one complete key without clobbering a canonical sibling.

        The staging name is derived from the journal generation and lives under
        the same parent, so the final native rename is an atomic same-parent
        publication.  ``RegRenameKey`` fails when the canonical child already
        exists; it does not replace that child.
        """

        if not self._complete_identity_shape(values, generation, ownership_value, identity_value):
            raise ValueError("invalid registry generation")
        parent, canonical_leaf = self._split_key_name(name)
        stage_leaf = f"{canonical_leaf}.atc-stage-{generation}"
        stage_name = self._key_path(parent, stage_leaf)
        with _REGISTRY_MUTATION_LOCK:
            stage_key: Any | None = None
            rename_attempted = False
            try:
                stage_key, created = self._native_key_handle(stage_name)
                if created:
                    self._set_staged_values(stage_key, values)
                elif not self._staged_values_match(stage_key, values):
                    raise OSError("registry staging key changed")
                if not self._staged_values_match(stage_key, values):
                    raise OSError("registry staging key changed")
                # Keep the validated handle live through publication.  A
                # parent-plus-stage-name rename would reopen a mutable path
                # after this check and could publish a vendor replacement.
                rename_attempted = True
                self._rename_open_key(stage_key, canonical_leaf)
                # The exact key was moved, but its contents changed before
                # publication could be accepted.  Preserve the ambiguous
                # canonical object and fail closed.
                return self._staged_values_match(stage_key, values)
            except FileExistsError:
                if stage_key is not None:
                    tombstone = self._key_path(parent, f"{stage_leaf}.atc-tombstone")
                    try:
                        try:
                            with self._module.OpenKey(self._current_user, tombstone, 0, _KEY_READ):
                                return False
                        except FileNotFoundError:
                            pass
                        self._isolate_and_delete_open_key(
                            stage_key,
                            values,
                            tombstone,
                            f"{stage_leaf}.atc-tombstone",
                        )
                    finally:
                        stage_key.Close()
                        stage_key = None
                # A canonical sibling won the no-clobber barrier.  The stage
                # handle itself was used for cleanup, so a reused stage path
                # cannot redirect deletion to another key object.
                return False
            except BaseException:
                if stage_key is not None:
                    # Once the native rename was attempted, its outcome is
                    # ambiguous.  Never reopen and delete the stage path:
                    # another owner may already have reused that name.
                    if not rename_attempted:
                        try:
                            tombstone = self._key_path(parent, f"{stage_leaf}.atc-tombstone")
                            try:
                                with self._module.OpenKey(
                                    self._current_user, tombstone, 0, _KEY_READ
                                ):
                                    return False
                            except FileNotFoundError:
                                pass
                            self._isolate_and_delete_open_key(
                                stage_key,
                                values,
                                tombstone,
                                f"{stage_leaf}.atc-tombstone",
                            )
                        finally:
                            stage_key.Close()
                            stage_key = None
                    else:
                        stage_key.Close()
                        stage_key = None
                raise
            finally:
                if stage_key is not None:
                    stage_key.Close()

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
        try:
            with self._module.OpenKey(self._current_user, tombstone_name, 0, _KEY_READ):
                return self._delete_isolated_key(
                    tombstone_name,
                    values,
                    ownership_value=_REGISTRY_OWNERSHIP_VALUE,
                    identity_value=_REGISTRY_IDENTITY_VALUE,
                )
        except FileNotFoundError:
            return self._delete_exact_key(
                stage_name,
                values,
                tombstone_name=tombstone_name,
                ownership_value=_REGISTRY_OWNERSHIP_VALUE,
                identity_value=_REGISTRY_IDENTITY_VALUE,
            )

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
        """Remove an owned key through a journal-bound rename/verify/delete.

        Renaming the canonical child to a private backup first makes a vendor
        insertion after the rename remain at the canonical path.  If a vendor
        value was already present before the rename, exact verification fails
        and the private backup is retained as an explicit residual.
        """

        if not self._complete_identity_shape(values, generation, ownership_value, identity_value):
            return False
        parent, canonical_leaf = self._split_key_name(name)
        backup_leaf = f"{canonical_leaf}.atc-backup-{generation}"
        backup_name = self._key_path(parent, backup_leaf)
        backup_tombstone_name = self._key_path(parent, f"{backup_leaf}.atc-tombstone")
        with _REGISTRY_MUTATION_LOCK:
            canonical_exists = True
            try:
                with self._module.OpenKey(self._current_user, name, 0, _KEY_READ) as key:
                    canonical_exists = True
                    if not self._staged_values_match(key, values):
                        return False
            except FileNotFoundError:
                canonical_exists = False

            backup_tombstone_exists = False
            try:
                with self._module.OpenKey(self._current_user, backup_tombstone_name, 0, _KEY_READ):
                    backup_tombstone_exists = True
            except FileNotFoundError:
                pass

            try:
                with self._module.OpenKey(self._current_user, backup_name, 0, _KEY_READ) as key:
                    backup_exists = True
                    backup_matches = self._staged_values_match(key, values)
            except FileNotFoundError:
                backup_exists = False
                backup_matches = False

            if backup_tombstone_exists:
                if not self._delete_isolated_key(
                    backup_tombstone_name,
                    values,
                    ownership_value=ownership_value,
                    identity_value=identity_value,
                ):
                    return False
                backup_tombstone_exists = False

            if backup_exists:
                if not backup_matches:
                    # A private key with a matching generation but a changed
                    # or incomplete shape is not ours.  Preserve it and do
                    # not let canonical disappearance turn it into a
                    # replacement registration.
                    return False
                if not self._delete_exact_key(
                    backup_name,
                    values,
                    tombstone_name=backup_tombstone_name,
                    ownership_value=ownership_value,
                    identity_value=identity_value,
                ):
                    return False
                backup_exists = False
                if not canonical_exists:
                    return True

            if not backup_exists and canonical_exists:
                # The source key handle, rather than the parent plus a path,
                # is the ownership barrier.  A vendor insertion at the
                # canonical path after this move remains a separate key.
                source_key: Any | None = None
                moved_to_backup = False
                try:
                    source_key = self._module.OpenKey(
                        self._current_user,
                        name,
                        0,
                        _KEY_READ | _KEY_SET_VALUE | _KEY_CREATE_SUB_KEY | _DELETE,
                    )
                    if not self._staged_values_match(source_key, values):
                        return False
                    if backup_tombstone_exists:
                        return False
                    self._rename_open_key(source_key, backup_leaf)
                    moved_to_backup = True
                    backup_matches = self._staged_values_match(source_key, values)
                except FileNotFoundError:
                    canonical_exists = False
                finally:
                    if source_key is not None:
                        with suppress(OSError):
                            source_key.Close()
                if moved_to_backup:
                    backup_exists = True
                else:
                    # Re-read the private sibling after the source-handle
                    # race.  A vendor may have created or changed it while
                    # the canonical key disappeared; that state is never
                    # adopted and is retained for review/retry.
                    try:
                        with self._module.OpenKey(
                            self._current_user, backup_name, 0, _KEY_READ
                        ) as key:
                            backup_exists = True
                            backup_matches = self._staged_values_match(key, values)
                    except FileNotFoundError:
                        backup_exists = False
                        backup_matches = False

            if not backup_exists:
                return not canonical_exists
            if not backup_matches:
                # A private key with a matching generation but a changed or
                # incomplete shape is not ours.  In particular, never move it
                # into canonical merely because canonical disappeared.
                return False

            return self._delete_exact_key(
                backup_name,
                values,
                tombstone_name=backup_tombstone_name,
                ownership_value=ownership_value,
                identity_value=identity_value,
            )

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

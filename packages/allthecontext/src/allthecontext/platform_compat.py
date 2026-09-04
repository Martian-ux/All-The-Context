"""Late-bound platform APIs with cross-platform static types."""

from __future__ import annotations

import ctypes
import importlib
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_SUCCESS = 0
_KEY_SET_VALUE = 0x0002
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


class WindowsRegistryAdapter:
    """Standard ``winreg`` compatibility plus path-bound registration primitives.

    The transaction only uses the lower-case mutation methods below.  They
    open the requested HKCU path inside this adapter, compare the requested
    preimage, and mutate that same path without returning a key handle to the
    caller.  The public winreg-shaped methods remain delegated for read-only
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

    @staticmethod
    def _matches(expected: object, current: tuple[object, int] | None) -> bool:
        if current is None:
            return not bool(getattr(expected, "present", False))
        if not bool(getattr(expected, "present", False)):
            return False
        return current[1] == getattr(
            expected, "value_type", None
        ) and WindowsRegistryAdapter._data_equal(current[0], getattr(expected, "data", None))

    @staticmethod
    def _data_equal(left: object, right: object) -> bool:
        if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            return tuple(left) == tuple(right)
        return left == right

    def _read(self, key: Any, name: str) -> tuple[object, int] | None:
        try:
            data, value_type = self._module.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return data, int(value_type)

    def _create_key_native(self, name: str) -> bool:
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
                _KEY_ALL_ACCESS,
                None,
                ctypes.byref(result_handle),
                ctypes.byref(disposition),
            )
        )
        if result != _ERROR_SUCCESS:
            _raise_windows_error(result, "unable to create registry key")
        try:
            return int(disposition.value) == 1
        finally:
            if result_handle.value:
                close_key(result_handle)

    def create_key_if_absent(self, name: str) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            return self._create_key_native(name)

    def set_value_if_unchanged(
        self,
        name: str,
        expected: object,
        value_type: int,
        data: object,
    ) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            try:
                with self._open(name, _KEY_READ | _KEY_SET_VALUE) as key:
                    value_name = str(_attribute(expected, "name"))
                    current = self._read(key, value_name)
                    if not self._matches(expected, current):
                        return False
                    self._module.SetValueEx(key, value_name, 0, value_type, data)
                    return True
            except FileNotFoundError:
                return False

    def delete_value_if_unchanged(self, name: str, expected: object) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            try:
                with self._open(name, _KEY_READ | _KEY_SET_VALUE) as key:
                    value_name = str(_attribute(expected, "name"))
                    current = self._read(key, value_name)
                    if not self._matches(expected, current):
                        return False
                    self._module.DeleteValue(key, value_name)
                    return True
            except FileNotFoundError:
                return False

    def delete_key_if_unchanged(self, name: str, expected: tuple[object, ...]) -> bool:
        with _REGISTRY_MUTATION_LOCK:
            try:
                with self._open(name, _KEY_READ) as key:
                    subkeys, values, _ = self._module.QueryInfoKey(key)
                    if int(subkeys) != 0 or int(values) != len(
                        [item for item in expected if bool(getattr(item, "present", False))]
                    ):
                        return False
                    for item in expected:
                        value_name = str(_attribute(item, "name"))
                        current = self._read(key, value_name)
                        if current is None:
                            if bool(getattr(item, "present", False)):
                                return False
                        elif not self._matches(item, current):
                            return False
            except FileNotFoundError:
                return False
            try:
                delete_key = getattr(self._module, "DeleteKeyEx", None)
                if callable(delete_key):
                    delete_key(self._current_user, name, 0)
                else:
                    self._module.DeleteKey(self._current_user, name)
            except FileNotFoundError:
                return False
            return True

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

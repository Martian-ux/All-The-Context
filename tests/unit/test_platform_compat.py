from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from allthecontext import platform_compat


def test_windows_last_error_skips_missing_ctypes_api_on_non_windows(monkeypatch) -> None:
    calls: list[bool] = []

    def unexpected_getter() -> int:
        calls.append(True)
        raise AssertionError("non-Windows code must not read ctypes.get_last_error")

    monkeypatch.setattr(platform_compat.os, "name", "posix")
    monkeypatch.setattr(
        platform_compat.ctypes,
        "get_last_error",
        unexpected_getter,
        raising=False,
    )

    assert platform_compat._windows_last_error() == 0
    assert calls == []


def test_windows_last_error_reads_the_native_value_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(platform_compat.os, "name", "nt")
    monkeypatch.setattr(platform_compat.ctypes, "get_last_error", lambda: 87, raising=False)

    assert platform_compat._windows_last_error() == 87


def test_windows_last_error_fails_closed_when_windows_api_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(platform_compat.os, "name", "nt")
    monkeypatch.delattr(platform_compat.ctypes, "get_last_error", raising=False)

    with pytest.raises(OSError, match="last-error API is unavailable"):
        platform_compat._windows_last_error()


def test_windows_last_error_uses_an_explicit_provider_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(platform_compat.os, "name", "posix")

    class FakeWindowsProvider:
        def get_last_error(self) -> int:
            return 5

    assert platform_compat._windows_last_error(FakeWindowsProvider()) == 5


def test_windows_last_error_rejects_an_unmodeled_provider_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(platform_compat.os, "name", "posix")

    with pytest.raises(OSError, match="provider last-error API is unavailable"):
        platform_compat._windows_last_error(object())


def test_windows_registry_does_not_import_winreg_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(platform_compat.os, "name", "posix")

    def unexpected_import(_name: str) -> object:
        raise AssertionError("non-Windows code must not import winreg")

    monkeypatch.setattr(platform_compat.importlib, "import_module", unexpected_import)
    with pytest.raises(OSError, match="Windows registry is unavailable"):
        platform_compat.windows_registry()


def test_linux_identity_delete_reports_unavailable_without_native_or_path_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Linux must not advertise the unsupported unlinkat identity path."""

    target = tmp_path / "registration.lnk"
    target.write_bytes(b"original")
    replacement = tmp_path / "replacement.lnk"
    replacement.write_bytes(b"replacement")
    identity = SimpleNamespace(
        device=target.stat().st_dev,
        inode=target.stat().st_ino,
        size=target.stat().st_size,
        modified_ns=target.stat().st_mtime_ns,
        links=target.stat().st_nlink,
        attributes=0,
    )
    os.replace(replacement, target)

    monkeypatch.setattr(platform_compat.os, "name", "posix")
    # Exercise the old Linux branch even when this focused suite runs on
    # Windows; the production implementation no longer imports ``sys``.
    monkeypatch.setattr(platform_compat, "sys", SimpleNamespace(platform="linux"), raising=False)

    def unexpected_native_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Linux identity deletion must not call a native fallback")

    monkeypatch.setattr(platform_compat.os, "open", unexpected_native_call)
    monkeypatch.setattr(platform_compat.ctypes, "CDLL", unexpected_native_call)

    with pytest.raises(OSError, match="identity-bound file deletion is unavailable"):
        platform_compat.delete_file_by_identity(target, identity)

    assert target.read_bytes() == b"replacement"


def test_linux_identity_delete_does_not_follow_a_symlink_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must survive")
    link = tmp_path / "registration.lnk"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    metadata = link.lstat()
    identity = SimpleNamespace(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        links=metadata.st_nlink,
        attributes=0,
    )
    monkeypatch.setattr(platform_compat.os, "name", "posix")
    monkeypatch.setattr(platform_compat, "sys", SimpleNamespace(platform="linux"), raising=False)

    with pytest.raises(OSError, match="identity-bound file deletion is unavailable"):
        platform_compat.delete_file_by_identity(link, identity)

    assert link.is_symlink()
    assert outside.read_bytes() == b"must survive"

from __future__ import annotations

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

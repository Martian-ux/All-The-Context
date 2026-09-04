from __future__ import annotations

import base64
import copy
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from allthecontext import application_install


class _FakeKey:
    def __init__(self, registry: _FakeRegistry, name: str, access: int) -> None:
        self.registry = registry
        self.name = name
        self.access = access

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def Close(self) -> None:
        return None


class _FakeRegistry:
    HKEY_CURRENT_USER = object()
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3
    REG_DWORD = 4
    REG_MULTI_SZ = 5
    REG_QWORD = 6
    KEY_READ = 0x20019
    KEY_QUERY_VALUE = 0x0001
    KEY_SET_VALUE = 0x0002
    KEY_ALL_ACCESS = 0xF003F

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, tuple[int, object]]] = {}
        self.subkeys: set[str] = set()
        self.open_calls: list[tuple[str, int]] = []
        self.fail_before: set[str] = set()
        self.fail_after: str | None = None
        self.fail_restore_name: str | None = None
        self.fail_restore_data: object | None = None
        self.fail_restore_delete: str | None = None
        self.fail_delete: set[str] = set()
        self.fail_create_after = False

    def OpenKey(self, _root: object, name: str, *_args: object) -> _FakeKey:
        if name not in self.keys:
            raise FileNotFoundError(name)
        access = int(_args[1]) if len(_args) >= 2 else self.KEY_READ
        self.open_calls.append((name, access))
        return _FakeKey(self, name, access)

    def CreateKey(self, _root: object, name: str) -> _FakeKey:
        self.keys.setdefault(name, {})
        if self.fail_create_after:
            self.fail_create_after = False
            raise OSError(name)
        return _FakeKey(self, name, self.KEY_ALL_ACCESS)

    def CreateKeyIfAbsent(self, _root: object, name: str) -> tuple[_FakeKey, bool]:
        if name in self.keys:
            return _FakeKey(self, name, self.KEY_ALL_ACCESS), False
        self.keys[name] = {}
        if self.fail_create_after:
            self.fail_create_after = False
            raise OSError(name)
        return _FakeKey(self, name, self.KEY_ALL_ACCESS), True

    def QueryValueEx(self, key: _FakeKey, name: str) -> tuple[object, int]:
        if not key.access & self.KEY_QUERY_VALUE:
            raise PermissionError("query access required")
        try:
            value_type, data = self.keys[key.name][name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc
        return copy.deepcopy(data), value_type

    def SetValueEx(
        self,
        key: _FakeKey,
        name: str,
        _reserved: int,
        value_type: int,
        data: object,
    ) -> None:
        if not key.access & self.KEY_SET_VALUE:
            raise PermissionError("set access required")
        if name in self.fail_before:
            raise PermissionError(name)
        if self.fail_restore_name == name and data == self.fail_restore_data:
            raise PermissionError(name)
        self.keys[key.name][name] = (value_type, copy.deepcopy(data))
        if self.fail_after == name:
            self.fail_after = None
            raise OSError(name)

    def SetValueIfUnchanged(
        self,
        key: _FakeKey,
        name: str,
        expected: application_install.WindowsRegistryValueSnapshot,
        value_type: int,
        data: object,
    ) -> bool:
        if not key.access & self.KEY_SET_VALUE:
            raise PermissionError("set access required")
        current = self.keys[key.name].get(name)
        if expected.present:
            if (
                current is None
                or current[0] != expected.value_type
                or application_install._registry_data_copy(current[1]) != expected.data
            ):
                return False
        elif current is not None:
            return False
        if name in self.fail_before:
            raise PermissionError(name)
        if self.fail_restore_name == name and data == self.fail_restore_data:
            raise PermissionError(name)
        self.keys[key.name][name] = (value_type, copy.deepcopy(data))
        if self.fail_after == name:
            self.fail_after = None
            raise OSError(name)
        return True

    def DeleteValue(self, key: _FakeKey, name: str) -> None:
        if not key.access & self.KEY_SET_VALUE:
            raise PermissionError("set access required")
        if name in self.fail_delete:
            raise PermissionError(name)
        if self.fail_restore_delete == name:
            raise PermissionError(name)
        try:
            del self.keys[key.name][name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def DeleteValueIfUnchanged(
        self,
        key: _FakeKey,
        name: str,
        expected: application_install.WindowsRegistryValueSnapshot,
    ) -> bool:
        if not key.access & self.KEY_SET_VALUE:
            raise PermissionError("set access required")
        current = self.keys[key.name].get(name)
        if (
            current is None
            or not expected.present
            or current[0] != expected.value_type
            or application_install._registry_data_copy(current[1]) != expected.data
        ):
            return False
        if name in self.fail_delete:
            raise PermissionError(name)
        if self.fail_restore_delete == name:
            raise PermissionError(name)
        del self.keys[key.name][name]
        return True

    def QueryInfoKey(self, key: _FakeKey) -> tuple[int, int, int]:
        if not key.access & self.KEY_QUERY_VALUE:
            raise PermissionError("query access required")
        return (
            len([name for name in self.subkeys if name.startswith(f"{key.name}\\")]),
            len(self.keys[key.name]),
            0,
        )

    def DeleteKey(self, _root: object, name: str) -> None:
        if name not in self.keys:
            raise FileNotFoundError(name)
        if self.keys[name] or name in self.subkeys:
            raise OSError(name)
        del self.keys[name]

    def DeleteKeyIfUnchanged(
        self,
        _root: object,
        name: str,
        expected: tuple[application_install.WindowsRegistryValueSnapshot, ...],
    ) -> bool:
        if name not in self.keys or name in self.subkeys:
            return False
        if len(self.keys[name]) != sum(value.present for value in expected):
            return False
        for value in expected:
            current = self.keys[name].get(value.name)
            if not value.present:
                if current is not None:
                    return False
                continue
            if (
                current is None
                or current[0] != value.value_type
                or application_install._registry_data_copy(current[1]) != value.data
            ):
                return False
        del self.keys[name]
        return True


def _patch_shortcut_writer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_at: int | None = None,
) -> list[Path]:
    calls: list[Path] = []

    def write_shortcut(
        path: Path,
        executable: Path,
        *,
        arguments: str = "",
        description: str,
    ) -> None:
        index = len(calls)
        calls.append(path)
        if fail_at == index:
            raise OSError("injected shortcut failure")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{executable}|{arguments}|{description}".encode())

    monkeypatch.setattr(application_install, "_create_windows_shortcut", write_shortcut)
    return calls


def _make_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    registry: _FakeRegistry | None = None,
    desktop: bool = True,
) -> tuple[
    application_install.WindowsApplicationRegistrationTransaction,
    Path,
    Path,
    Path | None,
    _FakeRegistry,
]:
    monkeypatch.setattr(application_install.platform, "system", lambda: "Windows")
    executable = tmp_path / "AllTheContext.exe"
    executable.write_bytes(b"executable")
    monkeypatch.setenv("ATC_INSTALL_DIR", str(tmp_path))
    start_menu = tmp_path / "Programs" / "All The Context"
    desktop_path = tmp_path / "Desktop" if desktop else None
    fake_registry = registry or _FakeRegistry()
    transaction = application_install.WindowsApplicationRegistrationTransaction(
        executable,
        start_menu=start_menu,
        desktop=desktop_path,
        registry=fake_registry,
    )
    return transaction, executable, start_menu, desktop_path, fake_registry


def _surface(
    start_menu: Path,
    desktop: Path | None,
    registry: _FakeRegistry,
) -> tuple[dict[str, bytes | None], dict[str, dict[str, tuple[int, object]]]]:
    paths = {
        "launcher": start_menu / "All The Context.lnk",
        "uninstall": start_menu / "Uninstall All The Context.lnk",
    }
    if desktop is not None:
        paths["desktop"] = desktop / "All The Context.lnk"
    files = {name: path.read_bytes() if path.is_file() else None for name, path in paths.items()}
    return files, copy.deepcopy(registry.keys)


def _write_legacy_journal(
    transaction: application_install.WindowsApplicationRegistrationTransaction,
    snapshot: application_install.WindowsApplicationRegistrationSnapshot,
    *,
    phase: str,
    active: tuple[str, ...],
    desired_shortcuts: dict[str, bytes],
) -> Path:
    assert transaction._journal is not None
    payload = {
        "schema": 1,
        "install_root": str(transaction._install_root),
        "executable": str(transaction._executable),
        "start_menu": str(transaction._start_menu),
        "desktop": str(transaction._desktop) if transaction._desktop is not None else None,
        "uninstall_key": transaction._uninstall_key,
        "phase": phase,
        "active": list(active),
        "snapshot": application_install._encode_snapshot(snapshot),
        "desired_shortcuts": {
            name: base64.b64encode(data).decode("ascii") for name, data in desired_shortcuts.items()
        },
        "desired_registry": {
            name: {
                "value_type": value_type,
                "data": application_install._encode_registry_data(data),
            }
            for name, (value_type, data) in transaction._journal.desired_registry.items()
        },
    }
    path = transaction._journal_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="ascii")
    return path


def test_windows_locations_use_redirected_shell_folders(monkeypatch, tmp_path: Path) -> None:
    programs = tmp_path / "Redirected Programs"
    desktop = tmp_path / "OneDrive" / "Desktop"

    def known_folder(name: str, *, fallback: Path | None = None) -> Path | None:
        del fallback
        return {"Programs": programs, "Desktop": desktop}[name]

    monkeypatch.setattr(application_install, "_windows_known_folder", known_folder)

    start_menu, actual_desktop = application_install._windows_locations()

    assert start_menu == programs / "All The Context"
    assert actual_desktop == desktop


def test_windows_locations_fall_back_to_environment(monkeypatch, tmp_path: Path) -> None:
    app_data = tmp_path / "Roaming"
    profile = tmp_path / "Profile"
    monkeypatch.setenv("APPDATA", str(app_data))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setattr(
        application_install,
        "_windows_known_folder",
        lambda _name, *, fallback=None: fallback.resolve() if fallback else None,
    )

    start_menu, desktop = application_install._windows_locations()

    assert (
        start_menu
        == (
            app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "All The Context"
        ).resolve()
    )
    assert desktop == (profile / "Desktop").resolve()


def test_packaged_smoke_registration_is_isolated(monkeypatch, tmp_path: Path) -> None:
    programs = tmp_path / "Programs"
    desktop = tmp_path / "Desktop"
    registry_key = r"Software\AllTheContext\Smoke\isolated-test"
    monkeypatch.setenv("ATC_PACKAGED_SMOKE", "1")
    monkeypatch.setenv("ATC_SMOKE_PROGRAMS_DIR", str(programs))
    monkeypatch.setenv("ATC_SMOKE_DESKTOP_DIR", str(desktop))
    monkeypatch.setenv("ATC_SMOKE_UNINSTALL_KEY", registry_key)

    start_menu, actual_desktop = application_install._windows_locations()

    assert start_menu == programs.resolve() / "All The Context"
    assert actual_desktop == desktop.resolve()
    assert application_install._windows_uninstall_key() == registry_key


def test_registration_snapshot_distinguishes_absent_and_present_states(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = _FakeRegistry()
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    start_menu.mkdir(parents=True)
    assert desktop is not None
    desktop.mkdir()
    (start_menu / "All The Context.lnk").write_bytes(b"old-launcher")
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "DisplayName": (registry.REG_EXPAND_SZ, "%ATC_DISPLAY_NAME%"),
        "NoModify": (registry.REG_QWORD, 17),
        "Unrelated": (registry.REG_BINARY, b"leave-me"),
    }

    snapshot = transaction.snapshot()

    states = {value.name: value for value in snapshot.registry_values}
    assert states["DisplayName"].present is True
    assert states["DisplayName"].value_type == registry.REG_EXPAND_SZ
    assert states["DisplayName"].data == "%ATC_DISPLAY_NAME%"
    assert states["DisplayVersion"].present is False
    assert states["NoModify"].value_type == registry.REG_QWORD
    shortcuts = {shortcut.name: shortcut for shortcut in snapshot.shortcuts}
    assert shortcuts["launcher"].present is True
    assert shortcuts["launcher"].data == b"old-launcher"
    assert shortcuts["desktop"].present is False
    assert snapshot.uninstall_key_present is True
    assert "%ATC_DISPLAY_NAME%" not in repr(snapshot)
    assert str(tmp_path) not in repr(snapshot)


def test_registration_refuses_to_overwrite_preexisting_vendor_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "DisplayName": (registry.REG_EXPAND_SZ, "%NAME%"),
        "DisplayVersion": (registry.REG_MULTI_SZ, ["one", "two"]),
        "Publisher": (registry.REG_BINARY, b"publisher"),
        "InstallLocation": (registry.REG_QWORD, 42),
        "DisplayIcon": (registry.REG_SZ, "old-icon"),
        "UninstallString": (registry.REG_DWORD, 99),
        "NoModify": (registry.REG_QWORD, 7),
        "NoRepair": (registry.REG_BINARY, b"old-repair"),
        "Unrelated": (registry.REG_SZ, "preserve"),
    }
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    start_menu.mkdir(parents=True)
    assert desktop is not None
    desktop.mkdir()
    (start_menu / "All The Context.lnk").write_bytes(b"prior-launcher")
    (start_menu / "Uninstall All The Context.lnk").write_bytes(b"prior-uninstall")
    (desktop / "All The Context.lnk").write_bytes(b"prior-desktop")
    before = _surface(start_menu, desktop, registry)

    snapshot = transaction.snapshot()
    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.apply(snapshot)

    assert raised.value.code == "registration_target_changed"
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["Unrelated"] == (
        registry.REG_SZ,
        "preserve",
    )
    assert _surface(start_menu, desktop, registry) == before
    assert not application_install._registration_journal_path(tmp_path).exists()


@pytest.mark.parametrize("fail_at", [0, 1, 2])
def test_shortcut_failure_compensates_every_prior_shortcut(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_at: int
) -> None:
    calls = _patch_shortcut_writer(monkeypatch, fail_at=fail_at)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    before = _surface(start_menu, desktop, registry)
    snapshot = transaction.snapshot()

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.apply(snapshot)

    assert raised.value.status is None
    assert len(calls) == fail_at + 1
    assert _surface(start_menu, desktop, registry) == before
    assert registry.keys == {}


@pytest.mark.parametrize(
    "fail_name",
    [
        "DisplayName",
        "DisplayVersion",
        "Publisher",
        "InstallLocation",
        "DisplayIcon",
        "UninstallString",
        "NoModify",
        "NoRepair",
    ],
)
def test_registry_failure_compensates_all_prior_mutations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_name: str
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "Unrelated": (registry.REG_SZ, "preserve")
    }
    registry.fail_before.add(fail_name)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    start_menu.mkdir(parents=True)
    assert desktop is not None
    desktop.mkdir()
    before = _surface(start_menu, desktop, registry)
    snapshot = transaction.snapshot()

    with pytest.raises(application_install.WindowsRegistrationError):
        transaction.apply(snapshot)

    assert _surface(start_menu, desktop, registry) == before


def test_registry_failure_after_write_is_compensated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "DisplayVersion": (registry.REG_QWORD, 123),
        "Unrelated": (registry.REG_SZ, "keep"),
    }
    registry.fail_after = "DisplayName"
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    before = _surface(start_menu, desktop, registry)

    with pytest.raises(application_install.WindowsRegistrationError):
        transaction.apply(transaction.snapshot())

    assert _surface(start_menu, desktop, registry) == before


def test_registry_key_creation_failure_after_creation_is_compensated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.fail_create_after = True
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    before = _surface(start_menu, desktop, registry)

    with pytest.raises(application_install.WindowsRegistrationError):
        transaction.apply(transaction.snapshot())

    assert _surface(start_menu, desktop, registry) == (
        before[0],
        {application_install.WINDOWS_UNINSTALL_KEY: {}},
    )


def test_failed_registry_compensation_is_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "Unrelated": (registry.REG_SZ, "keep"),
    }
    registry.fail_before.add("DisplayVersion")
    registry.fail_restore_delete = "DisplayName"
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )

    with pytest.raises(application_install.WindowsRegistrationCompensationError) as raised:
        transaction.apply(transaction.snapshot())

    assert raised.value.status is not None
    assert raised.value.status.complete is False
    assert "DisplayName" in raised.value.status.pending
    registry.fail_restore_delete = None
    status = transaction.restore()
    assert status.complete is True
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY] == {
        "Unrelated": (registry.REG_SZ, "keep")
    }


def test_install_entrypoints_uses_reversible_registration_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch_shortcut_writer(monkeypatch)
    _transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(
        application_install,
        "_windows_locations",
        lambda: (start_menu, desktop),
    )
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)

    result = application_install.install_application_entrypoints(executable)

    assert result is not None
    assert result.launcher == start_menu / "All The Context.lnk"
    assert result.desktop_shortcut == desktop / "All The Context.lnk"
    assert result.uninstall_registered is True
    assert len(calls) == 3


def test_failed_compensation_retains_bounded_retryable_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.fail_before.add("DisplayName")
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    original_restore = transaction._restore_shortcut

    def locked_restore(mutation: object) -> None:
        if getattr(mutation, "name", None) == "launcher":
            raise PermissionError("locked")
        original_restore(mutation)  # type: ignore[arg-type]

    monkeypatch.setattr(transaction, "_restore_shortcut", locked_restore)

    with pytest.raises(application_install.WindowsRegistrationCompensationError) as raised:
        transaction.apply(transaction.snapshot())

    assert raised.value.status is not None
    assert raised.value.status.complete is False
    assert raised.value.status.retryable is True
    assert "launcher" in raised.value.status.pending
    assert raised.value.transaction is transaction
    monkeypatch.setattr(transaction, "_restore_shortcut", original_restore)
    status = transaction.restore()
    assert status.complete is True
    assert status.pending == ()


def test_apply_and_restore_are_idempotent_for_one_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    snapshot = transaction.snapshot()

    first = transaction.apply(snapshot)
    call_count = len(calls)
    second = transaction.apply(snapshot)
    assert len(calls) == call_count
    assert second.changed_entries == ()
    assert first.changed_entries
    assert transaction.restore(snapshot).complete is True
    assert transaction.restore(snapshot).complete is True
    assert _surface(start_menu, desktop, registry)[0] == {
        "launcher": None,
        "uninstall": None,
        "desktop": None,
    }


def test_locked_existing_shortcut_fails_without_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    start_menu.mkdir(parents=True)
    (start_menu / "All The Context.lnk").write_bytes(b"locked-prior")
    original_publish = transaction._publish_existing_shortcut

    def locked_publish(*_args: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(transaction, "_publish_existing_shortcut", locked_publish)
    before = _surface(start_menu, desktop, registry)

    with pytest.raises(application_install.WindowsRegistrationError):
        transaction.apply(transaction.snapshot())

    assert _surface(start_menu, desktop, registry) == before
    monkeypatch.setattr(transaction, "_publish_existing_shortcut", original_publish)


def test_snapshot_rejects_concurrent_target_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    snapshot = transaction.snapshot()
    start_menu.mkdir(parents=True)
    replacement = start_menu / "All The Context.lnk"
    replacement.write_bytes(b"user-replacement")

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.apply(snapshot)

    assert raised.value.code == "registration_target_changed"
    assert replacement.read_bytes() == b"user-replacement"
    assert _surface(start_menu, desktop, registry)[1] == {}


def test_apply_rejects_target_substitution_during_shortcut_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    _patch_shortcut_writer(monkeypatch)
    snapshot = transaction.snapshot()
    start_menu.mkdir(parents=True)

    def racing_writer(
        path: Path,
        executable: Path,
        *,
        arguments: str = "",
        description: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{executable}|{arguments}|{description}".encode())
        if path.parent == start_menu:
            (start_menu / "All The Context.lnk").write_bytes(b"concurrent-user-file")

    monkeypatch.setattr(application_install, "_create_windows_shortcut", racing_writer)
    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.apply(snapshot)

    assert raised.value.code == "registration_target_changed"
    assert (start_menu / "All The Context.lnk").read_bytes() == b"concurrent-user-file"
    assert _surface(start_menu, desktop, registry)[1] == {}


def test_unrelated_files_values_and_subkeys_survive_apply_and_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "Unrelated": (registry.REG_BINARY, b"user-value")
    }
    registry.subkeys.add(application_install.WINDOWS_UNINSTALL_KEY + r"\UserChild")
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    start_menu.mkdir(parents=True)
    assert desktop is not None
    desktop.mkdir()
    unrelated_start = start_menu / "User file.txt"
    unrelated_desktop = desktop / "User file.txt"
    unrelated_start.write_bytes(b"start-user")
    unrelated_desktop.write_bytes(b"desktop-user")
    snapshot = transaction.snapshot()

    transaction.apply(snapshot)
    transaction.restore(snapshot)

    assert unrelated_start.read_bytes() == b"start-user"
    assert unrelated_desktop.read_bytes() == b"desktop-user"
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["Unrelated"] == (
        registry.REG_BINARY,
        b"user-value",
    )
    assert application_install.WINDOWS_UNINSTALL_KEY + r"\UserChild" in registry.subkeys


def test_unsafe_parent_reparse_target_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    _transaction, executable, _start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    del executable, desktop, registry
    real_start = tmp_path / "Real Programs"
    real_start.mkdir()
    linked_start = tmp_path / "Linked Programs"
    try:
        linked_start.symlink_to(real_start, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    linked_transaction = application_install.WindowsApplicationRegistrationTransaction(
        tmp_path / "AllTheContext.exe",
        start_menu=linked_start / "All The Context",
        desktop=None,
        registry=_FakeRegistry(),
    )
    with pytest.raises(application_install.WindowsRegistrationError, match="reparse"):
        linked_transaction.snapshot()


def test_unsafe_shortcut_symlink_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    del desktop, registry
    start_menu.mkdir(parents=True)
    real_file = tmp_path / "real.lnk"
    real_file.write_bytes(b"target")
    shortcut = start_menu / "All The Context.lnk"
    try:
        shortcut.symlink_to(real_file)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    with pytest.raises(application_install.WindowsRegistrationError, match="reparse"):
        transaction.snapshot()


def test_unsafe_shortcut_hardlink_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    del desktop, registry
    start_menu.mkdir(parents=True)
    real_file = tmp_path / "real.lnk"
    real_file.write_bytes(b"hardlink")
    try:
        os.link(real_file, start_menu / "All The Context.lnk")
    except OSError:
        pytest.skip("hardlinks are unavailable")
    with pytest.raises(application_install.WindowsRegistrationError, match="hardlink"):
        transaction.snapshot()


def test_reparse_attribute_is_rejected_without_windows_specific_fixture() -> None:
    metadata = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
    assert application_install._is_reparse_or_link(metadata) is True


def test_non_windows_install_is_a_noop_without_loading_winreg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(application_install.platform, "system", lambda: "Linux")

    def unexpected_registry() -> object:
        raise AssertionError("winreg must remain late-bound on non-Windows")

    monkeypatch.setattr(application_install, "windows_registry", unexpected_registry)
    assert application_install.install_application_entrypoints(Path("unused.exe")) is None


def test_existing_registry_key_is_opened_for_query_and_set_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "Unrelated": (registry.REG_SZ, "preserve")
    }
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )

    transaction.apply(transaction.snapshot())

    assert any(
        name == application_install.WINDOWS_UNINSTALL_KEY
        and access == registry.KEY_READ | registry.KEY_SET_VALUE
        for name, access in registry.open_calls
    )


def test_registration_refuses_vendor_surface_and_keeps_shared_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "DisplayName": (registry.REG_EXPAND_SZ, "%VENDOR_NAME%"),
        "Unrelated": (registry.REG_BINARY, b"vendor-value"),
    }
    registry.subkeys.add(application_install.WINDOWS_UNINSTALL_KEY + r"\VendorChild")
    _transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    assert desktop is not None
    start_menu.mkdir(parents=True)
    desktop.mkdir()
    (start_menu / "All The Context.lnk").write_bytes(b"vendor-launcher")
    (start_menu / "Uninstall All The Context.lnk").write_bytes(b"vendor-uninstall")
    (desktop / "All The Context.lnk").write_bytes(b"vendor-desktop")
    before = _surface(start_menu, desktop, registry)
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.install_application_entrypoints(executable)

    assert raised.value.code == "registration_target_changed"
    assert _surface(start_menu, desktop, registry) == before
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys
    assert application_install.WINDOWS_UNINSTALL_KEY + r"\VendorChild" in registry.subkeys
    assert not calls
    assert not application_install._registration_journal_path(tmp_path).exists()
    application_install.remove_application_entrypoints()


def test_matching_preexisting_shortcuts_are_not_overwritten_without_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    assert desktop is not None
    start_menu.mkdir(parents=True)
    desktop.mkdir()
    shortcut_specs = (
        (start_menu / "All The Context.lnk", "", "Open your local All The Context Core"),
        (desktop / "All The Context.lnk", "", "Open your local All The Context Core"),
        (
            start_menu / "Uninstall All The Context.lnk",
            "--uninstall",
            "Uninstall All The Context (your context data is kept)",
        ),
    )
    for path, arguments, description in shortcut_specs:
        path.write_bytes(f"{executable}|{arguments}|{description}".encode())

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.apply(transaction.snapshot())

    assert raised.value.code == "registration_target_changed"
    assert not calls
    assert registry.keys == {}
    assert all(path.exists() for path, _arguments, _description in shortcut_specs)


def test_version_transition_migrates_installed_registration_without_preimage_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    before = _surface(start_menu, desktop, registry)
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)

    monkeypatch.setattr(application_install, "__version__", "0.1.0-beta.6")
    application_install.install_application_entrypoints(executable)
    old_journal = transaction._load_journal()
    assert old_journal is not None and old_journal.phase == "installed"

    monkeypatch.setattr(application_install, "__version__", "0.1.0-beta.7")
    result = application_install.install_application_entrypoints(executable)

    assert result is not None
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["DisplayVersion"] == (
        registry.REG_SZ,
        "0.1.0-beta.7",
    )
    migrated = transaction._load_journal()
    assert migrated is not None and migrated.phase == "installed"
    assert migrated.snapshot == old_journal.snapshot

    application_install.remove_application_entrypoints()
    assert _surface(start_menu, desktop, registry) == (
        before[0],
        {application_install.WINDOWS_UNINSTALL_KEY: {}},
    )


def test_schema1_installed_journal_is_rejected_without_authenticated_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    initial = transaction.snapshot()
    application_install.install_application_entrypoints(executable)
    installed = transaction._load_journal()
    assert installed is not None and installed.phase == "installed"
    path = _write_legacy_journal(
        transaction,
        initial,
        phase="installed",
        active=transaction._owned_names(),
        desired_shortcuts=installed.desired_shortcuts,
    )

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction._load_journal()

    assert raised.value.code == "registration_journal_invalid"
    assert json.loads(path.read_text(encoding="ascii"))["schema"] == 1
    assert _surface(start_menu, desktop, registry)[0]["launcher"] is not None
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys


def test_schema1_active_shortcut_is_rejected_without_publication_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    initial = transaction.snapshot()
    transaction._prepare_journal(initial)
    plan = transaction._shortcut_plans()[0]
    temporary = transaction._temporary_path(plan.path)
    generated = transaction._desired_shortcut_data(plan, temporary)
    assert transaction._journal is not None
    transaction._journal.desired_shortcuts[plan.name] = generated
    transaction._persist_journal("applying", (plan.name,))
    transaction._publish_new_shortcut(temporary, plan.path)
    _write_legacy_journal(
        transaction,
        initial,
        phase="applying",
        active=(plan.name,),
        desired_shortcuts={plan.name: generated},
    )

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.recover_application_entrypoints()

    assert raised.value.code == "registration_journal_invalid"
    assert plan.path.exists()
    assert transaction._journal_path.exists()


def test_schema1_ambiguous_or_tampered_surface_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    initial = transaction.snapshot()
    transaction._prepare_journal(initial)
    plan = transaction._shortcut_plans()[0]
    temporary = transaction._temporary_path(plan.path)
    generated = transaction._desired_shortcut_data(plan, temporary)
    assert transaction._journal is not None
    _write_legacy_journal(
        transaction,
        initial,
        phase="applying",
        active=(plan.name,),
        desired_shortcuts={plan.name: generated},
    )
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    plan.path.write_bytes(b"vendor-shortcut")

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.recover_application_entrypoints()

    assert raised.value.code == "registration_journal_invalid"
    assert plan.path.read_bytes() == b"vendor-shortcut"
    assert json.loads(transaction._journal_path.read_text(encoding="ascii"))["schema"] == 1
    assert temporary.exists()


def test_schema1_same_byte_replacement_never_becomes_owned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    initial = transaction.snapshot()
    application_install.install_application_entrypoints(executable)
    installed = transaction._load_journal()
    assert installed is not None
    _write_legacy_journal(
        transaction,
        initial,
        phase="installed",
        active=transaction._owned_names(),
        desired_shortcuts=installed.desired_shortcuts,
    )
    target = start_menu / "All The Context.lnk"
    replacement = tmp_path / "same-byte-replacement.lnk"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.remove_application_entrypoints()

    assert raised.value.code == "registration_journal_invalid"
    assert target.read_bytes() == f"{executable}||Open your local All The Context Core".encode()
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys


def test_schema1_forged_empty_key_presence_cannot_authorize_key_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {}
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    initial = transaction.snapshot()
    assert initial.uninstall_key_present is True
    application_install.install_application_entrypoints(executable)
    installed = transaction._load_journal()
    assert installed is not None
    _write_legacy_journal(
        transaction,
        initial,
        phase="installed",
        active=transaction._owned_names(),
        desired_shortcuts=installed.desired_shortcuts,
    )
    raw = json.loads(transaction._journal_path.read_text(encoding="ascii"))
    raw["snapshot"]["uninstall_key_present"] = False
    transaction._journal_path.write_text(json.dumps(raw, separators=(",", ":")), encoding="ascii")

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.remove_application_entrypoints()

    assert raised.value.code == "registration_journal_invalid"
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]


def test_rewrapped_presence_forge_cannot_delete_preexisting_empty_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {}
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    application_install.install_application_entrypoints(executable)
    journal = transaction._load_journal()
    assert journal is not None and journal.registry_key_created is False

    def protect_for_test(payload: bytes) -> bytes:
        return (
            application_install.hmac.new(
                application_install._JOURNAL_TEST_KEY,
                payload,
                application_install.hashlib.sha256,
            ).digest()
            + payload
        )

    def unprotect_for_test(protected_payload: bytes) -> bytes:
        expected = application_install.hmac.new(
            application_install._JOURNAL_TEST_KEY,
            protected_payload[32:],
            application_install.hashlib.sha256,
        ).digest()
        if protected_payload[:32] != expected:
            raise application_install.WindowsRegistrationError("registration_journal_auth_invalid")
        return protected_payload[32:]

    monkeypatch.setattr(application_install, "_protect_registration_payload", protect_for_test)
    monkeypatch.setattr(application_install, "_unprotect_registration_payload", unprotect_for_test)
    encoded = application_install._encode_journal(journal, phase="installed", active=journal.active)
    outer = json.loads(encoded.decode("ascii"))
    inner = json.loads(base64.b64decode(outer["protected"])[32:].decode("ascii"))
    inner["snapshot"]["uninstall_key_present"] = False
    inner["registry_key_created"] = True
    inner_bytes = (json.dumps(inner, separators=(",", ":")) + "\n").encode("ascii")
    outer["protected"] = base64.b64encode(protect_for_test(inner_bytes)).decode("ascii")
    transaction._journal_path.write_text(json.dumps(outer, separators=(",", ":")), encoding="ascii")

    application_install.remove_application_entrypoints()

    assert _surface(start_menu, desktop, registry) == (
        {"launcher": None, "uninstall": None, "desktop": None},
        {application_install.WINDOWS_UNINSTALL_KEY: {}},
    )
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys


def test_rewrapped_forged_journal_cannot_touch_vendor_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    application_install.install_application_entrypoints(executable)
    journal = transaction._load_journal()
    assert journal is not None

    def protect_for_test(payload: bytes) -> bytes:
        return (
            application_install.hmac.new(
                application_install._JOURNAL_TEST_KEY,
                payload,
                application_install.hashlib.sha256,
            ).digest()
            + payload
        )

    def unprotect_for_test(protected_payload: bytes) -> bytes:
        expected = application_install.hmac.new(
            application_install._JOURNAL_TEST_KEY,
            protected_payload[32:],
            application_install.hashlib.sha256,
        ).digest()
        if protected_payload[:32] != expected:
            raise application_install.WindowsRegistrationError("registration_journal_auth_invalid")
        return protected_payload[32:]

    monkeypatch.setattr(application_install, "_protect_registration_payload", protect_for_test)
    monkeypatch.setattr(application_install, "_unprotect_registration_payload", unprotect_for_test)
    encoded = application_install._encode_journal(journal, phase="installed", active=journal.active)
    outer = json.loads(encoded.decode("ascii"))
    inner = json.loads(base64.b64decode(outer["protected"])[32:].decode("ascii"))
    inner["snapshot"]["uninstall_key_present"] = False
    for item in inner["snapshot"]["registry_values"]:
        if item["name"] == "DisplayName":
            item["present"] = True
            item["value_type"] = registry.REG_SZ
            item["data"] = {"kind": "str", "value": "vendor-preimage"}
    inner["desired_shortcuts"]["launcher"] = base64.b64encode(b"forged").decode("ascii")
    inner_bytes = (json.dumps(inner, separators=(",", ":")) + "\n").encode("ascii")
    protected = (
        application_install.hmac.new(
            application_install._JOURNAL_TEST_KEY,
            inner_bytes,
            application_install.hashlib.sha256,
        ).digest()
        + inner_bytes
    )
    outer["protected"] = base64.b64encode(protected).decode("ascii")
    transaction._journal_path.write_text(json.dumps(outer, separators=(",", ":")), encoding="ascii")

    target = start_menu / "All The Context.lnk"
    target.write_bytes(b"vendor-shortcut")
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["DisplayName"] = (
        registry.REG_EXPAND_SZ,
        "%VENDOR_NAME%",
    )
    before = _surface(start_menu, desktop, registry)

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.remove_application_entrypoints()

    assert raised.value.code in {
        "registration_target_changed",
        "registration_journal_mismatch",
    }
    assert _surface(start_menu, desktop, registry) == before


def test_registration_journal_rejects_impossible_registry_presence_shape() -> None:
    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install._decode_registry_snapshot(
            {
                "name": "DisplayName",
                "present": False,
                "value_type": None,
                "data": {"kind": "str", "value": "should-not-exist"},
            }
        )

    assert raised.value.code == "registration_journal_invalid"


def test_deep_bounded_journal_json_is_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / ".atc-registration" / "registration-v1.json"
    path.parent.mkdir()
    path.write_text("[" * 3000 + "0" + "]" * 3000, encoding="ascii")

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install._read_registration_journal(path)

    assert raised.value.code == "registration_journal_invalid"


def test_unhashable_journal_shapes_are_safe_invalid_results() -> None:
    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install._decode_snapshot(
            {
                "plan_token": "0" * 64,
                "uninstall_key_present": False,
                "shortcuts": [{"name": [], "present": False, "data": None, "identity": None}],
                "registry_values": [],
            }
        )
    assert raised.value.code == "registration_journal_invalid"

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install._decode_legacy_journal(
            {
                "schema": 1,
                "install_root": "C:/install",
                "executable": "C:/install/AllTheContext.exe",
                "start_menu": "C:/Programs/All The Context",
                "desktop": None,
                "uninstall_key": application_install.WINDOWS_UNINSTALL_KEY,
                "phase": [],
                "active": [],
                "snapshot": {},
                "desired_shortcuts": {},
                "desired_registry": {},
            }
        )
    assert raised.value.code == "registration_journal_invalid"


def test_interrupted_version_transition_finishes_from_forward_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    _transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    monkeypatch.setattr(application_install, "__version__", "0.1.0-beta.6")
    application_install.install_application_entrypoints(executable)

    monkeypatch.setattr(application_install, "__version__", "0.1.0-beta.7")
    resumed = application_install.WindowsApplicationRegistrationTransaction(
        executable,
        start_menu=start_menu,
        desktop=desktop,
        registry=registry,
        install_root=tmp_path,
    )
    journal = resumed._load_journal()
    assert journal is not None and journal.phase == "installed"
    current = resumed._current_snapshot()
    current_version = resumed._registry_values_by_name(current)["DisplayVersion"]
    journal.desired_registry = {
        name: (value_type, data) for name, value_type, data in resumed._desired_registry()
    }
    journal.registry_before = {"DisplayVersion": current_version}
    resumed._persist_journal("migrating", ("DisplayVersion",))

    registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["DisplayVersion"] = (
        registry.REG_SZ,
        "0.1.0-beta.7",
    )
    status = application_install.recover_application_entrypoints()

    assert status is not None and status.complete is True
    recovered = resumed._load_journal()
    assert recovered is not None and recovered.phase == "installed"
    assert recovered.registry_before == {}
    application_install.remove_application_entrypoints()


def test_same_byte_shortcut_replacement_is_preserved_and_blocks_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    _transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    assert desktop is not None
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    application_install.install_application_entrypoints(executable)

    target = start_menu / "All The Context.lnk"
    replacement = tmp_path / "replacement.lnk"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.remove_application_entrypoints()

    assert raised.value.code == "registration_target_changed"
    assert target.read_bytes() == (f"{executable}||Open your local All The Context Core".encode())
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys


def test_shortcut_delete_swap_is_quarantined_without_losing_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, _desktop, _registry = _make_transaction(
        monkeypatch, tmp_path, desktop=False
    )
    snapshot = transaction.snapshot()
    transaction.apply(snapshot)
    assert transaction._journal is not None
    target = start_menu / "All The Context.lnk"
    mutation = application_install._ShortcutMutation(
        "launcher",
        target,
        snapshot.shortcuts[0],
        transaction._journal.desired_shortcuts["launcher"],
        transaction._journal.desired_shortcut_identities["launcher"],
        True,
    )
    original_replace = application_install.os.replace

    def swap_before_quarantine(source: Path, destination: Path) -> None:
        if source == target:
            target.write_bytes(b"replacement-after-validation")
        original_replace(source, destination)

    monkeypatch.setattr(application_install.os, "replace", swap_before_quarantine)
    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction._restore_shortcut(mutation)

    assert raised.value.code == "registration_restore_target_changed"
    assert target.read_bytes() == b"replacement-after-validation"
    assert not application_install._shortcut_quarantine_path(target).exists()


def test_registry_set_swap_is_rejected_before_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "Unrelated": (registry.REG_SZ, "preserve")
    }
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry, desktop=False
    )
    snapshot = transaction.snapshot()
    original_set = registry.SetValueIfUnchanged
    swapped = False

    def swap_before_set(
        key: _FakeKey,
        name: str,
        expected: application_install.WindowsRegistryValueSnapshot,
        value_type: int,
        data: object,
    ) -> bool:
        nonlocal swapped
        if not swapped:
            registry.keys[key.name][name] = (registry.REG_SZ, "vendor-replacement")
            swapped = True
        return original_set(key, name, expected, value_type, data)

    monkeypatch.setattr(registry, "SetValueIfUnchanged", swap_before_set)
    with pytest.raises(application_install.WindowsRegistrationCompensationError) as raised:
        transaction.apply(snapshot)

    assert raised.value.status is not None
    assert raised.value.status.complete is False
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["DisplayName"] == (
        registry.REG_SZ,
        "vendor-replacement",
    )


def test_registry_delete_swap_is_rejected_without_deleting_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "Unrelated": (registry.REG_SZ, "preserve")
    }
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry, desktop=False
    )
    snapshot = transaction.snapshot()
    transaction.apply(snapshot)
    original_delete = registry.DeleteValueIfUnchanged
    swapped = False

    def swap_before_delete(
        key: _FakeKey,
        name: str,
        expected: application_install.WindowsRegistryValueSnapshot,
    ) -> bool:
        nonlocal swapped
        if not swapped:
            registry.keys[key.name][name] = (registry.REG_SZ, "vendor-replacement")
            swapped = True
        return original_delete(key, name, expected)

    monkeypatch.setattr(registry, "DeleteValueIfUnchanged", swap_before_delete)
    status = transaction.restore(snapshot)

    assert status.complete is False
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["NoRepair"] == (
        registry.REG_SZ,
        "vendor-replacement",
    )


def test_registry_key_creation_race_does_not_claim_vendor_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, desktop=False
    )

    def vendor_wins(_root: object, name: str) -> tuple[_FakeKey, bool]:
        registry.keys[name] = {"Unrelated": (registry.REG_SZ, "vendor")}
        return _FakeKey(registry, name, registry.KEY_ALL_ACCESS), False

    monkeypatch.setattr(registry, "CreateKeyIfAbsent", vendor_wins)
    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.apply(transaction.snapshot())

    assert raised.value.code == "registration_target_changed"
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY] == {
        "Unrelated": (registry.REG_SZ, "vendor")
    }
    assert not (start_menu / "All The Context.lnk").exists()


def test_registry_key_delete_swap_is_rejected_without_deleting_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, desktop=False
    )
    transaction.apply(transaction.snapshot())
    original_delete = registry.DeleteKeyIfUnchanged
    swapped = False

    def swap_before_key_delete(
        root: object,
        name: str,
        expected: tuple[application_install.WindowsRegistryValueSnapshot, ...],
    ) -> bool:
        nonlocal swapped
        if not swapped:
            registry.keys[name] = {"Unrelated": (registry.REG_SZ, "vendor-replacement")}
            swapped = True
        return original_delete(root, name, expected)

    monkeypatch.setattr(registry, "DeleteKeyIfUnchanged", swap_before_key_delete)
    status = transaction.restore(transaction._snapshot)

    assert status.complete is False
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY] == {
        "Unrelated": (registry.REG_SZ, "vendor-replacement")
    }


def test_tampered_authenticated_journal_preserves_registration_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    _transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    assert desktop is not None
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    application_install.install_application_entrypoints(executable)
    journal_path = application_install._registration_journal_path(tmp_path)
    payload = json.loads(journal_path.read_text(encoding="ascii"))
    protected = payload["protected"]
    payload["protected"] = ("A" if protected[0] != "A" else "B") + protected[1:]
    journal_path.write_text(json.dumps(payload), encoding="ascii")

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.remove_application_entrypoints()

    assert raised.value.code in {
        "registration_journal_auth_invalid",
        "registration_journal_invalid",
    }
    assert (start_menu / "All The Context.lnk").exists()
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys


@pytest.mark.parametrize("journal_state", ["missing", "corrupt"])
def test_missing_or_corrupt_journal_never_reports_stale_uninstall_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, journal_state: str
) -> None:
    _patch_shortcut_writer(monkeypatch)
    _transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    assert desktop is not None
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    application_install.install_application_entrypoints(executable)
    journal_path = application_install._registration_journal_path(tmp_path)
    if journal_state == "missing":
        journal_path.unlink()
    else:
        journal_path.write_bytes(b"not a registration journal")

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        application_install.remove_application_entrypoints()

    assert raised.value.code in {
        "registration_journal_missing",
        "registration_journal_invalid",
        "registration_journal_auth_invalid",
    }
    assert (start_menu / "All The Context.lnk").exists()
    assert application_install.WINDOWS_UNINSTALL_KEY in registry.keys


def test_journalless_uninstall_is_idempotent_when_only_vendor_preimages_remain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "DisplayName": (registry.REG_EXPAND_SZ, "%VENDOR_NAME%"),
        "Unrelated": (registry.REG_BINARY, b"vendor-value"),
    }
    _transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )
    assert desktop is not None
    start_menu.mkdir(parents=True)
    desktop.mkdir()
    (start_menu / "All The Context.lnk").write_bytes(b"vendor-launcher")
    (start_menu / "Uninstall All The Context.lnk").write_bytes(b"vendor-uninstall")
    (desktop / "All The Context.lnk").write_bytes(b"vendor-desktop")
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)

    application_install.remove_application_entrypoints()
    application_install.remove_application_entrypoints()

    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["Unrelated"] == (
        registry.REG_BINARY,
        b"vendor-value",
    )


def test_interrupted_apply_is_recovered_from_durable_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    snapshot = transaction.snapshot()
    transaction._prepare_journal(snapshot)
    plan = transaction._shortcut_plans()[0]
    temporary = transaction._temporary_path(plan.path)
    generated = transaction._desired_shortcut_data(plan, temporary)
    assert transaction._journal is not None
    transaction._journal.desired_shortcuts[plan.name] = generated
    transaction._persist_journal("applying", transaction._active_with(plan.name))
    mutation = application_install._ShortcutMutation(
        plan.name, plan.path, snapshot.shortcuts[0], generated
    )
    transaction._mutations.append(mutation)
    transaction._publish_new_shortcut(temporary, plan.path)
    final = application_install._shortcut_state(plan.name, plan.path)
    assert final.identity is not None
    transaction._journal.desired_shortcut_identities[plan.name] = final.identity
    transaction._persist_journal("applying", transaction._journal.active)

    status = application_install.recover_application_entrypoints()

    assert status is not None and status.complete is True
    assert not (start_menu / "All The Context.lnk").exists()
    assert registry.keys == {}
    assert not application_install._registration_journal_path(tmp_path).exists()
    del executable


def test_shortcut_publish_crash_has_durable_destination_identity_and_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    snapshot = transaction.snapshot()
    transaction._prepare_journal(snapshot)
    plan = transaction._shortcut_plans()[0]
    persist_calls = 0
    original_persist = transaction._persist_journal

    def crash_after_publication(
        phase: str, active: tuple[application_install.RegistrationName, ...]
    ) -> None:
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 2:
            raise RuntimeError("simulated process crash")
        original_persist(phase, active)

    monkeypatch.setattr(transaction, "_persist_journal", crash_after_publication)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        transaction._apply_shortcut(plan, snapshot.shortcuts[0], [])

    durable = application_install._read_registration_journal(transaction._journal_path)
    current = application_install._shortcut_state(plan.name, plan.path)
    assert durable is not None
    assert current.identity is not None
    assert durable.desired_shortcut_identities[plan.name] == current.identity
    assert plan.path.exists()

    status = application_install.recover_application_entrypoints()

    assert status is not None and status.complete is True
    assert not plan.path.exists()
    assert not transaction._journal_path.exists()
    application_install.install_application_entrypoints(executable)
    application_install.remove_application_entrypoints()
    assert len(calls) >= 4


def test_hardlink_publish_crash_with_nlink_two_converges_on_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, _executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    snapshot = transaction.snapshot()
    transaction._prepare_journal(snapshot)
    plan = transaction._shortcut_plans()[0]
    temporary = transaction._temporary_path(plan.path)
    generated = transaction._desired_shortcut_data(plan, temporary)
    temporary_metadata = application_install._validate_file_path(temporary, allow_missing=False)
    assert temporary_metadata is not None
    assert transaction._journal is not None
    transaction._journal.desired_shortcuts[plan.name] = generated
    transaction._journal.desired_shortcut_identities[plan.name] = (
        application_install._file_identity(temporary_metadata)
    )
    transaction._persist_journal("applying", (plan.name,))
    os.link(temporary, plan.path)
    assert temporary.stat().st_nlink == 2
    assert plan.path.stat().st_nlink == 2

    status = application_install.recover_application_entrypoints()

    assert status is not None and status.complete is True
    assert not temporary.exists()
    assert not plan.path.exists()
    assert not transaction._journal_path.exists()


def test_interrupted_uninstall_replays_only_owned_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(application_install, "_windows_locations", lambda: (start_menu, desktop))
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    application_install.install_application_entrypoints(executable)

    transaction = application_install.WindowsApplicationRegistrationTransaction(
        executable,
        start_menu=start_menu,
        desktop=desktop,
        registry=registry,
        install_root=tmp_path,
    )
    transaction._check_platform_and_plan()
    journal = transaction._load_journal()
    assert journal is not None and journal.phase == "installed"
    transaction._snapshot = journal.snapshot
    transaction._key_created = not journal.snapshot.uninstall_key_present
    transaction._mutations = transaction._journal_mutations(journal, transaction._owned_names())
    transaction._persist_journal("uninstalling", transaction._owned_names())
    partial = transaction._mutations.pop()
    assert isinstance(partial, application_install._RegistryMutation)
    transaction._restore_registry(partial)
    transaction._persist_journal(
        "uninstalling", tuple(mutation.name for mutation in transaction._mutations)
    )

    status = application_install.recover_application_entrypoints()

    assert status is not None and status.complete is True
    assert _surface(start_menu, desktop, registry) == (
        {"launcher": None, "uninstall": None, "desktop": None},
        {application_install.WINDOWS_UNINSTALL_KEY: {}},
    )


def test_generator_failure_cleans_written_temporary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transaction, _executable, start_menu, _desktop, _registry = _make_transaction(
        monkeypatch, tmp_path
    )

    def write_then_fail(
        path: Path,
        _executable: Path,
        *,
        arguments: str = "",
        description: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{arguments}|{description}".encode())
        raise OSError("generator failed after writing")

    monkeypatch.setattr(application_install, "_create_windows_shortcut", write_then_fail)
    with pytest.raises(application_install.WindowsRegistrationError):
        transaction.apply(transaction.snapshot())

    assert not list(start_menu.rglob("*.atc-new"))


def test_install_root_mismatch_is_rejected_before_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    canonical_root = tmp_path / "Canonical Install"
    canonical_root.mkdir()
    wrong_root = tmp_path / "Wrong Install"
    wrong_root.mkdir()
    wrong_executable = wrong_root / application_install.WINDOWS_APP_NAME
    wrong_executable.write_bytes(b"wrong")
    monkeypatch.setenv("ATC_INSTALL_DIR", str(canonical_root))
    monkeypatch.setattr(application_install.platform, "system", lambda: "Windows")
    transaction = application_install.WindowsApplicationRegistrationTransaction(
        wrong_executable,
        start_menu=tmp_path / "Programs" / "All The Context",
        desktop=None,
        registry=_FakeRegistry(),
        install_root=canonical_root,
    )

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        transaction.snapshot()

    assert raised.value.code == "registration_executable_mismatch"


def test_executable_symlink_is_rejected_without_resolving_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _transaction, executable, start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, desktop=False
    )
    linked = executable
    executable.unlink()
    real_executable = tmp_path / "real.exe"
    real_executable.write_bytes(b"real")
    try:
        linked.symlink_to(real_executable)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    linked_transaction = application_install.WindowsApplicationRegistrationTransaction(
        linked,
        start_menu=start_menu,
        desktop=None,
        registry=registry,
        install_root=tmp_path,
    )

    with pytest.raises(application_install.WindowsRegistrationError) as raised:
        linked_transaction.snapshot()

    assert raised.value.code == "registration_reparse_path"

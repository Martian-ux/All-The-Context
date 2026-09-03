from __future__ import annotations

import copy
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from allthecontext import application_install


class _FakeKey:
    def __init__(self, registry: _FakeRegistry, name: str) -> None:
        self.registry = registry
        self.name = name

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
    KEY_SET_VALUE = 0x0002

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, tuple[int, object]]] = {}
        self.subkeys: set[str] = set()
        self.fail_before: set[str] = set()
        self.fail_after: str | None = None
        self.fail_restore_name: str | None = None
        self.fail_restore_data: object | None = None
        self.fail_delete: set[str] = set()
        self.fail_create_after = False

    def OpenKey(self, _root: object, name: str, *_args: object) -> _FakeKey:
        if name not in self.keys:
            raise FileNotFoundError(name)
        return _FakeKey(self, name)

    def CreateKey(self, _root: object, name: str) -> _FakeKey:
        self.keys.setdefault(name, {})
        if self.fail_create_after:
            self.fail_create_after = False
            raise OSError(name)
        return _FakeKey(self, name)

    def QueryValueEx(self, key: _FakeKey, name: str) -> tuple[object, int]:
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
        if name in self.fail_before:
            raise PermissionError(name)
        if self.fail_restore_name == name and data == self.fail_restore_data:
            raise PermissionError(name)
        self.keys[key.name][name] = (value_type, copy.deepcopy(data))
        if self.fail_after == name:
            self.fail_after = None
            raise OSError(name)

    def DeleteValue(self, key: _FakeKey, name: str) -> None:
        if name in self.fail_delete:
            raise PermissionError(name)
        try:
            del self.keys[key.name][name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def QueryInfoKey(self, key: _FakeKey) -> tuple[int, int, int]:
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


def test_registration_apply_and_restore_preserve_exact_prior_files_types_and_data(
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
    result = transaction.apply(snapshot)
    assert set(result.changed_entries) == {
        "launcher",
        "desktop",
        "uninstall",
        *transaction._REGISTRY_NAMES,
    }
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["Unrelated"] == (
        registry.REG_SZ,
        "preserve",
    )

    status = transaction.restore(snapshot)
    assert status.complete is True
    assert status.retryable is False
    assert _surface(start_menu, desktop, registry) == before
    assert transaction.restore(snapshot).complete is True


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

    assert _surface(start_menu, desktop, registry) == before


def test_failed_registry_compensation_is_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_shortcut_writer(monkeypatch)
    registry = _FakeRegistry()
    registry.keys[application_install.WINDOWS_UNINSTALL_KEY] = {
        "DisplayName": (registry.REG_QWORD, 123),
        "Unrelated": (registry.REG_SZ, "keep"),
    }
    registry.fail_before.add("DisplayVersion")
    registry.fail_restore_name = "DisplayName"
    registry.fail_restore_data = 123
    transaction, _executable, _start_menu, _desktop, registry = _make_transaction(
        monkeypatch, tmp_path, registry=registry
    )

    with pytest.raises(application_install.WindowsRegistrationCompensationError) as raised:
        transaction.apply(transaction.snapshot())

    assert raised.value.status is not None
    assert raised.value.status.complete is False
    assert "DisplayName" in raised.value.status.pending
    registry.fail_restore_name = None
    status = transaction.restore()
    assert status.complete is True
    assert registry.keys[application_install.WINDOWS_UNINSTALL_KEY]["DisplayName"] == (
        registry.REG_QWORD,
        123,
    )


def test_install_entrypoints_uses_reversible_registration_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _patch_shortcut_writer(monkeypatch)
    transaction, executable, start_menu, desktop, registry = _make_transaction(
        monkeypatch, tmp_path
    )
    del transaction
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

from __future__ import annotations

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.user_startup import (
    STARTUP_NAME,
    _desktop_exec,
    install_user_startup,
    remove_smoke_windows_startup_key,
    remove_user_startup,
)


def test_xdg_exec_serializer_escapes_reserved_characters() -> None:
    command = ('/tmp/A\\B $HOME `who` "quoted" 100%', "--core")

    rendered = _desktop_exec(command)

    assert rendered == r'"/tmp/A\\B \$HOME \`who\` \"quoted\" 100%%" "--core"'


def test_linux_startup_adapter_uses_xdg_autostart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("allthecontext.user_startup.platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    executable = tmp_path / "All The Context" / "all-the-context"
    runtime = RuntimeCommand(executable)

    result = install_user_startup(runtime)

    startup_file = tmp_path / "autostart" / "all-the-context.desktop"
    assert result.mechanism == "XDG autostart"
    assert startup_file.is_file()
    content = startup_file.read_text(encoding="utf-8")
    assert f"Exec={_desktop_exec(runtime.core())}" in content
    assert "Terminal=false" in content

    remove_user_startup()
    assert not startup_file.exists()


def test_windows_startup_adapter_uses_mocked_per_user_registry(tmp_path: Path, monkeypatch) -> None:
    values: dict[str, tuple[str, int, str]] = {}

    class Key:
        def __enter__(self) -> Key:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        REG_SZ=1,
        CreateKey=lambda _root, _path: Key(),
        SetValueEx=lambda _key, name, _reserved, kind, value: values.__setitem__(
            name, (name, kind, value)
        ),
    )
    monkeypatch.setattr("allthecontext.user_startup.windows_registry", lambda: fake_winreg)
    monkeypatch.setattr("allthecontext.user_startup.platform.system", lambda: "Windows")
    executable = tmp_path / "Installed App" / "AllTheContext.exe"

    result = install_user_startup(RuntimeCommand(executable))

    assert result.mechanism == "HKCU Run"
    assert values["All The Context Core"][2] == f'"{executable}" --core'


def test_macos_startup_adapter_writes_isolated_launch_agent(tmp_path: Path, monkeypatch) -> None:
    launch_agents = tmp_path / "LaunchAgents"
    executable = (
        tmp_path / "Applications" / "All The Context.app" / "Contents" / "MacOS" / ("AllTheContext")
    )
    monkeypatch.setattr("allthecontext.user_startup.platform.system", lambda: "Darwin")
    monkeypatch.setenv("ATC_PACKAGED_SMOKE", "1")
    monkeypatch.setenv("ATC_SMOKE_LAUNCH_AGENTS_DIR", str(launch_agents))

    result = install_user_startup(RuntimeCommand(executable))

    path = launch_agents / "com.allthecontext.core.plist"
    with path.open("rb") as stream:
        payload = plistlib.load(stream)
    assert result.mechanism == "LaunchAgent"
    assert payload["Label"] == "com.allthecontext.core"
    assert payload["ProgramArguments"] == [str(executable), "--core"]
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is False
    assert payload["ProcessType"] == "Background"

    remove_user_startup()
    assert not path.exists()


def test_startup_test_overrides_require_explicit_packaged_smoke(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("allthecontext.user_startup.platform.system", lambda: "Darwin")
    monkeypatch.setenv("ATC_SMOKE_LAUNCH_AGENTS_DIR", str(tmp_path))

    try:
        install_user_startup(RuntimeCommand(tmp_path / "all-the-context"))
    except OSError as exc:
        assert "unsafe LaunchAgent" in str(exc)
    else:
        raise AssertionError("unsafe LaunchAgent override was accepted")


def test_successful_smoke_startup_path_leaves_zero_orphan_registry_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install → remove value → delete empty smoke key leaves no orphan state."""
    smoke_key = r"Software\AllTheContext\Smoke\packaged-startup-test"
    values: dict[str, tuple[str, int, str]] = {}
    keys_present = {smoke_key}
    deleted_keys: list[str] = []

    class Key:
        def __init__(self, path: str) -> None:
            self.path = path

        def __enter__(self) -> Key:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def create_key(_root: object, path: str) -> Key:
        keys_present.add(path)
        return Key(path)

    def open_key(_root: object, path: str, *_args: object) -> Key:
        if path not in keys_present:
            raise FileNotFoundError(path)
        return Key(path)

    def set_value(_key: Key, name: str, _reserved: int, kind: int, value: str) -> None:
        values[name] = (name, kind, value)

    def delete_value(_key: Key, name: str) -> None:
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    def query_info(_key: Key) -> tuple[int, int, int]:
        return (0, len(values), 0)

    def delete_key(_root: object, path: str) -> None:
        if path not in keys_present:
            raise FileNotFoundError(path)
        if values:
            raise OSError("key still has values")
        keys_present.remove(path)
        deleted_keys.append(path)

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        REG_SZ=1,
        KEY_SET_VALUE=2,
        KEY_READ=1,
        CreateKey=create_key,
        OpenKey=open_key,
        SetValueEx=set_value,
        DeleteValue=delete_value,
        QueryInfoKey=query_info,
        DeleteKey=delete_key,
    )
    monkeypatch.setattr("allthecontext.user_startup.windows_registry", lambda: fake_winreg)
    monkeypatch.setattr("allthecontext.user_startup.platform.system", lambda: "Windows")
    monkeypatch.setenv("ATC_PACKAGED_SMOKE", "1")
    monkeypatch.setenv("ATC_SMOKE_STARTUP_WINDOWS_KEY", smoke_key)
    executable = tmp_path / "Installed App" / "AllTheContext.exe"

    install_user_startup(RuntimeCommand(executable))
    assert smoke_key in keys_present
    assert STARTUP_NAME in values

    remove_user_startup()
    assert STARTUP_NAME not in values
    assert smoke_key in keys_present  # value gone, empty key remains without smoke cleanup

    remove_smoke_windows_startup_key(windows_key=smoke_key)
    assert smoke_key not in keys_present
    assert deleted_keys == [smoke_key]

    # Fail closed: product Run key and nonempty smoke keys are never deleted.
    with pytest.raises(OSError, match="non-smoke"):
        remove_smoke_windows_startup_key(
            windows_key=r"Software\Microsoft\Windows\CurrentVersion\Run"
        )
    keys_present.add(smoke_key)
    values[STARTUP_NAME] = (STARTUP_NAME, 1, "still-present")
    with pytest.raises(OSError, match="nonempty"):
        remove_smoke_windows_startup_key(windows_key=smoke_key)

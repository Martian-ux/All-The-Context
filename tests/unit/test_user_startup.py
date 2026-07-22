from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.user_startup import install_user_startup, remove_user_startup


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
    assert f'Exec="{executable}" "--core"' in content
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
    monkeypatch.setitem(__import__("sys").modules, "winreg", fake_winreg)
    monkeypatch.setattr("allthecontext.user_startup.platform.system", lambda: "Windows")
    executable = tmp_path / "Installed App" / "AllTheContext.exe"

    result = install_user_startup(RuntimeCommand(executable))

    assert result.mechanism == "HKCU Run"
    assert values["All The Context Core"][2] == f'"{executable}" --core'

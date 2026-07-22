from __future__ import annotations

from pathlib import Path

from allthecontext import application_install


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

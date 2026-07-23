"""Exercise isolated native credential and per-user startup adapters."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import secrets
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from allthecontext.credentials import (
    DevelopmentFileCredentialStore,
    verify_isolated_os_credential_round_trip,
)
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.user_startup import STARTUP_NAME, install_user_startup, remove_user_startup


def exercise_credentials(*, require_os_store: bool) -> dict[str, str]:
    credential_name = f"acceptance:{secrets.token_hex(16)}"
    token = secrets.token_urlsafe(48)
    os_status = "unavailable"
    try:
        verify_isolated_os_credential_round_trip()
        os_status = "round-trip-and-delete-passed"
    except Exception as exc:
        if require_os_store:
            raise RuntimeError(
                "the platform OS credential store failed its isolated acceptance check"
            ) from exc

    with tempfile.TemporaryDirectory(prefix="atc-credential-acceptance-") as temporary:
        fallback = DevelopmentFileCredentialStore(Path(temporary) / "credentials.json")
        fallback.set(credential_name, token)
        if fallback.get(credential_name) != token:
            raise RuntimeError("the explicit fallback credential did not round trip")
        fallback.delete(credential_name)
        if fallback.get(credential_name) is not None:
            raise RuntimeError("the explicit fallback credential was not deleted")
    return {
        "os_credential": os_status,
        "explicit_fallback": "round-trip-and-delete-passed",
    }


def _restore_environment(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def exercise_startup() -> dict[str, str]:
    system = platform.system()
    names = (
        "ATC_PACKAGED_SMOKE",
        "ATC_SMOKE_STARTUP_WINDOWS_KEY",
        "ATC_SMOKE_LAUNCH_AGENTS_DIR",
        "XDG_CONFIG_HOME",
    )
    previous = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory(prefix="atc-startup-acceptance-") as temporary:
        root = Path(temporary)
        executable = (
            root / "runtime" / ("AllTheContext.exe" if system == "Windows" else "all-the-context")
        )
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"isolated startup acceptance placeholder")
        runtime = RuntimeCommand(executable)
        windows_key: str | None = None
        os.environ["ATC_PACKAGED_SMOKE"] = "1"
        if system == "Windows":
            windows_key = f"Software\\AllTheContext\\Smoke\\startup-{secrets.token_hex(12)}"
            os.environ["ATC_SMOKE_STARTUP_WINDOWS_KEY"] = windows_key
        elif system == "Darwin":
            os.environ["ATC_SMOKE_LAUNCH_AGENTS_DIR"] = str(root / "LaunchAgents")
        else:
            os.environ["XDG_CONFIG_HOME"] = str(root / "config")

        installed = False
        try:
            result = install_user_startup(runtime)
            installed = True
            if system == "Windows":
                import winreg

                assert windows_key is not None
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, windows_key) as key:
                    command, _kind = winreg.QueryValueEx(key, STARTUP_NAME)
                if command != subprocess.list2cmdline(runtime.core()):
                    raise RuntimeError("Windows startup command did not match the runtime")
            elif system == "Darwin":
                path = root / "LaunchAgents" / "com.allthecontext.core.plist"
                with path.open("rb") as stream:
                    payload = plistlib.load(stream)
                if payload.get("ProgramArguments") != list(runtime.core()):
                    raise RuntimeError("LaunchAgent command did not match the runtime")
                plutil = Path("/usr/bin/plutil")
                if plutil.is_file():
                    subprocess.run(
                        [str(plutil), "-lint", str(path)],
                        check=True,
                        capture_output=True,
                        timeout=20,
                    )
            else:
                path = root / "config" / "autostart" / "all-the-context.desktop"
                content = path.read_text(encoding="utf-8")
                if "Type=Application" not in content or str(executable) not in content:
                    raise RuntimeError("XDG startup entry did not match the runtime")

            remove_user_startup()
            installed = False
            if system == "Windows":
                import winreg

                assert windows_key is not None
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, windows_key) as key:
                    try:
                        winreg.QueryValueEx(key, STARTUP_NAME)
                    except FileNotFoundError:
                        pass
                    else:
                        raise RuntimeError("Windows startup value was not removed")
            elif system == "Darwin":
                if (root / "LaunchAgents" / "com.allthecontext.core.plist").exists():
                    raise RuntimeError("LaunchAgent was not removed")
            elif (root / "config" / "autostart" / "all-the-context.desktop").exists():
                raise RuntimeError("XDG startup entry was not removed")
        finally:
            if installed:
                with suppress(Exception):
                    remove_user_startup()
            if system == "Windows" and windows_key is not None:
                import winreg

                with suppress(FileNotFoundError):
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, windows_key)
            _restore_environment(previous)
    return {"mechanism": result.mechanism, "install_remove": "passed"}


def run_acceptance(*, require_os_store: bool) -> dict[str, Any]:
    return {
        "platform": platform.system(),
        "credential": exercise_credentials(require_os_store=require_os_store),
        "startup": exercise_startup(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-os-credential",
        action="store_true",
        help="Fail instead of accepting the explicit development fallback",
    )
    arguments = parser.parse_args()
    report = run_acceptance(require_os_store=arguments.require_os_credential)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from allthecontext import __version__
from allthecontext.core.app import run_update_health_check


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_health(process: subprocess.Popen[bytes], port: int) -> None:
    url = f"http://127.0.0.1:{port}/health"
    for _ in range(100):
        if process.poll() is not None:
            raise AssertionError(f"Core exited during startup with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    raise AssertionError("Core did not become healthy within five seconds")


def _start_core(data_dir: Path, port: int) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "ATC_CORE_DATA_DIR": str(data_dir),
            "ATC_CORE_HOST": "127.0.0.1",
            "ATC_CORE_PORT": str(port),
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "allthecontext.core.app"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_core(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_core_process_starts_stops_and_restarts(tmp_path: Path) -> None:
    port = _available_port()
    first = _start_core(tmp_path, port)
    try:
        _wait_for_health(first, port)
    finally:
        _stop_core(first)

    second = _start_core(tmp_path, port)
    try:
        _wait_for_health(second, port)
    finally:
        _stop_core(second)


def test_update_health_mode_runs_real_loopback_core_and_exits_cleanly(
    tmp_path: Path, monkeypatch
) -> None:
    port = _available_port()
    data_dir = tmp_path / "health-core"
    report = data_dir / "updates" / "transactions" / ("c" * 24) / "health.json"
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ATC_CORE_HOST", "127.0.0.1")
    monkeypatch.setenv("ATC_CORE_PORT", str(port))
    monkeypatch.setenv("ATC_UPDATE_HEALTH_OPERATION", "c" * 24)

    assert run_update_health_check(report) == 0
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "component": "core",
        "health": "ok",
        "version": __version__,
    }
    assert (data_dir / "core.sqlite3").is_file()

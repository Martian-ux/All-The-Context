"""Regression coverage for the active replacement-candidate version surfaces."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.0-beta.7"
PYTHON_LOCK_VERSION = "0.1.0b7"


def test_candidate_version_is_coherent_across_release_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    runtime = (ROOT / "packages/allthecontext/src/allthecontext/__init__.py").read_text(
        encoding="utf-8"
    )
    dashboard = json.loads((ROOT / "apps/dashboard/package.json").read_text(encoding="utf-8"))
    dashboard_lock = json.loads(
        (ROOT / "apps/dashboard/package-lock.json").read_text(encoding="utf-8")
    )

    locked_project = [
        package
        for package in uv_lock["package"]
        if isinstance(package, dict) and package.get("name") == "all-the-context"
    ]

    assert pyproject["project"]["version"] == VERSION
    assert re.search(rf'^__version__ = "{re.escape(VERSION)}"$', runtime, re.MULTILINE)
    assert dashboard["version"] == VERSION
    assert dashboard_lock["version"] == VERSION
    assert dashboard_lock["packages"][""]["version"] == VERSION
    assert len(locked_project) == 1
    assert locked_project[0]["version"] == PYTHON_LOCK_VERSION

"""Focused contracts for exact-lock install and dependency audit."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_uv_lock_contains_hashed_build_environment() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {
        item["name"]: item
        for item in lock["package"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for name in ("packaging", "setuptools", "wheel"):
        package = packages[name]
        version = package["version"]
        assert isinstance(version, str) and version
        digests: list[str] = []
        wheels = package.get("wheels")
        if isinstance(wheels, list):
            for entry in wheels:
                if isinstance(entry, dict) and isinstance(entry.get("hash"), str):
                    digests.append(entry["hash"])
        sdist = package.get("sdist")
        if isinstance(sdist, dict) and isinstance(sdist.get("hash"), str):
            digests.append(sdist["hash"])
        assert digests, f"{name} lacks reviewed digests in uv.lock"
        assert all(item.startswith("sha256:") for item in digests)


@pytest.mark.parametrize("missing_name", ["packaging", "setuptools", "wheel"])
def test_install_locked_build_backends_fail_closed_when_requirement_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    install = _load_script("install_locked_python.py")
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    # Drop one required package stanza while leaving the rest intact.
    packages = lock_text.split("\n[[package]]\n")
    kept = [packages[0]]
    for stanza in packages[1:]:
        if stanza.lstrip().startswith(f'name = "{missing_name}"'):
            continue
        kept.append(stanza)
    (tmp_path / "uv.lock").write_text("\n[[package]]\n".join(kept), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        calls.append(command)

    monkeypatch.setattr(install, "_run", fake_run)
    with (
        tempfile.TemporaryDirectory() as temporary_name,
        pytest.raises(
            RuntimeError,
            match=rf"missing hashed build-environment packages.*{missing_name}",
        ),
    ):
        install._install_locked_build_backends(sys.executable, tmp_path, Path(temporary_name))
    assert calls == []


def test_install_locked_build_backends_requires_both_exact_hashed_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _load_script("install_locked_python.py")
    (tmp_path / "uv.lock").write_text(
        (ROOT / "uv.lock").read_text(encoding="utf-8"), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        calls.append(command)

    monkeypatch.setattr(install, "_run", fake_run)
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)
        install._install_locked_build_backends(sys.executable, tmp_path, temporary)
        requirements = (temporary / "build-backends.txt").read_text(encoding="utf-8")
    assert "packaging==" in requirements
    assert "setuptools==" in requirements
    assert "wheel==" in requirements
    assert requirements.count("--hash=sha256:") >= 3
    assert len(calls) == 1
    assert calls[0][:5] == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--require-hashes",
    ]


def test_ensure_pinned_uv_fails_closed_without_network_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = _load_script("install_locked_python.py")
    monkeypatch.setattr(install.shutil, "which", lambda _name: None)
    monkeypatch.setattr(install, "_uv_version", lambda _path: None)
    runs: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        runs.append(command)

    monkeypatch.setattr(install, "_run", fake_run)
    with pytest.raises(RuntimeError, match=r"pinned uv==0\.11\.32 is unavailable"):
        install.ensure_pinned_uv(sys.executable)
    assert runs == []


def test_dependency_audit_pip_audit_command_uses_frozen_requirements_mode(
    tmp_path: Path,
) -> None:
    audit = _load_script("dependency_audit.py")
    requirements = tmp_path / "locked-requirements.txt"
    requirements.write_text("example==1.0.0 --hash=sha256:deadbeef\n", encoding="utf-8")
    command = audit.pip_audit_command(sys.executable, requirements)
    assert command == [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(requirements),
        "--disable-pip",
        "--progress-spinner",
        "off",
        "--desc",
        "off",
    ]
    assert str(ROOT) not in command
    assert "--local" not in command


def test_dependency_audit_python_exports_frozen_lock_before_pip_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = _load_script("dependency_audit.py")
    install = _load_script("install_locked_python.py")
    (tmp_path / "uv.lock").write_text(
        (ROOT / "uv.lock").read_text(encoding="utf-8"), encoding="utf-8"
    )
    export_calls: list[list[str]] = []
    audit_calls: list[list[str]] = []

    def fake_ensure(_python: str) -> str:
        return "uv-pinned"

    def fake_run(command: list[str], **kwargs: object) -> object:
        del kwargs
        if command and command[0] == "uv-pinned":
            export_calls.append(command)
            output = Path(command[command.index("--output-file") + 1])
            output.write_text(
                "pip-audit==2.10.1 --hash=sha256:" + ("ab" * 32) + "\n",
                encoding="utf-8",
            )

            class Completed:
                returncode = 0
                stdout = ""
                stderr = ""

            return Completed()
        audit_calls.append(command)

        class Completed:
            returncode = 0
            stdout = "No known vulnerabilities found\n"
            stderr = ""

        return Completed()

    monkeypatch.setattr(install, "ensure_pinned_uv", fake_ensure)
    monkeypatch.setattr(audit, "_load_install_locked", lambda: install)
    monkeypatch.setattr(audit.importlib.metadata, "version", lambda _name: "2.10.1")
    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    result = audit.audit_python(tmp_path)
    assert result == {
        "ecosystem": "python",
        "tool": "pip-audit",
        "tool_version": "2.10.1",
        "ok": True,
    }
    assert export_calls
    assert export_calls[0][:4] == ["uv-pinned", "export", "--frozen", "--no-emit-project"]
    assert "--extra" in export_calls[0]
    assert "dev" in export_calls[0]
    assert "packaging" in export_calls[0]
    assert audit_calls
    assert "--requirement" in audit_calls[0]
    assert "--disable-pip" in audit_calls[0]
    # Audits the exported requirements file, not the project path as a resolve root.
    assert str(tmp_path) not in audit_calls[0]

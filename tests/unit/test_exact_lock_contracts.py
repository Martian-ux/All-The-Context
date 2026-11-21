"""Focused contracts for exact-lock install and dependency audit."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace

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


@pytest.fixture
def installer_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ModuleType, list[list[str]], dict[str, str]]:
    """Exercise main with real lock files and no actual exports or installations."""

    install = _load_script("install_locked_python.py")
    (tmp_path / "uv.lock").write_text(
        (ROOT / "uv.lock").read_text(encoding="utf-8"), encoding="utf-8"
    )
    prefix = tmp_path / "venv"
    (prefix / "Scripts").mkdir(parents=True)
    (prefix / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
    monkeypatch.setattr(install.sys, "prefix", str(prefix))
    monkeypatch.setattr(install.sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(install.sys, "executable", str(prefix / "Scripts" / "python.exe"))
    monkeypatch.setattr(install.sys, "argv", ["installer", "--repository-root", str(tmp_path)])
    monkeypatch.setattr(install.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        install.importlib.metadata,
        "distributions",
        lambda **_kwargs: [SimpleNamespace(metadata={"Name": "pip"})],
    )
    monkeypatch.setattr(install, "ensure_pinned_uv", lambda _python: "reviewed-uv")
    commands: list[list[str]] = []
    files = {"constraints.txt": "example==1.0.0 --hash=sha256:" + "ab" * 32 + "\n"}

    def fake_export(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = Path(command[command.index("--output-file") + 1])
        output.write_text(files["constraints.txt"], encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_install(command: list[str], **_kwargs: object) -> None:
        commands.append(command)
        for index, argument in enumerate(command):
            if argument == "-r":
                requirements = Path(command[index + 1])
                files[requirements.name] = requirements.read_text(encoding="utf-8")

    monkeypatch.setattr(install.subprocess, "run", fake_export)
    monkeypatch.setattr(install, "_run", fake_install)
    return install, commands, files


@pytest.mark.parametrize("extras", [[], ["dev", "packaging"]])
@pytest.mark.parametrize("skip_project", [False, True])
def test_windows_uv_installer_keeps_hashed_closure_and_editable_separate(
    installer_main: tuple[ModuleType, list[list[str]], dict[str, str]],
    extras: list[str],
    skip_project: bool,
) -> None:
    install, commands, files = installer_main
    install.sys.argv.extend(["--installer", "uv"])
    for extra in extras:
        install.sys.argv.extend(["--extra", extra])
    if skip_project:
        install.sys.argv.append("--skip-project")

    assert install.main() == 0
    export, dependencies = commands[:2]
    assert export[:4] == ["reviewed-uv", "export", "--frozen", "--no-emit-project"]
    expected_extras = [item for extra in extras for item in ("--extra", extra)]
    assert export[6:] == ["--no-config", *(expected_extras if extras else ["--no-dev"])]
    base = [
        "reviewed-uv",
        "pip",
        "install",
        "--python",
        install.sys.executable,
        "--link-mode",
        "copy",
        "--no-config",
    ]
    assert dependencies[: len(base)] == base
    requirements = dependencies[len(base) :]
    assert requirements[:2] == ["--require-hashes", "-r"]
    assert Path(requirements[2]).name == "constraints.txt"
    assert "--no-hashes" not in export
    if skip_project:
        assert len(commands) == 2
        assert len(requirements) == 3
        assert "build-backends.txt" not in files
    else:
        assert len(commands) == 3
        assert requirements[3] == "-r"
        assert Path(requirements[4]).name == "build-backends.txt"
        assert len(requirements) == 5
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        assert files["build-backends.txt"].splitlines() == [
            install._hash_requirement_from_lock(lock, name)
            for name in ("packaging", "setuptools", "wheel")
        ]
        target = "." + ("[" + ",".join(extras) + "]" if extras else "")
        assert commands[2] == [*base, "--no-deps", "--no-build-isolation", "-e", target]


@pytest.mark.parametrize("installer", ["pip", "uv"])
@pytest.mark.parametrize("skip_project", [False, True])
@pytest.mark.parametrize("destination", ["target", "prefix"])
def test_uv_installer_isolates_configuration_for_every_operation(
    installer_main: tuple[ModuleType, list[list[str]], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installer: str,
    skip_project: bool,
    destination: str,
) -> None:
    install, commands, _files = installer_main
    install.sys.argv.extend(["--installer", installer])
    if skip_project:
        install.sys.argv.append("--skip-project")
    # Both discovered and explicitly selected configuration can redirect uv
    # despite --python. Environment overrides must not bypass the same guard.
    config = tmp_path / "uv.toml"
    config.write_text(
        f'[pip]\n{destination} = "{(tmp_path / "redirected").as_posix()}"\n',
        encoding="utf-8",
    )
    overrides = {
        "UV_CONFIG_FILE": str(config),
        "UV_TARGET": str(tmp_path / "target"),
        "UV_PREFIX": str(tmp_path / "prefix"),
        "UV_WORKING_DIR": str(tmp_path / "other-project"),
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ATC_INSTALL_CONTRACT", "preserved")
    observed: list[tuple[list[str], object]] = []
    export = install.subprocess.run
    run = install._run

    def capture_export(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append((command, kwargs.get("env")))
        return export(command, **kwargs)

    def capture_install(command: list[str], **kwargs: object) -> None:
        observed.append((command, kwargs.get("env")))
        run(command, **kwargs)

    monkeypatch.setattr(install.subprocess, "run", capture_export)
    monkeypatch.setattr(install, "_run", capture_install)

    assert install.main() == 0
    assert [command for command, _env in observed] == commands
    assert len(observed) == (2 if skip_project else (3 if installer == "uv" else 4))
    for command, environment in observed:
        if installer == "uv":
            assert "--no-config" in command
            assert isinstance(environment, dict)
            assert not any(key.upper().startswith("UV_") for key in environment)
            assert environment["ATC_INSTALL_CONTRACT"] == "preserved"
        else:
            assert "--no-config" not in command
            if environment is not None:
                assert isinstance(environment, dict)
                assert all(environment[key] == value for key, value in overrides.items())
    assert all(install.os.environ[key] == value for key, value in overrides.items())


@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
@pytest.mark.parametrize("skip_project", [False, True])
def test_default_installer_preserves_pip_and_skip_project(
    installer_main: tuple[ModuleType, list[list[str]], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    skip_project: bool,
) -> None:
    install, commands, files = installer_main
    monkeypatch.setattr(install.platform, "system", lambda: system)
    # The existing pip API does not require a fresh venv.
    monkeypatch.setattr(install.sys, "prefix", install.sys.base_prefix)
    if skip_project:
        install.sys.argv.append("--skip-project")

    assert install.main() == 0
    pip = [install.sys.executable, "-m", "pip", "install"]
    assert commands[1][:6] == [*pip, "--require-hashes", "-r"]
    assert len(commands) == (2 if skip_project else 4)
    if skip_project:
        assert "build-backends.txt" not in files
    else:
        assert commands[2][:6] == [*pip, "--require-hashes", "-r"]
        assert commands[3] == [*pip, "--no-deps", "--no-build-isolation", "-e", "."]


@pytest.mark.parametrize(
    "invalid",
    ["platform", "base-python", "outside-python", "missing-config", "system-site", "used-venv"],
)
def test_windows_uv_installer_rejects_unsafe_targets_before_export(
    installer_main: tuple[ModuleType, list[list[str]], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    install, commands, _files = installer_main
    install.sys.argv.extend(["--installer", "uv"])
    config = Path(install.sys.prefix) / "pyvenv.cfg"
    if invalid == "platform":
        monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    elif invalid == "base-python":
        monkeypatch.setattr(install.sys, "base_prefix", install.sys.prefix)
    elif invalid == "outside-python":
        monkeypatch.setattr(install.sys, "executable", str(config.parent / "python.exe"))
    elif invalid == "missing-config":
        config.unlink()
    elif invalid == "system-site":
        config.write_text("include-system-site-packages = true\n", encoding="utf-8")
    else:
        monkeypatch.setattr(
            install.importlib.metadata,
            "distributions",
            lambda **_kwargs: [SimpleNamespace(metadata={"Name": "unrelated-package"})],
        )

    assert install.main() == 1
    assert commands == []


@pytest.mark.parametrize("installer", ["pip", "uv"])
def test_installer_rejects_unhashed_export_before_install(
    installer_main: tuple[ModuleType, list[list[str]], dict[str, str]], installer: str
) -> None:
    install, commands, files = installer_main
    install.sys.argv.extend(["--installer", installer])
    files["constraints.txt"] = "example==1.0.0\n"

    assert install.main() == 1
    assert len(commands) == 1  # Export only; no install subprocess ran.


@pytest.mark.parametrize("missing_name", ["packaging", "setuptools", "wheel"])
def test_windows_uv_installer_requires_complete_backends_before_install(
    installer_main: tuple[ModuleType, list[list[str]], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    install, commands, _files = installer_main
    install.sys.argv.extend(["--installer", "uv"])
    original = install._hash_requirement_from_lock
    monkeypatch.setattr(
        install,
        "_hash_requirement_from_lock",
        lambda lock, name: None if name == missing_name else original(lock, name),
    )

    assert install.main() == 1
    assert len(commands) == 1  # No partially installed dependency environment.


def test_macos_packaging_install_requires_static_cryptography_openssl() -> None:
    install = _load_script("install_locked_python.py")
    baseline = {"EXISTING": "preserved", "OPENSSL_STATIC": "caller-value"}

    macos = install.locked_install_environment(["packaging"], system="Darwin", environ=baseline)
    windows = install.locked_install_environment(["packaging"], system="Windows", environ=baseline)
    macos_dev = install.locked_install_environment(["dev"], system="Darwin", environ=baseline)

    assert macos == {
        "EXISTING": "preserved",
        "OPENSSL_STATIC": "1",
        "PIP_NO_CACHE_DIR": "1",
    }
    assert windows == baseline
    assert macos_dev == baseline


def test_static_macos_cryptography_linkage_verification_rejects_dynamic_openssl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = _load_script("install_locked_python.py")

    class Spec:
        origin = "/tmp/cryptography/hazmat/bindings/_rust.abi3.so"

    monkeypatch.setattr(install.importlib.util, "find_spec", lambda _name: Spec())
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/usr/bin/otool")
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            "\n".join(
                [
                    f"{Spec.origin}:",
                    "\t/usr/local/opt/openssl@3/lib/libssl.3.dylib "
                    "(compatibility version 3.0.0, current version 3.6.0)",
                    "\t/usr/local/opt/openssl@3/lib/libcrypto.3.dylib "
                    "(compatibility version 3.0.0, current version 3.6.0)",
                    "\t/usr/lib/libSystem.B.dylib "
                    "(compatibility version 1.0.0, current version 1351.0.0)",
                ]
            ),
            "",
        ),
    )

    with pytest.raises(RuntimeError, match=r"libcrypto\.3\.dylib, libssl\.3\.dylib"):
        install.verify_static_macos_cryptography(["packaging"], system="Darwin")


def test_static_macos_cryptography_linkage_verification_accepts_static_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = _load_script("install_locked_python.py")

    class Spec:
        origin = "/tmp/cryptography/hazmat/bindings/_rust.abi3.so"

    monkeypatch.setattr(install.importlib.util, "find_spec", lambda _name: Spec())
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/usr/bin/otool")
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            "\n".join(
                [
                    f"{Spec.origin}:",
                    "\t/usr/lib/libSystem.B.dylib "
                    "(compatibility version 1.0.0, current version 1351.0.0)",
                ]
            ),
            "",
        ),
    )

    install.verify_static_macos_cryptography(["packaging"], system="Darwin")


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

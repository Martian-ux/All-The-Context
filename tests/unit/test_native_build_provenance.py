from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import pytest

from scripts import build_desktop
from scripts import native_build_provenance as provenance
from scripts import verify_reproducible_build as verifier
from scripts.verify_reproducible_build import (
    ReproducibleBuildError,
    _fresh_directory,
    generate_provenance,
)

SOURCE_COMMIT = "a" * 40
VERSION = "0.1.0-beta.7"
TOOLCHAIN = {
    "python": provenance.PINNED_PYTHON_VERSION,
    "pyinstaller": provenance.PINNED_PYINSTALLER_VERSION,
    "uv": provenance.PINNED_UV_VERSION,
}
LOCKS = {
    name: {"sha256": f"{index:064x}"}
    for index, name in enumerate(provenance.LOCK_FILE_NAMES, start=1)
}


def _build_tree(root: Path, *, mutation: str | None = None) -> tuple[Path, dict[str, Path]]:
    build_root = root / "build"
    dist_root = root / "dist"
    paths = {
        "main": dist_root / "AllTheContextSetup.exe",
        "mcp": build_root / "helper-dist" / "AllTheContextMCP.exe",
        "recovery": dist_root / "AllTheContextRecovery.exe",
        "updater": build_root / "update-helper-dist" / "AllTheContextUpdater.exe",
    }
    for role, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        value = f"{role}-bytes".encode("ascii")
        if role == mutation:
            value += b"-different"
        path.write_bytes(value)
    return build_root, paths


def _payload(
    first: provenance.BuildSnapshot, second: provenance.BuildSnapshot
) -> dict[str, object]:
    return provenance.build_payload(
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        toolchain=TOOLCHAIN,
        locks=LOCKS,
        first=first,
        second=second,
    )


def test_clean_double_build_receipt_is_canonical_and_path_free(tmp_path: Path) -> None:
    _first_build, first_paths = _build_tree(tmp_path / "first")
    _second_build, second_paths = _build_tree(tmp_path / "second")
    first = provenance.collect_snapshot(first_paths, label="clean-build-1")
    second = provenance.collect_snapshot(second_paths, label="clean-build-2")
    payload = _payload(first, second)
    output = tmp_path / "metadata" / provenance.PROVENANCE_FILE_NAME

    manifest, checksum = provenance.write_provenance(output, payload)
    loaded = provenance.load_provenance(manifest)
    assert loaded == payload
    assert checksum.read_text(encoding="ascii").endswith(f"  {provenance.PROVENANCE_FILE_NAME}\n")
    assert str(tmp_path) not in manifest.read_text(encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["components"][0] == {
        "build_filename": "AllTheContextSetup.exe",
        "filename": "AllTheContext.exe",
        "role": "main",
        "sha256": first.components[0].sha256,
        "size": first.components[0].size,
    }
    assert (
        provenance.verify_provenance(
            manifest,
            second_paths,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )
        == payload
    )


def test_nondeterministic_component_fails_before_output_staging(tmp_path: Path) -> None:
    _first_build, first_paths = _build_tree(tmp_path / "first")
    _second_build, second_paths = _build_tree(tmp_path / "second", mutation="mcp")
    first = provenance.collect_snapshot(first_paths, label="clean-build-1")
    second = provenance.collect_snapshot(second_paths, label="clean-build-2")

    with pytest.raises(provenance.NativeBuildProvenanceError, match="byte-identical"):
        _payload(first, second)


def test_missing_component_and_duplicate_input_fail_closed(tmp_path: Path) -> None:
    _build_root, paths = _build_tree(tmp_path)
    missing = dict(paths)
    del missing["updater"]
    with pytest.raises(provenance.NativeBuildProvenanceError, match="exactly four"):
        provenance.collect_snapshot(missing)

    duplicate = dict(paths)
    duplicate["updater"] = duplicate["mcp"]
    with pytest.raises(provenance.NativeBuildProvenanceError, match="filename"):
        provenance.collect_snapshot(duplicate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["contract"].pop("toolchain"),
        lambda payload: payload["contract"]["toolchain"].update(uv="latest"),
        lambda payload: payload["contract"]["locks"].pop("uv.lock"),
        lambda payload: payload["builds"].reverse(),
        lambda payload: payload["components"].reverse(),
        lambda payload: payload["components"][0].update(build_filename="C:\\leak.exe"),
        lambda payload: payload["builds"][0]["components"][0].update(size=True),
        lambda payload: payload.update(schema_version=1.0),
    ],
)
def test_unbound_metadata_order_and_path_mutations_are_rejected(mutate) -> None:
    first_root = Path("first")
    second_root = Path("second")
    # These are only used to create stable snapshots; no filesystem path is
    # serialized into the receipt.
    first_paths = {
        role: first_root / build_name for role, _installed_name, build_name in provenance.COMPONENTS
    }
    second_paths = {
        role: second_root / build_name
        for role, _installed_name, build_name in provenance.COMPONENTS
    }
    # Construct descriptors directly so this adversarial shape test remains
    # independent of a particular temporary filesystem layout.
    components = tuple(
        provenance.ComponentDigest(role, installed, build_name, f"{index:064x}", index)
        for index, (role, installed, build_name) in enumerate(provenance.COMPONENTS, start=1)
    )
    payload = _payload(
        provenance.BuildSnapshot("clean-build-1", components),
        provenance.BuildSnapshot("clean-build-2", components),
    )
    mutate(payload)
    with pytest.raises(provenance.NativeBuildProvenanceError):
        provenance.validate_payload(payload)
    assert first_paths["main"].name == second_paths["main"].name


def test_generate_provenance_stages_only_the_second_matching_build(tmp_path: Path) -> None:
    calls: list[tuple[Path, Path]] = []

    def fake_build(
        _repository_root: Path, build_root: Path, dist_root: Path
    ) -> provenance.BuildSnapshot:
        index = len(calls) + 1
        calls.append((build_root, dist_root))
        _unused_root, paths = _build_tree(tmp_path / f"build-{index}")
        # Copy the fixture bytes into the requested clean roots, matching the
        # real build runner's output layout.
        for role, path in paths.items():
            requested = {
                "main": dist_root / "AllTheContextSetup.exe",
                "mcp": build_root / "helper-dist" / "AllTheContextMCP.exe",
                "recovery": dist_root / "AllTheContextRecovery.exe",
                "updater": build_root / "update-helper-dist" / "AllTheContextUpdater.exe",
            }[role]
            requested.parent.mkdir(parents=True, exist_ok=True)
            requested.write_bytes(path.read_bytes())
        return provenance.collect_snapshot(
            {
                "main": dist_root / "AllTheContextSetup.exe",
                "mcp": build_root / "helper-dist" / "AllTheContextMCP.exe",
                "recovery": dist_root / "AllTheContextRecovery.exe",
                "updater": build_root / "update-helper-dist" / "AllTheContextUpdater.exe",
            }
        )

    payload, final_paths = generate_provenance(
        repository_root=tmp_path,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        final_build_root=tmp_path / "final-build",
        final_dist_root=tmp_path / "final-dist",
        output_dir=tmp_path / "output",
        toolchain=TOOLCHAIN,
        locks=LOCKS,
        build_runner=fake_build,
    )

    assert len(calls) == 2
    assert all(path.is_file() for path in final_paths.values())
    manifest = tmp_path / "output" / provenance.PROVENANCE_FILE_NAME
    assert provenance.verify_provenance(manifest, final_paths) == payload
    assert str(tmp_path) not in manifest.read_text(encoding="utf-8")


def test_final_or_output_directories_must_be_clean(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "old.bin").write_bytes(b"old")
    with pytest.raises(ReproducibleBuildError, match="absent or empty"):
        _fresh_directory(occupied, label="output")


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("failure", [None, "clean-build-1", "clean-build-2", "interrupt"])
def test_clean_arm_schedule_and_failure_draining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool, failure: str | None
) -> None:
    barrier = threading.Barrier(2, timeout=5)
    finished: set[str] = set()
    roots: list[tuple[Path, Path]] = []
    staged: list[str] = []
    original_stage = verifier._stage_verified_components

    def stage(snapshot, **kwargs):
        assert finished == {"clean-build-1", "clean-build-2"}
        staged.append(snapshot.label)
        return original_stage(snapshot, **kwargs)

    def runner(_source: Path, build: Path, dist: Path) -> provenance.BuildSnapshot:
        arm = build.parent.name
        assert build.parent == dist.parent
        assert not build.exists() and not dist.exists()
        roots.append((build, dist))
        if parallel:
            barrier.wait()
        elif arm == "clean-build-2":
            assert finished == {"clean-build-1"}
        try:
            if failure == arm:
                raise ReproducibleBuildError("injected child failure")
            if failure == "interrupt" and arm == "clean-build-2":
                raise KeyboardInterrupt("injected interruption")
            _, paths = _build_tree(build.parent)
            return provenance.collect_snapshot(paths)
        finally:
            finished.add(arm)

    monkeypatch.setattr(verifier, "_stage_verified_components", stage)
    arguments = dict(
        repository_root=tmp_path,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        final_build_root=tmp_path / "final-build",
        final_dist_root=tmp_path / "final-dist",
        output_dir=tmp_path / "output",
        toolchain=TOOLCHAIN,
        locks=LOCKS,
        build_runner=runner,
        parallel_windows=parallel,
    )
    if failure:
        exception = KeyboardInterrupt if failure == "interrupt" else ReproducibleBuildError
        with pytest.raises(exception, match="injected"):
            generate_provenance(**arguments)
        assert not staged
        assert not (tmp_path / "output").exists()
        assert not (tmp_path / "final-build").exists()
        if parallel:
            assert finished == {"clean-build-1", "clean-build-2"}
    else:
        _, paths = generate_provenance(**arguments)
        assert staged == ["clean-build-2"]
        assert len({path for pair in roots for path in pair}) == 4
        assert all(path.is_file() for path in paths.values())


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("failure", [None, "child", "missing", "interrupt", "setup"])
def test_windows_helper_schedule_isolation_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool, failure: str | None
) -> None:
    package = ModuleType("PyInstaller")
    main = ModuleType("PyInstaller.__main__")
    package.__main__ = main
    monkeypatch.setitem(sys.modules, "PyInstaller", package)
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", main)
    helper_names = {"AllTheContextMCP", "AllTheContextRecovery", "AllTheContextUpdater"}
    barrier = threading.Barrier(3, timeout=5)
    finished: set[str] = set()
    requests: dict[str, tuple[list[str], dict[str, str] | None]] = {}
    environment_before = dict(os.environ)

    def emit(arguments: list[str], environment: dict[str, str] | None = None) -> None:
        name = arguments[arguments.index("--name") + 1]
        requests[name] = (arguments, environment)
        if name in helper_names and parallel:
            barrier.wait()
        if name == "AllTheContextSetup":
            assert finished == helper_names
            for option, value in pairwise(arguments):
                if option == "--add-binary":
                    embedded = Path(value.rsplit(os.pathsep, 1)[0])
                    assert embedded.read_bytes() == embedded.stem.encode("ascii")
            if failure == "setup":
                raise subprocess.CalledProcessError(1, arguments)
        try:
            if name == "AllTheContextMCP":
                if failure == "child":
                    raise subprocess.CalledProcessError(1, arguments)
                if failure == "interrupt":
                    raise KeyboardInterrupt("injected interruption")
                if failure == "missing":
                    return
            destination = Path(arguments[arguments.index("--distpath") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"{name}.exe").write_bytes(name.encode("ascii"))
        finally:
            if name in helper_names:
                finished.add(name)

    def run(command, *, cwd, env, check):
        assert parallel and check
        assert command[:3] == [sys.executable, "-m", "PyInstaller"]
        assert cwd == build_desktop.ROOT
        emit(command[3:], env)

    main.run = emit
    monkeypatch.setattr(build_desktop.subprocess, "run", run)
    arguments = dict(
        system="Windows",
        build_root=tmp_path / "build",
        dist_root=tmp_path / "dist",
        source_commit=SOURCE_COMMIT,
        architecture="x86_64",
        parallel_windows=parallel,
    )
    if failure:
        exception = (
            KeyboardInterrupt
            if failure == "interrupt"
            else RuntimeError
            if failure == "missing"
            else subprocess.CalledProcessError
        )
        with pytest.raises(exception):
            build_desktop.build(**arguments)
        assert ("AllTheContextSetup" in requests) == (failure == "setup")
        if parallel:
            assert finished == helper_names
    else:
        artifact = build_desktop.build(**arguments)
        assert artifact.read_bytes() == b"AllTheContextSetup"
        paths = build_desktop.component_paths(
            build_root=tmp_path / "build", dist_root=tmp_path / "dist"
        )
        assert all(path.is_file() for path in paths.values())
        assert paths["recovery"].read_bytes() == b"AllTheContextRecovery"
        updater_arguments = requests["AllTheContextUpdater"][0]
        assert "--onefile" in updater_arguments and "--windowed" in updater_arguments
        assert updater_arguments[-1].endswith("update_helper_entry.py")
    assert dict(os.environ) == environment_before
    if parallel:
        configs = [env["PYINSTALLER_CONFIG_DIR"] for _, env in requests.values()]
        assert len(set(configs)) == len(requests)
        for flag in ("--specpath", "--workpath", "--distpath"):
            values = [args[args.index(flag) + 1] for args, _ in requests.values()]
            assert len(set(values)) == len(values)
        for _, env in requests.values():
            for key in ("SOURCE_DATE_EPOCH", "PYTHONHASHSEED"):
                assert env.get(key) == environment_before.get(key)
    else:
        assert all(env is None for _, env in requests.values())


@pytest.mark.parametrize("parallel", [False, True])
def test_clean_build_forwards_opt_in_and_deterministic_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    def run(command, *, cwd, env, **kwargs):
        assert ("--parallel-windows" in command) == parallel
        assert env["SOURCE_DATE_EPOCH"] == "0"
        assert env["PYTHONHASHSEED"] == "0"
        build = Path(command[command.index("--build-root") + 1])
        assert env["PYINSTALLER_CONFIG_DIR"] == str(build / "pyinstaller-config")
        _build_tree(build.parent)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verifier.subprocess, "run", run)
    snapshot = verifier._run_clean_build(
        tmp_path,
        tmp_path / "build",
        tmp_path / "dist",
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        parallel_windows=parallel,
    )
    assert len(snapshot.components) == 4


@pytest.mark.parametrize("parallel", [False, True])
def test_verifier_cli_exposes_schedule_without_bypassing_source_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parallel: bool
) -> None:
    monkeypatch.setattr(verifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verifier, "project_version", lambda root: VERSION)
    monkeypatch.setattr(verifier, "checked_out_commit", lambda root: SOURCE_COMMIT)
    calls: list[bool] = []

    def generate(**kwargs):
        calls.append(kwargs["parallel_windows"])
        return {"components": [1, 2, 3, 4]}, {}

    monkeypatch.setattr(verifier, "generate_provenance", generate)
    command = [
        "verify_reproducible_build.py",
        "--repository-root",
        str(tmp_path),
        "--source-commit",
        SOURCE_COMMIT,
    ]
    if parallel:
        command.append("--parallel-windows")
    monkeypatch.setattr(sys, "argv", command)
    assert verifier.main() == 0
    assert calls == [parallel]
    monkeypatch.setattr(verifier, "checked_out_commit", lambda root: "b" * 40)
    assert verifier.main() == 1
    assert calls == [parallel]


def test_parallel_windows_flag_keeps_linux_build_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = ModuleType("PyInstaller")
    main = ModuleType("PyInstaller.__main__")
    package.__main__ = main
    monkeypatch.setitem(sys.modules, "PyInstaller", package)
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", main)
    calls: list[list[str]] = []

    def emit(arguments: list[str]) -> None:
        calls.append(arguments)
        assert arguments[arguments.index("--name") + 1] == "all-the-context"
        (tmp_path / "dist" / "all-the-context").write_bytes(b"linux")

    main.run = emit
    artifact = build_desktop.build(
        system="Linux",
        build_root=tmp_path / "build",
        dist_root=tmp_path / "dist",
        source_commit=SOURCE_COMMIT,
        architecture="x86_64",
        parallel_windows=True,
    )
    assert artifact.read_bytes() == b"linux"
    assert len(calls) == 1

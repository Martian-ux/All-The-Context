from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import native_build_provenance as provenance
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

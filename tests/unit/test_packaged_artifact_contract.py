from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts import native_build_provenance as provenance
from scripts import packaged_artifact_contract as contract
from scripts import smoke_desktop_artifact as desktop_smoke

VERSION = "0.1.0-beta.7"
SOURCE_COMMIT = "a" * 40
TOOLCHAIN = {
    "python": provenance.PINNED_PYTHON_VERSION,
    "pyinstaller": provenance.PINNED_PYINSTALLER_VERSION,
    "uv": provenance.PINNED_UV_VERSION,
}
LOCKS = {
    name: {"sha256": f"{index:064x}"}
    for index, name in enumerate(provenance.LOCK_FILE_NAMES, start=1)
}


def _artifact_tree(
    tmp_path: Path, *, source_commit: str = SOURCE_COMMIT
) -> tuple[Path, Path, Path]:
    root = tmp_path / "artifact-root"
    paths = contract.native_component_paths(root)
    contents = {
        "main": b"main executable bytes",
        "mcp": b"mcp executable bytes",
        "recovery": b"recovery executable bytes",
        "updater": b"updater executable bytes",
    }
    components: list[provenance.ComponentDigest] = []
    for role, filename, build_filename in provenance.COMPONENTS:
        path = paths[role]
        path.parent.mkdir(parents=True, exist_ok=True)
        content = contents[role]
        path.write_bytes(content)
        components.append(
            provenance.ComponentDigest(
                role=role,
                filename=filename,
                build_filename=build_filename,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            )
        )
    snapshot = provenance.BuildSnapshot("clean-build-1", tuple(components))
    second = provenance.BuildSnapshot("clean-build-2", tuple(components))
    payload = provenance.build_payload(
        version=VERSION,
        source_commit=source_commit,
        toolchain=TOOLCHAIN,
        locks=LOCKS,
        first=snapshot,
        second=second,
    )
    manifest = root / "native-build-provenance" / provenance.PROVENANCE_FILE_NAME
    manifest.parent.mkdir(parents=True)
    _, checksum = provenance.write_provenance(manifest, payload)
    return root, manifest, checksum


def _validate(
    root: Path,
    manifest: Path,
    checksum: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(contract, "checked_out_source_commit", lambda _root: SOURCE_COMMIT)
    return contract.validate_windows_artifact_contract(
        artifact_root=root,
        provenance_manifest=manifest,
        provenance_checksum=checksum,
        source_root=Path("source-checkout"),
        version=VERSION,
    )


def test_explicit_root_manifest_and_checksum_bind_exact_component_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest, checksum = _artifact_tree(tmp_path)

    payload = _validate(root, manifest, checksum, monkeypatch)

    assert payload["source_commit"] == SOURCE_COMMIT


def test_darwin_paths_match_the_produced_bundle_structure(tmp_path: Path) -> None:
    root = tmp_path / "artifact-root"
    desktop = root / "dist" / "desktop"
    app = desktop / "AllTheContext.app"
    recovery, mode = contract.recovery_executable(root, "Darwin")

    assert contract.artifact_executable(root, "Darwin") == (
        app / "Contents" / "MacOS" / "AllTheContext"
    )
    assert recovery == app / "Contents" / "Frameworks" / "all-the-context-recovery"
    assert mode == "frozen-console-recovery-helper"


def test_darwin_wrong_or_missing_helper_is_rejected_without_fallback(tmp_path: Path) -> None:
    import scripts.smoke_packaged_recovery as smoke

    root = tmp_path / "artifact-root"
    app = root / "dist" / "desktop" / "AllTheContext.app"
    wrong_location = app / "Contents" / "MacOS" / "all-the-context-recovery"
    wrong_location.parent.mkdir(parents=True)
    wrong_location.write_bytes(b"stale helper")

    with pytest.raises(SystemExit, match="recovery"):
        smoke.recovery_command("Darwin", root)

    wrong_location.unlink()
    with pytest.raises(SystemExit, match="recovery"):
        smoke.recovery_command("Darwin", root)


def test_windows_contract_rejects_missing_provenance_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _manifest, _checksum = _artifact_tree(tmp_path)

    with pytest.raises(contract.PackagedArtifactContractError, match="Windows"):
        contract.validate_windows_artifact_contract(
            artifact_root=root,
            provenance_manifest=None,
            provenance_checksum=None,
            source_root=Path("source-checkout"),
            version=VERSION,
        )


def test_contract_rejects_wrong_root_before_any_component_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, checksum = _artifact_tree(tmp_path)
    wrong_root = tmp_path / "wrong-root"
    wrong_root.mkdir()

    with pytest.raises(contract.PackagedArtifactContractError, match="provenance paths"):
        _validate(wrong_root, manifest, checksum, monkeypatch)


def test_contract_rejects_missing_root_before_any_component_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, checksum = _artifact_tree(tmp_path)

    with pytest.raises(
        contract.PackagedArtifactContractError, match="artifact root is unavailable"
    ):
        _validate(tmp_path / "missing-root", manifest, checksum, monkeypatch)


def test_contract_rejects_manifest_bound_to_wrong_source_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest, checksum = _artifact_tree(tmp_path, source_commit="b" * 40)

    with pytest.raises(contract.PackagedArtifactContractError, match="source commit"):
        _validate(root, manifest, checksum, monkeypatch)


def test_desktop_smoke_rejects_wrong_source_provenance_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest, checksum = _artifact_tree(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "smoke_desktop_artifact.py",
            "--artifact-root",
            str(root),
            "--provenance-manifest",
            str(manifest),
            "--provenance-checksum",
            str(checksum),
        ],
    )
    monkeypatch.setattr(desktop_smoke.platform, "system", lambda: "Windows")
    real_run = desktop_smoke.subprocess.run
    executable_launches: list[list[str]] = []

    def observe_run(command: list[str], *args: object, **kwargs: object) -> object:
        if command[0] == "git":
            return real_run(command, *args, **kwargs)  # type: ignore[arg-type]
        executable_launches.append(command)
        raise AssertionError(f"unexpected executable launch: {command}")

    monkeypatch.setattr(desktop_smoke.subprocess, "run", observe_run)

    with pytest.raises(SystemExit, match="source commit"):
        desktop_smoke.main()

    assert executable_launches == []


def test_contract_rejects_component_hash_or_size_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest, checksum = _artifact_tree(tmp_path)
    contract.native_component_paths(root)["main"].write_bytes(b"tampered executable bytes")

    with pytest.raises(contract.PackagedArtifactContractError, match="executable bytes"):
        _validate(root, manifest, checksum, monkeypatch)


def test_contract_rejects_missing_component_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest, checksum = _artifact_tree(tmp_path)
    contract.native_component_paths(root)["recovery"].unlink()

    with pytest.raises(contract.PackagedArtifactContractError):
        _validate(root, manifest, checksum, monkeypatch)

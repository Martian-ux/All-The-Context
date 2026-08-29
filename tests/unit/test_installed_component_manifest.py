from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.build_release_assets import build_archive
from scripts.installed_component_manifest import (
    CHECKSUM_FILE_NAME,
    MANIFEST_FILE_NAME,
    InstalledComponentManifestError,
    build_manifest,
    canonical_json,
    create_manifest,
    verify_archive,
    verify_manifest,
)

SOURCE_COMMIT = "a" * 40
VERSION = "0.1.0-beta.7"


def _pe_image(*, certificate_offset: int = 0, certificate_size: int = 0) -> bytes:
    pe_offset = 128
    optional_size = 240
    header_size = pe_offset + 24 + optional_size
    image = bytearray(max(header_size, certificate_offset + certificate_size))
    image[:2] = b"MZ"
    image[60:64] = pe_offset.to_bytes(4, "little")
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    image[pe_offset + 20 : pe_offset + 22] = optional_size.to_bytes(2, "little")
    optional_offset = pe_offset + 24
    image[optional_offset : optional_offset + 2] = (0x20B).to_bytes(2, "little")
    image[optional_offset + 108 : optional_offset + 112] = (16).to_bytes(4, "little")
    certificate_entry = optional_offset + 112 + (4 * 8)
    image[certificate_entry : certificate_entry + 4] = certificate_offset.to_bytes(4, "little")
    image[certificate_entry + 4 : certificate_entry + 8] = certificate_size.to_bytes(4, "little")
    return bytes(image)


def _inputs(tmp_path: Path) -> dict[str, Path]:
    build = tmp_path / "build"
    build.mkdir(parents=True)
    data = {
        "main": _pe_image(),
        "mcp": _pe_image(certificate_offset=392, certificate_size=16),
        "recovery": _pe_image(),
        "updater": _pe_image(),
    }
    names = {
        "main": "AllTheContextSetup.exe",
        "mcp": "AllTheContextMCP.exe",
        "recovery": "AllTheContextRecovery.exe",
        "updater": "AllTheContextUpdater.exe",
    }
    paths: dict[str, Path] = {}
    for role, contents in data.items():
        path = build / names[role]
        path.write_bytes(contents)
        paths[role] = path
    return paths


def _stage(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    components = _inputs(tmp_path)
    direct_dir = tmp_path / "release"
    direct_dir.mkdir()
    direct = direct_dir / "all-the-context-0.1.0-beta.7-windows-x86_64-unsigned.exe"
    direct.write_bytes(components["main"].read_bytes())
    payload = tmp_path / "payload"
    payload.mkdir()
    package = payload / "AllTheContextSetup.exe"
    package.write_bytes(direct.read_bytes())
    return package, direct, components


def test_installed_component_manifest_is_canonical_and_verifiable(tmp_path: Path) -> None:
    package, direct, components = _stage(tmp_path)

    manifest_path, checksum_path = create_manifest(
        output_dir=package.parent,
        package_path=package,
        direct_package_path=direct,
        component_paths=components,
        source_root=tmp_path,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
    )

    raw = manifest_path.read_bytes()
    payload = json.loads(raw)
    assert raw == canonical_json(payload)
    assert checksum_path.name == CHECKSUM_FILE_NAME
    assert payload["component_count"] == 4
    assert [item["filename"] for item in payload["components"]] == [
        "AllTheContext.exe",
        "AllTheContextMCP.exe",
        "AllTheContextRecovery.exe",
        "AllTheContextUpdater.exe",
    ]
    assert payload["components"][0]["authenticode"]["status"] == "not-present"
    assert payload["components"][1]["authenticode"]["status"] == "present-unverified"
    assert (
        verify_manifest(
            manifest_path=manifest_path,
            package_path=package,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
        )
        == payload
    )


def test_manifest_is_bound_to_deterministic_archive_member(tmp_path: Path) -> None:
    package, direct, components = _stage(tmp_path)
    create_manifest(
        output_dir=package.parent,
        package_path=package,
        direct_package_path=direct,
        component_paths=components,
        source_root=tmp_path,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
    )
    archive = build_archive(
        package.parent,
        tmp_path / "release-assets",
        version=VERSION,
        platform_name="windows",
        architecture="x86_64",
    )

    payload = verify_archive(
        archive_path=archive,
        direct_package_path=direct,
        component_paths=components,
        source_root=tmp_path,
    )
    assert payload["package"]["filename"] == "AllTheContextSetup.exe"
    assert (
        archive.read_bytes()
        == build_archive(
            package.parent,
            tmp_path / "release-assets-2",
            version=VERSION,
            platform_name="windows",
            architecture="x86_64",
        ).read_bytes()
    )


def test_manifest_rejects_main_component_package_mismatch(tmp_path: Path) -> None:
    package, direct, components = _stage(tmp_path)
    replacement = _pe_image(certificate_offset=400, certificate_size=8)
    package.write_bytes(replacement)
    direct.write_bytes(replacement)

    with pytest.raises(InstalledComponentManifestError, match="main executable"):
        build_manifest(
            package_path=package,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )


def test_windows_package_build_without_provenance_args_still_works(tmp_path: Path) -> None:
    from scripts.package_desktop import build_platform_package

    source = tmp_path / "AllTheContextSetup.exe"
    source.write_bytes(_pe_image())
    outputs = build_platform_package(
        source,
        tmp_path / "release",
        version=VERSION,
        platform_name="windows",
        architecture="x86_64",
    )

    assert outputs[0].is_file()
    assert not (tmp_path / "installed-component-package").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "0.1.0-beta.8"),
        ("source_commit", "b" * 40),
        ("platform", "linux"),
        ("architecture", "arm64"),
    ],
)
@pytest.mark.parametrize("archive", [False, True])
def test_verifiers_reject_mismatched_expected_header(
    tmp_path: Path, field: str, value: str, archive: bool
) -> None:
    package, direct, components = _stage(tmp_path)
    create_manifest(
        output_dir=package.parent,
        package_path=package,
        direct_package_path=direct,
        component_paths=components,
        source_root=tmp_path,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
    )
    archive_path = build_archive(
        package.parent,
        tmp_path / "release-assets",
        version=VERSION,
        platform_name="windows",
        architecture="x86_64",
    )
    expected = {
        "version": VERSION,
        "source_commit": SOURCE_COMMIT,
        "platform": "windows",
        "architecture": "x86_64",
    }
    expected[field] = value

    with pytest.raises(InstalledComponentManifestError, match="header"):
        if archive:
            verify_archive(
                archive_path=archive_path,
                direct_package_path=direct,
                component_paths=components,
                source_root=tmp_path,
                **expected,
            )
        else:
            verify_manifest(
                manifest_path=package.parent / MANIFEST_FILE_NAME,
                package_path=package,
                direct_package_path=direct,
                component_paths=components,
                source_root=tmp_path,
                **expected,
            )


@pytest.mark.parametrize(
    ("role", "replacement", "message"),
    [
        ("mcp", "missing.exe", "missing"),
        ("mcp", "Unexpected.exe", "unexpected source filename"),
    ],
)
def test_manifest_rejects_missing_or_unexpected_executable(
    tmp_path: Path,
    role: str,
    replacement: str,
    message: str,
) -> None:
    package, direct, components = _stage(tmp_path)
    replacement_path = components[role].with_name(replacement)
    if message == "missing":
        components[role] = replacement_path
    else:
        components[role].rename(replacement_path)
        components[role] = replacement_path

    with pytest.raises(InstalledComponentManifestError, match=message):
        build_manifest(
            package_path=package,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )


def test_manifest_rejects_source_escape_and_duplicate_hardlink(tmp_path: Path) -> None:
    package, direct, components = _stage(tmp_path)
    outside = tmp_path.parent / "outside-AllTheContextMCP.exe"
    outside.write_bytes(_pe_image())
    components["mcp"] = outside
    with pytest.raises(InstalledComponentManifestError, match="escapes"):
        build_manifest(
            package_path=package,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )

    components = _inputs(tmp_path / "duplicate-components")
    package, direct, _unused = _stage(tmp_path / "duplicate-stage")
    try:
        components["recovery"].unlink()
        components["recovery"].hardlink_to(components["mcp"])
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation is unavailable on this host")
    with pytest.raises(InstalledComponentManifestError, match="duplicate executable input"):
        build_manifest(
            package_path=package,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )


def test_manifest_rejects_mutation_between_hash_passes(tmp_path: Path, monkeypatch) -> None:
    package, direct, components = _stage(tmp_path)
    import scripts.installed_component_manifest as module

    original = module._hash_file
    mutated = False

    def mutate_once(path: Path, *, label: str):
        nonlocal mutated
        measurement = original(path, label=label)
        if label == "mcp executable" and not mutated:
            path.write_bytes(path.read_bytes() + b"changed")
            mutated = True
        return measurement

    monkeypatch.setattr(module, "_hash_file", mutate_once)
    with pytest.raises(InstalledComponentManifestError, match="changed"):
        build_manifest(
            package_path=package,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )


def test_archive_verifier_rejects_escape_and_symlink_entries(tmp_path: Path) -> None:
    package, direct, components = _stage(tmp_path)
    create_manifest(
        output_dir=package.parent,
        package_path=package,
        direct_package_path=direct,
        component_paths=components,
        source_root=tmp_path,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
    )
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../AllTheContextSetup.exe", package.read_bytes())
        bundle.writestr(
            MANIFEST_FILE_NAME, package.parent.joinpath(MANIFEST_FILE_NAME).read_bytes()
        )
        bundle.writestr(
            CHECKSUM_FILE_NAME, package.parent.joinpath(CHECKSUM_FILE_NAME).read_bytes()
        )
    with pytest.raises(InstalledComponentManifestError, match="escaping"):
        verify_archive(
            archive_path=archive,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
        )

    with zipfile.ZipFile(archive, "w") as bundle:
        link = zipfile.ZipInfo("AllTheContextSetup.exe")
        link.external_attr = 0o120777 << 16
        bundle.writestr(link, "target")
        bundle.writestr(
            MANIFEST_FILE_NAME, package.parent.joinpath(MANIFEST_FILE_NAME).read_bytes()
        )
        bundle.writestr(
            CHECKSUM_FILE_NAME, package.parent.joinpath(CHECKSUM_FILE_NAME).read_bytes()
        )
    with pytest.raises(InstalledComponentManifestError, match="symlink"):
        verify_archive(
            archive_path=archive,
            direct_package_path=direct,
            component_paths=components,
            source_root=tmp_path,
        )

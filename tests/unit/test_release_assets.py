from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest
from allthecontext.release_manifest import sha256_file

from scripts.build_release_assets import build_archive, write_metadata, write_subject_sbom


def test_release_archive_and_metadata_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "AllTheContext.app"
    (source / "Contents" / "MacOS").mkdir(parents=True)
    (source / "Contents" / "MacOS" / "AllTheContext").write_bytes(b"binary")
    first = build_archive(
        source,
        tmp_path / "first",
        version="0.2.0-beta.1",
        platform_name="macos",
        architecture="arm64",
    )
    second = build_archive(
        source,
        tmp_path / "second",
        version="0.2.0-beta.1",
        platform_name="macos",
        architecture="arm64",
    )
    assert sha256_file(first) == sha256_file(second)
    checksum, sbom = write_metadata(first, version="0.2.0-beta.1")
    assert sha256_file(first)[0] in checksum.read_text(encoding="utf-8")
    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert document["spdxVersion"] == "SPDX-2.3"
    assert document["name"] == f"{first.name}-sbom"
    assert document["packages"][0]["filesAnalyzed"] is True
    assert document["files"][0]["fileName"].endswith("AllTheContext")


def test_direct_package_gets_an_explicit_unanalyzed_spdx_subject(tmp_path: Path) -> None:
    package = tmp_path / "all-the-context-0.1.0-beta.1-windows-x86_64-unsigned.exe"
    package.write_bytes(b"native package")

    sbom = write_subject_sbom(package, version="0.1.0-beta.1")

    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert document["name"] == f"{package.name}-sbom"
    assert document["packages"][0]["packageFileName"] == package.name
    assert document["packages"][0]["filesAnalyzed"] is False
    assert document["files"] == []


def test_macos_ota_archive_preserves_safe_internal_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "AllTheContext.app"
    target = source / "Contents/Versions/A/AllTheContext"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"binary")
    link = source / "Contents/AllTheContext"
    try:
        link.symlink_to(Path("Versions/A/AllTheContext"))
    except OSError:
        pytest.skip("symlink creation is not available on this Windows host")

    archive = build_archive(
        source,
        tmp_path / "release",
        version="0.1.0-beta.1",
        platform_name="macos",
        architecture="arm64",
    )

    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo("AllTheContext.app/Contents/AllTheContext")
        assert stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
        assert bundle.read(info) == b"Versions/A/AllTheContext"


def test_release_archive_rejects_symlink_that_escapes_source(tmp_path: Path) -> None:
    source = tmp_path / "AllTheContext.app"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    link = source / "outside"
    try:
        link.symlink_to(Path("../outside"))
    except OSError:
        pytest.skip("symlink creation is not available on this Windows host")

    with pytest.raises(ValueError, match="escapes"):
        build_archive(
            source,
            tmp_path / "release",
            version="0.1.0-beta.1",
            platform_name="macos",
            architecture="arm64",
        )

from __future__ import annotations

import json
from pathlib import Path

from allthecontext.release_manifest import sha256_file

from scripts.build_release_assets import build_archive, write_metadata


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
    assert json.loads(sbom.read_text(encoding="utf-8"))["spdxVersion"] == "SPDX-2.3"

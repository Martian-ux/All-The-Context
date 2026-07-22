"""Create deterministic, immutable native release archives and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import BinaryIO

from allthecontext.release_manifest import ReleaseVersion, sha256_file

ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _copy(stream: BinaryIO, source: Path) -> None:
    with source.open("rb") as input_stream:
        for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
            stream.write(chunk)


def build_archive(
    source: Path,
    output_dir: Path,
    *,
    version: str,
    platform_name: str,
    architecture: str,
) -> Path:
    ReleaseVersion.parse(version)
    if platform_name not in {"windows", "macos", "linux"}:
        raise ValueError(f"unsupported platform: {platform_name}")
    if architecture not in {"x86_64", "arm64"}:
        raise ValueError(f"unsupported architecture: {architecture}")
    if not source.exists():
        raise FileNotFoundError(source)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"all-the-context-{version}-{platform_name}-{architecture}.zip"
    root_name = source.name
    files = (
        [source]
        if source.is_file()
        else sorted(
            (path for path in source.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(source).as_posix().casefold(),
        )
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files:
            relative = (
                Path(root_name) if source.is_file() else Path(root_name) / path.relative_to(source)
            )
            info = zipfile.ZipInfo(relative.as_posix(), ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100755 << 16
            with bundle.open(info, "w") as stream:
                _copy(stream, path)
    return archive


def write_metadata(archive: Path, *, version: str) -> tuple[Path, Path]:
    digest, size = sha256_file(archive)
    checksum = archive.parent / f"{archive.name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
    sbom = archive.parent / f"{archive.name}.spdx.json"
    namespace_hash = hashlib.sha256(f"{archive.name}:{digest}".encode()).hexdigest()
    payload = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{archive.name}-sbom",
        "documentNamespace": f"https://github.com/all-the-context/releases/{namespace_hash}",
        "creationInfo": {
            "created": "1980-01-01T00:00:00Z",
            "creators": ["Tool: scripts/build_release_assets.py"],
        },
        "packages": [
            {
                "name": "all-the-context",
                "SPDXID": "SPDXRef-Package-AllTheContext",
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "NOASSERTION",
                "checksums": [{"algorithm": "SHA256", "checksumValue": digest}],
                "packageFileName": archive.name,
                "comment": f"Immutable native archive; size={size} bytes",
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": "SPDXRef-Package-AllTheContext",
            }
        ],
    }
    sbom.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return checksum, sbom


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    parser.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    arguments = parser.parse_args()
    archive = build_archive(
        arguments.source,
        arguments.output_dir,
        version=arguments.version,
        platform_name=arguments.platform,
        architecture=arguments.architecture,
    )
    checksum, sbom = write_metadata(archive, version=arguments.version)
    print(archive)
    print(checksum)
    print(sbom)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

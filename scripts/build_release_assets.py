"""Create deterministic, immutable native release archives and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from allthecontext.release_manifest import ReleaseVersion, sha256_file

ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class ArchivedFile:
    name: str
    sha1: str
    sha256: str
    size: int


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
            (path for path in source.rglob("*") if path.is_file() or path.is_symlink()),
            key=lambda path: path.relative_to(source).as_posix().casefold(),
        )
    )
    source_root = source.resolve() if source.is_dir() else source.parent.resolve()
    archived_names: set[str] = set()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files:
            try:
                path.resolve(strict=True).relative_to(source_root)
            except ValueError as exc:
                raise ValueError(
                    f"release input escapes its source directory: {path.name}"
                ) from exc
            relative = (
                Path(root_name) if source.is_file() else Path(root_name) / path.relative_to(source)
            )
            folded = relative.as_posix().casefold()
            if folded in archived_names:
                raise ValueError(f"case-insensitive archive path collision: {relative.as_posix()}")
            archived_names.add(folded)
            info = zipfile.ZipInfo(relative.as_posix(), ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            if path.is_symlink():
                target = path.readlink()
                if target.is_absolute():
                    raise ValueError(
                        f"release symlink target must be relative: {relative.as_posix()}"
                    )
                try:
                    (path.parent / target).resolve(strict=True).relative_to(source_root)
                except ValueError as exc:
                    raise ValueError(
                        f"release symlink target escapes its source: {relative.as_posix()}"
                    ) from exc
                info.external_attr = 0o120777 << 16
                bundle.writestr(info, target.as_posix().encode("utf-8"))
            else:
                info.external_attr = 0o100755 << 16
                with bundle.open(info, "w") as stream:
                    _copy(stream, path)
    return archive


def _archive_inventory(archive: Path) -> list[ArchivedFile]:
    inventory: list[ArchivedFile] = []
    with zipfile.ZipFile(archive, "r") as bundle:
        for info in sorted(bundle.infolist(), key=lambda item: item.filename.casefold()):
            if info.is_dir():
                continue
            # SHA-1 is mandated for the SPDX package verification-code format.
            sha1 = hashlib.sha1()
            sha256 = hashlib.sha256()
            size = 0
            with bundle.open(info, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    sha1.update(chunk)
                    sha256.update(chunk)
            if size != info.file_size:
                raise ValueError(f"ZIP entry size changed while generating SBOM: {info.filename}")
            inventory.append(
                ArchivedFile(info.filename, sha1.hexdigest(), sha256.hexdigest(), size)
            )
    if not inventory:
        raise ValueError("release archive must contain at least one file")
    return inventory


def write_subject_sbom(
    subject: Path,
    *,
    version: str,
    inventory: list[ArchivedFile] | None = None,
) -> Path:
    digest, size = sha256_file(subject)
    sbom = subject.parent / f"{subject.name}.spdx.json"
    if sbom.exists():
        raise FileExistsError(sbom)
    namespace_hash = hashlib.sha256(f"{subject.name}:{digest}".encode()).hexdigest()
    files_analyzed = inventory is not None
    package: dict[str, object] = {
        "name": "all-the-context",
        "SPDXID": "SPDXRef-Package-AllTheContext",
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": files_analyzed,
        "licenseConcluded": "MIT",
        "licenseDeclared": "MIT",
        "copyrightText": "NOASSERTION",
        "checksums": [{"algorithm": "SHA256", "checksumValue": digest}],
        "packageFileName": subject.name,
        "comment": f"Immutable native package; size={size} bytes",
    }
    files: list[dict[str, object]] = []
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-AllTheContext",
        }
    ]
    if inventory is not None:
        package["packageVerificationCode"] = {
            "packageVerificationCodeValue": hashlib.sha1(
                "".join(sorted(item.sha1 for item in inventory)).encode("ascii")
            ).hexdigest()
        }
        package["licenseInfoFromFiles"] = ["NOASSERTION"]
        files = [
            {
                "fileName": f"./{item.name}",
                "SPDXID": (
                    "SPDXRef-File-" + hashlib.sha256(item.name.encode("utf-8")).hexdigest()[:24]
                ),
                "checksums": [
                    {"algorithm": "SHA1", "checksumValue": item.sha1},
                    {"algorithm": "SHA256", "checksumValue": item.sha256},
                ],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "comment": f"Archived file; size={item.size} bytes",
            }
            for item in inventory
        ]
        relationships.extend(
            {
                "spdxElementId": "SPDXRef-Package-AllTheContext",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": (
                    "SPDXRef-File-" + hashlib.sha256(item.name.encode("utf-8")).hexdigest()[:24]
                ),
            }
            for item in inventory
        )
    payload = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{subject.name}-sbom",
        "documentNamespace": f"https://spdx.org/spdxdocs/all-the-context-{namespace_hash}",
        "creationInfo": {
            "created": "1980-01-01T00:00:00Z",
            "creators": ["Tool: scripts/build_release_assets.py"],
        },
        "packages": [package],
        "files": files,
        "relationships": relationships,
    }
    sbom.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sbom


def write_metadata(archive: Path, *, version: str) -> tuple[Path, Path]:
    digest, _ = sha256_file(archive)
    inventory = _archive_inventory(archive)
    checksum = archive.parent / f"{archive.name}.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
    sbom = write_subject_sbom(archive, version=version, inventory=inventory)
    return checksum, sbom


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    parser.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    parser.add_argument(
        "--subject-metadata-only",
        action="store_true",
        help="write only an SPDX subject document beside an already-built native package",
    )
    arguments = parser.parse_args()
    if arguments.subject_metadata_only:
        sbom = write_subject_sbom(arguments.source, version=arguments.version)
        print(sbom)
        return 0
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

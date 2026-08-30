from __future__ import annotations

import hashlib
import itertools
import json
import os
import struct
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import verify_installed_component_manifest_independent as verifier
from scripts.build_release_assets import build_archive

SOURCE_COMMIT = "a" * 40
VERSION = "0.1.0-beta.7"
SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / ("verify_installed_component_manifest_independent.py")
)


@dataclass
class _Candidate:
    root: Path
    archive: Path
    direct: Path
    components: dict[str, Path]
    payload: dict[str, Any]


def _pe_image(*, certificate_offset: int = 0, certificate_size: int = 0) -> bytes:
    pe_offset = 128
    optional_size = 240
    header_size = pe_offset + 24 + optional_size
    image = bytearray(max(header_size, certificate_offset + certificate_size))
    image[:2] = b"MZ"
    image[60:64] = pe_offset.to_bytes(4, "little")
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    image[pe_offset + 4 : pe_offset + 6] = verifier.AMD64_MACHINE.to_bytes(2, "little")
    image[pe_offset + 20 : pe_offset + 22] = optional_size.to_bytes(2, "little")
    optional_offset = pe_offset + 24
    image[optional_offset : optional_offset + 2] = (0x20B).to_bytes(2, "little")
    image[optional_offset + 108 : optional_offset + 112] = (16).to_bytes(4, "little")
    certificate_entry = optional_offset + 112 + (4 * 8)
    image[certificate_entry : certificate_entry + 4] = certificate_offset.to_bytes(4, "little")
    image[certificate_entry + 4 : certificate_entry + 8] = certificate_size.to_bytes(4, "little")
    return bytes(image)


def _canonical(value: dict[str, Any]) -> bytes:
    rendered = json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
    return f"{rendered}\n".encode()


def _descriptor(filename: str, raw: bytes) -> dict[str, Any]:
    return {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _members(
    candidate: _Candidate, *, raw_manifest: bytes | None = None
) -> list[tuple[str, bytes]]:
    manifest = raw_manifest or _canonical(candidate.payload)
    checksum = f"{hashlib.sha256(manifest).hexdigest()}  {verifier.MANIFEST_FILE_NAME}\n".encode()
    return [
        (
            verifier.ARCHIVE_MEMBER_PREFIX + "AllTheContextSetup.exe",
            candidate.components["main"].read_bytes(),
        ),
        (verifier.ARCHIVE_MEMBER_PREFIX + verifier.MANIFEST_FILE_NAME, manifest),
        (verifier.ARCHIVE_MEMBER_PREFIX + verifier.CHECKSUM_FILE_NAME, checksum),
    ]


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, raw in members:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100755 << 16
            bundle.writestr(info, raw)


def _candidate(tmp_path: Path) -> _Candidate:
    root = tmp_path / "candidate"
    build = root / "build"
    release = root / "release"
    build.mkdir(parents=True)
    release.mkdir()
    data = {
        "main": _pe_image(),
        "mcp": _pe_image(certificate_offset=392, certificate_size=16),
        "recovery": _pe_image() + b"recovery",
        "updater": _pe_image() + b"updater",
    }
    source_names = {
        "main": "AllTheContextSetup.exe",
        "mcp": "AllTheContextMCP.exe",
        "recovery": "AllTheContextRecovery.exe",
        "updater": "AllTheContextUpdater.exe",
    }
    components: dict[str, Path] = {}
    for role, raw in data.items():
        path = build / source_names[role]
        path.write_bytes(raw)
        components[role] = path
    direct = release / f"all-the-context-{VERSION}-windows-x86_64-unsigned.exe"
    direct.write_bytes(data["main"])
    component_descriptors = [
        {
            "authenticode": {"status": "not-present"},
            **_descriptor("AllTheContext.exe", data["main"]),
            "role": "main",
        },
        {
            "authenticode": {"status": "present-unverified"},
            **_descriptor("AllTheContextMCP.exe", data["mcp"]),
            "role": "mcp",
        },
        {
            "authenticode": {"status": "not-present"},
            **_descriptor("AllTheContextRecovery.exe", data["recovery"]),
            "role": "recovery",
        },
        {
            "authenticode": {"status": "not-present"},
            **_descriptor("AllTheContextUpdater.exe", data["updater"]),
            "role": "updater",
        },
    ]
    payload: dict[str, Any] = {
        "architecture": "x86_64",
        "component_count": 4,
        "components": component_descriptors,
        "manifest_type": "installed-component",
        "package": {
            "direct_package": _descriptor(direct.name, data["main"]),
            **_descriptor("AllTheContextSetup.exe", data["main"]),
        },
        "platform": "windows",
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "version": VERSION,
    }
    archive = root / f"all-the-context-{VERSION}-windows-x86_64.zip"
    candidate = _Candidate(root, archive, direct, components, payload)
    _write_archive(archive, _members(candidate))
    return candidate


def _verify(candidate: _Candidate, **expected: str) -> dict[str, Any]:
    return verifier.verify_archive(
        archive_path=candidate.archive,
        direct_package_path=candidate.direct,
        component_paths=candidate.components,
        source_root=candidate.root,
        version=expected.pop("version", VERSION),
        source_commit=expected.pop("source_commit", SOURCE_COMMIT),
        platform=expected.pop("platform", "windows"),
        architecture=expected.pop("architecture", "x86_64"),
    )


def test_independent_verifier_accepts_synthetic_candidate() -> None:
    source = Path(verifier.__file__).read_text(encoding="utf-8")
    assert "from allthecontext" not in source
    assert "installed_component_manifest import" not in source


def test_independent_verifier_accepts_synthetic_candidate_archive(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)

    assert _verify(candidate) == candidate.payload


def test_independent_verifier_accepts_build_release_assets_archive(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    archive_source = candidate.root / verifier.ARCHIVE_MEMBER_PREFIX.rstrip("/")
    archive_source.mkdir()
    for name, raw in _members(candidate):
        (archive_source / name.removeprefix(verifier.ARCHIVE_MEMBER_PREFIX)).write_bytes(raw)
    candidate.archive = build_archive(
        archive_source,
        candidate.root / "release-assets",
        version=VERSION,
        platform_name="windows",
        architecture="x86_64",
    )

    assert candidate.archive.name == f"all-the-context-{VERSION}-windows-x86_64.zip"
    assert _verify(candidate) == candidate.payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "0.1.0-beta.8"),
        ("source_commit", "b" * 40),
        ("platform", "linux"),
        ("architecture", "arm64"),
    ],
)
def test_rejects_mismatched_expected_headers(tmp_path: Path, field: str, value: str) -> None:
    candidate = _candidate(tmp_path)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="header"):
        _verify(candidate, **{field: value})


def test_rejects_noncanonical_manifest_and_bad_checksum(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    noncanonical = json.dumps(candidate.payload, separators=(",", ":")).encode()
    _write_archive(candidate.archive, _members(candidate, raw_manifest=noncanonical))
    with pytest.raises(verifier.IndependentManifestVerificationError, match="canonical"):
        _verify(candidate)

    members = _members(candidate)
    checksum = b"0" * 64 + b"  " + verifier.MANIFEST_FILE_NAME.encode() + b"\n"
    members[2] = (verifier.ARCHIVE_MEMBER_PREFIX + verifier.CHECKSUM_FILE_NAME, checksum)
    _write_archive(candidate.archive, members)
    with pytest.raises(verifier.IndependentManifestVerificationError, match="checksum"):
        _verify(candidate)


def test_rejects_duplicate_json_fields(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    duplicate = b'{"version":"0.1.0-beta.7","version":"0.1.0-beta.7"}'
    _write_archive(candidate.archive, _members(candidate, raw_manifest=duplicate))

    with pytest.raises(verifier.IndependentManifestVerificationError, match="duplicate"):
        _verify(candidate)


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ("traversal", "escaping"),
        ("extra", "exactly three files"),
        ("duplicate", r"duplicate|exactly three files"),
        ("symlink", r"symlink|envelope"),
    ],
)
def test_rejects_unsafe_or_nonexact_archive_members(
    tmp_path: Path, members: str, message: str
) -> None:
    candidate = _candidate(tmp_path)
    valid = _members(candidate)
    if members == "traversal":
        entries = [
            (verifier.ARCHIVE_MEMBER_PREFIX + "../AllTheContextSetup.exe", valid[0][1]),
            valid[1],
            valid[2],
        ]
    elif members == "extra":
        entries = [
            *valid,
            (verifier.ARCHIVE_MEMBER_PREFIX + "extra.txt", b"unexpected"),
        ]
    elif members == "duplicate":
        entries = [valid[0], valid[0], valid[1], valid[2]]
    else:
        link = zipfile.ZipInfo(verifier.ARCHIVE_MEMBER_PREFIX + "AllTheContextSetup.exe")
        link.external_attr = 0o120777 << 16
        entries = valid
        with zipfile.ZipFile(candidate.archive, "w") as bundle:
            bundle.writestr(link, b"target")
            bundle.writestr(
                verifier.ARCHIVE_MEMBER_PREFIX + verifier.MANIFEST_FILE_NAME, valid[1][1]
            )
            bundle.writestr(
                verifier.ARCHIVE_MEMBER_PREFIX + verifier.CHECKSUM_FILE_NAME, valid[2][1]
            )
    if members != "symlink":
        if members == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                _write_archive(candidate.archive, entries)
        else:
            _write_archive(candidate.archive, entries)

    with pytest.raises(verifier.IndependentManifestVerificationError, match=message):
        _verify(candidate)


@pytest.mark.parametrize("role", ["main", "mcp", "recovery", "updater"])
def test_rejects_component_digest_or_size_drift(tmp_path: Path, role: str) -> None:
    candidate = _candidate(tmp_path)
    candidate.components[role].write_bytes(candidate.components[role].read_bytes() + b"mutation")

    with pytest.raises(verifier.IndependentManifestVerificationError, match="match"):
        _verify(candidate)


def test_rejects_direct_package_drift(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    candidate.direct.write_bytes(candidate.direct.read_bytes() + b"mutation")

    with pytest.raises(verifier.IndependentManifestVerificationError, match="direct package"):
        _verify(candidate)


def test_rejects_archive_package_drift_and_malformed_zip(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    members = _members(candidate)
    members[0] = (members[0][0], members[0][1] + b"mutation")
    _write_archive(candidate.archive, members)
    with pytest.raises(
        verifier.IndependentManifestVerificationError, match="release archive package"
    ):
        _verify(candidate)

    candidate.archive.write_bytes(b"not a ZIP")
    with pytest.raises(verifier.IndependentManifestVerificationError, match=r"valid ZIP|envelope"):
        _verify(candidate)


def test_rejects_component_path_escape_and_duplicate_identity(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    outside = tmp_path / "outside-AllTheContextMCP.exe"
    outside.write_bytes(candidate.components["mcp"].read_bytes())
    candidate.components["mcp"] = outside
    with pytest.raises(verifier.IndependentManifestVerificationError, match="escapes"):
        _verify(candidate)

    candidate = _candidate(tmp_path / "duplicate")
    try:
        candidate.components["recovery"].unlink()
        candidate.components["recovery"].hardlink_to(candidate.components["mcp"])
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation is unavailable on this host")
    with pytest.raises(verifier.IndependentManifestVerificationError, match=r"hardlink|duplicate"):
        _verify(candidate)


def test_rejects_mutation_between_stable_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    original = verifier._hash_stream
    mutated = False

    def mutate_once(stream: Any, *, label: str, maximum_size: int) -> tuple[str, int]:
        nonlocal mutated
        measurement = original(stream, label=label, maximum_size=maximum_size)
        if label == "mcp executable" and not mutated:
            candidate.components["mcp"].write_bytes(
                candidate.components["mcp"].read_bytes() + b"changed"
            )
            mutated = True
        return measurement

    monkeypatch.setattr(verifier, "_hash_stream", mutate_once)
    with pytest.raises(
        verifier.IndependentManifestVerificationError,
        match=r"between stable reads|while it was read",
    ):
        _verify(candidate)


def test_cli_verifies_without_project_imports(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    command = [
        sys.executable,
        str(SCRIPT),
        "verify-archive",
        "--archive",
        str(candidate.archive),
        "--direct-package",
        str(candidate.direct),
        "--source-root",
        str(candidate.root),
        "--version",
        VERSION,
        "--source-commit",
        SOURCE_COMMIT,
        "--platform",
        "windows",
        "--architecture",
        "x86_64",
        "--main",
        str(candidate.components["main"]),
        "--mcp",
        str(candidate.components["mcp"]),
        "--recovery",
        str(candidate.components["recovery"]),
        "--updater",
        str(candidate.components["updater"]),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)

    assert completed.returncode == 0
    assert completed.stdout == "verified installed-component archive: pass\n"
    assert str(candidate.archive) not in completed.stdout


def _patch_first_central_directory(
    archive: Path, *, compressed_size: int | None = None, uncompressed_size: int | None = None
) -> None:
    raw = bytearray(archive.read_bytes())
    offset = raw.find(b"PK\x01\x02")
    local_offset = raw.find(b"PK\x03\x04")
    assert offset >= 0
    assert local_offset == 0
    if compressed_size is not None:
        raw[offset + 20 : offset + 24] = struct.pack("<I", compressed_size)
        raw[local_offset + 18 : local_offset + 22] = struct.pack("<I", compressed_size)
    if uncompressed_size is not None:
        raw[offset + 24 : offset + 28] = struct.pack("<I", uncompressed_size)
        raw[local_offset + 22 : local_offset + 26] = struct.pack("<I", uncompressed_size)
    archive.write_bytes(raw)


def _patch_first_central_name(archive: Path, replacement: bytes) -> None:
    raw = bytearray(archive.read_bytes())
    offset = raw.find(b"PK\x01\x02")
    assert offset >= 0
    name_size = struct.unpack_from("<H", raw, offset + 28)[0]
    assert len(replacement) == name_size
    raw[offset + 46 : offset + 46 + name_size] = replacement
    archive.write_bytes(raw)


def _patch_first_local_name(archive: Path, replacement: bytes) -> None:
    raw, local_offset, _central_offset, _eocd_offset = _zip_offsets(archive)
    name_size = struct.unpack_from("<H", raw, local_offset + 26)[0]
    assert len(replacement) == name_size
    raw[local_offset + 30 : local_offset + 30 + name_size] = replacement
    archive.write_bytes(raw)


def _zip_offsets(archive: Path) -> tuple[bytearray, int, int, int]:
    raw = bytearray(archive.read_bytes())
    local_offset = raw.find(b"PK\x03\x04")
    central_offset = raw.find(b"PK\x01\x02")
    eocd_offset = raw.rfind(b"PK\x05\x06")
    assert local_offset == 0
    assert central_offset > 0
    assert eocd_offset > central_offset
    return raw, local_offset, central_offset, eocd_offset


def _central_offsets(archive: Path) -> list[int]:
    raw, _local_offset, central_offset, _eocd_offset = _zip_offsets(archive)
    offsets: list[int] = []
    cursor = central_offset
    for _ in range(3):
        assert raw[cursor : cursor + 4] == b"PK\x01\x02"
        offsets.append(cursor)
        name_size = struct.unpack_from("<H", raw, cursor + 28)[0]
        extra_size = struct.unpack_from("<H", raw, cursor + 30)[0]
        comment_size = struct.unpack_from("<H", raw, cursor + 32)[0]
        cursor += 46 + name_size + extra_size + comment_size
    return offsets


def _patch_first_local_field(archive: Path, *, offset: int, value: int, width: int = 2) -> None:
    raw, local_offset, _central_offset, _eocd_offset = _zip_offsets(archive)
    raw[local_offset + offset : local_offset + offset + width] = value.to_bytes(width, "little")
    archive.write_bytes(raw)


def _patch_first_entry_field(
    archive: Path,
    *,
    central_field: int,
    local_field: int,
    central_value: int,
    local_value: int,
    width: int = 2,
) -> None:
    raw, local_offset, central_offset, _eocd_offset = _zip_offsets(archive)
    raw[central_offset + central_field : central_offset + central_field + width] = (
        central_value.to_bytes(width, "little")
    )
    raw[local_offset + local_field : local_offset + local_field + width] = local_value.to_bytes(
        width, "little"
    )
    archive.write_bytes(raw)


def _patch_eocd_field(archive: Path, *, offset: int, value: int, width: int) -> None:
    raw, _local_offset, _central_offset, eocd_offset = _zip_offsets(archive)
    raw[eocd_offset + offset : eocd_offset + offset + width] = value.to_bytes(width, "little")
    archive.write_bytes(raw)


def _write_archive_with_first_extra(path: Path, members: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for index, (name, raw) in enumerate(members):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100755 << 16
            if index == 0:
                info.extra = b"x"
            bundle.writestr(info, raw)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_enforces_canonical_member_order(tmp_path: Path, order: tuple[int, int, int]) -> None:
    candidate = _candidate(tmp_path)
    members = _members(candidate)
    _write_archive(candidate.archive, [members[index] for index in order])

    if order == (0, 1, 2):
        assert _verify(candidate) == candidate.payload
    else:
        with pytest.raises(verifier.IndependentManifestVerificationError, match="member order"):
            _verify(candidate)


def test_rejects_noncanonical_physical_local_member_order(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    raw, _local_offset, _central_offset, _eocd_offset = _zip_offsets(candidate.archive)
    offsets = _central_offsets(candidate.archive)
    first_local = bytes(raw[offsets[0] + 42 : offsets[0] + 46])
    second_local = bytes(raw[offsets[1] + 42 : offsets[1] + 46])
    raw[offsets[0] + 42 : offsets[0] + 46] = second_local
    raw[offsets[1] + 42 : offsets[1] + 46] = first_local
    candidate.archive.write_bytes(raw)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="local member order"):
        _verify(candidate)


@pytest.mark.parametrize("member_index", range(3))
@pytest.mark.parametrize(
    "attribute",
    [
        "external_attr",
        "internal_attr",
        "create_system",
        "create_version",
        "extract_version",
        "flags",
    ],
)
def test_rejects_noncanonical_member_attributes(
    tmp_path: Path, member_index: int, attribute: str
) -> None:
    candidate = _candidate(tmp_path)
    raw, _local_offset, _central_offset, _eocd_offset = _zip_offsets(candidate.archive)
    offset = _central_offsets(candidate.archive)[member_index]
    if attribute == "external_attr":
        field, width, value = 38, 4, 0
    elif attribute == "internal_attr":
        field, width, value = 36, 2, 1
    elif attribute == "create_system":
        field, width, value = 4, 2, (2 << 8) | verifier.EXPECTED_ZIP_CREATE_VERSION
    elif attribute == "create_version":
        field, width, value = 4, 2, (verifier.EXPECTED_ZIP_CREATE_SYSTEM << 8) | 19
    elif attribute == "extract_version":
        field, width, value = 6, 2, 19
    else:
        field, width, value = 8, 2, 1
    raw[offset + field : offset + field + width] = value.to_bytes(width, "little")
    candidate.archive.write_bytes(raw)

    with pytest.raises(
        verifier.IndependentManifestVerificationError,
        match=r"envelope|metadata|canonical|inventory",
    ):
        _verify(candidate)


def test_rejects_pe_header_offset_before_dos_header(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    raw = bytearray(candidate.components["main"].read_bytes())
    raw[60:64] = (2).to_bytes(4, "little")
    candidate.components["main"].write_bytes(raw)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="header offset"):
        _verify(candidate)


def test_rejects_oversized_central_directory_before_zipfile_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    _patch_eocd_field(
        candidate.archive,
        offset=12,
        value=verifier.MAX_ZIP_CENTRAL_DIRECTORY_BYTES + 1,
        width=4,
    )

    def unexpected_inventory(_bundle: object) -> list[zipfile.ZipInfo]:
        raise AssertionError("ZipFile inventory must follow primitive envelope validation")

    monkeypatch.setattr(zipfile.ZipFile, "infolist", unexpected_inventory)
    with pytest.raises(verifier.IndependentManifestVerificationError, match="central directory"):
        _verify(candidate)


def test_rejects_noncanonical_record_count_before_zipfile_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    _patch_eocd_field(candidate.archive, offset=8, value=4, width=2)
    _patch_eocd_field(candidate.archive, offset=10, value=4, width=2)

    def unexpected_inventory(_bundle: object) -> list[zipfile.ZipInfo]:
        raise AssertionError("ZipFile inventory must follow primitive envelope validation")

    monkeypatch.setattr(zipfile.ZipFile, "infolist", unexpected_inventory)
    with pytest.raises(verifier.IndependentManifestVerificationError, match="exactly three"):
        _verify(candidate)


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_rejects_noncanonical_zip_compression_before_member_read(
    tmp_path: Path, method: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    _patch_first_entry_field(
        candidate.archive,
        central_field=10,
        local_field=8,
        central_value=method,
        local_value=method,
    )

    def unexpected_member_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsupported compression must be rejected before decompression")

    monkeypatch.setattr(zipfile.ZipFile, "open", unexpected_member_read)
    with pytest.raises(
        verifier.IndependentManifestVerificationError,
        match=r"envelope|compression",
    ):
        _verify(candidate)


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_rejects_declared_one_byte_unsupported_compression_without_decompression(
    tmp_path: Path, method: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    _patch_first_entry_field(
        candidate.archive,
        central_field=10,
        local_field=8,
        central_value=method,
        local_value=method,
    )
    _patch_first_entry_field(
        candidate.archive,
        central_field=20,
        local_field=18,
        central_value=1,
        local_value=1,
        width=4,
    )
    _patch_first_entry_field(
        candidate.archive,
        central_field=24,
        local_field=22,
        central_value=1,
        local_value=1,
        width=4,
    )

    def unexpected_member_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsupported compression must be rejected before decompression")

    monkeypatch.setattr(zipfile.ZipFile, "open", unexpected_member_read)
    with pytest.raises(
        verifier.IndependentManifestVerificationError,
        match=r"envelope|compression",
    ):
        _verify(candidate)


def test_rejects_prepended_bytes_and_trailing_bytes(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    candidate.archive.write_bytes(b"prefix" + candidate.archive.read_bytes())
    with pytest.raises(verifier.IndependentManifestVerificationError, match="begin"):
        _verify(candidate)

    candidate = _candidate(tmp_path / "trailing")
    candidate.archive.write_bytes(candidate.archive.read_bytes() + b"trailing")
    with pytest.raises(verifier.IndependentManifestVerificationError, match="end record"):
        _verify(candidate)


def test_rejects_archive_comment_and_member_extra(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    with zipfile.ZipFile(candidate.archive, "a") as bundle:
        bundle.comment = b"comment"
    with pytest.raises(verifier.IndependentManifestVerificationError, match=r"comments|end record"):
        _verify(candidate)

    candidate = _candidate(tmp_path / "extra")
    _write_archive_with_first_extra(candidate.archive, _members(candidate))
    with pytest.raises(verifier.IndependentManifestVerificationError, match="envelope"):
        _verify(candidate)


@pytest.mark.parametrize("eocd_field", [4, 6])
def test_rejects_multi_disk_zip_envelope(tmp_path: Path, eocd_field: int) -> None:
    candidate = _candidate(tmp_path)
    _patch_eocd_field(candidate.archive, offset=eocd_field, value=1, width=2)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="multi-disk"):
        _verify(candidate)


@pytest.mark.parametrize("flags", [0x1, 0x8])
def test_rejects_encrypted_or_data_descriptor_zip_entries(tmp_path: Path, flags: int) -> None:
    candidate = _candidate(tmp_path)
    _patch_first_entry_field(
        candidate.archive,
        central_field=8,
        local_field=6,
        central_value=flags,
        local_value=flags,
    )

    with pytest.raises(verifier.IndependentManifestVerificationError, match="envelope"):
        _verify(candidate)


def test_rejects_zip64_sentinel_without_large_artifact(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    _patch_eocd_field(candidate.archive, offset=12, value=0xFFFFFFFF, width=4)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="ZIP64"):
        _verify(candidate)


@pytest.mark.parametrize(
    ("central_field", "local_field", "width"),
    [(8, 6, 2), (10, 8, 2), (16, 14, 4), (20, 18, 4), (24, 22, 4)],
)
def test_rejects_local_central_header_differentials(
    tmp_path: Path, central_field: int, local_field: int, width: int
) -> None:
    candidate = _candidate(tmp_path)
    raw, local_offset, central_offset, _eocd_offset = _zip_offsets(candidate.archive)
    central_value = int.from_bytes(
        raw[central_offset + central_field : central_offset + central_field + width],
        "little",
    )
    local_value = int.from_bytes(
        raw[local_offset + local_field : local_offset + local_field + width],
        "little",
    )
    _patch_first_entry_field(
        candidate.archive,
        central_field=central_field,
        local_field=local_field,
        central_value=central_value,
        local_value=local_value + 1,
        width=width,
    )

    with pytest.raises(
        verifier.IndependentManifestVerificationError, match=r"local|central|envelope"
    ):
        _verify(candidate)


def test_rejects_local_central_filename_differential(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    raw, local_offset, _central_offset, _eocd_offset = _zip_offsets(candidate.archive)
    name_size = struct.unpack_from("<H", raw, local_offset + 26)[0]
    name = bytes(raw[local_offset + 30 : local_offset + 30 + name_size])
    _patch_first_local_name(candidate.archive, b"X" + name[1:])

    with pytest.raises(verifier.IndependentManifestVerificationError, match="filenames"):
        _verify(candidate)


def test_rejects_zipinfo_filename_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    original_infolist = zipfile.ZipFile.infolist

    def normalized_inventory(bundle: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = original_infolist(bundle)
        infos[0].orig_filename = infos[0].filename + ".normalized"
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", normalized_inventory)
    with pytest.raises(verifier.IndependentManifestVerificationError, match="normalized"):
        _verify(candidate)


@pytest.mark.parametrize(
    "member_name",
    [
        "AllTheContextSetup.exe",
        "other/AllTheContextSetup.exe",
        verifier.ARCHIVE_MEMBER_PREFIX + "./AllTheContextSetup.exe",
        verifier.ARCHIVE_MEMBER_PREFIX + "C:/AllTheContextSetup.exe",
        verifier.ARCHIVE_MEMBER_PREFIX + "AllTheContextSetup.exe\x00suffix",
        verifier.ARCHIVE_MEMBER_PREFIX.replace("installed", "Installed") + "AllTheContextSetup.exe",
        verifier.ARCHIVE_MEMBER_PREFIX.replace("/", "\\") + "AllTheContextSetup.exe",
    ],
)
def test_rejects_noncanonical_archive_member_paths(tmp_path: Path, member_name: str) -> None:
    candidate = _candidate(tmp_path)
    valid = _members(candidate)
    entries = [(member_name, valid[0][1]), valid[1], valid[2]]
    _write_archive(candidate.archive, entries)
    if "\x00" in member_name:
        _patch_first_central_name(
            candidate.archive,
            (verifier.ARCHIVE_MEMBER_PREFIX + "AllTheContextSetup.ex").encode() + b"\x00",
        )
    elif "\\" in member_name:
        _patch_first_central_name(
            candidate.archive,
            (verifier.ARCHIVE_MEMBER_PREFIX + "AllTheContextSetup.exe")
            .replace("/", "\\", 1)
            .encode(),
        )

    with pytest.raises(
        verifier.IndependentManifestVerificationError,
        match=r"unsafe|escaping|unexpected|normalized",
    ):
        _verify(candidate)


def test_rejects_oversized_declared_zip_member_without_allocating_it(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    _patch_first_central_directory(
        candidate.archive, uncompressed_size=verifier.MAX_MEMBER_BYTES + 1
    )

    with pytest.raises(verifier.IndependentManifestVerificationError, match="member"):
        _verify(candidate)


def test_rejects_zip_bomb_like_compression_ratio_before_member_read(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    _patch_first_central_directory(
        candidate.archive,
        compressed_size=1,
        uncompressed_size=verifier.MAX_COMPRESSION_RATIO + 1,
    )

    with pytest.raises(verifier.IndependentManifestVerificationError, match="compression ratio"):
        _verify(candidate)


def _pretend_oversized(path: Path, monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    original_stat = Path.stat

    class _OversizedStat:
        def __init__(self, real: os.stat_result) -> None:
            self._real = real
            self.st_size = size

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    def stat(value: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        result = original_stat(value, *args, **kwargs)
        if value == path:
            return _OversizedStat(result)  # type: ignore[return-value]
        return result

    monkeypatch.setattr(Path, "stat", stat)


def test_rejects_oversized_regular_input_without_large_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    _pretend_oversized(candidate.components["mcp"], monkeypatch, verifier.MAX_EXECUTABLE_BYTES + 1)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="maximum"):
        _verify(candidate)


def test_rejects_oversized_archive_without_large_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    _pretend_oversized(candidate.archive, monkeypatch, verifier.MAX_ARCHIVE_BYTES + 1)

    with pytest.raises(verifier.IndependentManifestVerificationError, match="maximum"):
        _verify(candidate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["package"].update(size=True),
        lambda payload: payload["package"].update(size=1.0),
        lambda payload: payload.update(component_count=True),
        lambda payload: payload.update(component_count=4.0),
        lambda payload: payload.update(schema_version=True),
        lambda payload: payload.update(schema_version=1.0),
        lambda payload: payload["components"][1]["authenticode"].update(status=True),
        lambda payload: payload.update(
            components=[*payload["components"][1:], payload["components"][0]]
        ),
        lambda payload: payload.update(
            components=[
                payload["components"][0],
                payload["components"][0],
                *payload["components"][2:],
            ]
        ),
    ],
)
def test_rejects_manifest_schema_differentials(tmp_path: Path, mutate: Any) -> None:
    candidate = _candidate(tmp_path)
    altered = json.loads(_canonical(candidate.payload))
    mutate(altered)
    _write_archive(candidate.archive, _members(candidate, raw_manifest=_canonical(altered)))

    with pytest.raises(verifier.IndependentManifestVerificationError):
        _verify(candidate)


def test_rejects_nonfinite_manifest_json(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    raw = b'{"schema_version":NaN}'
    _write_archive(candidate.archive, _members(candidate, raw_manifest=raw))

    with pytest.raises(verifier.IndependentManifestVerificationError, match="non-finite"):
        _verify(candidate)


@pytest.mark.parametrize("role", ["main", "mcp", "recovery", "updater"])
@pytest.mark.parametrize("field", ["machine", "magic"])
def test_rejects_non_amd64_or_non_pe32_plus_components(
    tmp_path: Path, role: str, field: str
) -> None:
    candidate = _candidate(tmp_path)
    raw = bytearray(candidate.components[role].read_bytes())
    if field == "machine":
        raw[128 + 4 : 128 + 6] = (0x14C).to_bytes(2, "little")
    else:
        raw[128 + 24 : 128 + 26] = (0x10B).to_bytes(2, "little")
    candidate.components[role].write_bytes(raw)

    with pytest.raises(verifier.IndependentManifestVerificationError, match=r"AMD64|PE32"):
        _verify(candidate)


def test_rejects_component_path_swap_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    original = verifier._stable_executable
    swapped = False

    def swap_before_read(
        path: Path, *, label: str, expected_identity: tuple[int, int]
    ) -> verifier._ExecutableMeasurement:
        nonlocal swapped
        if label == "mcp executable" and not swapped:
            replacement = path.with_name("replacement.exe")
            replacement.write_bytes(path.read_bytes())
            path.unlink()
            replacement.replace(path)
            swapped = True
        return original(path, label=label, expected_identity=expected_identity)

    monkeypatch.setattr(verifier, "_stable_executable", swap_before_read)
    with pytest.raises(verifier.IndependentManifestVerificationError, match="path validation"):
        _verify(candidate)


@pytest.mark.parametrize("asset", ["archive", "direct"])
def test_rejects_release_asset_filename_differential(tmp_path: Path, asset: str) -> None:
    candidate = _candidate(tmp_path)
    if asset == "archive":
        replacement = candidate.root / "wrong.zip"
        candidate.archive.rename(replacement)
        candidate.archive = replacement
    else:
        replacement = candidate.direct.with_name("wrong.exe")
        candidate.direct.rename(replacement)
        candidate.direct = replacement

    with pytest.raises(verifier.IndependentManifestVerificationError, match="filename"):
        _verify(candidate)


def test_expected_header_fields_are_required_by_public_api() -> None:
    import inspect

    signature = inspect.signature(verifier.verify_archive)
    for name in ("version", "source_commit", "platform", "architecture"):
        assert signature.parameters[name].default is inspect.Parameter.empty


def test_cli_errors_are_content_free(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    missing = candidate.root / "missing.exe"
    command = [
        sys.executable,
        str(SCRIPT),
        "verify-archive",
        "--archive",
        str(candidate.archive),
        "--direct-package",
        str(missing),
        "--source-root",
        str(candidate.root),
        "--version",
        VERSION,
        "--source-commit",
        SOURCE_COMMIT,
        "--platform",
        "windows",
        "--architecture",
        "x86_64",
        "--main",
        str(candidate.components["main"]),
        "--mcp",
        str(candidate.components["mcp"]),
        "--recovery",
        str(candidate.components["recovery"]),
        "--updater",
        str(candidate.components["updater"]),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert str(candidate.root) not in completed.stderr
    assert completed.stderr == (
        "verify-installed-component-manifest: error: verification arguments are invalid\n"
    )

    invalid_command = subprocess.run(
        [sys.executable, str(SCRIPT), str(candidate.root / "not-a-command")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_command.returncode == 2
    assert str(candidate.root) not in invalid_command.stderr

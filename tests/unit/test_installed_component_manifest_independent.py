from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import verify_installed_component_manifest_independent as verifier

SOURCE_COMMIT = "a" * 40
VERSION = "0.1.0-beta.7"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / (
    "verify_installed_component_manifest_independent.py"
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
        ("AllTheContextSetup.exe", candidate.components["main"].read_bytes()),
        (verifier.MANIFEST_FILE_NAME, manifest),
        (verifier.CHECKSUM_FILE_NAME, checksum),
    ]


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, raw in members:
            bundle.writestr(name, raw)


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
    archive = root / "candidate.zip"
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
    members[2] = (verifier.CHECKSUM_FILE_NAME, checksum)
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
        ("duplicate", "duplicate"),
        ("symlink", "symlink"),
    ],
)
def test_rejects_unsafe_or_nonexact_archive_members(
    tmp_path: Path, members: str, message: str
) -> None:
    candidate = _candidate(tmp_path)
    valid = _members(candidate)
    if members == "traversal":
        entries = [("../AllTheContextSetup.exe", valid[0][1]), valid[1], valid[2]]
    elif members == "extra":
        entries = [*valid, ("extra.txt", b"unexpected")]
    elif members == "duplicate":
        entries = [valid[0], valid[0], valid[1], valid[2]]
    else:
        link = zipfile.ZipInfo("AllTheContextSetup.exe")
        link.external_attr = 0o120777 << 16
        entries = valid
        with zipfile.ZipFile(candidate.archive, "w") as bundle:
            bundle.writestr(link, b"target")
            bundle.writestr(verifier.MANIFEST_FILE_NAME, valid[1][1])
            bundle.writestr(verifier.CHECKSUM_FILE_NAME, valid[2][1])
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
    with pytest.raises(verifier.IndependentManifestVerificationError, match="valid ZIP"):
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
    with pytest.raises(verifier.IndependentManifestVerificationError, match="duplicate"):
        _verify(candidate)


def test_rejects_mutation_between_stable_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path)
    original = verifier._read_once
    mutated = False

    def mutate_once(path: Path, *, label: str) -> bytes:
        nonlocal mutated
        raw = original(path, label=label)
        if label == "mcp executable" and not mutated:
            path.write_bytes(raw + b"changed")
            mutated = True
        return raw

    monkeypatch.setattr(verifier, "_read_once", mutate_once)
    with pytest.raises(verifier.IndependentManifestVerificationError, match="between stable reads"):
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
    assert str(candidate.archive) in completed.stdout

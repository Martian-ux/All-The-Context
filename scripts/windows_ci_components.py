"""Recompose pinned Windows components with freshly compiled exact-source ATC.

This is a retained dependency-closure build, not a fresh PyInstaller scan. Each
arm runs in its own interpreter and fresh roots. No donor code is executed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import marshal
import os
import struct
import subprocess
import sys
import sysconfig
import tarfile
import tomllib
import zlib
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from types import CodeType

# Donor extraction uses Archive before the locked build dependencies are installed.
if __package__:
    from scripts import native_build_provenance as provenance
    from scripts import windows_ci_pilot as pilot
    from scripts.windows_ci_pilot import SOURCE, git, require
else:
    import native_build_provenance as provenance
    import windows_ci_pilot as pilot
    from windows_ci_pilot import SOURCE, git, require

ROLE_NAMES = {
    "mcp": "AllTheContextMCP.exe",
    "recovery": "AllTheContextRecovery.exe",
    "updater": "AllTheContextUpdater.exe",
    "main": "AllTheContextSetup.exe",
}
ENTRYPOINTS = {
    "mcp": "mcp_entry",
    "recovery": "recovery_entry",
    "updater": "update_helper_entry",
    "main": "desktop_entry",
}
DONOR_HASHES = {
    "mcp": "0716ef18cd6776966cfbecbcc14ea5ad1b2f62bdc59e7ce2f8e524023bf31f13",
    "recovery": "a4b4755ebc49ee9aa7de5009556977042c0bac2afaa3698b33e2cdfa951b3b2a",
    "updater": "1bdebbf38fbd890b2c4c3cc3269d810b9177c847e03203db7853c17434abda25",
    "main": "3c136c9916e20cabbb834c682538e46a027a8bd87c98b41b63e3312774d544b5",
}
IDENTITY = r"allthecontext\build-identity-v1.json"
SRC = "packages/allthecontext/src/"
COOKIE = struct.Struct("!8sIIII64s")
ROW = struct.Struct("!IIIIBc")
MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
MAX_RAW = 100_000_000


def decode(stored: bytes, size: int, flag: int) -> bytes:
    require(0 <= size <= MAX_RAW and flag in (0, 1), "invalid payload bounds")
    if not flag:
        require(len(stored) == size, "invalid uncompressed size")
        return stored
    decoder = zlib.decompressobj()
    raw = decoder.decompress(stored, size + 1)
    require(
        len(raw) == size
        and decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail,
        "invalid compressed payload",
    )
    return raw


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    flag: int
    stored: bytes
    raw: bytes


class Archive:
    """Read a bounded unsigned x64 CArchive without extracting arbitrary paths."""

    def __init__(self, data: bytes):
        require(128 <= len(data) <= 110_000_000 and data[:2] == b"MZ", "invalid PE input")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        require(pe + 264 <= len(data) and data[pe : pe + 4] == b"PE\0\0", "invalid PE header")
        require(
            struct.unpack_from("<H", data, pe + 4)[0] == 0x8664
            and struct.unpack_from("<H", data, pe + 24)[0] == 0x20B,
            "unsupported PE ABI",
        )
        require(struct.unpack_from("<II", data, pe + 168) == (0, 0), "signed donor unsupported")
        cookie_at = len(data) - COOKIE.size
        magic, length, toc_offset, toc_size, version, dll = COOKIE.unpack_from(data, cookie_at)
        start = len(data) - length
        toc = start + toc_offset
        require(
            magic == MAGIC
            and version == 312
            and dll.rstrip(b"\0") == b"python312.dll"
            and pe + 264 <= start < toc < cookie_at
            and toc + toc_size == cookie_at
            and toc_size <= 1_000_000,
            "invalid CArchive cookie",
        )
        self.prefix = data[:start]
        self.python_library = dll
        self.entries: dict[str, Entry] = {}
        intervals = []
        raw_total = 0
        position = toc
        while position < cookie_at:
            require(position + ROW.size <= cookie_at and len(self.entries) < 3000, "invalid TOC")
            row_size, offset, stored_size, raw_size, flag, code = ROW.unpack_from(data, position)
            require(
                ROW.size < row_size <= cookie_at - position
                and row_size % 16 == 0
                and offset + stored_size <= toc_offset,
                "invalid TOC bounds",
            )
            name = data[position + ROW.size : position + row_size].rstrip(b"\0").decode("utf-8")
            require(
                bool(name) and "\0" not in name and name not in self.entries, "invalid TOC name"
            )
            stored = data[start + offset : start + offset + stored_size]
            raw = decode(stored, raw_size, flag)
            raw_total += len(raw)
            require(raw_total <= 500_000_000, "oversized CArchive")
            self.entries[name] = Entry(name, code.decode("ascii"), flag, stored, raw)
            if stored_size:
                intervals.append((offset, offset + stored_size))
            position += row_size
        require(position == cookie_at, "truncated TOC")
        ordered = sorted(intervals)
        require(
            all(left[1] <= right[0] for left, right in pairwise(ordered)),
            "overlapping CArchive payloads",
        )


def encode(entry: Entry, raw: bytes) -> bytes:
    """Reuse only this exact role/name/method/data's already bounded encoding."""
    if raw == entry.raw:
        return entry.stored
    from PyInstaller.archive.writers import CArchiveWriter

    return zlib.compress(raw, CArchiveWriter._COMPRESSION_LEVEL) if entry.flag else raw


def compose(path: Path, archive: Archive, prefix: bytes, replacements: dict[str, bytes]) -> None:
    from PyInstaller.archive.writers import CArchiveWriter
    from PyInstaller.utils.win32.winutils import update_exe_pe_checksum

    require(replacements.keys() <= archive.entries.keys(), "unexpected replacement member")
    toc = []
    with path.open("xb") as target:
        target.write(prefix)
        start = target.tell()
        for name, entry in archive.entries.items():
            raw = replacements.get(name, entry.raw)
            stored = encode(entry, raw)
            toc.append((target.tell() - start, len(stored), len(raw), entry.flag, entry.kind, name))
            target.write(stored)
        offset = target.tell() - start
        table = CArchiveWriter._serialize_toc(toc)
        target.write(table)
        target.write(
            COOKIE.pack(
                MAGIC,
                offset + len(table) + COOKIE.size,
                offset,
                len(table),
                312,
                archive.python_library,
            )
        )
    update_exe_pe_checksum(str(path))


def compile_module(source: bytes, filename: str) -> bytes:
    from PyInstaller.building.utils import replace_filename_in_code_object

    parsed = compile(source, filename, "exec", ast.PyCF_ONLY_AST, True)
    original = compile(parsed, filename, "exec", 0, True, 0)
    # Match the stock modulegraph cache's code-object lifetime during marshal.
    normalized = replace_filename_in_code_object(original, filename)
    return marshal.dumps(normalized)


def compile_pyz(data: bytes, source: dict[str, bytes]) -> bytes:
    from PyInstaller.archive.writers import ZlibArchiveWriter
    from PyInstaller.loader.pyimod01_archive import PYZ_ITEM_MODULE, PYZ_ITEM_PKG

    header = ZlibArchiveWriter._HEADER_LENGTH
    require(
        data[:8] == b"PYZ\0" + importlib.util.MAGIC_NUMBER and len(data) > header,
        "unsupported PYZ ABI",
    )
    toc_at = struct.unpack_from("!i", data, 8)[0]
    require(header <= toc_at < len(data) and len(data) - toc_at < 1_000_000, "invalid PYZ bounds")
    index = marshal.loads(data[toc_at:])
    require(isinstance(index, list) and len(index) < 2000, "invalid PYZ index")
    output, fresh_index, names = io.BytesIO(), [], set()
    output.write(data[:header])
    owned = 0
    for name, (kind, offset, size) in index:
        require(
            isinstance(name, str)
            and name not in names
            and all(part.isidentifier() for part in name.split("."))
            and header <= offset <= toc_at
            and 0 <= size <= toc_at - offset,
            "invalid PYZ member",
        )
        names.add(name)
        stored = data[offset : offset + size]
        if name.split(".")[0] == "allthecontext":
            require(kind in (PYZ_ITEM_MODULE, PYZ_ITEM_PKG), "unsupported ATC module kind")
            filename = name.replace(".", "\\") + (
                "\\__init__.py" if kind == PYZ_ITEM_PKG else ".py"
            )
            raw = compile_module(source[SRC + filename.replace("\\", "/")], filename)
            stored = zlib.compress(raw, ZlibArchiveWriter._COMPRESSION_LEVEL)
            owned += 1
        fresh_index.append((name, (kind, output.tell(), len(stored))))
        output.write(stored)
    require(owned > 0, "missing ATC source modules")
    new_toc = output.tell()
    output.write(data[toc_at:] if fresh_index == index else marshal.dumps(fresh_index))
    output.seek(8)
    output.write(struct.pack("!i", new_toc))
    return output.getvalue()


def validate_abi(root: Path) -> None:
    import importlib.metadata

    require(
        sys.version_info[:3] == (3, 12, 10)
        and sys.flags.optimize == 0
        and zlib.ZLIB_VERSION == zlib.ZLIB_RUNTIME_VERSION == "1.3.1",
        "unsupported compression ABI",
    )
    # Hooks are graph-driving, even when the top-level PyInstaller version matches.
    import tomllib

    locked = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    versions = {row["name"]: row["version"] for row in locked["package"]}
    for name in ("pyinstaller", "pyinstaller-hooks-contrib", "mcp", "keyring"):
        require(importlib.metadata.version(name) == versions[name], "packaging dependency mismatch")


def installed_metadata(root: Path, entries: dict[str, Entry]) -> dict[str, bytes]:
    """Refresh exact archive destinations from this fresh hash-locked environment."""
    import re
    from importlib.metadata import Distribution

    versions = {
        (row["name"], row["version"])
        for row in tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))["package"]
    }
    site = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    require(
        sys.prefix != sys.base_prefix and site.is_relative_to(Path(sys.prefix).resolve()),
        "metadata requires the isolated packaging environment",
    )
    replacements = {}
    for name, entry in entries.items():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+\.dist-info)\\(INSTALLER|RECORD)", name)
        if match is None:
            continue
        directory, leaf = match.groups()
        candidate = site / directory / leaf
        path = candidate.resolve(strict=True)
        require(path == candidate and entry.kind == "b", "invalid metadata member")
        provenance._regular_file(path, label="installed metadata")
        distribution = Distribution.at(path.parent)
        package = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        require((package, distribution.version) in versions, "unlocked installed metadata")
        require(
            directory == f"{package.replace('-', '_')}-{distribution.version}.dist-info",
            "metadata destination mismatch",
        )
        require(path.stat().st_size <= 10_000_000, "oversized installed metadata")
        replacements[name] = path.read_bytes()
    return replacements


def canonical_pyz(data: bytes, source: dict[str, bytes], destination: Path) -> bytes:
    """Use stock writer/order for the entire PYZ, including retained dependencies."""
    from PyInstaller.archive.writers import ZlibArchiveWriter
    from PyInstaller.loader.pyimod01_archive import (
        PYZ_ITEM_MODULE,
        PYZ_ITEM_NSPKG,
        PYZ_ITEM_PKG,
    )

    header = ZlibArchiveWriter._HEADER_LENGTH
    require(data[:8] == b"PYZ\0" + importlib.util.MAGIC_NUMBER, "unsupported PYZ ABI")
    toc_at = struct.unpack_from("!i", data, 8)[0]
    require(header <= toc_at < len(data) and len(data) - toc_at < 1_000_000, "invalid PYZ bounds")
    index = marshal.loads(data[toc_at:])
    require(isinstance(index, list) and len(index) < 2000, "invalid PYZ index")
    rows, codes, names = [], {}, set()
    owned = 0
    for name, (kind, offset, size) in index:
        require(
            isinstance(name, str)
            and name not in names
            and all(part.isidentifier() for part in name.split("."))
            and header <= offset <= toc_at
            and 0 <= size <= toc_at - offset,
            "invalid PYZ member",
        )
        names.add(name)
        if kind == PYZ_ITEM_NSPKG:
            require(not size and name.split(".")[0] != "allthecontext", "unsupported namespace")
            rows.append((name, None, "PYMODULE"))
            continue
        require(kind in (PYZ_ITEM_MODULE, PYZ_ITEM_PKG), "unsupported PYZ module kind")
        filename = name.replace(".", "\\") + ("\\__init__.py" if kind == PYZ_ITEM_PKG else ".py")
        if name.split(".")[0] == "allthecontext":
            raw = compile_module(source[SRC + filename.replace("\\", "/")], filename)
            owned += 1
        else:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(data[offset : offset + size], MAX_RAW + 1)
            require(
                len(raw) <= MAX_RAW
                and decoder.eof
                and not decoder.unused_data
                and not decoder.unconsumed_tail,
                "invalid PYZ payload",
            )
        code = marshal.loads(raw)
        require(isinstance(code, CodeType), "invalid PYZ code")
        codes[name] = code
        rows.append((name, filename, "PYMODULE"))
    require(owned > 0, "missing ATC modules")
    ZlibArchiveWriter(str(destination), rows, codes)
    return destination.read_bytes()


def read_source(root: Path, commit: str) -> dict[str, bytes]:
    data = git(
        root,
        "archive",
        "--format=tar",
        commit,
        SRC + "allthecontext",
        "pyproject.toml",
        *(f"scripts/{entry}.py" for entry in ENTRYPOINTS.values()),
    )
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        source = {}
        for member in archive.getmembers():
            require(member.isfile() or member.isdir(), "unsupported source member")
            if member.isfile():
                stream = archive.extractfile(member)
                require(stream is not None, "unreadable source member")
                with stream:
                    source[member.name] = stream.read()
        return source


def build_arm(
    root: Path,
    commit: str,
    donor: Path,
    build: Path,
    dist: Path,
    *,
    witness_profile: bool = False,
    donor_descriptor: pilot.RollingDonor | None = None,
) -> None:
    from PyInstaller.building.utils import replace_filename_in_code_object
    from PyInstaller.utils.win32 import versioninfo

    if __package__:
        from scripts import verify_reproducible_build as verifier
    else:
        import verify_reproducible_build as verifier

    build_desktop = verifier.build_desktop
    validate_abi(root)
    if witness_profile:
        if __package__:
            from scripts.windows_ci_canonical import install
        else:
            from windows_ci_canonical import install
        install()
    require(os.environ.get("PYTHONHASHSEED") == "0", "non-deterministic compilation environment")
    verifier._fresh_directory(build, label="pilot build root")
    verifier._fresh_directory(dist, label="pilot dist root")
    source = read_source(root, commit)
    version = verifier.project_version(root)
    identity_path, identity = build_desktop._write_build_identity(
        build_root=build,
        version=version,
        source_commit=commit,
        system="Windows",
        architecture="x86_64",
    )
    outputs = build_desktop.component_paths(build_root=build, dist_root=dist)
    donor_rows = None
    if donor_descriptor:
        require(witness_profile, "rolling donor requires current stock witness")
        payload = pilot.verify_donor(donor, pilot.donor_component_paths(donor), donor_descriptor)
        require(
            payload["contract"]["locks"] == verifier.lock_digests(root)
            and payload["contract"]["toolchain"] == verifier.current_toolchain(),
            "donor lock or toolchain mismatch",
        )
        donor_rows = {row["role"]: row for row in payload["components"]}
    helpers = {}
    for role, filename in ROLE_NAMES.items():
        path = provenance._regular_file(donor / filename, label="worker donor")
        data = path.read_bytes()
        require(
            hashlib.sha256(data).hexdigest()
            == (donor_rows[role]["sha256"] if donor_rows else DONOR_HASHES[role])
            and (donor_rows is None or len(data) == donor_rows[role]["size"]),
            "donor role bytes changed",
        )
        archive = Archive(data)
        old_identity = json.loads(archive.entries[IDENTITY].raw)
        require(
            old_identity
            == {
                **identity.as_dict(),
                "source_commit": donor_descriptor.source if donor_descriptor else SOURCE,
            },
            "donor identity mismatch",
        )
        entry = ENTRYPOINTS[role]
        require(
            archive.entries[entry].kind == "s"
            and archive.entries["PYZ.pyz"].kind == "z"
            and archive.entries["PYZ.pyz"].flag == 0,
            "donor role layout mismatch",
        )
        original = compile(
            source[f"scripts/{entry}.py"],
            f"scripts/{entry}.py",
            "exec",
            dont_inherit=True,
            optimize=0,
        )
        changes = {
            IDENTITY: identity_path.read_bytes(),
            "PYZ.pyz": (
                canonical_pyz(archive.entries["PYZ.pyz"].raw, source, build / f"{role}.pyz")
                if witness_profile
                else compile_pyz(archive.entries["PYZ.pyz"].raw, source)
            ),
            entry: marshal.dumps(replace_filename_in_code_object(original, f"{entry}.py")),
        }
        if witness_profile:
            changes.update(installed_metadata(root, archive.entries))
            for name, row in archive.entries.items():
                if row.kind in ("s", "m", "M"):
                    code = (
                        replace_filename_in_code_object(original, f"{entry}.py")
                        if name == entry
                        else marshal.loads(row.raw)
                    )
                    require(isinstance(code, CodeType), "invalid archive code member")
                    changes[name] = marshal.dumps(code, 2)
        for name in archive.entries:
            if name.startswith("allthecontext\\") and name != IDENTITY:
                changes[name] = source[SRC + name.replace("\\", "/")]
        if role == "main":
            require(set(helpers) == set(ROLE_NAMES.values()) - {filename}, "missing own helpers")
            changes.update(helpers)
        resource = build_desktop._windows_version_file(
            build_root=build, identity=identity, component=Path(filename).stem
        )
        prefix = build / f"{role}-prefix.dat"
        prefix.write_bytes(archive.prefix)
        info = versioninfo.load_version_info_from_text_file(str(resource))
        versioninfo.write_version_info_to_executable(str(prefix), info)
        output = outputs[role]
        output.parent.mkdir(parents=True, exist_ok=True)
        compose(output, archive, prefix.read_bytes(), changes)
        verify_version_resource(output, info.toRaw())
        # Read back every member, including each arm's own linked helpers.
        current = Archive(output.read_bytes())
        require(list(current.entries) == list(archive.entries), "component inventory changed")
        for name, prior in archive.entries.items():
            observed = current.entries[name]
            require(
                (observed.kind, observed.flag, observed.raw)
                == (prior.kind, prior.flag, changes.get(name, prior.raw)),
                "component member mismatch",
            )
        if role != "main":
            helpers[filename] = output.read_bytes()


def verify_version_resource(path: Path, expected: bytes) -> None:
    from pefile import PE

    with PE(str(path), fast_load=True) as pe:
        pe.parse_data_directories(directories=[2])
        versions = [entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries if entry.id == 16]
        raw = [
            pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
            for version in versions
            for entry in version.directory.entries
            for language in entry.directory.entries
        ]
    require(raw == [expected], "rebuilt PE version identity mismatch")


def component_runner(
    root: Path,
    commit: str,
    donor_paths: dict[str, Path],
    *,
    witness_profile: bool = False,
    donor_descriptor: pilot.RollingDonor | None = None,
):
    if __package__:
        from scripts import verify_reproducible_build as verifier
    else:
        import verify_reproducible_build as verifier

    validate_abi(root)
    donor = donor_paths["main"].parent

    def run(_source: Path, build: Path, dist: Path):
        command = [
            sys.executable,
            "-s",
            "-B",
            str(Path(__file__).resolve()),
            "--root",
            str(root),
            "--commit",
            commit,
            "--donor",
            str(donor),
            "--build",
            str(build),
            "--dist",
            str(dist),
        ]
        if witness_profile:
            command.append("--witness-profile")
        if donor_descriptor:
            command.extend(["--donor-descriptor", donor_descriptor.raw().decode("ascii")])
        completed = subprocess.run(
            command, cwd=root, check=False, env=verifier.reproducible_environment(config_root=build)
        )
        require(completed.returncode == 0, "admitted Windows CI component build failed")
        return provenance.collect_snapshot(
            verifier.build_desktop.component_paths(build_root=build, dist_root=dist),
            label="pilot-build",
        )

    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("root", "donor", "build", "dist"):
        parser.add_argument(f"--{option}", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--witness-profile", action="store_true")
    parser.add_argument("--donor-descriptor")
    args = parser.parse_args()
    build_arm(
        args.root,
        args.commit,
        args.donor,
        args.build,
        args.dist,
        witness_profile=args.witness_profile,
        donor_descriptor=pilot.RollingDonor.parse(args.donor_descriptor.encode("ascii"))
        if args.donor_descriptor
        else None,
    )


if __name__ == "__main__":
    main()

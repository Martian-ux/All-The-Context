"""Provider-neutral authenticated portable export and restore."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"ATCEXP1\x00"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
MAX_RESTORE_ENTRY_BYTES = 512 * 1024 * 1024
EXCLUDED_TABLES = {
    "schema_migrations",
    "context_fts",
    "context_fts_data",
    "context_fts_idx",
    "context_fts_docsize",
    "context_fts_config",
}


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 10:
        raise ValueError("export passphrase must contain at least 10 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(str(row[0]) for row in rows if str(row[0]) not in EXCLUDED_TABLES)


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    return value


def _database_to_zip(
    database_path: Path,
    zip_path: Path,
    *,
    include_sources: bool,
    include_audit: bool,
) -> dict[str, Any]:
    hashes_by_file: dict[str, str] = {}
    counts: dict[str, int] = {}
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for table in _table_names(connection):
                lowered = table.casefold()
                if not include_sources and ("source" in lowered or "blob" in lowered):
                    continue
                if not include_audit and "audit" in lowered:
                    continue
                columns = [
                    str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
                ]
                digest = hashlib.sha256()
                count = 0
                with archive.open(f"tables/{table}.jsonl", "w") as output:
                    for row in connection.execute(f'SELECT * FROM "{table}"'):
                        document = {column: _json_value(row[column]) for column in columns}
                        encoded = (
                            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
                        ).encode("utf-8")
                        output.write(encoded)
                        digest.update(encoded)
                        count += 1
                hashes_by_file[f"tables/{table}.jsonl"] = digest.hexdigest()
                counts[table] = count
            manifest = {
                "format": "all-the-context",
                "format_version": 1,
                "schema_version": 1,
                "include_sources": include_sources,
                "include_audit": include_audit,
                "tables": counts,
                "sha256": hashes_by_file,
            }
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
    finally:
        connection.close()
    return manifest


def _encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(passphrase, salt)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        outgoing.write(MAGIC + salt + nonce)
        for chunk in iter(lambda: incoming.read(CHUNK_SIZE), b""):
            outgoing.write(encryptor.update(chunk))
        outgoing.write(encryptor.finalize())
        outgoing.write(encryptor.tag)


def create_export(
    database_path: Path,
    destination: Path,
    passphrase: str,
    *,
    include_sources: bool = False,
    include_audit: bool = False,
) -> dict[str, Any]:
    """Create an encrypted portable package without placing plaintext beside it."""
    database_path = database_path.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atc-export-") as temporary:
        archive_path = Path(temporary) / "payload.zip"
        manifest = _database_to_zip(
            database_path,
            archive_path,
            include_sources=include_sources,
            include_audit=include_audit,
        )
        _encrypt_file(archive_path, destination, passphrase)
    return manifest


def _decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    total = source.stat().st_size
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE
    if total <= header_size + TAG_SIZE:
        raise ValueError("invalid or truncated export")
    with source.open("rb") as incoming:
        if incoming.read(len(MAGIC)) != MAGIC:
            raise ValueError("not an All The Context encrypted export")
        salt = incoming.read(SALT_SIZE)
        nonce = incoming.read(NONCE_SIZE)
        incoming.seek(-TAG_SIZE, os.SEEK_END)
        tag = incoming.read(TAG_SIZE)
        incoming.seek(header_size)
        remaining = total - header_size - TAG_SIZE
        decryptor = Cipher(
            algorithms.AES(_derive_key(passphrase, salt)), modes.GCM(nonce, tag)
        ).decryptor()
        with destination.open("wb") as outgoing:
            while remaining:
                chunk = incoming.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ValueError("truncated encrypted payload")
                remaining -= len(chunk)
                outgoing.write(decryptor.update(chunk))
            outgoing.write(decryptor.finalize())


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$bytes"}:
        return base64.b64decode(value["$bytes"], validate=True)
    return value


def _iter_jsonl(stream: IO[bytes]) -> Iterable[dict[str, Any]]:
    for line in stream:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("portable table row must be a JSON object")
        yield {key: _decode_value(item) for key, item in value.items()}


def restore_export(
    source: Path,
    database_path: Path,
    passphrase: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and duplicate-safely restore known rows into a migrated database."""
    source = source.resolve()
    database_path = database_path.resolve()
    with tempfile.TemporaryDirectory(prefix="atc-restore-") as temporary:
        archive_path = Path(temporary) / "payload.zip"
        _decrypt_file(source, archive_path, passphrase)
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if any(
                info.file_size > MAX_RESTORE_ENTRY_BYTES
                or Path(info.filename).is_absolute()
                or ".." in Path(info.filename).parts
                for info in infos
            ):
                raise ValueError("unsafe or oversized export entry")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "all-the-context" or manifest.get("format_version") != 1:
                raise ValueError("unsupported export format")
            for name, expected in manifest.get("sha256", {}).items():
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                if actual != expected:
                    raise ValueError(f"integrity check failed for {name}")
            if dry_run:
                return {"valid": True, "dry_run": True, "manifest": manifest}
            connection = sqlite3.connect(database_path)
            try:
                existing = set(_table_names(connection))
                with connection:
                    for table in manifest.get("tables", {}):
                        if table not in existing:
                            continue
                        name = f"tables/{table}.jsonl"
                        with archive.open(name) as stream:
                            for row in _iter_jsonl(stream):
                                columns = list(row)
                                quoted = ",".join(f'"{column}"' for column in columns)
                                placeholders = ",".join("?" for _ in columns)
                                connection.execute(
                                    (
                                        f'INSERT OR IGNORE INTO "{table}" ({quoted}) '
                                        f"VALUES ({placeholders})"
                                    ),
                                    [row[column] for column in columns],
                                )
            finally:
                connection.close()
    return {"valid": True, "dry_run": False, "manifest": manifest}

from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.release_manifest import (
    MAX_KEYRING_BYTES,
    ManifestError,
    public_key_fingerprint,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.release_keyring import (
    MAX_AUDIT_FILE_BYTES,
    MAX_PUBLIC_KEY_BYTES,
    _read_bounded_file,
    _read_bounded_keyring_bytes,
    audit_private_key_material,
    contains_private_key_block,
    import_reviewed_public_key,
    load_reviewable_public_key,
    reviewed_entry,
    validate_keyring_pair,
)

TEST_ONLY_SEED = bytes(reversed(range(32)))


def _public_key(path: Path) -> Path:
    public = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED).public_key()
    path.write_bytes(
        public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return path


def _empty_keyrings(tmp_path: Path) -> tuple[Path, Path]:
    value = '{"schema_version": 1, "keys": []}\n'
    operator = tmp_path / "keys.json"
    packaged = tmp_path / "update_keys.json"
    operator.write_text(value, encoding="utf-8")
    packaged.write_text(value, encoding="utf-8")
    return operator, packaged


def test_reviewed_public_key_import_updates_both_trust_stores(tmp_path: Path) -> None:
    public_path = _public_key(tmp_path / "release.pub.pem")
    operator, packaged = _empty_keyrings(tmp_path)
    preliminary = reviewed_entry(
        public_path,
        key_id="release-test-2026",
        channels=["beta"],
        expected_fingerprint=(
            "sha256:141ddf2e77d4f690748cf74ecd390d44687d477b31b8931fa37abd02c35dbaba"
        ),
    )

    imported = import_reviewed_public_key(
        public_path,
        key_id="release-test-2026",
        channels=["beta"],
        expected_fingerprint=preliminary["public_key_sha256"],
        operator_path=operator,
        packaged_path=packaged,
    )

    assert imported == preliminary
    assert operator.read_bytes() == packaged.read_bytes()
    keyring = validate_keyring_pair(operator, packaged, required_channel="beta")
    assert keyring["keys"] == [preliminary]
    assert public_key_fingerprint(imported["public_key"]) == imported["public_key_sha256"]


def test_public_key_import_fails_closed_on_unreviewed_or_private_material(tmp_path: Path) -> None:
    public_path = _public_key(tmp_path / "release.pub.pem")
    with pytest.raises(ManifestError, match="fingerprint"):
        reviewed_entry(
            public_path,
            key_id="release-test-2026",
            channels=["beta"],
            expected_fingerprint=f"sha256:{'0' * 64}",
        )

    private_path = tmp_path / "forbidden-private.pem"
    private_path.write_bytes(
        Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(ManifestError, match="private key material is forbidden"):
        load_reviewable_public_key(private_path)
    with pytest.raises(ManifestError, match="tracked private-key material"):
        audit_private_key_material([private_path])


def test_public_key_reader_bounds_exact_multibyte_and_binary_input(tmp_path: Path) -> None:
    raw = b"\x00\xc3\xa9" + b" " * (MAX_PUBLIC_KEY_BYTES - 3)
    path = tmp_path / "public-key.bin"
    path.write_bytes(raw)

    assert (
        _read_bounded_file(
            path,
            maximum_bytes=MAX_PUBLIC_KEY_BYTES,
            oversize_message="public key file is empty or unreasonably large",
            unreadable_message="public key file could not be read safely",
        )
        == raw
    )


def test_public_key_reader_rejects_limit_plus_one_without_retaining_path(tmp_path: Path) -> None:
    path = tmp_path / "oversized-public-key.bin"
    path.write_bytes(b"\x00" * (MAX_PUBLIC_KEY_BYTES + 1))

    with pytest.raises(
        ManifestError, match="public key file is empty or unreasonably large"
    ) as exc:
        load_reviewable_public_key(path)
    assert str(path) not in str(exc.value)


def test_private_key_audit_bounds_exact_file_and_rejects_oversize(tmp_path: Path) -> None:
    exact = tmp_path / "exact.bin"
    exact.write_bytes(b"\x00\xc3\xa9" + b" " * (MAX_AUDIT_FILE_BYTES - 3))
    audit_private_key_material([exact])

    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * (MAX_AUDIT_FILE_BYTES + 1))
    with pytest.raises(ManifestError, match="tracked file exceeds the audit size limit") as exc:
        audit_private_key_material([oversized])
    assert str(oversized) not in str(exc.value)


def test_private_key_audit_allows_policy_text_but_detects_complete_blocks() -> None:
    marker_reference = b'policy = b"-----BEGIN ENCRYPTED PRIVATE KEY-----"'
    complete_block = (
        b"-----BEGIN "
        b"ENCRYPTED PRIVATE KEY-----\n"
        b"dGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5\n"
        b"-----END "
        b"ENCRYPTED PRIVATE KEY-----\n"
    )

    assert contains_private_key_block(marker_reference) is False
    assert contains_private_key_block(complete_block) is True


def test_keyring_pair_rejects_drift_and_fingerprint_tampering(tmp_path: Path) -> None:
    operator, packaged = _empty_keyrings(tmp_path)
    packaged.write_text(json.dumps({"schema_version": 1, "keys": []}), encoding="utf-8")
    with pytest.raises(ManifestError, match="byte-for-byte"):
        validate_keyring_pair(operator, packaged)

    packaged.write_bytes(operator.read_bytes())
    packaged.write_text('{"schema_version": 1, "keys": [{"bad": true}]}', encoding="utf-8")
    with pytest.raises(ManifestError):
        validate_keyring_pair(operator, packaged)


@pytest.mark.parametrize("schema_version", [True, 1.0, "1"])
def test_keyring_pair_rejects_non_integer_schema_version(
    tmp_path: Path, schema_version: object
) -> None:
    operator, packaged = _empty_keyrings(tmp_path)
    value = {"schema_version": schema_version, "keys": []}
    operator.write_text(json.dumps(value), encoding="utf-8")
    packaged.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ManifestError, match="schema"):
        validate_keyring_pair(operator, packaged)


def test_script_keyring_byte_reader_accepts_exact_multibyte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b'{"schema_version":1,"keys":[],"label":"\xc3\xa9"}'
    raw += b" " * (MAX_KEYRING_BYTES - len(raw))
    path = tmp_path / "keys.json"
    path.write_bytes(raw)
    read_sizes: list[int] = []
    original_open = Path.open

    class TrackingReader:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def __enter__(self) -> TrackingReader:
            self._handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self._handle.__exit__(*args)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self._handle.read(size)  # type: ignore[attr-defined,no-any-return]

    def tracking_open(target: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(target, *args, **kwargs)
        return TrackingReader(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracking_open)

    assert _read_bounded_keyring_bytes(path) == raw
    assert read_sizes == [MAX_KEYRING_BYTES + 1]


def test_keyring_pair_rejects_oversized_raw_comparison_input(tmp_path: Path) -> None:
    operator = tmp_path / "keys.json"
    packaged = tmp_path / "update_keys.json"
    oversized = b"{" + b" " * MAX_KEYRING_BYTES
    operator.write_bytes(oversized)
    packaged.write_bytes(oversized)

    with pytest.raises(ManifestError, match="size limit"):
        validate_keyring_pair(operator, packaged)


@pytest.mark.parametrize(
    "raw",
    [b"\xff", b'{"keys":' + b"[" * 7000 + b"0" + b"]" * 7000 + b"}"],
    ids=["invalid-utf8", "deep-json"],
)
def test_keyring_pair_contains_parser_failures(tmp_path: Path, raw: bytes) -> None:
    operator, packaged = _empty_keyrings(tmp_path)
    operator.write_bytes(raw)
    packaged.write_bytes(raw)

    with pytest.raises(ManifestError):
        validate_keyring_pair(operator, packaged)

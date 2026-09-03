from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from allthecontext.release_manifest import (
    MAX_KEYRING_BYTES,
    MAX_PRIVATE_KEY_BYTES,
    MAX_VERSION_COMPONENT_DIGITS,
    MAX_VERSION_COMPONENTS,
    MAX_VERSION_TEXT_LENGTH,
    ManifestError,
    ReleaseVersion,
    canonical_payload,
    create_manifest,
    load_keyring,
    load_private_key,
    public_key_fingerprint,
    public_key_value,
    read_private_key_bytes,
    verify_manifest,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import release_manifest as release_manifest_script
from scripts.release_manifest import (
    load_encrypted_private_key_interactive,
    require_private_key_outside_repository,
)

TEST_ONLY_SEED = bytes(range(32))
ROOT = Path(__file__).resolve().parents[2]


def _release(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    artifact = tmp_path / "all-the-context-0.2.0-windows-x86_64.zip"
    artifact.write_bytes(b"deterministic test-only release artifact\n")
    private_key = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    manifest = create_manifest(
        artifact=artifact,
        version="0.2.0",
        channel="stable",
        platform_name="windows",
        architecture="x86_64",
        artifact_url=(
            "https://github.com/example/all-the-context/releases/download/"
            "v0.2.0/all-the-context-0.2.0-windows-x86_64.zip"
        ),
        minimum_supported_version="0.1.0",
        mandatory=False,
        release_notes_url="https://github.com/example/all-the-context/releases/tag/v0.2.0",
        key_id="test-only-2026",
        private_key=private_key,
    )
    keyring = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "test-only-2026",
                "algorithm": "Ed25519",
                "public_key": public_key_value(private_key),
                "public_key_sha256": public_key_fingerprint(public_key_value(private_key)),
                "channels": ["stable", "beta"],
                "status": "active",
            }
        ],
    }
    return manifest, keyring


def test_manifest_is_deterministic_and_verifies(tmp_path: Path) -> None:
    manifest, keyring = _release(tmp_path)
    repeated, _ = _release(tmp_path)
    assert json.dumps(manifest, sort_keys=True) == json.dumps(repeated, sort_keys=True)
    verify_manifest(manifest, keyring, current_version="0.1.0", expected_channel="stable")


def test_release_version_parser_accepts_conservative_boundaries() -> None:
    exact_component = f"{'1' * MAX_VERSION_COMPONENT_DIGITS}.0.0"
    exact_text = (
        f"{'1' * MAX_VERSION_COMPONENT_DIGITS}."
        f"{'1' * MAX_VERSION_COMPONENT_DIGITS}."
        f"{'1' * MAX_VERSION_COMPONENT_DIGITS}-beta.11"
    )

    parsed = ReleaseVersion.parse(exact_component)
    assert parsed.major == int("1" * MAX_VERSION_COMPONENT_DIGITS)
    assert len(exact_text) == MAX_VERSION_TEXT_LENGTH
    assert ReleaseVersion.parse(exact_text).stability == 0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-version",
        "-1.2.3",
        "1.-2.3",
        "1.2.-3",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-beta.01",
    ],
)
def test_release_version_parser_rejects_malformed_values_without_echo(value: str) -> None:
    with pytest.raises(ManifestError, match=r"^invalid release version$") as raised:
        ReleaseVersion.parse(value)
    if value:
        assert value not in str(raised.value)


@pytest.mark.parametrize("value", [None, 123, b"1.2.3"])
def test_release_version_parser_rejects_non_string_values(value: object) -> None:
    with pytest.raises(ManifestError, match=r"^invalid release version$"):
        ReleaseVersion.parse(value)  # type: ignore[arg-type]


def test_release_version_parser_rejects_component_digit_and_text_overflow() -> None:
    component_overflow = f"{'1' * (MAX_VERSION_COMPONENT_DIGITS + 1)}.0.0"
    pathological_component = f"{'1' * 5_000}.0.0"
    text_overflow = (
        f"{'1' * MAX_VERSION_COMPONENT_DIGITS}."
        f"{'1' * MAX_VERSION_COMPONENT_DIGITS}."
        f"{'1' * MAX_VERSION_COMPONENT_DIGITS}-beta.111"
    )

    for value in (component_overflow, pathological_component, text_overflow):
        with pytest.raises(ManifestError, match=r"^invalid release version$") as raised:
            ReleaseVersion.parse(value)
        assert value not in str(raised.value)


def test_release_version_parser_rejects_too_many_components() -> None:
    value = "1.2.3-beta.1.2"
    assert value.count(".") + 1 == MAX_VERSION_COMPONENTS + 1

    with pytest.raises(ManifestError, match=r"^invalid release version$") as raised:
        ReleaseVersion.parse(value)
    assert value not in str(raised.value)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"{",
        b"[]",
        b'{"value":' + b"9" * 5000 + b"}",
        b'{"value":' + b"[" * 7000 + b"0" + b"]" * 7000 + b"}",
    ],
    ids=["invalid-utf8", "malformed", "non-object", "huge-integer", "deep-nesting"],
)
def test_keyring_loader_contains_bounded_parser_failures(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "keys.json"
    path.write_bytes(raw)

    with pytest.raises(ManifestError, match=r"(decoded safely|JSON object|size limit|schema)"):
        load_keyring(path)


def test_keyring_loader_rejects_oversized_input_without_reading_unbounded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "keys.json"
    path.write_bytes(b"{" + b" " * MAX_KEYRING_BYTES)

    with pytest.raises(ManifestError, match="size limit"):
        load_keyring(path)


def test_keyring_loader_reads_exact_limit_plus_one_and_accepts_multibyte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, keyring = _release(tmp_path)
    encoded = json.dumps(keyring, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    raw = encoded + b" " * (MAX_KEYRING_BYTES - len(encoded))
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

        def fileno(self) -> int:
            return self._handle.fileno()  # type: ignore[attr-defined,no-any-return]

    def tracking_open(target: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(target, *args, **kwargs)
        return TrackingReader(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracking_open)

    assert load_keyring(path) == keyring
    assert len(raw) == MAX_KEYRING_BYTES
    assert read_sizes == [MAX_KEYRING_BYTES + 1]


def test_keyring_loader_contains_multibyte_json_at_exact_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = '{"schema_version":1,"keys":[],"extra":"é"}'.encode()
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

        def fileno(self) -> int:
            return self._handle.fileno()  # type: ignore[attr-defined,no-any-return]

    def tracking_open(target: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(target, *args, **kwargs)
        return TrackingReader(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracking_open)
    with pytest.raises(ManifestError, match="schema"):
        load_keyring(path)
    assert read_sizes == [MAX_KEYRING_BYTES + 1]


def test_keyring_loader_rejects_nonregular_and_symlink_paths(tmp_path: Path) -> None:
    directory = tmp_path / "keys-directory"
    directory.mkdir()
    with pytest.raises(ManifestError, match="trusted plain file"):
        load_keyring(directory)

    target = tmp_path / "outside-keys.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "keys-link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this filesystem")
    with pytest.raises(ManifestError, match="trusted plain file"):
        load_keyring(link)
    assert target.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("control_exception", [SystemExit, KeyboardInterrupt, GeneratorExit])
def test_keyring_loader_does_not_swallow_process_control_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_exception: type[BaseException],
) -> None:
    _, keyring = _release(tmp_path)
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(keyring), encoding="utf-8")

    def fail(_value: str) -> object:
        raise control_exception("sentinel")

    monkeypatch.setattr("allthecontext.release_manifest.json.loads", fail)
    with pytest.raises(control_exception):
        load_keyring(path)


def test_keyring_loader_does_not_swallow_unexpected_programming_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, keyring = _release(tmp_path)
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(keyring), encoding="utf-8")

    def fail(_value: str) -> object:
        raise RuntimeError("programming failure")

    monkeypatch.setattr("allthecontext.release_manifest.json.loads", fail)
    with pytest.raises(RuntimeError, match="programming failure"):
        load_keyring(path)


def test_private_key_reader_accepts_exact_limit(tmp_path: Path) -> None:
    path = tmp_path / "private.pem"
    raw = b"x" * MAX_PRIVATE_KEY_BYTES
    path.write_bytes(raw)

    assert read_private_key_bytes(path) == raw


def test_private_key_loader_rejects_limit_plus_one(tmp_path: Path) -> None:
    path = tmp_path / "private.pem"
    path.write_bytes(b"x" * (MAX_PRIVATE_KEY_BYTES + 1))

    with pytest.raises(ManifestError, match=r"^private key file exceeds the size limit$"):
        load_private_key(path)


def test_private_key_loader_rejects_empty_input(tmp_path: Path) -> None:
    path = tmp_path / "private.pem"
    path.write_bytes(b"")

    with pytest.raises(ManifestError, match=r"^private key file is empty$"):
        load_private_key(path)


def test_private_key_loader_accepts_ed25519_pem(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    path = tmp_path / "private.pem"
    path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    assert public_key_value(load_private_key(path)) == public_key_value(private)


@pytest.mark.parametrize(
    "raw",
    [b"not a PEM key", b"\x00\xffbinary", "multibyte-\N{SNOWMAN}".encode("utf-8")],
    ids=["invalid", "binary", "multibyte"],
)
def test_private_key_loader_rejects_invalid_input(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "private.pem"
    path.write_bytes(raw)

    with pytest.raises(
        ManifestError,
        match=r"^private key is not a valid PEM Ed25519 key for the supplied password$",
    ):
        load_private_key(path)


def test_private_key_loader_reads_once_with_bounded_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    path = tmp_path / "private.pem"
    path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
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

        def fileno(self) -> int:
            return self._handle.fileno()  # type: ignore[attr-defined,no-any-return]

    def tracking_open(target: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(target, *args, **kwargs)
        return TrackingReader(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracking_open)

    load_private_key(path)

    assert read_sizes == [MAX_PRIVATE_KEY_BYTES + 1]


def test_packaged_update_keyring_matches_operator_keyring() -> None:
    operator = json.loads((ROOT / "release" / "keys.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        (
            ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "update_keys.json"
        ).read_text(encoding="utf-8")
    )
    assert packaged == operator


def test_tamper_revocation_and_downgrade_are_rejected(tmp_path: Path) -> None:
    manifest, keyring = _release(tmp_path)
    tampered = {**manifest, "mandatory": True}
    with pytest.raises(ManifestError, match="signature"):
        verify_manifest(tampered, keyring)
    revoked = json.loads(json.dumps(keyring))
    revoked["keys"][0]["status"] = "revoked"
    with pytest.raises(ManifestError, match="revoked"):
        verify_manifest(manifest, revoked)
    with pytest.raises(ManifestError, match="downgrade"):
        verify_manifest(manifest, keyring, current_version="0.3.0")
    requires_manual = {**manifest, "minimum_supported_version": "0.1.1"}
    private_key = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    requires_manual["signature"] = (
        base64.urlsafe_b64encode(private_key.sign(canonical_payload(requires_manual)))
        .rstrip(b"=")
        .decode("ascii")
    )
    with pytest.raises(ManifestError, match="manual supported"):
        verify_manifest(requires_manual, keyring, current_version="0.1.0")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/all-the-context/releases/latest/download/app.zip",
        "https://raw.githubusercontent.com/example/all-the-context/main/app.zip",
        "http://downloads.example.test/v0.2.0/app.zip",
    ],
)
def test_mutable_or_insecure_artifact_urls_are_rejected(tmp_path: Path, url: str) -> None:
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"artifact")
    with pytest.raises(ManifestError):
        create_manifest(
            artifact=artifact,
            version="0.2.0",
            channel="stable",
            platform_name="linux",
            architecture="x86_64",
            artifact_url=url,
            minimum_supported_version="0.1.0",
            mandatory=False,
            release_notes_url="https://example.test/releases/v0.2.0",
            key_id="test-only-2026",
            private_key=Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED),
        )


def test_offline_signing_key_must_resolve_outside_checkout(tmp_path: Path) -> None:
    repository = tmp_path / "checkout"
    repository.mkdir()
    inside = repository / "release-private.pem"
    outside = tmp_path / "offline-private.pem"
    inside.write_text("test-only", encoding="utf-8")
    outside.write_text("test-only", encoding="utf-8")

    with pytest.raises(ManifestError, match="outside"):
        require_private_key_outside_repository(inside, repository)
    assert require_private_key_outside_repository(outside, repository) == outside.resolve()


def test_offline_signing_loads_password_protected_key_with_no_echo_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "test-only-password"
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    encrypted = tmp_path / "encrypted-private.pem"
    encrypted.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
        )
    )
    prompts: list[str] = []
    monkeypatch.setattr(release_manifest_script.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        release_manifest_script.getpass,
        "getpass",
        lambda prompt: prompts.append(prompt) or password,
    )

    loaded = load_encrypted_private_key_interactive(encrypted)

    assert public_key_value(loaded) == public_key_value(private)
    assert prompts == ["Offline release key password: "]


def test_offline_signing_reads_key_once_with_bounded_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = "test-only-password"
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    encrypted = tmp_path / "encrypted-private.pem"
    encrypted.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
        )
    )
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

        def fileno(self) -> int:
            return self._handle.fileno()  # type: ignore[attr-defined,no-any-return]

    def tracking_open(target: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(target, *args, **kwargs)
        return TrackingReader(handle) if target == encrypted else handle

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(release_manifest_script.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(release_manifest_script.getpass, "getpass", lambda _prompt: password)

    loaded = load_encrypted_private_key_interactive(encrypted)

    assert public_key_value(loaded) == public_key_value(private)
    assert read_sizes == [MAX_PRIVATE_KEY_BYTES + 1]


def test_offline_signing_rejects_plaintext_key_and_noninteractive_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    plaintext = tmp_path / "plaintext-private.pem"
    plaintext.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(ManifestError) as error:
        load_encrypted_private_key_interactive(plaintext)
    assert str(error.value) == "offline release signing requires an encrypted PKCS8 PEM private key"
    assert str(plaintext) not in str(error.value)

    encrypted = tmp_path / "encrypted-private.pem"
    encrypted.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(b"test-only-password"),
        )
    )
    monkeypatch.setattr(release_manifest_script.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ManifestError, match="interactive terminal"):
        load_encrypted_private_key_interactive(encrypted)

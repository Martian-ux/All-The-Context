"""Create or verify a signed All The Context OTA manifest."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from allthecontext.release_manifest import (
    ManifestError,
    create_manifest,
    load_keyring,
    load_private_key,
    verify_manifest,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require_private_key_outside_repository(path: Path, repository_root: Path) -> Path:
    """Reject a signing key located anywhere in the source checkout."""

    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(repository_root.resolve(strict=True))
    except ValueError:
        return resolved
    raise ManifestError("release signing private key must remain outside the repository")


def load_encrypted_private_key_interactive(path: Path) -> Ed25519PrivateKey:
    """Load an encrypted PKCS8 key using a no-echo terminal prompt only."""

    value = path.read_bytes()
    if b"-----BEGIN ENCRYPTED PRIVATE KEY-----" not in value:
        raise ManifestError("offline release signing requires an encrypted PKCS8 PEM private key")
    if not sys.stdin.isatty():
        raise ManifestError("an interactive terminal is required for the private-key password")
    password_text = getpass.getpass("Offline release key password: ")
    if not password_text:
        raise ManifestError("private-key password cannot be empty")
    password = password_text.encode("utf-8")
    password_text = ""
    try:
        return load_private_key(path, password=password)
    finally:
        password = b""


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="hash an artifact and sign its manifest")
    create.add_argument("--artifact", type=Path, required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--channel", choices=("stable", "beta"), required=True)
    create.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    create.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    create.add_argument("--url", required=True)
    create.add_argument("--minimum-supported-version", required=True)
    create.add_argument("--mandatory", action="store_true")
    create.add_argument("--release-notes-url", required=True)
    create.add_argument("--key-id", required=True)
    create.add_argument("--private-key", type=Path, required=True)
    create.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify signature, trust, channel, and policy")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--keyring", type=Path, required=True)
    verify.add_argument("--artifact", type=Path)
    verify.add_argument("--current-version")
    verify.add_argument("--channel", choices=("stable", "beta"))
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "create":
            private_key_path = require_private_key_outside_repository(
                arguments.private_key,
                arguments.repository_root,
            )
            manifest = create_manifest(
                artifact=arguments.artifact,
                version=arguments.version,
                channel=arguments.channel,
                platform_name=arguments.platform,
                architecture=arguments.architecture,
                artifact_url=arguments.url,
                minimum_supported_version=arguments.minimum_supported_version,
                mandatory=arguments.mandatory,
                release_notes_url=arguments.release_notes_url,
                key_id=arguments.key_id,
                private_key=load_encrypted_private_key_interactive(private_key_path),
            )
            _write_json(arguments.output, manifest)
            return 0
        manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        verify_manifest(
            manifest,
            load_keyring(arguments.keyring),
            current_version=arguments.current_version,
            expected_channel=arguments.channel,
        )
        if arguments.artifact is not None:
            from allthecontext.release_manifest import sha256_file

            digest, size = sha256_file(arguments.artifact)
            if digest != manifest["sha256"] or size != manifest["size"]:
                raise ManifestError("local artifact does not match signed digest and size")
        print(
            f"verified {manifest['channel']} {manifest['version']} {manifest['platform']}/"
            f"{manifest['architecture']} with {manifest['key_id']}"
        )
        return 0
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"release manifest error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())

"""OS credential-storage abstraction with an explicit development fallback."""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import keyring
from filelock import FileLock
from keyring.errors import KeyringError

OS_CREDENTIAL_STORAGE = "operating-system credential store"
FALLBACK_CREDENTIAL_STORAGE = "insecure development credential file"
DEVELOPMENT_FALLBACK_ENV = "ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE"
SECRET_REFERENCE_PREFIX = "atc-secret-ref-v1:"
_SECRET_REFERENCE_RE = re.compile(
    rf"^{re.escape(SECRET_REFERENCE_PREFIX)}[0-9a-f]{{8}}-"
    rf"[0-9a-f]{{4}}-4[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$"
)
MAX_SECRET_REFERENCE_VALUE_CHARS = 1_000_000


class CredentialStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class KeyringCredentialStore:
    """Windows Credential Manager, macOS Keychain, or Linux secret service."""

    def __init__(self, service_name: str = "All The Context") -> None:
        self.service_name = service_name

    def get(self, name: str) -> str | None:
        try:
            return keyring.get_password(self.service_name, name)
        except KeyringError as exc:
            raise RuntimeError("the operating-system credential store is unavailable") from exc

    def set(self, name: str, value: str) -> None:
        try:
            keyring.set_password(self.service_name, name, value)
        except KeyringError as exc:
            raise RuntimeError("the operating-system credential store is unavailable") from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(self.service_name, name)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as exc:
            raise RuntimeError("the operating-system credential store is unavailable") from exc


class LocalSecretReferenceVault:
    """Store raw operational secrets only behind an OS credential reference.

    The reference, never the value, is suitable for application metadata. A
    plaintext development store is deliberately rejected even when the global
    development fallback is enabled.
    """

    def __init__(
        self,
        credential_store: CredentialStore | None = None,
        *,
        service_name: str = "All The Context secret references",
    ) -> None:
        store = credential_store or KeyringCredentialStore(service_name)
        if not isinstance(store, KeyringCredentialStore):
            raise RuntimeError("secret references require an operating-system credential store")
        self._store = store

    @staticmethod
    def _reference(value: str) -> str:
        if type(value) is not str or _SECRET_REFERENCE_RE.fullmatch(value) is None:
            raise ValueError("invalid secret reference")
        return value

    def put(self, value: str) -> str:
        if type(value) is not str or not value or len(value) > MAX_SECRET_REFERENCE_VALUE_CHARS:
            raise ValueError("secret value is outside its bound")
        reference = SECRET_REFERENCE_PREFIX + str(uuid4())
        self._store.set(reference, value)
        return reference

    def get(self, reference: str) -> str | None:
        value = self._store.get(self._reference(reference))
        if value is not None and type(value) is not str:
            raise RuntimeError("operating-system credential store returned an invalid value")
        return value

    def delete(self, reference: str) -> None:
        self._store.delete(self._reference(reference))


SecretReferenceVault = LocalSecretReferenceVault


class DevelopmentFileCredentialStore:
    """Plaintext fallback for tests/development; never silently selected."""

    warning = "INSECURE DEVELOPMENT CREDENTIAL STORE: values are stored as plaintext"

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def _lock(self) -> FileLock:
        return FileLock(str(self.path.with_suffix(self.path.suffix + ".lock")), timeout=5)

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
        ):
            raise RuntimeError("invalid development credential file")
        return value

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, name: str) -> str | None:
        with self._lock():
            return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        with self._lock():
            values = self._read()
            values[name] = value
            self._write(values)

    def delete(self, name: str) -> None:
        with self._lock():
            if not self.path.exists():
                return
            values = self._read()
            values.pop(name, None)
            if values:
                self._write(values)
            else:
                self.path.unlink(missing_ok=True)


def development_file_credentials_enabled() -> bool:
    """Return whether the operator deliberately enabled the plaintext development store."""

    return os.environ.get(DEVELOPMENT_FALLBACK_ENV, "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


def require_development_file_credentials() -> None:
    """Fail closed unless the insecure development-only store was explicitly enabled."""

    if not development_file_credentials_enabled():
        raise RuntimeError(
            "the operating-system credential store is unavailable; plaintext credential "
            f"storage is disabled (development only: set {DEVELOPMENT_FALLBACK_ENV}=1)"
        )


def verify_isolated_os_credential_round_trip() -> None:
    """Set, read, and remove one unique test credential without exposing its value."""

    credential_name = f"acceptance:{secrets.token_hex(16)}"
    value = secrets.token_urlsafe(48)
    store = KeyringCredentialStore(
        service_name=f"All The Context packaging acceptance {secrets.token_hex(12)}"
    )
    stored = False
    try:
        store.set(credential_name, value)
        stored = True
        if store.get(credential_name) != value:
            raise RuntimeError("the operating-system credential did not round trip")
        store.delete(credential_name)
        if store.get(credential_name) is not None:
            raise RuntimeError("the operating-system credential was not deleted")
        stored = False
    finally:
        if stored:
            store.delete(credential_name)

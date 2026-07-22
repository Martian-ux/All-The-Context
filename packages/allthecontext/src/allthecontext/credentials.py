"""OS credential-storage abstraction with an explicit development fallback."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Protocol

import keyring
from filelock import FileLock
from keyring.errors import KeyringError

OS_CREDENTIAL_STORAGE = "operating-system credential store"
FALLBACK_CREDENTIAL_STORAGE = "local app-data fallback"


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
            values = self._read()
            values.pop(name, None)
            self._write(values)

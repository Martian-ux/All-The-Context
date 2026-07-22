"""Core-side hosted Edge enrollment, pairing, and background synchronization."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import quote

import httpx
from filelock import FileLock

from .config import CoreConfig
from .credentials import DevelopmentFileCredentialStore, KeyringCredentialStore
from .edge_setup import (
    EdgeEnrollmentBundle,
    EdgeSetupError,
    generate_enrollment,
    normalize_edge_url,
    proof_matches,
)
from .storage import CoreStore
from .sync import CoreRelaySync
from .sync import HttpClientLike as SyncHttpClientLike

LEGACY_EDGE_CREDENTIAL_NAME = "edge:connection:v1"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ResponseLike(Protocol):
    status_code: int

    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class HttpClientLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def post(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def delete(self, url: str, **kwargs: Any) -> ResponseLike: ...


@dataclass(frozen=True, slots=True)
class EdgeState:
    vault_id: str
    prepared_at: str
    credential_storage: str
    edge_url: str | None = None
    connected_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    last_sequence: int = 0
    proposals_imported: int = 0

    @classmethod
    def from_mapping(cls, value: object) -> EdgeState:
        if not isinstance(value, dict):
            raise RuntimeError("Edge state must be a JSON object")
        expected = {
            "vault_id",
            "prepared_at",
            "credential_storage",
            "edge_url",
            "connected_at",
            "last_success_at",
            "last_error",
            "last_sequence",
            "proposals_imported",
        }
        if set(value) != expected:
            raise RuntimeError("Edge state fields do not match version 1")
        return cls(
            vault_id=str(value["vault_id"]),
            prepared_at=str(value["prepared_at"]),
            credential_storage=str(value["credential_storage"]),
            edge_url=str(value["edge_url"]) if value["edge_url"] is not None else None,
            connected_at=(
                str(value["connected_at"]) if value["connected_at"] is not None else None
            ),
            last_success_at=(
                str(value["last_success_at"]) if value["last_success_at"] is not None else None
            ),
            last_error=str(value["last_error"]) if value["last_error"] is not None else None,
            last_sequence=int(value["last_sequence"]),
            proposals_imported=int(value["proposals_imported"]),
        )


@dataclass(frozen=True, slots=True)
class EdgeMaterial:
    bundle: EdgeEnrollmentBundle
    recovery_code: str
    credential_storage: str


class EdgeConnectionStore:
    """Persist public state in app data and secrets in the OS credential store."""

    def __init__(self, config: CoreConfig) -> None:
        self.config = config
        self.path = config.data_dir / "edge.json"
        self.lock = FileLock(str(config.data_dir / "edge.lock"), timeout=5)
        self.fallback = DevelopmentFileCredentialStore(
            config.data_dir / "edge-credentials.development.json"
        )
        namespace = hashlib.sha256(str(config.data_dir.resolve()).casefold().encode()).hexdigest()
        self.credential_name = f"edge:connection:v1:{namespace[:20]}"

    def state(self) -> EdgeState | None:
        with self.lock:
            if not self.path.is_file():
                return None
            try:
                return EdgeState.from_mapping(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError("stored Edge connection state is invalid") from exc

    def save_state(self, state: EdgeState) -> None:
        with self.lock:
            self.config.prepare()
            temporary = self.path.with_name(f"{self.path.name}.{secrets.token_hex(6)}.tmp")
            try:
                temporary.write_text(
                    json.dumps(asdict(state), sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def _read_secret(self) -> tuple[str | None, str]:
        try:
            keyring = KeyringCredentialStore()
            value = keyring.get(self.credential_name)
            legacy = keyring.get(LEGACY_EDGE_CREDENTIAL_NAME) if not value else None
        except RuntimeError:
            value = None
            legacy = None
        if value:
            return value, "operating-system credential store"
        if legacy:
            storage = self._write_secret(legacy)
            with suppress(RuntimeError):
                keyring.delete(LEGACY_EDGE_CREDENTIAL_NAME)
            return legacy, storage
        value = self.fallback.get(self.credential_name)
        legacy = self.fallback.get(LEGACY_EDGE_CREDENTIAL_NAME) if not value else None
        if legacy:
            self.fallback.set(self.credential_name, legacy)
            self.fallback.delete(LEGACY_EDGE_CREDENTIAL_NAME)
            value = legacy
        return value, "local app-data fallback"

    def _write_secret(self, value: str) -> str:
        keyring = KeyringCredentialStore()
        try:
            previous = keyring.get(self.credential_name)
        except RuntimeError:
            # Do not attempt a write after an availability probe fails. That
            # keeps the development fallback from accidentally duplicating a
            # secret after an ambiguous partial keyring write.
            self.fallback.set(self.credential_name, value)
            return "local app-data fallback"
        try:
            keyring.set(self.credential_name, value)
            if keyring.get(self.credential_name) != value:
                raise RuntimeError("the operating-system credential store did not persist Edge")
            self.fallback.delete(self.credential_name)
            return "operating-system credential store"
        except RuntimeError:
            try:
                if previous is None:
                    keyring.delete(self.credential_name)
                    if keyring.get(self.credential_name) is not None:
                        raise RuntimeError("the new operating-system credential still exists")
                else:
                    keyring.set(self.credential_name, previous)
                    if keyring.get(self.credential_name) != previous:
                        raise RuntimeError(
                            "the previous operating-system credential was not restored"
                        )
            except RuntimeError as rollback_error:
                raise RuntimeError(
                    "The operating-system credential write failed and could not be rolled "
                    "back. No fallback copy was created; retry secure storage before deploying"
                ) from rollback_error
            self.fallback.set(self.credential_name, value)
            return "local app-data fallback"

    def material(self) -> EdgeMaterial | None:
        encoded, storage = self._read_secret()
        if not encoded:
            return None
        try:
            parsed = json.loads(encoded)
            if not isinstance(parsed, dict) or set(parsed) != {"bundle", "recovery_code"}:
                raise ValueError("unexpected fields")
            bundle = EdgeEnrollmentBundle.decode(str(parsed["bundle"]))
            recovery = str(parsed["recovery_code"])
        except (ValueError, json.JSONDecodeError, EdgeSetupError) as exc:
            raise RuntimeError("stored Edge enrollment credential is invalid") from exc
        return EdgeMaterial(bundle, recovery, storage)

    def prepare(self, vault_id: str) -> EdgeMaterial:
        # FileLock is recursive for one caller and serializes simultaneous UI
        # requests and separate Core processes around the whole key generation.
        with self.lock:
            existing = self.material()
            state = self.state()
            if existing is not None:
                if existing.bundle.vault_id != vault_id:
                    raise RuntimeError("stored Edge enrollment belongs to a different vault")
                if state is None:
                    state = EdgeState(vault_id, _utc_now(), existing.credential_storage)
                    self.save_state(state)
                return existing

            if state is not None and state.vault_id != vault_id:
                raise RuntimeError("stored Edge setup belongs to a different vault")
            if state is not None and state.edge_url is not None:
                raise RuntimeError(
                    "Edge is still paired but its local enrollment credential is missing. "
                    "The existing remote service was not changed. Restore the credential, "
                    "or delete the hosted service and explicitly forget the local connection"
                )

            bundle, recovery = generate_enrollment(vault_id)
            encoded = json.dumps(
                {"bundle": bundle.encode(), "recovery_code": recovery},
                sort_keys=True,
                separators=(",", ":"),
            )
            storage = self._write_secret(encoded)
            material = EdgeMaterial(bundle, recovery, storage)
            self.save_state(EdgeState(vault_id, _utc_now(), storage))
            return material

    def migrate_credential_to_os_store(self) -> str:
        """Retry OS-backed storage without discarding a recoverable fallback secret."""

        with self.lock:
            encoded, current_storage = self._read_secret()
            if not encoded:
                raise RuntimeError("Edge enrollment credential is unavailable")
            if current_storage == "operating-system credential store":
                return current_storage
            storage = self._write_secret(encoded)
            if storage != "operating-system credential store":
                raise RuntimeError(
                    "The operating-system credential store is still unavailable. "
                    "The existing local app-data fallback was kept"
                )
            state = self.state()
            if state is not None:
                self.save_state(replace(state, credential_storage=storage))
            return storage

    def reset(self) -> None:
        """Forget local enrollment after the hosted Edge has been decommissioned."""

        with self.lock:
            keyring = KeyringCredentialStore()
            credential_names = (self.credential_name, LEGACY_EDGE_CREDENTIAL_NAME)
            try:
                for name in credential_names:
                    keyring.delete(name)
                    if keyring.get(name) is not None:
                        raise RuntimeError(
                            "the operating-system credential store retained an Edge credential"
                        )
            except RuntimeError as exc:
                raise RuntimeError(
                    "Could not verify removal from the operating-system credential store. "
                    "Edge recovery state was kept"
                ) from exc
            try:
                for name in credential_names:
                    self.fallback.delete(name)
                    if self.fallback.get(name) is not None:
                        raise RuntimeError("the local fallback retained an Edge credential")
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError(
                    "Could not verify removal from the local credential fallback. "
                    "Edge recovery state was kept"
                ) from exc
            self.path.unlink(missing_ok=True)

    def connect(
        self,
        edge_url: str,
        *,
        client: HttpClientLike | None = None,
        timeout_seconds: float = 10.0,
    ) -> EdgeState:
        material = self.material()
        state = self.state()
        if material is None or state is None:
            raise RuntimeError("Prepare the Edge deployment before pairing it")
        origin = normalize_edge_url(edge_url)
        self._verify_origin(
            origin,
            material,
            client=client,
            timeout_seconds=timeout_seconds,
        )
        connected = replace(
            state,
            edge_url=origin,
            connected_at=_utc_now(),
            last_error=None,
        )
        self.save_state(connected)
        return connected

    def verify(
        self,
        *,
        client: HttpClientLike | None = None,
        timeout_seconds: float = 10.0,
        allow_decommissioned: bool = False,
    ) -> EdgeState:
        """Re-prove the stored Edge identity before releasing any bearer token."""

        material = self.material()
        state = self.state()
        if material is None or state is None or state.edge_url is None:
            raise RuntimeError("Edge is not connected")
        self._verify_origin(
            state.edge_url,
            material,
            client=client,
            timeout_seconds=timeout_seconds,
            allow_decommissioned=allow_decommissioned,
        )
        return state

    @staticmethod
    def _verify_origin(
        origin: str,
        material: EdgeMaterial,
        *,
        client: HttpClientLike | None,
        timeout_seconds: float,
        allow_decommissioned: bool = False,
    ) -> None:
        challenge = secrets.token_urlsafe(32)
        owns_client = client is None
        active_client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        try:
            response = active_client.get(
                f"{origin}/healthz",
                params={"challenge": challenge, "vault_id": material.bundle.vault_id},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise RuntimeError("The Edge URL could not be verified") from exc
        finally:
            if owns_client and isinstance(active_client, httpx.Client):
                active_client.close()
        proof = body.get("proof") if isinstance(body, dict) else None
        if (
            not isinstance(proof, str)
            or body.get("component") != "edge"
            or body.get("authority") != "core"
            or not proof_matches(
                material.bundle.replication_secret,
                public_url=origin,
                vault_id=material.bundle.vault_id,
                challenge=challenge,
                supplied=proof,
            )
        ):
            raise RuntimeError(
                "The service at that URL could not prove it is this vault's All The Context Edge"
            )
        status = body.get("status")
        if status == "decommissioned" and not allow_decommissioned:
            raise RuntimeError(
                "That All The Context Edge was already decommissioned and cannot be paired"
            )
        if status not in ({"ok", "decommissioned"} if allow_decommissioned else {"ok"}):
            raise RuntimeError("The verified Edge did not report a healthy service state")

    def update_sync(
        self,
        *,
        success: bool,
        last_sequence: int | None = None,
        proposals_imported: int = 0,
        error: str | None = None,
    ) -> EdgeState | None:
        with self.lock:
            state = self.state()
            if state is None:
                return None
            updated = replace(
                state,
                last_success_at=_utc_now() if success else state.last_success_at,
                last_error=None if success else (error or "Edge sync failed")[:500],
                last_sequence=(last_sequence if last_sequence is not None else state.last_sequence),
                proposals_imported=state.proposals_imported + proposals_imported,
            )
            self.save_state(updated)
            return updated


def decommission_edge_connection(
    connection: EdgeConnectionStore,
    *,
    client: HttpClientLike | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    """Verify, remotely decommission, and only then forget Edge without opening Core SQLite."""

    owns_client = client is None
    active_client = client or cast(
        HttpClientLike,
        httpx.Client(timeout=timeout_seconds, follow_redirects=False),
    )
    try:
        state = connection.state()
        material = connection.material()
        if state is None and material is None:
            return
        if state is None:
            raise RuntimeError(
                "Edge credentials remain but the connection state is missing. "
                "Restore the Edge connection in All The Context or delete the hosted "
                "Edge at its provider before uninstalling"
            )
        # Core cannot distinguish an abandoned local preparation from a hosted
        # deployment whose public URL was never pasted back into the app. Do
        # not silently discard the only recovery material in either case.
        if state.edge_url is None:
            raise RuntimeError(
                "Edge setup was prepared, but Core has no paired address and cannot "
                "verify whether a hosted service was deployed. Delete any hosted Edge "
                "service, disk, and backups before forgetting this setup"
            )
        if material is None:
            raise RuntimeError(
                "Edge is connected but its credentials are unavailable. Restore the "
                "Edge connection or delete the hosted Edge at its provider before "
                "uninstalling"
            )
        connection.verify(
            client=active_client,
            timeout_seconds=timeout_seconds,
            allow_decommissioned=True,
        )
        response = active_client.post(
            f"{state.edge_url}/v1/edge/decommission",
            headers={
                "Authorization": f"Bearer {material.bundle.replication_token}",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        body = response.json()
        if (
            not isinstance(body, dict)
            or body.get("status") != "decommissioned"
            or body.get("terminal") is not True
            or body.get("records_remaining") != 0
        ):
            raise RuntimeError(
                "Edge did not provide a verified terminal decommission response; "
                "the local connection was kept"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Edge could not provide verified decommissioning; the local connection "
            "was kept for recovery"
        ) from exc
    finally:
        if owns_client and isinstance(active_client, httpx.Client):
            active_client.close()
    connection.reset()


class EdgeSyncManager:
    """Run application-level replication while Core is online."""

    def __init__(
        self,
        connection: EdgeConnectionStore,
        core_store: CoreStore,
        *,
        interval_seconds: float = 15.0,
    ) -> None:
        self.connection = connection
        self.core_store = core_store
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._sync_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._active_client: httpx.Client | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="all-the-context-edge-sync",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        with self._client_lock:
            if self._active_client is not None:
                self._active_client.close()
        if self._thread is not None:
            self._thread.join(timeout_seconds)

    def trigger(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                state = self.connection.state()
                if state is not None and state.edge_url is not None:
                    self.sync_now()
            except Exception as exc:
                with suppress(Exception):
                    # Corrupt connection metadata must not terminate the daemon;
                    # the dashboard reset path remains available for recovery.
                    self.connection.update_sync(success=False, error=self._safe_error(exc))
            self._wake.wait(self.interval_seconds)
            self._wake.clear()

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"Edge rejected synchronization with HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.RequestError):
            return "Edge is temporarily unreachable"
        return f"Edge synchronization failed ({type(exc).__name__})"

    def sync_now(self, *, http_client: SyncHttpClientLike | None = None) -> dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            return {"state": "busy"}
        try:
            state = self.connection.state()
            material = self.connection.material()
            if state is None or material is None or state.edge_url is None:
                return {"state": "not_connected"}
            managed_client: httpx.Client | None = None
            try:
                if http_client is None:
                    managed_client = httpx.Client(
                        timeout=httpx.Timeout(10.0, connect=5.0),
                        follow_redirects=False,
                    )
                    with self._client_lock:
                        self._active_client = managed_client
                    http_client = cast(SyncHttpClientLike, managed_client)
                self.connection.verify(client=cast(HttpClientLike, http_client))
                with CoreRelaySync(
                    self.core_store.database_path,
                    state.edge_url,
                    material.bundle.replication_secret.encode("utf-8"),
                    material.bundle.replication_token,
                    http_client=http_client,
                ) as sync:
                    remote_before = sync.edge_status(material.bundle.vault_id)
                    checkpoint = int(remote_before.get("last_applied_sequence", 0))
                    pushed = sync.push(
                        vault_id=material.bundle.vault_id,
                        after_sequence=checkpoint,
                    )
                    imported = sync.pull_proposals(
                        material.bundle.vault_id,
                        self.core_store,
                    )
                    remote = sync.edge_status(material.bundle.vault_id)
                sequence = int(remote.get("last_applied_sequence", state.last_sequence))
                updated = self.connection.update_sync(
                    success=True,
                    last_sequence=sequence,
                    proposals_imported=imported,
                )
                return {
                    "state": "ready",
                    "pushed": pushed,
                    "proposals_imported": imported,
                    "last_sequence": sequence,
                    "last_success_at": updated.last_success_at if updated else None,
                }
            except Exception as exc:
                message = self._safe_error(exc)
                self.connection.update_sync(success=False, error=message)
                return {"state": "degraded", "error": message}
            finally:
                if managed_client is not None:
                    with self._client_lock:
                        if self._active_client is managed_client:
                            self._active_client = None
                    managed_client.close()
        finally:
            self._sync_lock.release()

    def owner_link(
        self,
        *,
        client: HttpClientLike | None = None,
        timeout_seconds: float = 10.0,
    ) -> str:
        state = self.connection.state()
        material = self.connection.material()
        if state is None or material is None or state.edge_url is None:
            raise RuntimeError("Edge is not connected")
        owns_client = client is None
        active_client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        try:
            self.connection.verify(
                client=cast(HttpClientLike, active_client),
                timeout_seconds=timeout_seconds,
            )
            response = active_client.post(
                f"{state.edge_url}/v1/edge/owner-ticket",
                headers={
                    "Authorization": f"Bearer {material.bundle.replication_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise RuntimeError("Edge could not create an owner connection link") from exc
        finally:
            if owns_client and isinstance(active_client, httpx.Client):
                active_client.close()
        url = body.get("connect_url") if isinstance(body, dict) else None
        if not isinstance(url, str) or not url.startswith(f"{state.edge_url}/owner/connect?"):
            raise RuntimeError("Edge returned an invalid owner connection link")
        return url

    def authorized_clients(
        self,
        *,
        client: HttpClientLike | None = None,
        timeout_seconds: float = 10.0,
    ) -> list[dict[str, Any]]:
        state, material, active_client, owns_client = self._management_client(
            client, timeout_seconds
        )
        try:
            response = active_client.get(
                f"{state.edge_url}/v1/edge/clients",
                headers={
                    "Authorization": f"Bearer {material.bundle.replication_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise RuntimeError("Edge could not list its authorized AI apps") from exc
        finally:
            if owns_client and isinstance(active_client, httpx.Client):
                active_client.close()
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise RuntimeError("Edge returned an invalid authorized-app response")
        return [dict(item) for item in items]

    def revoke_client(
        self,
        logical_client_id: str,
        *,
        client: HttpClientLike | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not logical_client_id.startswith("edge:") or len(logical_client_id) > 200:
            raise ValueError("invalid Edge client ID")
        state, material, active_client, owns_client = self._management_client(
            client, timeout_seconds
        )
        try:
            response = active_client.delete(
                f"{state.edge_url}/v1/edge/clients/{quote(logical_client_id, safe='')}",
                headers={
                    "Authorization": f"Bearer {material.bundle.replication_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError("Edge could not revoke that AI app") from exc
        finally:
            if owns_client and isinstance(active_client, httpx.Client):
                active_client.close()

    def decommission(
        self,
        *,
        client: HttpClientLike | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Revoke access and remove active Edge records before forgetting credentials."""

        with self._sync_lock:
            decommission_edge_connection(
                self.connection,
                client=client,
                timeout_seconds=timeout_seconds,
            )

    def forget_local(self) -> None:
        """Serialize a confirmed local forget with background synchronization."""

        with self._sync_lock:
            self.connection.reset()

    def _management_client(
        self,
        client: HttpClientLike | None,
        timeout_seconds: float,
    ) -> tuple[EdgeState, EdgeMaterial, HttpClientLike, bool]:
        state = self.connection.state()
        material = self.connection.material()
        if state is None or material is None or state.edge_url is None:
            raise RuntimeError("Edge is not connected")
        owns_client = client is None
        active_client = client or cast(
            HttpClientLike,
            httpx.Client(timeout=timeout_seconds, follow_redirects=False),
        )
        try:
            self.connection.verify(client=active_client, timeout_seconds=timeout_seconds)
        except Exception:
            if owns_client and isinstance(active_client, httpx.Client):
                active_client.close()
            raise
        return state, material, active_client, owns_client

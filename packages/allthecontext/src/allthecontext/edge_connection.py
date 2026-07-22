"""Core-side hosted Edge enrollment, pairing, and background synchronization."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import quote

import httpx
from filelock import FileLock

from .config import CoreConfig
from .credentials import DevelopmentFileCredentialStore, KeyringCredentialStore
from .edge_claim import (
    EdgeClaimBundle,
    EdgeClaimError,
    EdgeClaimPrivate,
    decrypt_claim,
    decrypt_forward_request,
    generate_claim,
    sign_claim,
)
from .edge_setup import (
    EdgeEnrollmentBundle,
    EdgeSetupError,
    generate_recovery_code,
    hash_recovery_code,
    normalize_edge_url,
    proof_matches,
)
from .models import Availability, BootstrapRequest, SearchRequest
from .relay.forwarding import EdgeForwardingBroker
from .retrieval import RetrievalEngine
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
    claim_bundle: EdgeClaimBundle | None = None
    claim_private: EdgeClaimPrivate | None = None
    forwarding_private_key: str | None = None
    forwarding_public_key: str | None = None


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
            if not isinstance(parsed, dict) or set(parsed) not in (
                {"bundle", "recovery_code"},
                {"bundle", "recovery_code", "claim_bundle", "claim_private"},
                {
                    "bundle",
                    "recovery_code",
                    "claim_bundle",
                    "claim_private",
                    "forwarding_private_key",
                    "forwarding_public_key",
                },
                {
                    "bundle",
                    "recovery_code",
                    "forwarding_private_key",
                    "forwarding_public_key",
                },
            ):
                raise ValueError("unexpected fields")
            bundle = EdgeEnrollmentBundle.decode(str(parsed["bundle"]))
            recovery = str(parsed["recovery_code"])
            claim_bundle = (
                EdgeClaimBundle.decode(str(parsed["claim_bundle"]))
                if "claim_bundle" in parsed
                else None
            )
            raw_private = parsed.get("claim_private")
            claim_private = (
                EdgeClaimPrivate(
                    signing_private_key=str(raw_private["signing_private_key"]),
                    encryption_private_key=str(raw_private["encryption_private_key"]),
                )
                if isinstance(raw_private, dict)
                else None
            )
        except (ValueError, json.JSONDecodeError, EdgeSetupError, EdgeClaimError) as exc:
            raise RuntimeError("stored Edge enrollment credential is invalid") from exc
        forwarding_private_key = parsed.get("forwarding_private_key")
        if forwarding_private_key is not None and not isinstance(forwarding_private_key, str):
            raise RuntimeError("stored Edge forwarding credential is invalid")
        forwarding_public_key = parsed.get("forwarding_public_key")
        if forwarding_public_key is not None and not isinstance(forwarding_public_key, str):
            raise RuntimeError("stored Edge forwarding public key is invalid")
        return EdgeMaterial(
            bundle,
            recovery,
            storage,
            claim_bundle,
            claim_private,
            forwarding_private_key,
            forwarding_public_key,
        )

    def prepare(self, vault_id: str) -> EdgeMaterial:
        # FileLock is recursive for one caller and serializes simultaneous UI
        # requests and separate Core processes around the whole key generation.
        with self.lock:
            existing = self.material()
            state = self.state()
            if existing is not None:
                if existing.bundle.vault_id != vault_id:
                    raise RuntimeError("stored Edge enrollment belongs to a different vault")
                if (
                    existing.claim_bundle is not None
                    and existing.claim_private is not None
                    and existing.claim_bundle.expires_at <= int(time.time())
                    and (state is None or state.edge_url is None)
                ):
                    claim_bundle, claim_private = generate_claim(
                        vault_id, existing.bundle.owner_secret_hash
                    )
                    encoded = json.dumps(
                        {
                            "bundle": existing.bundle.encode(),
                            "recovery_code": existing.recovery_code,
                            "claim_bundle": claim_bundle.encode(),
                            "claim_private": asdict(claim_private),
                            "forwarding_private_key": claim_private.encryption_private_key,
                            "forwarding_public_key": claim_bundle.encryption_public_key,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    storage = self._write_secret(encoded)
                    existing = EdgeMaterial(
                        existing.bundle,
                        existing.recovery_code,
                        storage,
                        claim_bundle,
                        claim_private,
                        claim_private.encryption_private_key,
                        claim_bundle.encryption_public_key,
                    )
                    state = EdgeState(vault_id, _utc_now(), storage)
                    self.save_state(state)
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

            recovery = generate_recovery_code()
            bundle = EdgeEnrollmentBundle(
                vault_id=vault_id,
                replication_secret=secrets.token_urlsafe(32),
                replication_token=secrets.token_urlsafe(32),
                owner_secret_hash=hash_recovery_code(recovery),
            )
            claim_bundle, claim_private = generate_claim(vault_id, bundle.owner_secret_hash)
            encoded = json.dumps(
                {
                    "bundle": bundle.encode(),
                    "recovery_code": recovery,
                    "claim_bundle": claim_bundle.encode(),
                    "claim_private": asdict(claim_private),
                    "forwarding_private_key": claim_private.encryption_private_key,
                    "forwarding_public_key": claim_bundle.encryption_public_key,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            storage = self._write_secret(encoded)
            material = EdgeMaterial(
                bundle,
                recovery,
                storage,
                claim_bundle,
                claim_private,
                claim_private.encryption_private_key,
                claim_bundle.encryption_public_key,
            )
            self.save_state(EdgeState(vault_id, _utc_now(), storage))
            return material

    def replace_bundle(
        self,
        material: EdgeMaterial,
        bundle: EdgeEnrollmentBundle,
        *,
        preserve_claim: bool = True,
    ) -> EdgeMaterial:
        payload: dict[str, object] = {
            "bundle": bundle.encode(),
            "recovery_code": material.recovery_code,
        }
        if preserve_claim and material.claim_bundle and material.claim_private:
            payload["claim_bundle"] = material.claim_bundle.encode()
            payload["claim_private"] = asdict(material.claim_private)
        if material.forwarding_private_key:
            payload["forwarding_private_key"] = material.forwarding_private_key
        if material.forwarding_public_key:
            payload["forwarding_public_key"] = material.forwarding_public_key
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        storage = self._write_secret(encoded)
        return EdgeMaterial(
            bundle,
            material.recovery_code,
            storage,
            material.claim_bundle if preserve_claim else None,
            material.claim_private if preserve_claim else None,
            material.forwarding_private_key,
            material.forwarding_public_key,
        )

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
        if material.claim_bundle is not None and material.claim_private is not None:
            owns_client = client is None
            active_client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
            try:
                challenge_response = active_client.post(f"{origin}/v1/edge/claim/challenge")
                challenge_response.raise_for_status()
                challenge_body = challenge_response.json()
                challenge = challenge_body.get("challenge")
                if not isinstance(challenge, str):
                    raise RuntimeError("Edge returned an invalid claim challenge")
                signature = sign_claim(
                    material.claim_private, material.claim_bundle, challenge, origin
                )
                claim_response = active_client.post(
                    f"{origin}/v1/edge/claim",
                    json={"challenge": challenge, "signature": signature},
                )
                claim_response.raise_for_status()
                envelope = claim_response.json()
                if not isinstance(envelope, dict):
                    raise RuntimeError("Edge returned an invalid claim envelope")
                credentials = decrypt_claim(material.claim_bundle, material.claim_private, envelope)
                material = self.replace_bundle(
                    material,
                    EdgeEnrollmentBundle(
                        vault_id=material.bundle.vault_id,
                        replication_secret=credentials["replication_secret"],
                        replication_token=credentials["replication_token"],
                        owner_secret_hash=material.bundle.owner_secret_hash,
                    ),
                )
                self._verify_origin(
                    origin,
                    material,
                    client=cast(HttpClientLike, active_client),
                    timeout_seconds=timeout_seconds,
                )
                acknowledged = active_client.post(
                    f"{origin}/v1/edge/claim/ack",
                    headers={"Authorization": f"Bearer {material.bundle.replication_token}"},
                )
                acknowledged.raise_for_status()
                material = self.replace_bundle(material, material.bundle, preserve_claim=False)
            except Exception as exc:
                raise RuntimeError("The Edge one-time claim could not be completed") from exc
            finally:
                if owns_client and isinstance(active_client, httpx.Client):
                    active_client.close()
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
        self.retrieval = RetrievalEngine(core_store)
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
                    forwarded = self._service_forwarding(sync)
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
                    "forwarded_requests": forwarded,
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

    def _service_forwarding(self, sync: CoreRelaySync) -> int:
        completed = 0
        for envelope in sync.claim_forward_requests(limit=8):
            request_id = envelope.get("request_id")
            claim_token = envelope.get("claim_token")
            try:
                if not isinstance(request_id, str) or not isinstance(claim_token, str):
                    continue
                active_request_id: str = request_id
                active_claim_token: str = claim_token
                response = self._execute_forward_request(envelope)
            except Exception:
                response = {"state": "error", "error": "Core could not complete the request"}
            if sync.answer_forward_request(active_request_id, active_claim_token, response):
                completed += 1
        return completed

    def _execute_forward_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
        client_id = envelope.get("client_id")
        operation = envelope.get("operation")
        request_envelope = envelope.get("request_envelope")
        expires_at = envelope.get("expires_at")
        request_id = envelope.get("request_id")
        material = self.connection.material()
        if (
            not isinstance(client_id, str)
            or not client_id.startswith("edge:")
            or not isinstance(request_id, str)
            or not isinstance(operation, str)
            or not isinstance(request_envelope, str)
            or not isinstance(expires_at, (int, float))
            or time.time() >= float(expires_at)
            or material is None
            or material.forwarding_private_key is None
        ):
            return {"state": "unavailable"}
        principal = self.core_store.remote_edge_principal(client_id)
        if principal is None or "context:read" not in principal.scopes:
            self.core_store.audit_access(
                None,
                "edge.forward.denied",
                (),
                trace_id=request_id,
                metadata={"reason": "remote_client_unavailable"},
            )
            return {"state": "unavailable"}
        approved_context_scopes = self.core_store.remote_edge_context_scopes(client_id)
        try:
            associated_data = EdgeForwardingBroker._request_aad(
                request_id, client_id, operation, float(expires_at)
            )
            raw = decrypt_forward_request(
                material.forwarding_private_key, request_envelope, associated_data
            )
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return {"state": "unavailable"}
        if not isinstance(payload, dict):
            return {"state": "unavailable"}
        requested_scopes = payload.get("scopes", payload.get("requested_scopes", []))
        if not isinstance(requested_scopes, list):
            return {"state": "unavailable"}
        if approved_context_scopes:
            payload_scopes = [
                str(scope) for scope in requested_scopes if str(scope) in approved_context_scopes
            ]
            if requested_scopes and not payload_scopes:
                return {"state": "available", "items": []}
            if "scopes" in payload:
                payload["scopes"] = payload_scopes
            if "requested_scopes" in payload:
                payload["requested_scopes"] = payload_scopes
        if operation == "search_context":
            search_request = SearchRequest(
                query=str(payload.get("query", "")),
                scopes=payload.get("scopes", []),
                kinds=payload.get("kinds", []),
                availability=[Availability.CORE],
                limit=min(int(payload.get("limit", 20)), 100),
                offset=min(int(payload.get("cursor", 0)), 10_000),
            )
            result = self.retrieval.search(search_request, principal).model_dump(mode="json")
            return self._bounded_forward_response({"state": "available", **result})
        if operation == "get_context_item":
            record = self.retrieval.get(str(payload.get("record_id", "")), principal)
            if record is None or record.availability != Availability.CORE:
                return {"state": "available", "found": False}
            return self._bounded_forward_response(
                {"state": "available", "found": True, "item": record.model_dump(mode="json")}
            )
        if operation == "bootstrap_context":
            bootstrap_request = BootstrapRequest.model_validate(payload)
            # Bootstrap through the existing policy/ranking path, then enforce the
            # forwarding availability boundary before anything leaves Core.
            result = self.retrieval.bootstrap(bootstrap_request, principal).model_dump(mode="json")
            result["items"] = [
                item
                for item in result.get("items", [])
                if isinstance(item, dict) and item.get("availability") == Availability.CORE.value
            ]
            result["context_mode"] = "core_via_edge"
            return self._bounded_forward_response({"state": "available", **result})
        return {"state": "unavailable"}

    @staticmethod
    def _bounded_forward_response(payload: dict[str, Any]) -> dict[str, Any]:
        # Edge has a hard 64 KiB envelope. Reduce whole items, never slice context
        # text into an ambiguous partial record.
        items = payload.get("items")
        if isinstance(items, list):
            while items and len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 60_000:
                items.pop()
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 60_000:
            return {"state": "error", "error": "Core response exceeded the safe size limit"}
        return payload

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

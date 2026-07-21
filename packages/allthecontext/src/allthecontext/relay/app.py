"""FastAPI transport for the hosted Relay service."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from allthecontext.relay.service import (
    AuthorizationError,
    ClientIdentity,
    EventSequenceError,
    InvalidEventPayloadError,
    ProposalConflictError,
    RelayService,
    ReplayMismatchError,
    SQLiteRelayStore,
)
from allthecontext.replication import (
    JsonValue,
    PayloadHashError,
    ReplicationError,
    SignatureError,
    canonical_json,
)


@dataclass(frozen=True, slots=True)
class RelayApplicationConfig:
    database_path: Path
    replication_secret: bytes
    replication_bearer_token: str
    client_tokens: Mapping[str, ClientIdentity]


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)
    scope: list[str] | str = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    sensitivity: str = Field(default="private", min_length=1, max_length=100)
    availability: str = Field(default="core_available")
    source_service: str | None = Field(default=None, max_length=200)
    provenance: dict[str, Any] | list[Any] | str | None = None


class ProposalAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class ContextErrorRequest(BaseModel):
    """Cloud-safe correction signal queued for authoritative Core review."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=64_000)
    evidence: str | None = Field(default=None, max_length=16_000)


class _TokenAuthenticator:
    def __init__(self, replication_token: str, clients: Mapping[str, ClientIdentity]) -> None:
        if not replication_token:
            raise ValueError("replication bearer token is required")
        self._replication_digest = self._digest(replication_token)
        self._client_by_digest = {
            self._digest(token): identity for token, identity in clients.items()
        }
        if any(not token for token in clients):
            raise ValueError("client bearer tokens cannot be empty")

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @staticmethod
    def _bearer(authorization: str | None) -> str:
        if authorization is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer authorization",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token

    def require_replication(self, authorization: str | None) -> None:
        supplied = self._digest(self._bearer(authorization))
        if not hmac.compare_digest(supplied, self._replication_digest):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credential"
            )

    def require_client(self, authorization: str | None) -> ClientIdentity:
        supplied = self._digest(self._bearer(authorization))
        identity = self._client_by_digest.get(supplied)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credential"
            )
        return identity


def create_app(
    service: RelayService,
    *,
    replication_bearer_token: str,
    client_tokens: Mapping[str, ClientIdentity],
    close_service_on_shutdown: bool = True,
) -> FastAPI:
    """Build an app around an injected service (useful for tests and hosting)."""

    authenticator = _TokenAuthenticator(replication_bearer_token, client_tokens)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if close_service_on_shutdown:
            service.close()

    app = FastAPI(title="All The Context Relay", version="0.1.0", lifespan=lifespan)

    def client_identity(
        authorization: Annotated[str | None, Header()] = None,
    ) -> ClientIdentity:
        return authenticator.require_client(authorization)

    def replication_authorized(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        authenticator.require_replication(authorization)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "relay", "authority": "core"}

    @app.post("/v1/replication/events")
    def apply_event(
        event: Annotated[dict[str, Any], Body()],
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        try:
            result = service.apply(event)
        except (SignatureError, PayloadHashError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except ReplicationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except EventSequenceError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(exc), "expected_sequence": exc.expected},
            ) from exc
        except ReplayMismatchError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except InvalidEventPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return {
            "accepted": True,
            "vault_id": result.vault_id,
            "sequence": result.sequence,
            "event_id": result.event_id,
            "replayed": result.replayed,
        }

    @app.get("/v1/context/search")
    def search_context(
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
        query: str = "",
        scope: Annotated[list[str] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        try:
            results = service.search(identity, query=query, scopes=scope, limit=limit)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return {"items": results, "count": len(results), "served_by": "relay"}

    @app.get("/v1/context/status")
    def context_status(
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        try:
            return service.status(identity)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/v1/context/{record_id}")
    def get_context_item(
        record_id: str,
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        try:
            item = service.get(identity, record_id)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="context item not found"
            )
        return item

    @app.post("/v1/proposals", status_code=status.HTTP_202_ACCEPTED)
    def propose_memory(
        request: ProposalRequest,
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        proposal = request.model_dump(exclude={"idempotency_key"})
        try:
            queued, replayed = service.propose(
                identity,
                idempotency_key=request.idempotency_key,
                proposal=proposal,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ProposalConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except (InvalidEventPayloadError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return {"proposal": queued, "replayed": replayed, "canonical": False}

    @app.post("/v1/ingestion/error", status_code=status.HTTP_202_ACCEPTED)
    def report_context_error(
        request: ContextErrorRequest,
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        proposal: dict[str, JsonValue] = {
            "kind": "context_error",
            "content": request.content,
            "scope": [],
            "sensitivity": "private",
            "availability": "core_available",
            "source_service": "relay",
            "provenance": {
                "record_id": request.record_id,
                "suggested_correction": request.evidence,
            },
        }
        idempotency_key = hashlib.sha256(canonical_json(proposal).encode("utf-8")).hexdigest()
        try:
            queued, replayed = service.propose(
                identity,
                idempotency_key=f"context-error:{idempotency_key}",
                proposal=proposal,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return {"proposal": queued, "replayed": replayed, "canonical": False}

    @app.get("/v1/replication/proposals")
    def queued_proposals(
        vault_id: str,
        _authorized: None = Depends(replication_authorized),
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        items = service.queued_proposals(vault_id, limit=limit)
        return {"items": items, "count": len(items), "canonical": False}

    @app.patch("/v1/replication/proposals/{proposal_id}")
    def acknowledge_proposal(
        proposal_id: str,
        vault_id: str,
        request: ProposalAckRequest,
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        if request.status not in {"imported", "rejected"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status must be imported or rejected",
            )
        updated = service.acknowledge_proposal(vault_id, proposal_id, request.status)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="queued proposal not found"
            )
        return {"updated": True, "proposal_id": proposal_id, "status": request.status}

    return app


def config_from_environment() -> RelayApplicationConfig:
    """Load deployment configuration without logging credential material."""

    try:
        from platformdirs import user_data_path
    except ImportError as exc:  # pragma: no cover - declared runtime dependency
        raise RuntimeError("platformdirs is required") from exc

    database_value = os.environ.get("ATC_RELAY_DATABASE")
    database_path = (
        Path(database_value).expanduser()
        if database_value
        else Path(user_data_path("AllTheContext", appauthor=False)) / "relay.sqlite3"
    )
    secret = os.environ.get("ATC_RELAY_REPLICATION_SECRET", "").encode("utf-8")
    replication_token = os.environ.get("ATC_RELAY_BEARER_TOKEN", "")
    clients_value = os.environ.get("ATC_RELAY_CLIENTS_JSON", "{}")
    try:
        raw_clients = json.loads(clients_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ATC_RELAY_CLIENTS_JSON is not valid JSON") from exc
    if not isinstance(raw_clients, dict):
        raise RuntimeError("ATC_RELAY_CLIENTS_JSON must be an object keyed by bearer token")
    clients: dict[str, ClientIdentity] = {}
    for token, raw_identity in raw_clients.items():
        if not isinstance(token, str) or not isinstance(raw_identity, dict):
            raise RuntimeError("invalid Relay client configuration")
        try:
            clients[token] = ClientIdentity(
                client_id=str(raw_identity["client_id"]),
                vault_id=str(raw_identity["vault_id"]),
                permissions=frozenset(str(item) for item in raw_identity.get("permissions", [])),
                context_scopes=frozenset(
                    str(item) for item in raw_identity.get("context_scopes", [])
                ),
            )
        except (KeyError, TypeError) as exc:
            raise RuntimeError("invalid Relay client configuration") from exc
    return RelayApplicationConfig(database_path, secret, replication_token, clients)


def build_environment_app() -> FastAPI:
    config = config_from_environment()
    service = RelayService(SQLiteRelayStore(config.database_path), config.replication_secret)
    return create_app(
        service,
        replication_bearer_token=config.replication_bearer_token,
        client_tokens=config.client_tokens,
    )


def main() -> None:
    """Run Relay; remote deployments must put TLS in front of this process."""

    import uvicorn

    host = os.environ.get("ATC_RELAY_HOST", "127.0.0.1")
    port = int(os.environ.get("ATC_RELAY_PORT", "8743"))
    uvicorn.run(build_environment_app(), host=host, port=port, log_config=None)


if __name__ == "__main__":  # pragma: no cover
    main()

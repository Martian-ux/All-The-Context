"""OAuth-protected MCP tools served directly by the hosted Edge replica."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from allthecontext.relay.oauth import (
    EDGE_SCOPES,
    PROPOSE_SCOPE,
    READ_SCOPE,
    EdgeOAuthProvider,
)
from allthecontext.relay.service import ClientIdentity, RelayService
from allthecontext.replication import JsonValue


def _annotations(*, read_only: bool, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations.model_validate(
        {
            "readOnlyHint": read_only,
            "destructiveHint": False,
            "idempotentHint": idempotent,
            "openWorldHint": False,
        }
    )


def _identity(vault_id: str, *, required_scope: str = READ_SCOPE) -> ClientIdentity:
    token = get_access_token()
    if token is None or required_scope not in token.scopes:
        raise PermissionError(f"OAuth scope {required_scope} is required")
    claims = token.claims or {}
    logical_client = claims.get("atc_client_id")
    if not isinstance(logical_client, str) or not logical_client:
        raise PermissionError("OAuth token is missing its Edge client identity")
    permissions = {"context:read"}
    if PROPOSE_SCOPE in token.scopes:
        permissions.add("proposal:write")
    return ClientIdentity(
        client_id=logical_client,
        vault_id=vault_id,
        permissions=frozenset(permissions),
        context_scopes=frozenset({"*"}),
    )


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    """Remove storage/policy internals that a model does not need to echo."""

    allowed = {
        "id",
        "kind",
        "content",
        "scope",
        "provenance",
        "source_service",
        "confidence",
        "sensitivity",
        "availability",
        "valid_from",
        "valid_until",
        "version",
        "supersedes",
        "approval_status",
        "content_hash",
        "updated_at",
        "score",
    }
    return {key: value for key, value in record.items() if key in allowed}


def _automatic_proposal_key(payload: object) -> str:
    """Make exact retries stable while allowing corrected metadata to be proposed."""

    return hashlib.sha256(json_for_hash(payload).encode("utf-8")).hexdigest()


def build_edge_mcp(
    service: RelayService,
    provider: EdgeOAuthProvider,
    *,
    vault_id: str,
) -> FastMCP:
    public = urlsplit(provider.public_url)
    if public.hostname is None:  # pragma: no cover - provider validates the URL
        raise ValueError("Edge public URL does not contain a hostname")
    server = FastMCP(
        "All The Context Edge",
        instructions=(
            "Use this context service automatically; do not ask the user to manage it. At the "
            "start of every substantive task where preferences, projects, people, constraints, "
            "or prior decisions could matter, call bootstrap_context before answering or acting, "
            "then search or fetch specific records when needed. Retrieve only approved "
            "always-available context. When the user states or corrects durable context, call "
            "propose_memory before the task ends if the granted scope permits it. Core remains "
            "authoritative: a proposal is reviewable, not canonical. Never propose secrets, "
            "hidden reasoning, provider instructions, or guesses as established facts."
        ),
        website_url=provider.public_url,
        host=public.hostname,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(provider.public_url),
            resource_server_url=AnyHttpUrl(provider.resource),
            service_documentation_url=AnyHttpUrl(f"{provider.public_url}/about"),
            required_scopes=[READ_SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=list(EDGE_SCOPES),
                default_scopes=list(EDGE_SCOPES),
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public.netloc],
            allowed_origins=[
                provider.public_url,
                "https://chatgpt.com",
                "https://claude.ai",
            ],
        ),
    )

    @server.tool(
        title="Bootstrap approved context",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def bootstrap_context(
        task_description: str = "",
        requested_scopes: list[str] | None = None,
        character_budget: int = 8_000,
        current_project: str | None = None,
    ) -> dict[str, Any]:
        """Compile approved always-available context for the current task."""
        if len(task_description) > 4_000:
            raise ValueError("task_description must contain at most 4000 characters")
        if current_project is not None and len(current_project) > 512:
            raise ValueError("current_project must contain at most 512 characters")
        if requested_scopes is not None and len(requested_scopes) > 64:
            raise ValueError("requested_scopes must contain at most 64 values")
        if not 256 <= character_budget <= 50_000:
            raise ValueError("character_budget must be between 256 and 50000")
        query = " ".join(
            value for value in (task_description.strip(), (current_project or "").strip()) if value
        )[:4_000]
        mandatory = service.search(
            _identity(vault_id),
            query="",
            kinds=["interaction_preference"],
            limit=100,
        )
        relevant = service.search(
            _identity(vault_id),
            query=query,
            scopes=requested_scopes,
            limit=100,
        )
        mandatory_ids = {str(record["id"]) for record in mandatory}
        records = [
            *mandatory,
            *(record for record in relevant if record["id"] not in mandatory_ids),
        ]
        selected: list[dict[str, Any]] = []
        used = 0
        for raw in records:
            record = _public_record(raw)
            cost = len(str(record.get("content", "")))
            if selected and used + cost > character_budget:
                break
            if cost > character_budget and not selected:
                record["content"] = str(record["content"])[:character_budget]
                cost = character_budget
            selected.append(record)
            used += cost
        return {
            "items": selected,
            "count": len(selected),
            "character_budget": character_budget,
            "characters_used": used,
            "served_by": "edge",
            "authority": "core",
        }

    @server.tool(
        title="Search approved context",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def search_context(
        query: str,
        scopes: list[str] | None = None,
        kinds: list[str] | None = None,
        limit: int = 20,
        cursor: int = 0,
    ) -> dict[str, Any]:
        """Search approved always-available context with structured filters and full text."""
        if len(query) > 4_000:
            raise ValueError("query must contain at most 4000 characters")
        if scopes is not None and len(scopes) > 64:
            raise ValueError("scopes must contain at most 64 values")
        if kinds is not None and len(kinds) > 64:
            raise ValueError("kinds must contain at most 64 values")
        if not 0 <= cursor <= 10_000:
            raise ValueError("cursor must be between 0 and 10000")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        records = service.search(
            _identity(vault_id),
            query=query,
            scopes=scopes,
            kinds=kinds,
            limit=limit + 1,
            offset=cursor,
        )
        has_more = len(records) > limit
        page = [_public_record(record) for record in records[:limit]]
        return {
            "items": page,
            "count": len(page),
            "next_cursor": cursor + len(page) if has_more else None,
            "served_by": "edge",
            "authority": "core",
        }

    @server.tool(
        title="Get approved context item",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def get_context_item(record_id: str) -> dict[str, Any]:
        """Get one approved always-available context record by its stable ID."""
        record = service.get(_identity(vault_id), record_id)
        if record is None:
            return {"found": False, "id": record_id, "served_by": "edge"}
        return {"found": True, "item": _public_record(record), "served_by": "edge"}

    @server.tool(
        title="Check context availability",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def context_status() -> dict[str, Any]:
        """Report Edge freshness and make clear that Core remains authoritative."""
        result = service.status(_identity(vault_id))
        result.pop("vault_id", None)
        result["served_by"] = "edge"
        return result

    @server.tool(
        title="Propose durable memory",
        annotations=_annotations(read_only=False, idempotent=True),
    )
    def propose_memory(
        kind: str,
        content: str,
        scope: str,
        confidence: float,
        sensitivity: str = "normal",
        source_reference: str | None = None,
        evidence: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Call when durable context changes; queues Core review, never canonical memory."""
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        identity = _identity(vault_id, required_scope=PROPOSE_SCOPE)
        proposal: dict[str, JsonValue] = {
            "kind": kind,
            "content": content,
            "scope": [scope],
            "confidence": confidence,
            "sensitivity": sensitivity,
            "availability": "core_available",
            "source_service": identity.client_id,
            "provenance": {
                "source_reference": source_reference,
                "evidence": evidence,
            },
        }
        key = idempotency_key or _automatic_proposal_key(proposal)
        queued, replayed = service.propose(
            identity,
            idempotency_key=key,
            proposal=proposal,
        )
        return {
            "proposal": dict(queued),
            "replayed": replayed,
            "canonical": False,
            "review_required": True,
        }

    @server.tool(
        title="Report incorrect context",
        annotations=_annotations(read_only=False, idempotent=True),
    )
    def report_context_error(
        record_id: str,
        description: str,
        suggested_correction: str | None = None,
    ) -> dict[str, Any]:
        """Queue a correction signal for authoritative review in Core."""
        identity = _identity(vault_id, required_scope=PROPOSE_SCOPE)
        payload: dict[str, JsonValue] = {
            "kind": "context_error",
            "content": description,
            "scope": [],
            "sensitivity": "sensitive",
            "availability": "core_available",
            "source_service": identity.client_id,
            "provenance": {
                "record_id": record_id,
                "suggested_correction": suggested_correction,
            },
        }
        key = hashlib.sha256(json_for_hash(payload).encode()).hexdigest()
        queued, replayed = service.propose(
            identity,
            idempotency_key=f"context-error:{key}",
            proposal=payload,
        )
        return {
            "proposal": dict(queued),
            "replayed": replayed,
            "canonical": False,
            "review_required": True,
        }

    @server.tool(
        name="search",
        title="Search context for citations",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def provider_search(query: str) -> dict[str, Any]:
        """Search approved context using the data-app compatibility schema."""
        records = service.search(_identity(vault_id), query=query, limit=20)
        return {
            "results": [
                {
                    "id": str(record["id"]),
                    "title": f"{str(record['kind']).replace('_', ' ').title()}",
                    "url": f"{provider.public_url}/context/{record['id']}",
                }
                for record in records
            ]
        }

    @server.tool(
        name="fetch",
        title="Fetch context for citations",
        annotations=_annotations(read_only=True, idempotent=True),
    )
    def provider_fetch(id: str) -> dict[str, Any]:
        """Fetch one approved context item using the data-app compatibility schema."""
        record = service.get(_identity(vault_id), id)
        if record is None:
            raise ValueError("context item not found")
        return {
            "id": id,
            "title": str(record["kind"]).replace("_", " ").title(),
            "text": str(record["content"]),
            "url": f"{provider.public_url}/context/{id}",
            "metadata": {
                "kind": record["kind"],
                "scope": record["scope"],
                "source_service": record["source_service"],
                "version": record["version"],
            },
        }

    for tool in server._tool_manager.list_tools():
        tool.parameters["additionalProperties"] = False
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)

    return server


def json_for_hash(value: object) -> str:
    """Stable JSON without importing the replication module into tool schemas."""

    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

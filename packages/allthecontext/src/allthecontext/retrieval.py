"""Permission-first deterministic retrieval and context compilation."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from .ids import new_id, utc_now
from .models import (
    BootstrapRequest,
    BootstrapResponse,
    ContextRecordOut,
    SearchRequest,
    SearchResponse,
)
from .security import ClientPrincipal, record_is_allowed
from .storage import CoreStore


def _fts_query(value: str) -> str:
    """Produce a literal-token FTS query, not raw FTS syntax."""
    tokens = re.findall(r"[\w@.-]+", value, flags=re.UNICODE)[:32]
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _json_set(value: str) -> set[str]:
    parsed: Any = json.loads(value)
    return {str(item) for item in parsed} if isinstance(parsed, list) else set()


class RetrievalEngine:
    """Replaceable retrieval interface; policy filtering always precedes ranking."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store

    def search(
        self, request: SearchRequest, principal: ClientPrincipal | None = None
    ) -> SearchResponse:
        conditions = [
            "r.vault_id=?",
            "r.deleted_at IS NULL",
            "r.approval_status='approved'",
            "(r.valid_from IS NULL OR r.valid_from<=?)",
            "(r.expires_at IS NULL OR r.expires_at>?)",
            "NOT EXISTS (SELECT 1 FROM context_records newer "
            "WHERE newer.supersedes=r.id AND newer.deleted_at IS NULL)",
        ]
        now = utc_now()
        parameters: list[Any] = [self.store.vault_id(), now, now]
        join = ""
        order = "r.updated_at DESC"
        query = _fts_query(request.query)
        if query:
            join = " JOIN context_fts ON context_fts.record_id=r.id "
            conditions.append("context_fts MATCH ?")
            parameters.append(query)
            order = "bm25(context_fts), r.updated_at DESC"
        if request.kinds:
            placeholders = ",".join("?" for _ in request.kinds)
            conditions.append(f"r.kind IN ({placeholders})")
            parameters.extend(request.kinds)
        if request.availability:
            placeholders = ",".join("?" for _ in request.availability)
            conditions.append(f"r.availability IN ({placeholders})")
            parameters.extend(item.value for item in request.availability)
        sql = (
            "SELECT r.* FROM context_records r"
            + join
            + " WHERE "
            + " AND ".join(conditions)
            + " ORDER BY "
            + order
        )
        with self.store.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        requested_scopes = set(request.scopes)
        authorized: list[sqlite3.Row] = []
        denied: list[str] = []
        for row in rows:
            record_scopes = _json_set(str(row["scopes_json"]))
            if requested_scopes and not (record_scopes & requested_scopes):
                continue
            if record_is_allowed(
                principal,
                record_scopes,
                _json_set(str(row["allowed_clients_json"])),
                _json_set(str(row["denied_clients_json"])),
            ):
                authorized.append(row)
            else:
                denied.append(str(row["id"]))
        page = authorized[request.offset : request.offset + request.limit]
        items = [self.store._record_out(row) for row in page]
        trace_id = new_id()
        self.store.audit_access(
            principal.id if principal else None,
            "search_context",
            [item.id for item in items],
            denied_ids=denied,
            trace_id=trace_id,
            metadata={
                "query_present": bool(request.query),
                "requested_scopes": request.scopes,
                "result_count": len(items),
            },
        )
        return SearchResponse(items=items, total=len(authorized), trace_id=trace_id)

    def get(
        self, record_id: str, principal: ClientPrincipal | None = None
    ) -> ContextRecordOut | None:
        try:
            record = self.store.get_record(record_id)
        except Exception as error:
            from .storage import NotFoundError

            if isinstance(error, NotFoundError):
                return None
            raise
        allowed = record_is_allowed(
            principal,
            set(record.scopes),
            set(record.allowed_clients),
            set(record.denied_clients),
        )
        trace_id = new_id()
        self.store.audit_access(
            principal.id if principal else None,
            "get_context_item",
            [record.id] if allowed else [],
            denied_ids=[] if allowed else [record.id],
            trace_id=trace_id,
        )
        return record if allowed else None

    def bootstrap(
        self, request: BootstrapRequest, principal: ClientPrincipal | None = None
    ) -> BootstrapResponse:
        query_parts = [request.query]
        if request.current_project:
            query_parts.append(request.current_project)
        # An empty query loads deterministic profile material by recency.
        search = self.search(
            SearchRequest(
                query=" ".join(part for part in query_parts if part),
                scopes=request.requested_scopes,
                limit=100,
            ),
            principal,
        )
        mandatory = [item for item in search.items if item.kind == "interaction_preference"]
        remainder = [item for item in search.items if item.kind != "interaction_preference"]
        selected: list[ContextRecordOut] = []
        used = 0
        for item in [*mandatory, *remainder]:
            cost = len(item.content) + 64
            if used + cost > request.budget_chars:
                continue
            selected.append(item)
            used += cost
        granted = set(principal.scopes) if principal else set(request.requested_scopes)
        omitted = sorted(set(request.requested_scopes) - granted) if principal else []
        trace_id = new_id()
        self.store.audit_access(
            principal.id if principal else None,
            "bootstrap_context",
            [item.id for item in selected],
            trace_id=trace_id,
            metadata={"budget_chars": request.budget_chars, "used_chars": used},
        )
        return BootstrapResponse(
            items=selected,
            omitted_scopes=omitted,
            audit_trace_id=trace_id,
            used_chars=used,
        )


def record_ids(records: Sequence[ContextRecordOut]) -> list[str]:
    return [record.id for record in records]

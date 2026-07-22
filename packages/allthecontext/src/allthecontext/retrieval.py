"""Permission-first deterministic retrieval and context compilation."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

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

_MAX_QUERY_TOKENS = 32
_CHANNEL_LIMIT = 256
_RRF_K = 60
_LEXICAL_ALIASES: dict[str, tuple[str, ...]] = {
    # Deliberately small, inspectable lexical equivalences. These are not learned
    # from vault content and cannot create a canonical record or authority.
    "eviction": ("cache",),
}
_WORD_RE = re.compile(r"[\w@.]+", flags=re.UNICODE)


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _WORD_RE.findall(value)[:_MAX_QUERY_TOKENS]]


def _fts_terms(tokens: Sequence[str], operator: str) -> str:
    return f" {operator} ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _fts_query(value: str) -> str:
    """Produce a literal-token broad FTS query, not raw FTS syntax."""
    return _fts_terms(_tokens(value), "OR")


def _json_set(value: str) -> set[str]:
    parsed: Any = json.loads(value)
    return {str(item) for item in parsed} if isinstance(parsed, list) else set()


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_WORD_RE.findall(normalized))


@dataclass(frozen=True, slots=True)
class RankingExplanation:
    """Safe diagnostic details for a record that was authorized before scoring."""

    record_id: str
    score: float
    channel_ranks: dict[str, int]
    signals: dict[str, float]


@dataclass(slots=True)
class _RankState:
    row: sqlite3.Row
    channel_ranks: dict[str, int] = field(default_factory=dict)
    signals: dict[str, float] = field(default_factory=dict)
    score: float = 0.0


class CandidateRanker(Protocol):
    """Rank candidates that have already passed every hard policy predicate."""

    explanations: Sequence[RankingExplanation]

    def rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> list[sqlite3.Row]: ...


class EligibleRecordSelector:
    """Select the complete policy-safe set before any relevance operation."""

    def select(
        self,
        connection: sqlite3.Connection,
        request: SearchRequest,
        principal: ClientPrincipal | None,
        vault_id: str,
    ) -> tuple[list[sqlite3.Row], list[str]]:
        now = utc_now()
        conditions = [
            "r.vault_id=?",
            "r.deleted_at IS NULL",
            "r.approval_status='approved'",
            "(r.valid_from IS NULL OR r.valid_from<=?)",
            "(r.expires_at IS NULL OR r.expires_at>?)",
            "r.id NOT IN (SELECT newer.supersedes FROM context_records newer "
            "WHERE newer.supersedes IS NOT NULL AND newer.deleted_at IS NULL)",
        ]
        parameters: list[Any] = [vault_id, now, now]
        if request.kinds:
            placeholders = ",".join("?" for _ in request.kinds)
            conditions.append(f"r.kind IN ({placeholders})")
            parameters.extend(request.kinds)
        if request.availability:
            placeholders = ",".join("?" for _ in request.availability)
            conditions.append(f"r.availability IN ({placeholders})")
            parameters.extend(item.value for item in request.availability)

        if request.scopes:
            placeholders = ",".join("?" for _ in request.scopes)
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(r.scopes_json) scope "
                f"WHERE scope.value IN ({placeholders}))"
            )
            parameters.extend(request.scopes)
        if principal is not None:
            conditions.extend(
                [
                    "NOT EXISTS (SELECT 1 FROM json_each(r.denied_clients_json) denied "
                    "WHERE denied.value=?)",
                    "(json_array_length(r.allowed_clients_json)=0 OR EXISTS "
                    "(SELECT 1 FROM json_each(r.allowed_clients_json) allowed "
                    "WHERE allowed.value=?))",
                ]
            )
            parameters.extend((principal.id, principal.id))
        projection = "r.*" if not request.query else "r.id"
        rows = connection.execute(
            f"SELECT {projection} FROM context_records r WHERE " + " AND ".join(conditions),
            parameters,
        ).fetchall()
        # Relevance never sees rejected rows. We intentionally do not enumerate
        # unrelated denied IDs merely to populate an audit detail field.
        return list(rows), []


class V1CandidateRanker:
    """Preserve V1 BM25/recency ordering behind the policy boundary."""

    explanations: Sequence[RankingExplanation] = ()

    def rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> list[sqlite3.Row]:
        if not candidates:
            return []
        if not query:
            return sorted(
                candidates,
                key=lambda row: (str(row["updated_at"]), str(row["id"])),
                reverse=True,
            )
        _replace_permitted_candidates(connection, candidates)
        return connection.execute(
            "SELECT r.* FROM context_records r "
            "JOIN permitted_retrieval_candidates p ON p.record_id=r.id "
            "JOIN context_fts ON context_fts.record_id=r.id "
            "WHERE context_fts MATCH ? "
            "ORDER BY bm25(context_fts), r.updated_at DESC, r.id ASC",
            (_fts_query(query),),
        ).fetchall()


def _replace_permitted_candidates(
    connection: sqlite3.Connection, candidates: Sequence[sqlite3.Row]
) -> None:
    connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS permitted_retrieval_candidates "
        "(record_id TEXT PRIMARY KEY)"
    )
    connection.execute("DELETE FROM permitted_retrieval_candidates")
    connection.executemany(
        "INSERT INTO permitted_retrieval_candidates(record_id) VALUES (?)",
        ((str(row["id"]),) for row in candidates),
    )


class LexicalCandidateChannels:
    """Run bounded phrase/AND and broad BM25 channels over eligible IDs only."""

    def __init__(self, *, channel_limit: int = _CHANNEL_LIMIT) -> None:
        self.channel_limit = channel_limit

    def collect(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> dict[str, _RankState]:
        tokens = _tokens(query)
        if not candidates or not tokens:
            return {}
        _replace_permitted_candidates(connection, candidates)
        expanded = list(
            dict.fromkeys(
                token
                for original in tokens
                for token in (original, *_LEXICAL_ALIASES.get(original, ()))
            )
        )
        phrase = '"' + " ".join(token.replace('"', '""') for token in tokens) + '"'
        queries = {
            "phrase": phrase,
            "and": _fts_terms(tokens, "AND"),
            "broad": _fts_terms(expanded, "OR"),
        }
        states: dict[str, _RankState] = {}
        for channel, fts_query in queries.items():
            rows = connection.execute(
                "SELECT r.* FROM context_records r "
                "JOIN permitted_retrieval_candidates p ON p.record_id=r.id "
                "JOIN context_fts ON context_fts.record_id=r.id "
                "WHERE context_fts MATCH ? "
                "ORDER BY bm25(context_fts), r.updated_at DESC, r.id ASC LIMIT ?",
                (fts_query, self.channel_limit),
            ).fetchall()
            for rank, row in enumerate(rows, 1):
                record_id = str(row["id"])
                state = states.setdefault(record_id, _RankState(row=row))
                state.channel_ranks[channel] = rank
        return states


class ReciprocalRankFusion:
    """Fuse lexical ranks with small bounded structured and coverage signals."""

    def rank(self, states: dict[str, _RankState], query: str) -> list[_RankState]:
        query_tokens = set(_tokens(query))
        phrase = " ".join(_tokens(query))
        for state in states.values():
            row = state.row
            searchable = _normalized_text(
                " ".join(
                    (
                        str(row["content"]),
                        str(row["kind"]),
                        " ".join(sorted(_json_set(str(row["tags_json"])))),
                        " ".join(sorted(_json_set(str(row["scopes_json"])))),
                    )
                )
            )
            record_tokens = set(_tokens(searchable))
            coverage = (
                len(query_tokens & record_tokens) / len(query_tokens) if query_tokens else 0.0
            )
            kind_match = bool(query_tokens & set(_tokens(str(row["kind"]))))
            tags = " ".join(_json_set(str(row["tags_json"])))
            tag_match = bool(query_tokens & set(_tokens(tags)))
            project_tokens = {
                token
                for scope in _json_set(str(row["scopes_json"]))
                if scope.casefold().startswith("project:")
                for token in _tokens(scope.partition(":")[2])
            }
            project_match = bool(query_tokens & project_tokens)
            exact_phrase = bool(phrase and phrase in searchable)
            preference = (
                str(row["kind"]) == "interaction_preference"
                and bool(row["explicit_user_statement"])
            )
            state.signals = {
                "token_coverage": round(coverage, 6),
                "exact_phrase": float(exact_phrase),
                "kind_match": float(kind_match),
                "tag_match": float(tag_match),
                "project_match": float(project_match),
                "explicit_interaction_preference": float(preference),
            }
            fused = sum(1.0 / (_RRF_K + rank) for rank in state.channel_ranks.values())
            boost = min(
                0.01,
                coverage * 0.004
                + float(exact_phrase) * 0.002
                + float(kind_match) * 0.001
                + float(tag_match) * 0.001
                + float(project_match) * 0.001
                + float(preference) * 0.001,
            )
            state.score = fused + boost
        ranked = sorted(states.values(), key=lambda state: str(state.row["id"]))
        ranked.sort(key=lambda state: str(state.row["updated_at"]), reverse=True)
        ranked.sort(key=lambda state: len(state.channel_ranks), reverse=True)
        ranked.sort(key=lambda state: state.score, reverse=True)
        return ranked


class V2LexicalRanker:
    """Bounded lexical retrieval with reciprocal-rank fusion."""

    def __init__(
        self,
        channels: LexicalCandidateChannels | None = None,
        fusion: ReciprocalRankFusion | None = None,
    ) -> None:
        self.channels = channels or LexicalCandidateChannels()
        self.fusion = fusion or ReciprocalRankFusion()
        self.explanations: Sequence[RankingExplanation] = ()

    def rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> list[sqlite3.Row]:
        rows, explanations = self.rank_with_explanations(connection, candidates, query)
        self.explanations = explanations
        return rows

    def rank_with_explanations(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> tuple[list[sqlite3.Row], tuple[RankingExplanation, ...]]:
        """Return call-local diagnostics so concurrent principals cannot mix state."""
        if not query:
            ordered = sorted(
                candidates,
                key=lambda row: (str(row["updated_at"]), str(row["id"])),
                reverse=True,
            )
            explanations = tuple(
                RankingExplanation(str(row["id"]), 0.0, {}, {"recency_tiebreak": 1.0})
                for row in ordered
            )
            return ordered, explanations
        ranked = self.fusion.rank(self.channels.collect(connection, candidates, query), query)
        explanations = tuple(
            RankingExplanation(
                record_id=str(state.row["id"]),
                score=round(state.score, 9),
                channel_ranks=dict(state.channel_ranks),
                signals=dict(state.signals),
            )
            for state in ranked
        )
        return [state.row for state in ranked], explanations


def _dedupe_tokens(value: str) -> set[str]:
    suffixes = ("ing", "ed", "es", "s")
    result: set[str] = set()
    for token in _tokens(value):
        for suffix in suffixes:
            if len(token) > len(suffix) + 3 and token.endswith(suffix):
                token = token[: -len(suffix)]
                break
        if token not in {"a", "an", "the", "in", "of", "to", "with"}:
            result.add(token)
    return result


class ContextCompiler:
    """Compile deduplicated, diverse mandatory/primary/supporting context."""

    @staticmethod
    def _cost(item: ContextRecordOut) -> int:
        return len(item.content) + 64

    @staticmethod
    def _is_supporting(item: ContextRecordOut) -> bool:
        kind = item.kind.casefold()
        return item.evidence is not None or any(
            marker in kind for marker in ("evidence", "reference", "source", "support")
        )

    @staticmethod
    def _diversify(items: Sequence[ContextRecordOut]) -> list[ContextRecordOut]:
        remaining = list(enumerate(items))
        ordered: list[ContextRecordOut] = []
        seen: set[tuple[str, str]] = set()
        while remaining:
            def novelty(entry: tuple[int, ContextRecordOut]) -> tuple[int, int]:
                index, item = entry
                dimensions = {("kind", item.kind)}
                dimensions.update(
                    ("project", scope.casefold())
                    for scope in item.scopes
                    if scope.casefold().startswith("project:")
                )
                if item.source_service:
                    dimensions.add(("source", item.source_service))
                return (len(dimensions - seen), -index)

            chosen = max(remaining, key=novelty)
            remaining.remove(chosen)
            item = chosen[1]
            ordered.append(item)
            seen.add(("kind", item.kind))
            seen.update(
                ("project", scope.casefold())
                for scope in item.scopes
                if scope.casefold().startswith("project:")
            )
            if item.source_service:
                seen.add(("source", item.source_service))
        return ordered

    @staticmethod
    def _duplicate(item: ContextRecordOut, selected: Sequence[ContextRecordOut]) -> bool:
        normalized = _normalized_text(item.content)
        tokens = _dedupe_tokens(item.content)
        for existing in selected:
            if normalized == _normalized_text(existing.content):
                return True
            other = _dedupe_tokens(existing.content)
            shared = len(tokens & other)
            if shared >= 4 and shared / max(1, min(len(tokens), len(other))) >= 0.8:
                return True
        return False

    def compile(
        self,
        mandatory: Sequence[ContextRecordOut],
        relevant: Sequence[ContextRecordOut],
        budget_chars: int,
    ) -> tuple[list[ContextRecordOut], int]:
        mandatory_ordered = self._diversify(mandatory)
        primary = self._diversify([item for item in relevant if not self._is_supporting(item)])
        supporting = self._diversify([item for item in relevant if self._is_supporting(item)])
        reserve = min(budget_chars, max(256, budget_chars // 3))
        selected: list[ContextRecordOut] = []
        used = 0
        mandatory_used = 0
        for item in mandatory_ordered:
            cost = self._cost(item)
            if mandatory_used + cost > reserve or used + cost > budget_chars:
                continue
            if not self._duplicate(item, selected):
                selected.append(item)
                used += cost
                mandatory_used += cost
        mandatory_ids = {item.id for item in mandatory}
        for item in [*primary, *supporting]:
            if item.id in mandatory_ids or self._duplicate(item, selected):
                continue
            cost = self._cost(item)
            if used + cost <= budget_chars:
                selected.append(item)
                used += cost
        return selected, used


class RetrievalEngine:
    """Stable retrieval facade over policy, lexical ranking, and compilation."""

    def __init__(
        self,
        store: CoreStore,
        ranker: CandidateRanker | None = None,
        selector: EligibleRecordSelector | None = None,
        compiler: ContextCompiler | None = None,
    ) -> None:
        self.store = store
        self.ranker = ranker or V2LexicalRanker()
        self.selector = selector or EligibleRecordSelector()
        self.compiler = compiler or ContextCompiler()

    def _search(
        self, request: SearchRequest, principal: ClientPrincipal | None
    ) -> tuple[SearchResponse, Sequence[RankingExplanation]]:
        with self.store.connect() as connection:
            authorized, denied = self.selector.select(
                connection, request, principal, self.store.vault_id()
            )
            if isinstance(self.ranker, V2LexicalRanker):
                ranked, explanations = self.ranker.rank_with_explanations(
                    connection, authorized, request.query
                )
            else:
                ranked = self.ranker.rank(connection, authorized, request.query)
                explanations = tuple(getattr(self.ranker, "explanations", ()))
        page = ranked[request.offset : request.offset + request.limit]
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
        return SearchResponse(items=items, total=len(ranked), trace_id=trace_id), explanations

    def search(
        self, request: SearchRequest, principal: ClientPrincipal | None = None
    ) -> SearchResponse:
        response, _explanations = self._search(request, principal)
        return response

    def diagnose_search(
        self,
        request: SearchRequest,
        principal: ClientPrincipal | None = None,
        *,
        local_administrator: bool = False,
    ) -> dict[str, Any]:
        """Return safe ranking details only to an administrator diagnostic caller."""
        if not local_administrator and (principal is None or "admin" not in principal.scopes):
            raise PermissionError("ranking diagnostics require administrator access")
        response, explanations = self._search(request, principal)
        returned_ids = {item.id for item in response.items}
        return {
            **response.model_dump(mode="json"),
            "ranking_explanations": [
                {
                    "record_id": item.record_id,
                    "score": item.score,
                    "channel_ranks": item.channel_ranks,
                    "signals": item.signals,
                }
                for item in explanations
                if item.record_id in returned_ids
            ],
        }

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
        mandatory_search = self.search(
            SearchRequest(query="", kinds=["interaction_preference"], limit=100), principal
        )
        relevant_search = self.search(
            SearchRequest(
                query=" ".join(part for part in query_parts if part),
                scopes=request.requested_scopes,
                limit=100,
            ),
            principal,
        )
        selected, used = self.compiler.compile(
            mandatory_search.items, relevant_search.items, request.budget_chars
        )
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

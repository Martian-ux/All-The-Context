"""Permission-first deterministic retrieval and context compilation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .admissibility import (
    AdmissibilityBatch,
    AdmissibilityCandidate,
    AdmissibilityConfig,
    AdmissibilityContext,
    AdmissibilitySignals,
    ConflictState,
    DeterministicAdmissibilityGate,
)
from .ids import new_id, utc_now
from .lexical_v3 import LexicalV3, VocabularyDiagnostics
from .models import (
    BootstrapRequest,
    BootstrapResponse,
    ContextRecordOut,
    SearchRequest,
    SearchResponse,
)
from .security import ClientPrincipal, record_is_allowed
from .set_selection import (
    DeterministicSetSelector,
    SetSelectionCandidate,
    total_budget_cost,
)
from .storage import CoreStore
from .temporal import (
    PolicyEligibility,
    TemporalDiagnostic,
    TemporalFact,
    TemporalMaintenanceResult,
    TemporalQuery,
    TemporalReason,
    TemporalResolution,
    TemporalSidecar,
    normalize_utc,
)

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

    def select_authorized(
        self,
        connection: sqlite3.Connection,
        request: SearchRequest,
        principal: ClientPrincipal | None,
        vault_id: str,
    ) -> tuple[list[sqlite3.Row], list[str]]:
        """Select authorization-safe rows while leaving time to the resolver.

        Deleted rows remain in this content-safe set so their terminal state can
        participate in temporal resolution. They are never returned by the
        resolver or exposed to relevance scoring.
        """

        conditions = ["r.vault_id=?", "r.approval_status='approved'"]
        parameters: list[Any] = [vault_id]
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
            preference = str(row["kind"]) == "interaction_preference" and bool(
                row["explicit_user_statement"]
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


class LexicalV3CandidateRanker:
    """Adapt candidate-scoped weighted FTS5 results to the retrieval facade."""

    def __init__(self, lexical: LexicalV3 | None = None) -> None:
        # Production asks for a small evidence pool before declaring the
        # high-precision channel sufficient. This preserves semantic facets
        # for set-level compilation while every fallback remains hard bounded.
        self.lexical = lexical or LexicalV3(prefix_fallback_min_results=2)
        self.explanations: Sequence[RankingExplanation] = ()
        self.diagnostics: VocabularyDiagnostics | None = None

    def rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> list[sqlite3.Row]:
        rows, explanations, diagnostics = self.rank_with_explanations(
            connection, candidates, query, limit=100
        )
        self.explanations = explanations
        self.diagnostics = diagnostics
        return rows

    def rank_with_explanations(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
        *,
        limit: int,
    ) -> tuple[list[sqlite3.Row], tuple[RankingExplanation, ...], VocabularyDiagnostics | None]:
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
            return ordered, explanations, None
        candidate_ids = tuple(str(row["id"]) for row in candidates)
        result = self.lexical.search(
            connection,
            candidate_ids,
            query,
            limit=limit,
        )
        hit_ids = tuple(hit.record_id for hit in result.hits)
        if hit_ids:
            placeholders = ",".join("?" for _ in hit_ids)
            matched_rows = connection.execute(
                f"SELECT * FROM context_records WHERE id IN ({placeholders})",
                hit_ids,
            ).fetchall()
        else:
            matched_rows = []
        by_id = {str(row["id"]): row for row in matched_rows}
        explanations = tuple(
            RankingExplanation(
                record_id=hit.record_id,
                score=hit.score,
                channel_ranks={channel: rank for channel in hit.matched_channels},
                signals={
                    "weighted_bm25": hit.score,
                    "prefix_fallback": float(hit.best_channel == "prefix"),
                },
            )
            for rank, hit in enumerate(result.hits, 1)
        )
        return (
            [by_id[hit.record_id] for hit in result.hits if hit.record_id in by_id],
            explanations,
            result.diagnostics,
        )


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
    """Compile a compatible context set with deterministic marginal utility."""

    def __init__(self, selector: DeterministicSetSelector | None = None) -> None:
        self.selector = selector or DeterministicSetSelector()

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

    @staticmethod
    def _opaque_label(domain: str, value: str) -> str:
        digest = hashlib.sha256(f"{domain}\0{value}".encode()).hexdigest()
        return f"{domain}:{digest}"

    @classmethod
    def _redundancy_groups(cls, items: Sequence[ContextRecordOut]) -> dict[str, frozenset[str]]:
        """Return transitive near-duplicate groups without exposing record text."""

        parents = list(range(len(items)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[max(left_root, right_root)] = min(left_root, right_root)

        for left_index, left in enumerate(items):
            for right_index in range(left_index + 1, len(items)):
                if cls._duplicate(items[right_index], (left,)):
                    union(left_index, right_index)

        components: dict[int, list[str]] = {}
        for index, item in enumerate(items):
            components.setdefault(find(index), []).append(item.id)
        labels: dict[str, frozenset[str]] = {item.id: frozenset() for item in items}
        for member_ids in components.values():
            if len(member_ids) < 2:
                continue
            label = cls._opaque_label("redundancy", "\0".join(sorted(member_ids)))
            for record_id in member_ids:
                labels[record_id] = frozenset({label})
        return labels

    @classmethod
    def _semantic_facets(cls, item: ContextRecordOut) -> frozenset[str]:
        values = {f"kind:{item.kind.casefold()}"}
        values.update(f"token:{token}" for token in _dedupe_tokens(item.content))
        values.update(f"tag:{tag.casefold()}" for tag in item.tags)
        if item.entity_key and item.attribute_key:
            values.add(f"slot:{item.entity_key.casefold()}\0{item.attribute_key.casefold()}")
        return frozenset(cls._opaque_label("semantic", value) for value in values)

    @classmethod
    def _diversity_dimensions(cls, item: ContextRecordOut) -> frozenset[str]:
        values = {f"kind:{item.kind.casefold()}"}
        values.update(
            f"project:{scope.casefold()}"
            for scope in item.scopes
            if scope.casefold().startswith("project:")
        )
        for name, value in (
            ("source", item.source_id),
            ("service", item.source_service),
            ("type", item.source_type),
        ):
            if value:
                values.add(f"{name}:{value.casefold()}")
        return frozenset(cls._opaque_label("diversity", value) for value in values)

    @classmethod
    def _conflict_groups(cls, item: ContextRecordOut) -> frozenset[str]:
        if not item.entity_key or not item.attribute_key:
            return frozenset()
        slot = f"{item.entity_key.casefold()}\0{item.attribute_key.casefold()}"
        return frozenset({cls._opaque_label("conflict", slot)})

    def compile(
        self,
        mandatory: Sequence[ContextRecordOut],
        relevant: Sequence[ContextRecordOut],
        budget_chars: int,
    ) -> tuple[list[ContextRecordOut], int]:
        from .retrieval_contracts import SetSelectionConstraints

        ordered: list[ContextRecordOut] = []
        mandatory_ids = {item.id for item in mandatory}
        seen_ids: set[str] = set()
        for item in (*mandatory, *relevant):
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                ordered.append(item)

        redundancy = self._redundancy_groups(ordered)
        primary = [
            item
            for item in ordered
            if item.id not in mandatory_ids and not self._is_supporting(item)
        ]
        candidates: list[SetSelectionCandidate] = []
        count = len(ordered)
        for index, item in enumerate(ordered):
            mandatory_preference = item.id in mandatory_ids
            supports: set[str] = set()
            if not mandatory_preference and self._is_supporting(item):
                supports.update(
                    target.id
                    for target in primary
                    if (item.source_id is not None and item.source_id == target.source_id)
                    or (
                        item.entity_key is not None
                        and item.entity_key == target.entity_key
                        and item.attribute_key == target.attribute_key
                    )
                )
                # Preserve the established primary-before-evidence boundary when
                # imported evidence lacks explicit source/slot relationships.
                if not supports:
                    supports.update(target.id for target in primary)
            candidates.append(
                SetSelectionCandidate(
                    key=item.id,
                    budget_cost=self._cost(item),
                    base_utility=(count - index) * 1_000,
                    semantic_facets=self._semantic_facets(item),
                    diversity_dimensions=self._diversity_dimensions(item),
                    redundancy_groups=redundancy[item.id],
                    conflict_groups=self._conflict_groups(item),
                    supports=frozenset(supports),
                    mandatory_interaction_preference=mandatory_preference,
                    policy_authorized=True,
                    temporally_eligible=True,
                    task_admissible=True,
                )
            )
        selection = self.selector.select(
            candidates,
            SetSelectionConstraints(limit=len(candidates), budget=budget_chars),
        )
        by_id = {item.id: item for item in ordered}
        selected = [by_id[candidate.key] for candidate in selection.candidates]
        return selected, total_budget_cost(selection.candidates)


def _temporal_sidecar_path(database_path: Path) -> Path:
    resolved = database_path.resolve()
    return resolved.with_name(f"{resolved.stem}.retrieval-temporal.sqlite3")


def _series_roots(rows: Sequence[sqlite3.Row]) -> dict[str, str]:
    parents = {
        str(row["id"]): str(row["supersedes"]) for row in rows if row["supersedes"] is not None
    }
    roots: dict[str, str] = {}
    for record_id in sorted(str(row["id"]) for row in rows):
        path: list[str] = []
        positions: dict[str, int] = {}
        current = record_id
        while current in parents:
            if current in positions:
                cycle = path[positions[current] :]
                current = min(cycle)
                break
            positions[current] = len(path)
            path.append(current)
            current = parents[current]
        roots[record_id] = current
    return roots


def _canonical_temporal_facts(
    connection: sqlite3.Connection, vault_id: str
) -> tuple[TemporalFact, ...]:
    """Project content-free canonical metadata into rebuildable temporal facts."""

    rows = connection.execute(
        "SELECT id,created_at,updated_at,valid_from,expires_at,supersedes,version,deleted_at "
        "FROM context_records WHERE vault_id=? AND approval_status='approved' ORDER BY id",
        (vault_id,),
    ).fetchall()
    roots = _series_roots(rows)
    facts: list[TemporalFact] = []
    for row in rows:
        record_id = str(row["id"])
        valid_from = str(row["valid_from"]) if row["valid_from"] is not None else None
        expires_at = str(row["expires_at"]) if row["expires_at"] is not None else None
        # Imported records can already be expired when approved. With no
        # asserted validity start, model those as historical rather than
        # rejecting the entire sidecar because ingestion happened later.
        if valid_from is None and expires_at is not None and expires_at <= str(row["created_at"]):
            valid_from = "0001-01-01T00:00:00+00:00"
        facts.append(
            TemporalFact.active(
                record_id=record_id,
                series_key=f"record:{roots[record_id]}",
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                valid_from=valid_from,
                expires_at=expires_at,
                supersedes_record_id=(
                    str(row["supersedes"]) if row["supersedes"] is not None else None
                ),
                revision=int(row["version"]),
            )
        )
        if row["deleted_at"] is not None:
            facts.append(
                TemporalFact.deleted(
                    record_id=record_id,
                    deleted_at=str(row["deleted_at"]),
                )
            )
    tombstones = connection.execute(
        "SELECT stable_id,purged_at FROM purge_tombstones "
        "WHERE vault_id=? AND target_type='record' ORDER BY stable_id",
        (vault_id,),
    ).fetchall()
    facts.extend(
        TemporalFact.purged(record_id=str(row["stable_id"]), purged_at=str(row["purged_at"]))
        for row in tombstones
    )
    return tuple(facts)


def _canonical_temporal_marker(
    connection: sqlite3.Connection, vault_id: str
) -> tuple[int, int, str, int, str]:
    """Return a cheap mutation marker for public temporal write paths."""

    records = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(version),0),COALESCE(MAX(updated_at),'') "
        "FROM context_records WHERE vault_id=? AND approval_status='approved'",
        (vault_id,),
    ).fetchone()
    purges = connection.execute(
        "SELECT COUNT(*),COALESCE(MAX(purged_at),'') FROM purge_tombstones "
        "WHERE vault_id=? AND target_type='record'",
        (vault_id,),
    ).fetchone()
    assert records is not None and purges is not None
    return (
        int(records[0]),
        int(records[1]),
        str(records[2]),
        int(purges[0]),
        str(purges[1]),
    )


def _current_temporal_special_ids(facts: Sequence[TemporalFact], instant: str) -> frozenset[str]:
    """Identify records that need sidecar work for current-time resolution."""

    superseded = {
        fact.supersedes_record_id for fact in facts if fact.supersedes_record_id is not None
    }
    return frozenset(
        fact.record_id
        for fact in facts
        if fact.terminal_at is not None
        or fact.valid_from is not None
        or fact.valid_to is not None
        or fact.expires_at is not None
        or fact.supersedes_record_id is not None
        or fact.record_id in superseded
        or (fact.created_at is not None and normalize_utc(fact.created_at) > instant)
    )


def _with_trivial_temporal_selections(
    diagnostics: Sequence[TemporalDiagnostic], trivial_count: int
) -> tuple[TemporalDiagnostic, ...]:
    counts = {item.reason_code: item.count for item in diagnostics}
    counts[TemporalReason.SELECTED] = counts.get(TemporalReason.SELECTED, 0) + trivial_count
    return tuple(
        TemporalDiagnostic(reason_code=reason, count=counts[reason])
        for reason in TemporalReason
        if counts.get(reason, 0)
    )


def _conflict_states(
    connection: sqlite3.Connection, record_ids: Iterable[str]
) -> dict[str, ConflictState]:
    eligible = set(record_ids)
    states = dict.fromkeys(eligible, ConflictState.CLEAR)
    if not eligible:
        return states
    rows = connection.execute(
        "SELECT m.record_id,g.group_type,g.status FROM integrity_group_members m "
        "JOIN integrity_groups g ON g.id=m.group_id ORDER BY m.record_id,g.id"
    ).fetchall()
    for row in rows:
        record_id = str(row["record_id"])
        if record_id not in states or str(row["group_type"]) != "conflict":
            continue
        state = ConflictState.ACTIVE if str(row["status"]) == "open" else ConflictState.RESOLVED
        if state is ConflictState.ACTIVE or states[record_id] is ConflictState.CLEAR:
            states[record_id] = state
    return states


def _hydrate_ranked_rows(
    connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> list[sqlite3.Row]:
    if not rows or "content" in rows[0]:
        return list(rows)
    record_ids = [str(row["id"]) for row in rows]
    hydrated: dict[str, sqlite3.Row] = {}
    for start in range(0, len(record_ids), 500):
        chunk = record_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        fetched = connection.execute(
            f"SELECT * FROM context_records WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        hydrated.update((str(row["id"]), row) for row in fetched)
    return [hydrated[record_id] for record_id in record_ids if record_id in hydrated]


def _specificity(tokens: set[str]) -> float | None:
    return None if not tokens else round(min(1.0, len(tokens) / 3.0), 6)


def _admissibility_inputs(
    rows: Sequence[sqlite3.Row],
    request: SearchRequest,
    conflicts: dict[str, ConflictState],
) -> tuple[list[AdmissibilityCandidate], AdmissibilityContext]:
    raw_query_tokens = set(_tokens(request.query))
    query_tokens = set(raw_query_tokens)
    for token in raw_query_tokens:
        query_tokens.update(_LEXICAL_ALIASES.get(token, ()))
    candidates: list[AdmissibilityCandidate] = []
    requested_scopes = set(request.scopes)
    requested_kinds = set(request.kinds)
    project_scope = (
        f"project:{request.current_project.casefold()}"
        if request.current_project is not None
        else None
    )
    for row in rows:
        record_id = str(row["id"])
        searchable_tokens = set(
            _tokens(
                " ".join(
                    (
                        str(row["content"]),
                        str(row["kind"]),
                        " ".join(_json_set(str(row["tags_json"]))),
                        " ".join(_json_set(str(row["scopes_json"]))),
                    )
                )
            )
        )
        coverage = (
            len(query_tokens & searchable_tokens) / len(raw_query_tokens)
            if raw_query_tokens
            else None
        )
        row_scopes = _json_set(str(row["scopes_json"]))
        if project_scope is not None:
            scope_fit = float(project_scope in {scope.casefold() for scope in row_scopes})
        elif requested_scopes:
            scope_fit = len(requested_scopes & row_scopes) / len(requested_scopes)
        else:
            scope_fit = None
        kind_tokens = set(_tokens(str(row["kind"])))
        kind_fit = (
            1.0
            if requested_kinds and str(row["kind"]) in requested_kinds
            else (
                float(bool(query_tokens & kind_tokens))
                if request.current_project is not None and raw_query_tokens
                else None
            )
        )
        candidates.append(
            AdmissibilityCandidate(
                key=record_id,
                candidate_authorized=True,
                candidate_temporally_eligible=True,
                evidence_authorized=True,
                evidence_temporally_eligible=True,
                signals=AdmissibilitySignals(
                    task_query_coverage=(
                        round(min(1.0, coverage), 6) if coverage is not None else None
                    ),
                    scope_project_fit=(round(scope_fit, 6) if scope_fit is not None else None),
                    kind_compatibility=kind_fit,
                    confidence=float(row["confidence"]),
                    explicitness=float(bool(row["explicit_user_statement"])),
                    conflict_state=conflicts.get(record_id, ConflictState.CLEAR),
                ),
            )
        )
    specificity = (
        0.0
        if len(rows) == 1 or any(token in _LEXICAL_ALIASES for token in raw_query_tokens)
        else _specificity(raw_query_tokens)
    )
    return candidates, AdmissibilityContext(
        query_specificity=specificity,
        task_specificity=specificity,
    )


@dataclass(frozen=True, slots=True)
class _PipelineDiagnostics:
    temporal_maintenance: TemporalMaintenanceResult | None = None
    temporal: tuple[TemporalDiagnostic, ...] = ()
    lexical: VocabularyDiagnostics | None = None
    admissibility: AdmissibilityBatch | None = None

    def safe_dict(self) -> dict[str, Any]:
        lexical = self.lexical
        admissibility = self.admissibility
        return {
            "temporal": {
                "maintenance_reason": (
                    self.temporal_maintenance.reason_code.value
                    if self.temporal_maintenance is not None
                    else None
                ),
                "reason_counts": {item.reason_code.value: item.count for item in self.temporal},
            },
            "lexical": (
                None
                if lexical is None
                else {
                    "eligible_candidate_count": lexical.eligible_candidate_count,
                    "indexed_candidate_count": lexical.indexed_candidate_count,
                    "normalized_token_count": lexical.normalized_token_count,
                    "token_category_counts": dict(lexical.token_category_counts),
                    "high_precision_match_count": lexical.high_precision_match_count,
                    "exact_fallback_match_count": lexical.exact_fallback_match_count,
                    "prefix_match_count": lexical.prefix_match_count,
                    "prefix_token_count": lexical.prefix_token_count,
                    "query_truncated": lexical.query_truncated,
                    "secure_delete_status": lexical.secure_delete_status.value,
                    "reason_codes": [item.value for item in lexical.reason_codes],
                }
            ),
            "admissibility": (
                None
                if admissibility is None
                else {
                    "evaluated_count": admissibility.diagnostics.evaluated_count,
                    "admitted_count": admissibility.diagnostics.admitted_count,
                    "rejected_count": admissibility.diagnostics.rejected_count,
                    "fail_open_count": admissibility.diagnostics.fail_open_count,
                    "minimum_score": admissibility.diagnostics.minimum_score,
                    "mean_score": admissibility.diagnostics.mean_score,
                    "maximum_score": admissibility.diagnostics.maximum_score,
                    "had_rejections": admissibility.diagnostics.had_rejections,
                    "reason_counts": {
                        reason.value: count
                        for reason, count in admissibility.diagnostics.reason_counts
                    },
                }
            ),
        }


class RetrievalEngine:
    """Stable retrieval facade over policy, lexical ranking, and compilation."""

    def __init__(
        self,
        store: CoreStore,
        ranker: CandidateRanker | None = None,
        selector: EligibleRecordSelector | None = None,
        compiler: ContextCompiler | None = None,
        temporal_sidecar: TemporalSidecar | None = None,
        admissibility_gate: DeterministicAdmissibilityGate | None = None,
    ) -> None:
        self.store = store
        self.ranker = ranker or LexicalV3CandidateRanker()
        self.selector = selector or EligibleRecordSelector()
        self.compiler = compiler or ContextCompiler()
        self.temporal_sidecar = temporal_sidecar or TemporalSidecar(
            _temporal_sidecar_path(store.database_path)
        )
        self.admissibility_gate = admissibility_gate or DeterministicAdmissibilityGate(
            AdmissibilityConfig(rejection_threshold=0.70)
        )
        self._frozen_pipeline = bool(getattr(self.ranker, "frozen_pipeline", False))
        self._temporal_marker: tuple[int, int, str, int, str] | None = None
        self._current_temporal_special: frozenset[str] = frozenset()

    def _rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        request: SearchRequest,
    ) -> tuple[list[sqlite3.Row], Sequence[RankingExplanation], VocabularyDiagnostics | None]:
        if isinstance(self.ranker, LexicalV3CandidateRanker):
            return self.ranker.rank_with_explanations(
                connection,
                candidates,
                request.query,
                limit=100,
            )
        if isinstance(self.ranker, V2LexicalRanker):
            ranked, explanations = self.ranker.rank_with_explanations(
                connection, candidates, request.query
            )
            return ranked, explanations, None
        ranked = self.ranker.rank(connection, candidates, request.query)
        return ranked, tuple(getattr(self.ranker, "explanations", ())), None

    def _v3_rows(
        self,
        connection: sqlite3.Connection,
        request: SearchRequest,
        principal: ClientPrincipal | None,
    ) -> tuple[
        list[sqlite3.Row],
        Sequence[RankingExplanation],
        list[str],
        _PipelineDiagnostics,
    ]:
        vault_id = self.store.vault_id()
        authorized, denied = self.selector.select_authorized(
            connection, request, principal, vault_id
        )
        current_instant = utc_now()
        marker = _canonical_temporal_marker(connection, vault_id)
        if marker != self._temporal_marker:
            facts = _canonical_temporal_facts(connection, vault_id)
            maintenance = self.temporal_sidecar.recover(facts)
            self._current_temporal_special = _current_temporal_special_ids(facts, current_instant)
            self._temporal_marker = marker
        else:
            maintenance = self.temporal_sidecar.initialize()
        temporal_query = (
            TemporalQuery.as_of(request.as_of)
            if request.as_of is not None
            else TemporalQuery.current(at=current_instant)
        )
        authorized_ids = frozenset(str(row["id"]) for row in authorized)
        sidecar_eligible = (
            authorized_ids
            if request.as_of is not None
            else authorized_ids & self._current_temporal_special
        )
        resolution: TemporalResolution = self.temporal_sidecar.resolve(
            temporal_query,
            PolicyEligibility.after_hard_policy(sidecar_eligible),
        )
        trivial_ids = (
            frozenset()
            if request.as_of is not None
            else authorized_ids - self._current_temporal_special
        )
        selected_ids = set(resolution.selected_record_ids) | set(trivial_ids)
        by_id = {str(row["id"]): row for row in authorized}
        temporally_eligible = [row for record_id, row in by_id.items() if record_id in selected_ids]
        ranked, explanations, lexical = self._rank(connection, temporally_eligible, request)
        ranked = _hydrate_ranked_rows(connection, ranked)
        conflicts = _conflict_states(connection, (str(row["id"]) for row in ranked))
        gate_inputs, gate_context = _admissibility_inputs(ranked, request, conflicts)
        admissibility = self.admissibility_gate.evaluate_many(gate_inputs, gate_context)
        admitted = {decision.key for decision in admissibility.decisions if decision.admitted}
        gated = [row for row in ranked if str(row["id"]) in admitted]
        return (
            gated,
            explanations,
            denied,
            _PipelineDiagnostics(
                temporal_maintenance=maintenance,
                temporal=_with_trivial_temporal_selections(
                    resolution.diagnostics, len(trivial_ids)
                ),
                lexical=lexical,
                admissibility=admissibility,
            ),
        )

    def _search(
        self, request: SearchRequest, principal: ClientPrincipal | None
    ) -> tuple[SearchResponse, Sequence[RankingExplanation], _PipelineDiagnostics]:
        with self.store.connect() as connection:
            explanations: Sequence[RankingExplanation]
            if self._frozen_pipeline:
                authorized, denied = self.selector.select(
                    connection, request, principal, self.store.vault_id()
                )
                ranked = self.ranker.rank(connection, authorized, request.query)
                explanations = tuple(getattr(self.ranker, "explanations", ()))
                diagnostics = _PipelineDiagnostics()
            else:
                ranked, explanations, denied, diagnostics = self._v3_rows(
                    connection, request, principal
                )
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
                "temporal_mode": "as_of" if request.as_of is not None else "current",
            },
        )
        return (
            SearchResponse(items=items, total=len(ranked), trace_id=trace_id),
            explanations,
            diagnostics,
        )

    def search(
        self, request: SearchRequest, principal: ClientPrincipal | None = None
    ) -> SearchResponse:
        response, _explanations, _diagnostics = self._search(request, principal)
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
        response, explanations, diagnostics = self._search(request, principal)
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
            "pipeline_diagnostics": diagnostics.safe_dict(),
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
                current_project=request.current_project,
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

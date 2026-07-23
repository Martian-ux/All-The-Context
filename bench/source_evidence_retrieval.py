"""Bounded Wave 2 research benchmark for long imported-chat source evidence.

This module has no production authority or runtime integration. It freezes the
current candidate-scoped ``LexicalV3`` implementation as the source comparator,
then evaluates deterministic passage selectors over only those candidate
sources. Imported message text is always opaque data: it is indexed and
tokenized, never interpreted as instructions or configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from allthecontext.lexical_v3 import LexicalV3

FIXTURE_PATH = Path(__file__).with_name("source_evidence_fixtures.json")
FROZEN_COMPARATOR_COMMIT = "659c79136b5d5ba66b9cc5e38640a9d3f341cff3"
NORMAL_SOURCE_COUNTS = (64, 256)
MAX_NORMAL_SOURCE_COUNT = 256
MAX_SOURCE_COUNT = 1_024
MAX_REPETITIONS = 20
MAX_MESSAGES_PER_SOURCE = 64
MAX_MESSAGE_CHARS = 4_096
MAX_QUERY_CHARS = 512

CURRENT_LEXICAL_POOL = "current_lexical_candidate_pool"
LEXICAL_PASSAGES = "lexical_passages"
PASSAGE_MAXSIM = "deterministic_passage_maxsim"
DIVERSE_MAXSIM = "deterministic_diverse_maxsim"
VARIANT_IDS = (
    CURRENT_LEXICAL_POOL,
    LEXICAL_PASSAGES,
    PASSAGE_MAXSIM,
    DIVERSE_MAXSIM,
)

_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "applied",
        "for",
        "how",
        "it",
        "of",
        "the",
        "to",
        "was",
        "what",
        "when",
        "which",
        "why",
    }
)


@dataclass(frozen=True, slots=True)
class Passage:
    """One message from an imported chat; its text remains untrusted data."""

    passage_id: str
    source_id: str
    text: str
    duplicate_group: str | None


@dataclass(frozen=True, slots=True)
class Source:
    """A sanitized imported chat and its source-level eligibility decision."""

    source_id: str
    eligible: bool
    duplicate_group: str | None
    passages: tuple[Passage, ...]


@dataclass(frozen=True, slots=True)
class Query:
    """Gold evidence and facets for one source-evidence question."""

    query_id: str
    text: str
    gold_evidence: frozenset[str]
    facets: tuple[tuple[str, frozenset[str]], ...]


@dataclass(frozen=True, slots=True)
class Fixture:
    candidate_limit: int
    evidence_limit: int
    sources: tuple[Source, ...]
    queries: tuple[Query, ...]
    research_paths: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class Selection:
    selection_kind: str
    item_ids: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScoredPassage:
    passage_id: str
    source_id: str
    score: float
    matched_query_tokens: frozenset[str]
    content_tokens: frozenset[str]


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, label)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def load_fixture(path: Path = FIXTURE_PATH) -> Fixture:
    """Load and bound the checked-in sanitized fixture without interpreting text."""
    raw = _mapping(json.loads(path.read_text(encoding="utf-8")), "fixture")
    if raw.get("schema_version") != 1:
        raise ValueError("fixture schema_version must be 1")
    if raw.get("corpus_kind") != "sanitized_imported_chat_source_evidence":
        raise ValueError("fixture must contain sanitized imported-chat source evidence")
    candidate_limit = int(raw.get("candidate_limit", 0))
    evidence_limit = int(raw.get("evidence_limit", 0))
    if not 1 <= candidate_limit <= 32:
        raise ValueError("candidate_limit must be between 1 and 32")
    if not 1 <= evidence_limit <= 10:
        raise ValueError("evidence_limit must be between 1 and 10")

    sources: list[Source] = []
    source_ids: set[str] = set()
    passage_ids: set[str] = set()
    passage_eligibility: dict[str, bool] = {}
    for source_index, source_value in enumerate(_sequence(raw.get("sources"), "sources")):
        source_data = _mapping(source_value, f"sources[{source_index}]")
        source_id = _required_string(source_data.get("id"), f"sources[{source_index}].id")
        if source_id in source_ids:
            raise ValueError(f"duplicate source id: {source_id}")
        source_ids.add(source_id)
        if source_data.get("kind") != "imported_chat":
            raise ValueError(f"source {source_id} is not an imported_chat")
        eligible_value = source_data.get("eligible")
        if not isinstance(eligible_value, bool):
            raise ValueError(f"source {source_id} eligibility must be boolean")
        messages = _sequence(source_data.get("messages"), f"source {source_id} messages")
        if not 8 <= len(messages) <= MAX_MESSAGES_PER_SOURCE:
            raise ValueError(
                f"source {source_id} must contain 8 to {MAX_MESSAGES_PER_SOURCE} messages"
            )
        passages: list[Passage] = []
        for message_index, message_value in enumerate(messages):
            message = _mapping(message_value, f"source {source_id} messages[{message_index}]")
            passage_id = _required_string(message.get("id"), f"source {source_id} message id")
            if passage_id in passage_ids:
                raise ValueError(f"duplicate passage id: {passage_id}")
            passage_ids.add(passage_id)
            # The role is validated only as inert fixture structure. It never changes
            # control flow, trust, eligibility, limits, or scoring.
            _required_string(message.get("role"), f"passage {passage_id} role")
            text = _required_string(message.get("text"), f"passage {passage_id} text")
            if len(text) > MAX_MESSAGE_CHARS:
                raise ValueError(
                    f"passage {passage_id} exceeds the {MAX_MESSAGE_CHARS} character cap"
                )
            passage_eligibility[passage_id] = eligible_value
            passages.append(
                Passage(
                    passage_id=passage_id,
                    source_id=source_id,
                    text=text,
                    duplicate_group=_optional_string(
                        message.get("duplicate_group"),
                        f"passage {passage_id} duplicate_group",
                    ),
                )
            )
        sources.append(
            Source(
                source_id=source_id,
                eligible=eligible_value,
                duplicate_group=_optional_string(
                    source_data.get("duplicate_group"),
                    f"source {source_id} duplicate_group",
                ),
                passages=tuple(passages),
            )
        )

    queries: list[Query] = []
    query_ids: set[str] = set()
    for query_index, query_value in enumerate(_sequence(raw.get("queries"), "queries")):
        query_data = _mapping(query_value, f"queries[{query_index}]")
        query_id = _required_string(query_data.get("id"), f"queries[{query_index}].id")
        if query_id in query_ids:
            raise ValueError(f"duplicate query id: {query_id}")
        query_ids.add(query_id)
        text = _required_string(query_data.get("query"), f"query {query_id} text")
        if len(text) > MAX_QUERY_CHARS:
            raise ValueError(f"query {query_id} exceeds the {MAX_QUERY_CHARS} character cap")
        gold = frozenset(
            _required_string(value, f"query {query_id} gold evidence")
            for value in _sequence(
                query_data.get("gold_evidence"), f"query {query_id} gold_evidence"
            )
        )
        if not gold or not gold <= passage_ids:
            raise ValueError(f"query {query_id} gold evidence must reference known passages")
        if any(not passage_eligibility[passage_id] for passage_id in gold):
            raise ValueError(f"query {query_id} gold evidence must be eligible")
        facets_data = _mapping(query_data.get("facets"), f"query {query_id} facets")
        facets: list[tuple[str, frozenset[str]]] = []
        for facet_name, accepted_value in sorted(facets_data.items()):
            accepted = frozenset(
                _required_string(value, f"query {query_id} facet {facet_name}")
                for value in _sequence(accepted_value, f"query {query_id} facet {facet_name}")
            )
            if not accepted or not accepted <= gold:
                raise ValueError(
                    f"query {query_id} facet {facet_name} must reference gold evidence"
                )
            facets.append((facet_name, accepted))
        if not facets:
            raise ValueError(f"query {query_id} must define at least one evidence facet")
        queries.append(Query(query_id, text, gold, tuple(facets)))

    paths: list[dict[str, str]] = []
    for path_index, path_value in enumerate(_sequence(raw.get("research_paths"), "research_paths")):
        path_data = _mapping(path_value, f"research_paths[{path_index}]")
        status = _required_string(path_data.get("status"), "research path status")
        if status != "not_exercised":
            raise ValueError("unmeasured research paths must be not_exercised")
        paths.append(
            {
                "id": _required_string(path_data.get("id"), "research path id"),
                "status": status,
                "reason": _required_string(path_data.get("reason"), "research path reason"),
            }
        )
    if not paths:
        raise ValueError("fixture must declare unexercised model-backed research paths")
    return Fixture(
        candidate_limit=candidate_limit,
        evidence_limit=evidence_limit,
        sources=tuple(sources),
        queries=tuple(queries),
        research_paths=tuple(paths),
    )


def select_source_counts(requested: Sequence[int], include_large: bool) -> tuple[int, ...]:
    counts = tuple(requested) if requested else NORMAL_SOURCE_COUNTS
    if any(count <= 0 for count in counts):
        raise ValueError("source counts must be positive")
    maximum = MAX_SOURCE_COUNT if include_large else MAX_NORMAL_SOURCE_COUNT
    if any(count > maximum for count in counts):
        suffix = "" if include_large else "; use --include-large above 256"
        raise ValueError(f"source counts must not exceed {maximum}{suffix}")
    return counts


def _normalized_tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(_TOKEN_RE.findall(normalized))


def _query_tokens(text: str) -> tuple[str, ...]:
    tokens = tuple(token for token in _normalized_tokens(text) if token not in _STOP_WORDS)
    return tuple(dict.fromkeys(tokens))


def _filler_source(index: int) -> Source:
    source_id = f"neutral-source-{index:04d}"
    passages = tuple(
        Passage(
            passage_id=f"neutral-{index:04d}-{message_index:02d}",
            source_id=source_id,
            text=(
                f"Synthetic neutral archive {index} segment {message_index} covers "
                "routine agenda rotation, sample ledger labels, and offline fixture cleanup."
            ),
            duplicate_group=None,
        )
        for message_index in range(8)
    )
    return Source(source_id, True, None, passages)


def _expanded_sources(fixture: Fixture, source_count: int) -> tuple[Source, ...]:
    if source_count < len(fixture.sources):
        raise ValueError(
            f"source count must be at least the {len(fixture.sources)} checked-in sources"
        )
    fillers = tuple(_filler_source(index) for index in range(source_count - len(fixture.sources)))
    return fixture.sources + fillers


def _build_fts(rows: Iterable[tuple[str, str]]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute(
        "CREATE VIRTUAL TABLE context_fts USING fts5("
        "record_id UNINDEXED, content, kind, tags, scopes, "
        "tokenize='unicode61 remove_diacritics 2')"
    )
    connection.executemany(
        "INSERT INTO context_fts(record_id, content, kind, tags, scopes) "
        "VALUES(?, ?, 'imported_chat', '', '')",
        rows,
    )
    return connection


def _sqlite_bytes(connection: sqlite3.Connection) -> int:
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    return page_count * page_size


class CorpusIndex:
    """Ephemeral research indices derived from explicitly eligible source IDs."""

    def __init__(self, sources: Sequence[Source], *, include_ineligible: bool) -> None:
        visible = tuple(source for source in sources if include_ineligible or source.eligible)
        self.sources = {source.source_id: source for source in visible}
        self.passages = {
            passage.passage_id: passage for source in visible for passage in source.passages
        }
        self.eligible_source_ids = tuple(
            sorted(source.source_id for source in visible if source.eligible)
        )
        source_started = time.perf_counter()
        self.source_connection = _build_fts(
            (
                source.source_id,
                "\n".join(passage.text for passage in source.passages),
            )
            for source in visible
        )
        self.source_build_ms = (time.perf_counter() - source_started) * 1_000
        passage_started = time.perf_counter()
        self.passage_connection = _build_fts(
            (passage.passage_id, passage.text) for source in visible for passage in source.passages
        )
        self.passage_build_ms = (time.perf_counter() - passage_started) * 1_000
        token_started = time.perf_counter()
        self.passage_tokens = {
            passage_id: frozenset(_normalized_tokens(passage.text))
            for passage_id, passage in sorted(self.passages.items())
        }
        self.token_build_ms = (time.perf_counter() - token_started) * 1_000
        serialized_tokens = [
            [passage_id, sorted(tokens)]
            for passage_id, tokens in sorted(self.passage_tokens.items())
        ]
        self.token_storage_bytes = len(
            json.dumps(serialized_tokens, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        self.source_fts_bytes = _sqlite_bytes(self.source_connection)
        self.passage_fts_bytes = _sqlite_bytes(self.passage_connection)

    def close(self) -> None:
        self.source_connection.close()
        self.passage_connection.close()

    def candidate_sources(self, query: Query, limit: int) -> tuple[str, ...]:
        result = LexicalV3().search(
            self.source_connection,
            self.eligible_source_ids,
            query.text,
            limit=limit,
        )
        return tuple(hit.record_id for hit in result.hits)

    def candidate_passages(self, source_ids: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            passage.passage_id
            for source_id in source_ids
            for passage in self.sources[source_id].passages
        )


def _current_pool_selection(index: CorpusIndex, query: Query, candidate_limit: int) -> Selection:
    source_ids = index.candidate_sources(query, candidate_limit)
    return Selection("source", source_ids, source_ids)


def _lexical_passage_selection(
    index: CorpusIndex, query: Query, candidate_limit: int, evidence_limit: int
) -> Selection:
    source_ids = index.candidate_sources(query, candidate_limit)
    candidate_passages = index.candidate_passages(source_ids)
    if not candidate_passages:
        return Selection("passage", (), ())
    result = LexicalV3().search(
        index.passage_connection,
        candidate_passages,
        query.text,
        limit=evidence_limit,
    )
    item_ids = tuple(hit.record_id for hit in result.hits)
    return Selection(
        "passage",
        item_ids,
        tuple(index.passages[passage_id].source_id for passage_id in item_ids),
    )


def _maxsim_scores(
    index: CorpusIndex, query: Query, source_ids: Sequence[str]
) -> tuple[ScoredPassage, ...]:
    candidate_ids = index.candidate_passages(source_ids)
    query_tokens = _query_tokens(query.text)
    if not candidate_ids or not query_tokens:
        return ()
    document_frequency = Counter(
        token
        for passage_id in candidate_ids
        for token in set(query_tokens) & index.passage_tokens[passage_id]
    )
    weights = {
        token: math.log((len(candidate_ids) + 1) / (document_frequency[token] + 1)) + 1.0
        for token in query_tokens
    }
    total_weight = sum(weights.values())
    scored: list[ScoredPassage] = []
    for passage_id in candidate_ids:
        content_tokens = index.passage_tokens[passage_id]
        matched = frozenset(token for token in query_tokens if token in content_tokens)
        if not matched:
            continue
        # Exact lexical MaxSim: for each query token, take its maximum interaction
        # with any token in this passage (1 for exact normalized equality, else 0).
        maxsim = sum(weights[token] for token in matched) / total_weight
        density = len(matched) / max(1.0, math.sqrt(len(content_tokens)))
        score = maxsim + (0.05 * density)
        passage = index.passages[passage_id]
        scored.append(
            ScoredPassage(
                passage_id=passage_id,
                source_id=passage.source_id,
                score=score,
                matched_query_tokens=matched,
                content_tokens=content_tokens,
            )
        )
    return tuple(sorted(scored, key=lambda item: (-item.score, item.passage_id)))


def _maxsim_selection(
    index: CorpusIndex, query: Query, candidate_limit: int, evidence_limit: int
) -> Selection:
    source_ids = index.candidate_sources(query, candidate_limit)
    ranked = _maxsim_scores(index, query, source_ids)[:evidence_limit]
    return Selection(
        "passage",
        tuple(item.passage_id for item in ranked),
        tuple(item.source_id for item in ranked),
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _diverse_maxsim_selection(
    index: CorpusIndex, query: Query, candidate_limit: int, evidence_limit: int
) -> Selection:
    source_ids = index.candidate_sources(query, candidate_limit)
    remaining = list(_maxsim_scores(index, query, source_ids))
    selected: list[ScoredPassage] = []
    covered: set[str] = set()
    query_tokens = frozenset(_query_tokens(query.text))
    while remaining and len(selected) < evidence_limit:
        utilities: list[tuple[float, str, ScoredPassage]] = []
        for candidate in remaining:
            novel = (
                len(candidate.matched_query_tokens - covered) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            overlap = max(
                (_jaccard(candidate.content_tokens, prior.content_tokens) for prior in selected),
                default=0.0,
            )
            if overlap >= 0.8:
                # Near-identical imported messages consume evidence budget without
                # adding query coverage. The threshold is fixed and corpus-independent.
                continue
            same_source = any(candidate.source_id == prior.source_id for prior in selected)
            utility = candidate.score + (0.35 * novel) - (0.35 * overlap)
            if same_source:
                utility -= 0.05
            utilities.append((utility, candidate.passage_id, candidate))
        if not utilities:
            break
        _utility, _passage_id, chosen = min(utilities, key=lambda item: (-item[0], item[1]))
        selected.append(chosen)
        covered.update(chosen.matched_query_tokens)
        remaining.remove(chosen)
    return Selection(
        "passage",
        tuple(item.passage_id for item in selected),
        tuple(item.source_id for item in selected),
    )


def _execute_variant(
    variant_id: str,
    index: CorpusIndex,
    query: Query,
    candidate_limit: int,
    evidence_limit: int,
) -> Selection:
    if variant_id == CURRENT_LEXICAL_POOL:
        return _current_pool_selection(index, query, candidate_limit)
    if variant_id == LEXICAL_PASSAGES:
        return _lexical_passage_selection(index, query, candidate_limit, evidence_limit)
    if variant_id == PASSAGE_MAXSIM:
        return _maxsim_selection(index, query, candidate_limit, evidence_limit)
    if variant_id == DIVERSE_MAXSIM:
        return _diverse_maxsim_selection(index, query, candidate_limit, evidence_limit)
    raise ValueError(f"unknown source-evidence variant: {variant_id}")


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(statistics.median(values), 6) if values else 0.0,
        "p95_ms": round(_percentile(values, 0.95), 6),
    }


def _selection_groups(selection: Selection, index: CorpusIndex) -> tuple[str | None, ...]:
    if selection.selection_kind == "source":
        return tuple(index.sources[source_id].duplicate_group for source_id in selection.item_ids)
    return tuple(index.passages[passage_id].duplicate_group for passage_id in selection.item_ids)


def _evaluate_selection(
    selection: Selection, query: Query, index: CorpusIndex
) -> dict[str, float | int]:
    selected_sources = set(selection.source_ids)
    if selection.selection_kind == "source":
        recovered = {
            passage_id
            for passage_id in query.gold_evidence
            if index.passages[passage_id].source_id in selected_sources
        }
    else:
        recovered = set(selection.item_ids) & query.gold_evidence
    gold_sources = {index.passages[passage_id].source_id for passage_id in query.gold_evidence}
    covered_facets = 0
    for _facet_name, accepted in query.facets:
        if selection.selection_kind == "source":
            covered_facets += any(
                index.passages[passage_id].source_id in selected_sources for passage_id in accepted
            )
        else:
            covered_facets += bool(set(selection.item_ids) & accepted)
    group_counts = Counter(
        group for group in _selection_groups(selection, index) if group is not None
    )
    duplicate_items = sum(max(0, count - 1) for count in group_counts.values())
    policy_violations = sum(
        not index.sources[source_id].eligible for source_id in selection.source_ids
    )
    return {
        "source_evidence_recall_at_limit": round(len(recovered) / len(query.gold_evidence), 6),
        "facet_coverage_at_limit": round(covered_facets / len(query.facets), 6),
        "gold_source_recall_at_limit": round(
            len(selected_sources & gold_sources) / len(gold_sources), 6
        ),
        "redundancy_at_limit": round(
            duplicate_items / len(selection.item_ids) if selection.item_ids else 0.0,
            6,
        ),
        "policy_violation_count": policy_violations,
        "selected_item_count": len(selection.item_ids),
    }


def _ranking_fingerprint(results: dict[str, Selection]) -> str:
    payload = [
        [query_id, selection.selection_kind, list(selection.item_ids)]
        for query_id, selection in sorted(results.items())
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def _mean_query_metric(evaluations: Sequence[dict[str, float | int]], metric: str) -> float:
    return round(
        statistics.fmean(float(evaluation[metric]) for evaluation in evaluations),
        6,
    )


def _variant_storage(index: CorpusIndex, variant_id: str) -> dict[str, int]:
    incremental = 0
    if variant_id == LEXICAL_PASSAGES:
        incremental = index.passage_fts_bytes
    elif variant_id in {PASSAGE_MAXSIM, DIVERSE_MAXSIM}:
        incremental = index.token_storage_bytes
    return {
        "persistent_growth_bytes": 0,
        "benchmark_ephemeral_index_bytes": index.source_fts_bytes + incremental,
        "incremental_over_candidate_pool_bytes": incremental,
    }


def _variant_build_ms(index: CorpusIndex, variant_id: str) -> float:
    incremental = 0.0
    if variant_id == LEXICAL_PASSAGES:
        incremental = index.passage_build_ms
    elif variant_id in {PASSAGE_MAXSIM, DIVERSE_MAXSIM}:
        incremental = index.token_build_ms
    return round(index.source_build_ms + incremental, 6)


def run_profile(source_count: int, fixture: Fixture, *, repetitions: int) -> dict[str, Any]:
    if not 1 <= repetitions <= MAX_REPETITIONS:
        raise ValueError(f"repetitions must be between 1 and {MAX_REPETITIONS}")
    sources = _expanded_sources(fixture, source_count)
    index = CorpusIndex(sources, include_ineligible=True)
    isolated = CorpusIndex(sources, include_ineligible=False)
    try:
        variants: dict[str, Any] = {}
        for variant_id in VARIANT_IDS:
            selections: dict[str, Selection] = {}
            cold: list[float] = []
            warm: list[float] = []
            repeated_deterministic = True
            ineligible_invariant = True
            evaluations: list[dict[str, float | int]] = []
            query_metrics: dict[str, dict[str, float | int]] = {}
            for query in fixture.queries:
                started = time.perf_counter()
                selection = _execute_variant(
                    variant_id,
                    index,
                    query,
                    fixture.candidate_limit,
                    fixture.evidence_limit,
                )
                cold.append((time.perf_counter() - started) * 1_000)
                selections[query.query_id] = selection
                evaluation = _evaluate_selection(selection, query, index)
                evaluations.append(evaluation)
                query_metrics[query.query_id] = evaluation
                for _ in range(repetitions):
                    started = time.perf_counter()
                    repeated = _execute_variant(
                        variant_id,
                        index,
                        query,
                        fixture.candidate_limit,
                        fixture.evidence_limit,
                    )
                    warm.append((time.perf_counter() - started) * 1_000)
                    repeated_deterministic &= repeated == selection
                without_ineligible = _execute_variant(
                    variant_id,
                    isolated,
                    query,
                    fixture.candidate_limit,
                    fixture.evidence_limit,
                )
                ineligible_invariant &= without_ineligible == selection
            policy_violations = sum(
                int(evaluation["policy_violation_count"]) for evaluation in evaluations
            )
            variants[variant_id] = {
                "status": "exercised",
                "selection_kind": selections[fixture.queries[0].query_id].selection_kind,
                "metrics": {
                    "source_evidence_recall_at_limit": _mean_query_metric(
                        evaluations, "source_evidence_recall_at_limit"
                    ),
                    "facet_coverage_at_limit": _mean_query_metric(
                        evaluations, "facet_coverage_at_limit"
                    ),
                    "gold_source_recall_at_limit": _mean_query_metric(
                        evaluations, "gold_source_recall_at_limit"
                    ),
                    "redundancy_at_limit": _mean_query_metric(evaluations, "redundancy_at_limit"),
                    "policy_violation_count": policy_violations,
                    "repeated_rankings_deterministic": repeated_deterministic,
                    "ineligible_corpus_invariance": ineligible_invariant,
                    "cold_latency": _latency_summary(cold),
                    "warm_latency": _latency_summary(warm),
                    "index_build_ms": _variant_build_ms(index, variant_id),
                    "storage": _variant_storage(index, variant_id),
                    "ranking_fingerprint_sha256": _ranking_fingerprint(selections),
                },
                "queries": query_metrics,
            }

        baseline = variants[CURRENT_LEXICAL_POOL]["metrics"]
        for variant in variants.values():
            metrics = variant["metrics"]
            variant["comparison_to_current_lexical_pool"] = {
                "source_evidence_recall_delta": round(
                    metrics["source_evidence_recall_at_limit"]
                    - baseline["source_evidence_recall_at_limit"],
                    6,
                ),
                "facet_coverage_delta": round(
                    metrics["facet_coverage_at_limit"] - baseline["facet_coverage_at_limit"],
                    6,
                ),
                "redundancy_delta": round(
                    metrics["redundancy_at_limit"] - baseline["redundancy_at_limit"],
                    6,
                ),
                "warm_p95_ms_delta": round(
                    metrics["warm_latency"]["p95_ms"] - baseline["warm_latency"]["p95_ms"],
                    6,
                ),
                "incremental_storage_bytes": metrics["storage"][
                    "incremental_over_candidate_pool_bytes"
                ],
            }
        safety_passed = all(
            variant["metrics"]["policy_violation_count"] == 0
            and variant["metrics"]["repeated_rankings_deterministic"]
            and variant["metrics"]["ineligible_corpus_invariance"]
            for variant in variants.values()
        )
        return {
            "source_count": source_count,
            "passage_count": sum(len(source.passages) for source in sources),
            "eligible_source_count": sum(source.eligible for source in sources),
            "candidate_limit": fixture.candidate_limit,
            "evidence_limit": fixture.evidence_limit,
            "repetitions": repetitions,
            "safety_passed": safety_passed,
            "variants": variants,
        }
    finally:
        isolated.close()
        index.close()


def run(source_counts: Sequence[int], *, repetitions: int = 5) -> dict[str, Any]:
    fixture = load_fixture()
    started = time.perf_counter()
    profiles = {
        str(source_count): run_profile(source_count, fixture, repetitions=repetitions)
        for source_count in source_counts
    }
    fixture_hash = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "report_kind": "wave2_source_evidence_retrieval_research",
        "authority": "research_only_no_runtime_integration",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "sqlite": sqlite3.sqlite_version,
        },
        "fixture": {
            "path": FIXTURE_PATH.name,
            "sha256": fixture_hash,
            "query_count": len(fixture.queries),
            "seed_source_count": len(fixture.sources),
        },
        "frozen_comparator": {
            "id": CURRENT_LEXICAL_POOL,
            "implementation": "allthecontext.lexical_v3.LexicalV3",
            "source_commit": FROZEN_COMPARATOR_COMMIT,
            "selection_semantics": (
                "Gold evidence available anywhere inside each selected source; "
                "passage variants instead require exact evidence-message selection."
            ),
        },
        "bounds": {
            "normal_source_count_max": MAX_NORMAL_SOURCE_COUNT,
            "opt_in_source_count_max": MAX_SOURCE_COUNT,
            "maximum_repetitions": MAX_REPETITIONS,
        },
        "research_paths": list(fixture.research_paths),
        "profiles": profiles,
        "safety_passed": all(profile["safety_passed"] for profile in profiles.values()),
        "runtime_seconds": round(time.perf_counter() - started, 6),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a compact evidence report without raw queries or imported text."""
    lines = [
        "# Wave 2 source-evidence retrieval research",
        "",
        "This report is a bounded, deterministic, offline experiment over sanitized "
        "imported-chat fixtures. It has no default runtime integration or production "
        "authority. Imported text was treated only as untrusted indexed data.",
        "",
        f"Frozen comparator: `LexicalV3` at `{report['frozen_comparator']['source_commit']}`.",
        "The comparator's recall and coverage measure evidence available inside selected "
        "sources; passage variants must select the exact judged evidence messages.",
        "",
    ]
    for profile_name, profile in report["profiles"].items():
        lines.extend(
            [
                f"## {profile_name} sources",
                "",
                "| Variant | Evidence recall | Facet coverage | Redundancy | Warm p95 ms "
                "| Incremental bytes | Deterministic | Policy violations |",
                "| --- | ---: | ---: | ---: | ---: | ---: | :---: | ---: |",
            ]
        )
        for variant_id in VARIANT_IDS:
            metrics = profile["variants"][variant_id]["metrics"]
            lines.append(
                f"| `{variant_id}` | {metrics['source_evidence_recall_at_limit']:.3f} "
                f"| {metrics['facet_coverage_at_limit']:.3f} "
                f"| {metrics['redundancy_at_limit']:.3f} "
                f"| {metrics['warm_latency']['p95_ms']:.3f} "
                f"| {metrics['storage']['incremental_over_candidate_pool_bytes']} "
                f"| {'yes' if metrics['repeated_rankings_deterministic'] else 'no'} "
                f"| {metrics['policy_violation_count']} |"
            )
        lines.extend(
            [
                "",
                f"Safety result: **{'passed' if profile['safety_passed'] else 'failed'}**. "
                "This requires deterministic repeats, invariant rankings when forbidden "
                "sources are removed, and zero policy violations for every variant.",
                "",
            ]
        )
    lines.extend(["## Unexercised claims", ""])
    for path in report["research_paths"]:
        lines.append(f"- `{path['id']}`: **{path['status']}** - {path['reason']}")
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "The fixture is synthetic and intentionally small. Latency includes candidate "
            "pooling plus evidence selection but does not flush operating-system caches. "
            "Ephemeral storage is an implementation measurement, not a packaged-runtime "
            "commitment. No neural model, reranker service, ANN index, learned sparse "
            "retriever, hosted service, or production integration was exercised.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-counts", type=int, nargs="*", default=[])
    parser.add_argument("--include-large", action="store_true")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    arguments = parser.parse_args(argv)
    try:
        counts = select_source_counts(arguments.source_counts, arguments.include_large)
        if not 1 <= arguments.repetitions <= MAX_REPETITIONS:
            raise ValueError(f"repetitions must be between 1 and {MAX_REPETITIONS}")
        report = run(counts, repetitions=arguments.repetitions)
    except ValueError as error:
        parser.error(str(error))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {arguments.output}")
    if arguments.markdown is not None:
        arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
        arguments.markdown.write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote {arguments.markdown}")
    return 0 if report["safety_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

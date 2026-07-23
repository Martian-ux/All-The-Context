"""Candidate-scoped deterministic lexical retrieval for coordinator wiring.

The caller owns every policy and temporal decision.  This module accepts only
the resulting record IDs, builds an ephemeral FTS5 corpus from those IDs, and
never queries or derives statistics from an ineligible row.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

MAX_ELIGIBLE_CANDIDATES = 50_000
MAX_CANDIDATE_ID_CHARS = 256
MAX_RESULTS = 100
MAX_QUERY_CHARS = 512
MAX_QUERY_TOKENS = 16
MAX_TOKEN_CHARS = 64
MAX_EXPANDED_TOKENS = 24
CHANNEL_RESULT_CAP = 256
MIN_PREFIX_TOKEN_CHARS = 4
MAX_PREFIX_TOKENS = 4

# FTS5 bm25() arguments follow the declared column order. record_id is
# UNINDEXED; the remaining weights favor content and explicit tags over broad
# structural fields.
BM25_COLUMN_WEIGHTS = (0.0, 8.0, 2.0, 4.0, 1.0)

_ELIGIBLE_TABLE = "lexical_v3_eligible"
_MATCHED_TABLE = "lexical_v3_matched"
_CANDIDATE_FTS_TABLE = "lexical_v3_candidates_fts"
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_CHANNEL_PRIORITY = {"phrase": 4, "all_terms": 3, "exact_any": 2, "prefix": 1}
_CHANNEL_SCORE_WEIGHTS = {"phrase": 1.5, "all_terms": 1.25, "exact_any": 1.0, "prefix": 0.5}

# These equivalences are deliberately tiny, inspectable, and independent of
# vault content. They do not learn from or reveal the eligible vocabulary.
_CURATED_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "eviction": ("cache",),
}


class DiagnosticReason(StrEnum):
    """Bounded reason codes that reveal no query or corpus terms."""

    EMPTY_QUERY = "empty_query"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    HIGH_PRECISION_SUFFICIENT = "high_precision_sufficient"
    HIGH_PRECISION_INSUFFICIENT = "high_precision_insufficient"
    EXACT_FALLBACK_SUFFICIENT = "exact_fallback_sufficient"
    EXACT_FALLBACK_INSUFFICIENT = "exact_fallback_insufficient"
    PREFIX_USED = "prefix_used"
    PREFIX_UNAVAILABLE = "prefix_unavailable"
    NO_MATCHES = "no_matches"


class SecureDeleteStatus(StrEnum):
    """Outcome of attempt-based FTS5 secure-delete feature detection."""

    ENABLED = "enabled"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class VocabularyDiagnostics:
    """Aggregate lexical diagnostics with no terms, content, or record IDs."""

    eligible_candidate_count: int
    indexed_candidate_count: int
    normalized_token_count: int
    token_category_counts: tuple[tuple[str, int], ...]
    high_precision_match_count: int
    exact_fallback_match_count: int
    prefix_match_count: int
    prefix_token_count: int
    query_truncated: bool
    secure_delete_status: SecureDeleteStatus
    reason_codes: tuple[DiagnosticReason, ...]


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """A deterministic score for an ID already authorized by the caller."""

    record_id: str
    score: float
    best_channel: str
    matched_channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LexicalSearchResult:
    """Eligible lexical hits and separately safe aggregate diagnostics."""

    hits: tuple[LexicalHit, ...]
    diagnostics: VocabularyDiagnostics


@dataclass(frozen=True, slots=True)
class _PreparedQuery:
    tokens: tuple[str, ...]
    categories: tuple[tuple[str, int], ...]
    truncated: bool


@dataclass(slots=True)
class _HitState:
    record_id: str
    channel_scores: dict[str, float] = field(default_factory=dict)


def _quoted_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("SQLite identifiers must contain only ASCII letters, digits, and '_'")
    return f'"{value}"'


def _quoted_fts_token(value: str) -> str:
    """Quote one literal token so user input is never interpreted as FTS syntax."""
    return '"' + value.replace('"', '""') + '"'


def _token_category(token: str) -> str:
    if token.isascii() and token.isalpha():
        return "ascii_alpha"
    if token.isdecimal():
        return "numeric"
    letters = [character for character in token if character.isalpha()]
    if letters and any(character.isdecimal() for character in token):
        return "mixed_alphanumeric"
    if letters and all(
        unicodedata.name(character, "").startswith("LATIN ") for character in letters
    ):
        return "latin_non_ascii"
    if letters:
        return "non_latin_or_multiscript"
    return "other"


def _prepare_query(value: str) -> _PreparedQuery:
    # NFKC makes compatibility forms deterministic. Case comparison is left to
    # SQLite's unicode61 tokenizer so Python's newer Unicode tables do not claim
    # equivalence that the Unicode 6.1 tokenizer does not implement.
    normalized = unicodedata.normalize("NFKC", value)
    truncated = len(normalized) > MAX_QUERY_CHARS
    bounded = normalized[:MAX_QUERY_CHARS]
    found = _TOKEN_RE.findall(bounded)
    if len(found) > MAX_QUERY_TOKENS:
        truncated = True
    tokens: list[str] = []
    for token in found[:MAX_QUERY_TOKENS]:
        if len(token) > MAX_TOKEN_CHARS:
            truncated = True
        shortened = token[:MAX_TOKEN_CHARS]
        if shortened:
            tokens.append(shortened)
    categories = Counter(_token_category(token) for token in tokens)
    return _PreparedQuery(
        tokens=tuple(tokens),
        categories=tuple(sorted(categories.items())),
        truncated=truncated,
    )


def _deduplicated(tokens: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            result.append(token)
    return tuple(result)


def _expanded_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        expanded.extend(_CURATED_EQUIVALENTS.get(token.casefold(), ()))
    return _deduplicated(expanded)[:MAX_EXPANDED_TOKENS]


def _phrase_query(tokens: Sequence[str]) -> str:
    phrase = " ".join(tokens).replace('"', '""')
    return f'"{phrase}"'


def _joined_query(tokens: Sequence[str], operator: str) -> str:
    return f" {operator} ".join(_quoted_fts_token(token) for token in tokens)


def _prefix_query(tokens: Sequence[str]) -> str:
    prefixable = _deduplicated(
        token for token in tokens if len(token) >= MIN_PREFIX_TOKEN_CHARS
    )[:MAX_PREFIX_TOKENS]
    return " OR ".join(f"{_quoted_fts_token(token)}*" for token in prefixable)


def _attempt_fts5_secure_delete(
    execute: Callable[[str], object], table_name: str
) -> SecureDeleteStatus:
    """Feature-detect and enable FTS5 secure-delete without a version assumption."""
    table = _quoted_identifier(table_name)
    try:
        execute(f"INSERT INTO temp.{table}({table}, rank) VALUES('secure-delete', 1)")
    except sqlite3.OperationalError:
        # SQLite before 3.42 and builds without the option reject the command.
        # The table is ephemeral and is still deleted and dropped by the caller.
        return SecureDeleteStatus.UNSUPPORTED
    return SecureDeleteStatus.ENABLED


def _empty_diagnostics(
    *,
    candidate_count: int,
    prepared: _PreparedQuery,
    reason: DiagnosticReason,
) -> VocabularyDiagnostics:
    return VocabularyDiagnostics(
        eligible_candidate_count=candidate_count,
        indexed_candidate_count=0,
        normalized_token_count=len(prepared.tokens),
        token_category_counts=prepared.categories,
        high_precision_match_count=0,
        exact_fallback_match_count=0,
        prefix_match_count=0,
        prefix_token_count=0,
        query_truncated=prepared.truncated,
        secure_delete_status=SecureDeleteStatus.NOT_APPLICABLE,
        reason_codes=(reason,),
    )


class LexicalV3:
    """Weighted BM25 over an ephemeral, caller-authorized FTS5 corpus."""

    def __init__(
        self,
        *,
        source_fts_table: str = "context_fts",
        prefix_fallback_min_results: int = 1,
    ) -> None:
        _quoted_identifier(source_fts_table)
        if not 1 <= prefix_fallback_min_results <= MAX_RESULTS:
            raise ValueError(f"prefix_fallback_min_results must be between 1 and {MAX_RESULTS}")
        self.source_fts_table = source_fts_table
        self.prefix_fallback_min_results = prefix_fallback_min_results

    def search(
        self,
        connection: sqlite3.Connection,
        eligible_candidate_ids: Sequence[str],
        query: str,
        *,
        limit: int = 5,
    ) -> LexicalSearchResult:
        """Rank only caller-supplied IDs; policy evaluation is intentionally absent."""
        candidates = self._bounded_candidates(eligible_candidate_ids)
        if not 1 <= limit <= MAX_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
        prepared = _prepare_query(query)
        if not candidates:
            return LexicalSearchResult(
                (),
                _empty_diagnostics(
                    candidate_count=0,
                    prepared=prepared,
                    reason=DiagnosticReason.NO_ELIGIBLE_CANDIDATES,
                ),
            )
        if not prepared.tokens:
            return LexicalSearchResult(
                (),
                _empty_diagnostics(
                    candidate_count=len(candidates),
                    prepared=prepared,
                    reason=DiagnosticReason.EMPTY_QUERY,
                ),
            )

        self._create_candidate_scope(connection, candidates)
        try:
            secure_delete = _attempt_fts5_secure_delete(
                connection.execute, _CANDIDATE_FTS_TABLE
            )
            states: dict[str, _HitState] = {}
            high_count = 0
            exact_fallback_count = 0
            prefix_count = 0
            prefix_tokens = _deduplicated(
                token for token in prepared.tokens if len(token) >= MIN_PREFIX_TOKEN_CHARS
            )[:MAX_PREFIX_TOKENS]
            reasons: list[DiagnosticReason] = []
            threshold = min(limit, self.prefix_fallback_min_results)
            active_channels: list[tuple[str, str]] = []
            if len(prepared.tokens) > 1:
                phrase = _phrase_query(prepared.tokens)
                active_channels.append(("phrase", phrase))
                high_count += self._select_matching_candidates(
                    connection, phrase
                )
            all_terms = _joined_query(prepared.tokens, "AND")
            active_channels.append(("all_terms", all_terms))
            high_count += self._select_matching_candidates(
                connection, all_terms
            )
            matched_count = self._matched_count(connection)
            if matched_count >= threshold:
                reasons.append(DiagnosticReason.HIGH_PRECISION_SUFFICIENT)
            else:
                reasons.append(DiagnosticReason.HIGH_PRECISION_INSUFFICIENT)
                exact_any = _joined_query(_expanded_tokens(prepared.tokens), "OR")
                active_channels.append(("exact_any", exact_any))
                exact_fallback_count = self._select_matching_candidates(
                    connection, exact_any
                )
                matched_count = self._matched_count(connection)
                if matched_count >= threshold:
                    reasons.append(DiagnosticReason.EXACT_FALLBACK_SUFFICIENT)
                else:
                    reasons.append(DiagnosticReason.EXACT_FALLBACK_INSUFFICIENT)
                    prefix = _prefix_query(prepared.tokens)
                    if prefix:
                        reasons.append(DiagnosticReason.PREFIX_USED)
                        active_channels.append(("prefix", prefix))
                        prefix_count = self._select_matching_candidates(
                            connection, prefix
                        )
                    else:
                        reasons.append(DiagnosticReason.PREFIX_UNAVAILABLE)
            indexed_count = self._populate_candidate_fts(connection)
            for channel, fts_query in active_channels:
                self._collect_channel(connection, states, channel, fts_query)
            if not states:
                reasons.append(DiagnosticReason.NO_MATCHES)
            hits = self._rank(states, limit)
            diagnostics = VocabularyDiagnostics(
                eligible_candidate_count=len(candidates),
                indexed_candidate_count=indexed_count,
                normalized_token_count=len(prepared.tokens),
                token_category_counts=prepared.categories,
                high_precision_match_count=high_count,
                exact_fallback_match_count=exact_fallback_count,
                prefix_match_count=prefix_count,
                prefix_token_count=len(prefix_tokens),
                query_truncated=prepared.truncated,
                secure_delete_status=secure_delete,
                reason_codes=tuple(reasons),
            )
            return LexicalSearchResult(hits, diagnostics)
        finally:
            self._drop_candidate_scope(connection)

    @staticmethod
    def _bounded_candidates(candidate_ids: Sequence[str]) -> tuple[str, ...]:
        if len(candidate_ids) > MAX_ELIGIBLE_CANDIDATES:
            raise ValueError(
                f"eligible candidate count exceeds the {MAX_ELIGIBLE_CANDIDATES} hard cap"
            )
        unique: set[str] = set()
        for record_id in candidate_ids:
            if not record_id or len(record_id) > MAX_CANDIDATE_ID_CHARS:
                raise ValueError(
                    "eligible candidate IDs must be non-empty and no longer than "
                    f"{MAX_CANDIDATE_ID_CHARS} characters"
                )
            unique.add(record_id)
        return tuple(sorted(unique))

    def _create_candidate_scope(
        self, connection: sqlite3.Connection, candidates: Sequence[str]
    ) -> None:
        connection.execute(
            f"CREATE TEMP TABLE {_quoted_identifier(_ELIGIBLE_TABLE)} "
            "(record_id TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        connection.execute(
            f"CREATE TEMP TABLE {_quoted_identifier(_MATCHED_TABLE)} "
            "(record_id TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        connection.executemany(
            f"INSERT INTO temp.{_quoted_identifier(_ELIGIBLE_TABLE)}(record_id) VALUES (?)",
            ((record_id,) for record_id in candidates),
        )
        connection.execute(
            f"CREATE VIRTUAL TABLE temp.{_quoted_identifier(_CANDIDATE_FTS_TABLE)} "
            "USING fts5(record_id UNINDEXED, content, kind, tags, scopes, "
            "tokenize='unicode61 remove_diacritics 2')"
        )

    def _select_matching_candidates(
        self, connection: sqlite3.Connection, fts_query: str
    ) -> int:
        source = _quoted_identifier(self.source_fts_table)
        eligible = _quoted_identifier(_ELIGIBLE_TABLE)
        matched = _quoted_identifier(_MATCHED_TABLE)
        cursor = connection.execute(
            f"INSERT OR IGNORE INTO temp.{matched}(record_id) "
            f"SELECT source.record_id FROM main.{source} AS source "
            f"JOIN temp.{eligible} AS eligible ON eligible.record_id=source.record_id "
            f"WHERE {self.source_fts_table} MATCH ? ORDER BY source.record_id",
            (fts_query,),
        )
        return max(0, cursor.rowcount)

    @staticmethod
    def _matched_count(connection: sqlite3.Connection) -> int:
        matched = _quoted_identifier(_MATCHED_TABLE)
        row = connection.execute(f"SELECT COUNT(*) FROM temp.{matched}").fetchone()
        return int(row[0]) if row is not None else 0

    def _populate_candidate_fts(self, connection: sqlite3.Connection) -> int:
        source = _quoted_identifier(self.source_fts_table)
        target = _quoted_identifier(_CANDIDATE_FTS_TABLE)
        matched = _quoted_identifier(_MATCHED_TABLE)
        connection.execute(
            f"INSERT INTO temp.{target}(record_id, content, kind, tags, scopes) "
            f"SELECT source.record_id, source.content, source.kind, source.tags, source.scopes "
            f"FROM main.{source} AS source "
            f"JOIN temp.{matched} AS matched ON matched.record_id=source.record_id "
            "ORDER BY matched.record_id, source.rowid"
        )
        row = connection.execute(f"SELECT COUNT(*) FROM temp.{target}").fetchone()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _collect_channel(
        connection: sqlite3.Connection,
        states: dict[str, _HitState],
        channel: str,
        fts_query: str,
    ) -> int:
        target = _quoted_identifier(_CANDIDATE_FTS_TABLE)
        weights = ", ".join(str(weight) for weight in BM25_COLUMN_WEIGHTS)
        rows = connection.execute(
            f"SELECT record_id, bm25({_CANDIDATE_FTS_TABLE}, {weights}) AS lexical_score "
            f"FROM temp.{target} WHERE {_CANDIDATE_FTS_TABLE} MATCH ? "
            "ORDER BY lexical_score ASC, record_id ASC LIMIT ?",
            (fts_query, CHANNEL_RESULT_CAP),
        ).fetchall()
        for row in rows:
            record_id = str(row[0])
            state = states.setdefault(record_id, _HitState(record_id))
            bm25_relevance = max(0.0, -float(row[1]))
            state.channel_scores[channel] = (
                bm25_relevance * _CHANNEL_SCORE_WEIGHTS[channel]
            )
        return len(rows)

    @staticmethod
    def _rank(states: dict[str, _HitState], limit: int) -> tuple[LexicalHit, ...]:
        def ranking_key(state: _HitState) -> tuple[int, int, float, str]:
            best_priority = max(_CHANNEL_PRIORITY[channel] for channel in state.channel_scores)
            return (
                -best_priority,
                -len(state.channel_scores),
                -sum(state.channel_scores.values()),
                state.record_id,
            )

        ordered = sorted(states.values(), key=ranking_key)[:limit]
        hits: list[LexicalHit] = []
        for state in ordered:
            channels = tuple(
                sorted(
                    state.channel_scores,
                    key=lambda channel: (-_CHANNEL_PRIORITY[channel], channel),
                )
            )
            hits.append(
                LexicalHit(
                    record_id=state.record_id,
                    score=round(sum(state.channel_scores.values()), 12),
                    best_channel=channels[0],
                    matched_channels=channels,
                )
            )
        return tuple(hits)

    @staticmethod
    def _drop_candidate_scope(connection: sqlite3.Connection) -> None:
        target = _quoted_identifier(_CANDIDATE_FTS_TABLE)
        matched = _quoted_identifier(_MATCHED_TABLE)
        eligible = _quoted_identifier(_ELIGIBLE_TABLE)
        try:
            connection.execute(f"DELETE FROM temp.{target}")
        finally:
            try:
                connection.execute(f"DROP TABLE temp.{target}")
            finally:
                try:
                    connection.execute(f"DROP TABLE temp.{matched}")
                finally:
                    connection.execute(f"DROP TABLE temp.{eligible}")

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

import pytest
from allthecontext.lexical_v3 import (
    MAX_ELIGIBLE_CANDIDATES,
    MAX_PREFIX_TOKENS,
    MAX_QUERY_TOKENS,
    MAX_RESULTS,
    DiagnosticReason,
    LexicalV3,
    SecureDeleteStatus,
    _attempt_fts5_secure_delete,
    _prefix_query,
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute(
        "CREATE VIRTUAL TABLE context_fts USING fts5("
        "record_id UNINDEXED, content, kind, tags, scopes, "
        "tokenize='unicode61 remove_diacritics 2')"
    )
    return connection


def _insert(
    connection: sqlite3.Connection,
    record_id: str,
    *,
    content: str = "",
    kind: str = "fact",
    tags: str = "",
    scopes: str = "",
) -> None:
    connection.execute(
        "INSERT INTO context_fts(record_id, content, kind, tags, scopes) VALUES(?,?,?,?,?)",
        (record_id, content, kind, tags, scopes),
    )


def _ids(result: object) -> list[str]:
    assert hasattr(result, "hits")
    return [hit.record_id for hit in result.hits]


def test_weighted_bm25_favors_content_over_lower_weight_structural_fields() -> None:
    connection = _connection()
    _insert(connection, "content-hit", content="quartz")
    _insert(connection, "kind-hit", kind="quartz")

    result = LexicalV3().search(connection, ["kind-hit", "content-hit"], "quartz")

    assert _ids(result) == ["content-hit", "kind-hit"]
    assert result.hits[0].score > result.hits[1].score
    connection.close()


def test_bm25_ties_break_by_record_id_independent_of_input_order() -> None:
    connection = _connection()
    _insert(connection, "b-record", content="identical cobalt text")
    _insert(connection, "a-record", content="identical cobalt text")
    ranker = LexicalV3()

    first = ranker.search(connection, ["b-record", "a-record"], "cobalt")
    second = ranker.search(connection, ["a-record", "b-record"], "cobalt")

    assert _ids(first) == ["a-record", "b-record"]
    assert first == second
    connection.close()


def test_ineligible_rows_cannot_change_scores_results_or_diagnostics() -> None:
    connection = _connection()
    _insert(connection, "eligible-a", content="alpha common")
    _insert(connection, "eligible-b", content="alpha uncommon")
    ranker = LexicalV3()

    before = ranker.search(connection, ["eligible-b", "eligible-a"], "alpha")
    for index in range(200):
        _insert(
            connection,
            f"denied-{index}",
            content="alpha alpha alpha forbidden-vocabulary credential-material",
        )
    after = ranker.search(connection, ["eligible-b", "eligible-a"], "alpha")

    assert before == after
    assert _ids(after) == ["eligible-a", "eligible-b"]
    assert after.diagnostics.eligible_candidate_count == 2
    assert after.diagnostics.indexed_candidate_count == 2
    connection.close()


def test_ineligible_terms_and_ids_never_appear_in_safe_vocabulary_diagnostics() -> None:
    connection = _connection()
    _insert(connection, "authorized-id", content="ordinary allowed material")
    _insert(
        connection,
        "unauthorized-personal-id",
        content="private credential forbidden secretphrase",
    )

    result = LexicalV3().search(
        connection,
        ["authorized-id"],
        'secretphrase" OR credential*',
    )
    rendered = json.dumps(asdict(result.diagnostics), sort_keys=True)

    assert result.hits == ()
    assert DiagnosticReason.NO_MATCHES in result.diagnostics.reason_codes
    for forbidden in (
        "secretphrase",
        "credential",
        "private",
        "unauthorized-personal-id",
        "authorized-id",
    ):
        assert forbidden not in rendered
    connection.close()


def test_prefix_fallback_runs_only_when_exact_channels_are_insufficient() -> None:
    connection = _connection()
    _insert(connection, "exact", content="alpha")
    _insert(connection, "prefix-only", content="alphabet")

    sufficient = LexicalV3(prefix_fallback_min_results=1).search(
        connection, ["prefix-only", "exact"], "alpha"
    )
    insufficient = LexicalV3(prefix_fallback_min_results=2).search(
        connection, ["prefix-only", "exact"], "alpha"
    )

    assert _ids(sufficient) == ["exact"]
    assert DiagnosticReason.HIGH_PRECISION_SUFFICIENT in sufficient.diagnostics.reason_codes
    assert DiagnosticReason.PREFIX_USED not in sufficient.diagnostics.reason_codes
    assert _ids(insufficient) == ["exact", "prefix-only"]
    assert insufficient.hits[0].best_channel == "all_terms"
    assert insufficient.hits[1].best_channel == "prefix"
    assert DiagnosticReason.PREFIX_USED in insufficient.diagnostics.reason_codes
    connection.close()


def test_prefix_fallback_has_minimum_length_token_cap_and_literal_quoting() -> None:
    connection = _connection()
    _insert(connection, "short-prefix", content="editors")

    too_short = LexicalV3().search(connection, ["short-prefix"], "edi")
    rendered = _prefix_query(["abc", "abcd", 'ef"gh', "ijk*", "lmno", "pqrst"])

    assert too_short.hits == ()
    assert DiagnosticReason.PREFIX_UNAVAILABLE in too_short.diagnostics.reason_codes
    assert rendered == '"abcd"* OR "ef""gh"* OR "ijk*"* OR "lmno"*'
    assert rendered.count(" OR ") == MAX_PREFIX_TOKENS - 1
    connection.close()


def test_query_and_candidate_hard_bounds_are_enforced() -> None:
    connection = _connection()
    _insert(connection, "eligible", content="token")
    ranker = LexicalV3()

    with pytest.raises(ValueError, match="hard cap"):
        ranker.search(
            connection,
            [f"record-{index}" for index in range(MAX_ELIGIBLE_CANDIDATES + 1)],
            "token",
        )
    with pytest.raises(ValueError, match="limit"):
        ranker.search(connection, ["eligible"], "token", limit=MAX_RESULTS + 1)

    bounded_query = " ".join(f"token{index}" for index in range(MAX_QUERY_TOKENS + 5))
    result = ranker.search(connection, ["eligible"], bounded_query)
    assert result.diagnostics.normalized_token_count == MAX_QUERY_TOKENS
    assert result.diagnostics.query_truncated is True
    connection.close()


@pytest.mark.parametrize(
    ("content", "query"),
    [
        ("café planning", "\uff23\uff21\uff26\uff25"),
        ("Αθήνα σχέδιο", "ΑΘΉΝΑ"),
        ("Привет Москва", "ПРИВЕТ"),
        ("東京 会議", "東京"),
    ],
)
def test_nfkc_and_unicode61_case_behavior_for_representative_scripts(
    content: str, query: str
) -> None:
    connection = _connection()
    _insert(connection, "multiscript", content=content)

    result = LexicalV3().search(connection, ["multiscript"], query)

    assert _ids(result) == ["multiscript"]
    connection.close()


def test_diagnostics_use_normalized_token_categories_without_terms() -> None:
    connection = _connection()
    _insert(connection, "eligible", content="no matching vocabulary")

    result = LexicalV3().search(connection, ["eligible"], "CAFÉ Αθήνα 東京 123 abc123")

    assert dict(result.diagnostics.token_category_counts) == {
        "latin_non_ascii": 1,
        "mixed_alphanumeric": 1,
        "non_latin_or_multiscript": 2,
        "numeric": 1,
    }
    assert "CAFÉ" not in repr(result.diagnostics)
    assert "Αθήνα" not in repr(result.diagnostics)
    assert "東京" not in repr(result.diagnostics)
    connection.close()


def test_unicode61_limitations_are_not_misstated_as_stemming_or_word_segmentation() -> None:
    connection = _connection()
    _insert(connection, "german", content="Straße")
    _insert(connection, "irregular", content="mice")
    _insert(connection, "unsegmented", content="東京会議")
    ranker = LexicalV3()

    assert ranker.search(connection, ["german"], "STRASSE").hits == ()
    assert ranker.search(connection, ["irregular"], "mouse").hits == ()
    assert ranker.search(connection, ["unsegmented"], "東京").hits == ()
    connection.close()


def test_fts5_secure_delete_supported_and_unsupported_paths_are_host_independent() -> None:
    statements: list[str] = []

    def supported(statement: str) -> object:
        statements.append(statement)
        return object()

    def unsupported(_statement: str) -> object:
        raise sqlite3.OperationalError("simulated older SQLite")

    assert (
        _attempt_fts5_secure_delete(supported, "bounded_candidates") is SecureDeleteStatus.ENABLED
    )
    assert statements == [
        'INSERT INTO temp."bounded_candidates"("bounded_candidates", rank) '
        "VALUES('secure-delete', 1)"
    ]
    assert (
        _attempt_fts5_secure_delete(unsupported, "bounded_candidates")
        is SecureDeleteStatus.UNSUPPORTED
    )


def test_real_sqlite_secure_delete_detection_is_reported_without_version_assumption() -> None:
    connection = _connection()
    _insert(connection, "eligible", content="secure cleanup")

    result = LexicalV3().search(connection, ["eligible"], "secure")

    assert result.diagnostics.secure_delete_status in {
        SecureDeleteStatus.ENABLED,
        SecureDeleteStatus.UNSUPPORTED,
    }
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_temp_master "
            "WHERE name LIKE 'lexical_v3_eligible%' OR name LIKE 'lexical_v3_candidates_fts%'"
        ).fetchone()[0]
        == 0
    )
    connection.close()

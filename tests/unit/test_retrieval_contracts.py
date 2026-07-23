from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from allthecontext.models import SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.retrieval_contracts import (
    FROZEN_V2_COMPARATOR,
    DiagnosticMetricCode,
    DiagnosticReasonCode,
    DiagnosticValue,
    FrozenV2Comparator,
    SafeRetrievalDiagnostic,
    SetSelectionConstraints,
    ShadowRetrievalPlan,
    TemporalContext,
)
from allthecontext.security import ClientPrincipal

from bench.retrieval_benchmark import FIXTURES, build_database


def _fixture() -> dict[str, object]:
    loaded = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_contract_values_are_immutable_and_temporal_context_has_no_implicit_clock() -> None:
    local = datetime(2026, 7, 22, 8, tzinfo=UTC) + timedelta(hours=4)
    context = TemporalContext(evaluated_at=local, as_of=local - timedelta(days=1))

    assert context.evaluated_at.tzinfo is UTC
    assert context.effective_at == context.as_of
    with pytest.raises(FrozenInstanceError):
        context.as_of = None  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        TemporalContext(evaluated_at=datetime(2026, 7, 22))
    with pytest.raises(ValueError, match="non-negative"):
        SetSelectionConstraints(limit=-1)
    assert ShadowRetrievalPlan[str, str]().enabled is False


def test_safe_diagnostics_have_only_closed_codes_and_numeric_or_boolean_values() -> None:
    diagnostic = SafeRetrievalDiagnostic(
        DiagnosticReasonCode.POLICY_FILTERED,
        (
            DiagnosticValue(DiagnosticMetricCode.FILTERED_COUNT, 2),
            DiagnosticValue(DiagnosticMetricCode.DETERMINISTIC, True),
        ),
    )

    assert {field.name for field in fields(diagnostic)} == {"reason", "values"}
    assert all(isinstance(value.value, bool | int | float) for value in diagnostic.values)
    with pytest.raises(ValueError, match="finite"):
        DiagnosticValue(DiagnosticMetricCode.SCORE, float("nan"))
    with pytest.raises(ValueError, match="unique"):
        SafeRetrievalDiagnostic(
            DiagnosticReasonCode.SET_SELECTED,
            (
                DiagnosticValue(DiagnosticMetricCode.SELECTED_COUNT, 1),
                DiagnosticValue(DiagnosticMetricCode.SELECTED_COUNT, 2),
            ),
        )


def test_frozen_v2_comparator_is_deterministic_and_matches_production_default(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    records = fixture["records"]
    queries = fixture["queries"]
    assert isinstance(records, list)
    assert isinstance(queries, list)
    store, _elapsed = build_database(tmp_path / "comparator.sqlite3", len(records), fixture)
    principal = ClientPrincipal(
        "benchmark-reader", "Synthetic benchmark reader", frozenset({"context:read"})
    )
    production = RetrievalEngine(store)
    comparator = RetrievalEngine(store, ranker=FrozenV2Comparator())

    for query in queries:
        assert isinstance(query, dict)
        request = SearchRequest(query=str(query["query"]), limit=100)
        expected = [item.id for item in production.search(request, principal).items]
        repeated = [
            [item.id for item in comparator.search(request, principal).items] for _ in range(5)
        ]
        assert repeated == [expected] * 5

    assert FROZEN_V2_COMPARATOR.name == "retrieval_v2_lexical_rrf"
    assert FROZEN_V2_COMPARATOR.revision == 1

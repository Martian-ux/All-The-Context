from __future__ import annotations

from allthecontext.models import ContextRecordOut
from allthecontext.retrieval import ContextCompiler


def _record(record_id: str, kind: str, content: str, **values: object) -> ContextRecordOut:
    return ContextRecordOut(
        id=record_id,
        kind=kind,
        content=content,
        version=1,
        content_hash=record_id,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
        **values,
    )


def test_compiler_resolves_same_slot_conflicts_deterministically() -> None:
    preferred = _record(
        "preferred",
        "project_decision",
        "The synthetic launch color is cobalt.",
        entity_key="project:atlas",
        attribute_key="launch-color",
    )
    conflicting = _record(
        "conflicting",
        "project_decision",
        "The synthetic launch color is amber.",
        entity_key="project:atlas",
        attribute_key="launch-color",
    )
    neutral = _record("neutral", "fact", "The rehearsal is scheduled for Tuesday.")
    compiler = ContextCompiler()

    runs = [
        compiler.compile([], [preferred, conflicting, neutral], budget_chars=2_000)[0]
        for _ in range(5)
    ]

    assert [[item.id for item in run] for run in runs] == [
        ["preferred", "neutral"]
    ] * 5


def test_compiler_selects_primary_before_linked_supporting_evidence() -> None:
    support = _record(
        "support",
        "supporting_evidence",
        "The sanitized review approved cobalt.",
        source_id="source-atlas",
        evidence="Synthetic review transcript",
    )
    primary = _record(
        "primary",
        "project_decision",
        "Use cobalt for the synthetic launch.",
        source_id="source-atlas",
    )

    selected, used = ContextCompiler().compile(
        [], [support, primary], budget_chars=2_000
    )

    assert [item.id for item in selected] == ["primary", "support"]
    assert used == sum(len(item.content) + 64 for item in selected)


def test_compiler_suppresses_transitive_near_duplicate_redundancy() -> None:
    first = _record(
        "first",
        "interaction_preference",
        "Use ISO 8601 timestamps in exports.",
    )
    second = _record(
        "second",
        "interaction_preference",
        "Exports use ISO-8601 timestamp formatting.",
    )
    third = _record(
        "third",
        "interaction_preference",
        "Use ISO 8601 timestamp formatting for exports.",
    )

    selected, _used = ContextCompiler().compile(
        [first, second, third], [], budget_chars=2_000
    )

    assert len(selected) == 1
    assert selected[0].id in {"first", "second", "third"}

from __future__ import annotations

import pytest
from allthecontext.models import (
    CONTEXT_PACK_TRUNCATION_REASON_ALLOWLIST,
    MAX_CONTEXT_PACK_BUDGET_CHARS,
    MAX_CONTEXT_PACK_CANDIDATE_COUNT,
    MAX_CONTEXT_PACK_OMITTED_COUNT,
    MAX_CONTEXT_PACK_PROVENANCE_COUNT,
    MAX_CONTEXT_PACK_SELECTED_COUNT,
    MAX_CONTEXT_PACK_SUPPRESSED_COUNT,
    MAX_CONTEXT_PACK_USED_CHARS,
    ContextPackMetadata,
)
from pydantic import ValidationError


def _metadata_payload() -> dict[str, object]:
    return {
        "candidate_count": 5,
        "selected_count": 2,
        "omitted_count": 3,
        "budget_chars": 12_000,
        "used_chars": 1_024,
        "provenance_backed_count": 1,
        "candidate_pool_truncated": True,
        "truncated": True,
        "truncation_reasons": ["candidate_pool", "budget"],
        "duplicate_suppressed_count": 1,
        "conflict_suppressed_count": 0,
    }


def test_context_pack_metadata_valid_round_trip_preserves_public_dump() -> None:
    original = ContextPackMetadata.model_validate(_metadata_payload())
    dumped = original.model_dump(mode="json")

    assert ContextPackMetadata.model_validate(dumped).model_dump(mode="json") == dumped
    assert dumped["pack_schema"] == "atc.context-pack.v1"
    assert dumped["truncation_reasons"] == ["candidate_pool", "budget"]


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("candidate_count", MAX_CONTEXT_PACK_CANDIDATE_COUNT),
        ("selected_count", MAX_CONTEXT_PACK_SELECTED_COUNT),
        ("omitted_count", MAX_CONTEXT_PACK_OMITTED_COUNT),
        ("budget_chars", MAX_CONTEXT_PACK_BUDGET_CHARS),
        ("used_chars", MAX_CONTEXT_PACK_USED_CHARS),
        ("provenance_backed_count", MAX_CONTEXT_PACK_PROVENANCE_COUNT),
        ("duplicate_suppressed_count", MAX_CONTEXT_PACK_SUPPRESSED_COUNT),
        ("conflict_suppressed_count", MAX_CONTEXT_PACK_SUPPRESSED_COUNT),
    ],
)
def test_context_pack_metadata_accepts_each_explicit_count_boundary(
    field: str, maximum: int
) -> None:
    payload = _metadata_payload()
    payload[field] = maximum

    assert ContextPackMetadata.model_validate(payload).model_dump(mode="json")[field] == maximum


@pytest.mark.parametrize(
    "value",
    [True, False, 1.0, "1", -1, -1.0, "-1"],
)
@pytest.mark.parametrize(
    "field",
    [
        "candidate_count",
        "selected_count",
        "omitted_count",
        "budget_chars",
        "used_chars",
        "provenance_backed_count",
        "duplicate_suppressed_count",
        "conflict_suppressed_count",
    ],
)
def test_context_pack_metadata_count_fields_reject_coercion_and_negative_values(
    field: str, value: object
) -> None:
    payload = _metadata_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ContextPackMetadata.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("candidate_count", MAX_CONTEXT_PACK_CANDIDATE_COUNT),
        ("selected_count", MAX_CONTEXT_PACK_SELECTED_COUNT),
        ("omitted_count", MAX_CONTEXT_PACK_OMITTED_COUNT),
        ("budget_chars", MAX_CONTEXT_PACK_BUDGET_CHARS),
        ("used_chars", MAX_CONTEXT_PACK_USED_CHARS),
        ("provenance_backed_count", MAX_CONTEXT_PACK_PROVENANCE_COUNT),
        ("duplicate_suppressed_count", MAX_CONTEXT_PACK_SUPPRESSED_COUNT),
        ("conflict_suppressed_count", MAX_CONTEXT_PACK_SUPPRESSED_COUNT),
    ],
)
def test_context_pack_metadata_count_fields_reject_values_above_safe_maximum(
    field: str, maximum: int
) -> None:
    payload = _metadata_payload()
    payload[field] = maximum + 1

    with pytest.raises(ValidationError):
        ContextPackMetadata.model_validate(payload)


@pytest.mark.parametrize("field", ["candidate_pool_truncated", "truncated"])
@pytest.mark.parametrize("value", [1, 0, 1.0, "true", "false", None])
def test_context_pack_metadata_flags_reject_coercion(field: str, value: object) -> None:
    payload = _metadata_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ContextPackMetadata.model_validate(payload)


@pytest.mark.parametrize(
    "reasons",
    [
        ["unknown"],
        [1],
        ["1"],
        ("candidate_pool",),
        ["candidate_pool", "candidate_pool"],
        [*CONTEXT_PACK_TRUNCATION_REASON_ALLOWLIST, "candidate_pool"],
    ],
)
def test_context_pack_metadata_reasons_reject_unknown_coercible_duplicate_and_oversized(
    reasons: list[object],
) -> None:
    payload = _metadata_payload()
    payload["truncation_reasons"] = reasons

    with pytest.raises(ValidationError):
        ContextPackMetadata.model_validate(payload)


def test_context_pack_metadata_remains_closed_to_unknown_fields() -> None:
    payload = _metadata_payload()
    payload["attacker_controlled"] = "must reject"

    with pytest.raises(ValidationError):
        ContextPackMetadata.model_validate(payload)


def test_context_pack_metadata_round_trips_the_complete_reason_allowlist() -> None:
    payload = _metadata_payload()
    payload["truncation_reasons"] = [
        "candidate_pool",
        "budget",
        "record_limit",
        "edge_filter",
        "edge_envelope",
    ]

    metadata = ContextPackMetadata.model_validate(payload)

    assert metadata.model_dump(mode="json")["truncation_reasons"] == payload["truncation_reasons"]

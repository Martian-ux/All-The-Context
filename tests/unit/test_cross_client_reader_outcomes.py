from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from bench.cross_client_reader_outcomes import (
    ARM_IDS,
    GROUND_TRUTHS,
    TASK_IDS,
    TASK_PROMPTS,
    ReaderLimits,
    ReceiptValidationError,
    build_packet,
    build_reader_result,
    context_items_from_bootstrap,
    grade_reader_result,
    grade_six_cells,
    maintain_context_file,
    reader_result_from_mapping,
)

PREFERENCE = "I prefer concise answers."
CORRECTED_PREFERENCE = "I prefer evidence-backed answers."


def _packet(task_key: str, source: str = "test"):
    canonical_source = {
        ARM_IDS[0]: "no_memory",
        ARM_IDS[1]: "maintained_context_file",
        ARM_IDS[2]: "actual_atc_bootstrap",
    }.get(source, source)
    access_units = 1 if source == ARM_IDS[2] else 0
    retrieval_units = 1 if source == ARM_IDS[2] else 0
    return build_packet(
        TASK_PROMPTS[task_key],
        [CORRECTED_PREFERENCE] if source != "no_memory" else [],
        context_source=canonical_source,
        construction_units=0,
        access_units=access_units,
        retrieval_units=retrieval_units,
    )


def test_packets_are_reader_visible_only_and_hash_deterministically() -> None:
    packet = _packet("changed_preference")
    repeated = _packet("changed_preference")

    assert packet.prompt == (
        "State the user's current answer-style preference exactly, or reply UNKNOWN if context "
        "does not establish it."
        "\n\nContext:\nI prefer evidence-backed answers."
    )
    assert packet.prompt == repeated.prompt
    assert packet.packet_sha256 == repeated.packet_sha256
    assert "simple_no_memory" not in packet.prompt
    assert "changed_preference" not in packet.prompt
    assert CORRECTED_PREFERENCE in packet.prompt
    assert "UNKNOWN" in packet.prompt
    assert "scoring" not in packet.prompt.casefold()
    assert "canonical" not in packet.prompt.casefold()
    assert "expected" not in packet.prompt.casefold()


def test_query_blind_context_file_uses_last_explicit_value_per_slot() -> None:
    maintained = maintain_context_file(
        [
            {
                "slot": "response_style",
                "sequence": 1,
                "content": PREFERENCE,
                "explicit_user_statement": True,
            },
            {
                "slot": "response_style",
                "sequence": 2,
                "content": "Assistant suggestion only.",
                "explicit_user_statement": False,
            },
            {
                "slot": "response_style",
                "sequence": 3,
                "content": CORRECTED_PREFERENCE,
                "explicit_user_statement": True,
            },
        ]
    )

    assert maintained.query_blind is True
    assert maintained.items == (CORRECTED_PREFERENCE,)
    assert maintained.event_count == 3
    assert maintained.construction_units == 3


def test_bootstrap_content_extraction_discards_metadata_from_reader_context() -> None:
    assert context_items_from_bootstrap(
        {"items": [{"content": CORRECTED_PREFERENCE, "record_id": "record"}]}
    ) == (CORRECTED_PREFERENCE,)
    with pytest.raises(ReceiptValidationError, match="items must be a list"):
        context_items_from_bootstrap({"items": {}})


def test_build_packet_requires_the_canonical_task_instruction() -> None:
    altered = replace(
        TASK_PROMPTS["changed_preference"],
        instruction="Answer using the context and include the scoring rationale.",
    )

    with pytest.raises(ReceiptValidationError, match="canonical task exactly"):
        build_packet(
            altered,
            (),
            context_source="no_memory",
            construction_units=0,
            access_units=0,
            retrieval_units=0,
        )


def test_external_result_accepts_sparse_honest_usage_and_binds_hashes() -> None:
    packet = _packet("changed_preference")
    result = build_reader_result(
        packet,
        CORRECTED_PREFERENCE,
        evidence_kind="external_reader",
        config={"reader_route": "later_authorized_route"},
        usage={
            "input_tokens": 41,
            "output_tokens": 5,
            "model_calls": 1,
            "provider_calls": 1,
            "cost_microunits": 7,
        },
        provenance={"source": "external_receipt"},
    )
    round_trip = reader_result_from_mapping(result.as_dict())
    graded = grade_reader_result(packet, round_trip, GROUND_TRUTHS["changed_preference"])

    assert round_trip == result
    assert graded["success"] is True
    assert graded["usage"] == {
        "input_tokens": 41,
        "output_tokens": 5,
        "model_calls": 1,
        "provider_calls": 1,
        "cost_microunits": 7,
    }
    assert graded["usage_fields_observed"] == [
        "input_tokens",
        "output_tokens",
        "model_calls",
        "provider_calls",
        "cost_microunits",
    ]
    assert len(graded["config_sha256"]) == 64
    assert len(graded["result_sha256"]) == 64


def test_common_reader_ceiling_is_fixed_and_complete() -> None:
    assert _packet("changed_preference").reader_limits.as_dict() == {
        "max_input_tokens": 2_048,
        "max_output_tokens": 32,
        "tool_calls": 0,
        "overflow_policy": "fail_before_provider",
        "model_config_placeholder": "deferred_external_reader_config",
        "tokenizer_config_placeholder": "deferred_external_tokenizer_config",
        "padding_policy": "no_padding",
        "truncation_policy": "no_truncation",
    }
    with pytest.raises(ReceiptValidationError, match="fixed"):
        ReaderLimits(max_input_tokens=2_049)
    with pytest.raises(ReceiptValidationError, match="fixed"):
        ReaderLimits(max_input_tokens=True)


def test_semantic_use_is_not_an_external_reader_field() -> None:
    result = build_reader_result(
        _packet("changed_preference"),
        PREFERENCE,
        provenance={"source": "canned"},
    ).as_dict()
    result["semantic_use"] = "stale"

    with pytest.raises(ReceiptValidationError, match="semantic_use"):
        reader_result_from_mapping(result)


def test_unknown_usage_fields_are_rejected_on_round_trip() -> None:
    result = build_reader_result(
        _packet("changed_preference"),
        CORRECTED_PREFERENCE,
        provenance={"source": "external_receipt"},
        evidence_kind="external_reader",
        usage={"input_tokens": 41},
    ).as_dict()
    result["usage"]["unreported_tokens"] = 2

    with pytest.raises(ReceiptValidationError, match="unknown fields"):
        reader_result_from_mapping(result)


def test_fixture_results_cannot_fabricate_reader_usage() -> None:
    packet = _packet("changed_preference")
    with pytest.raises(ReceiptValidationError, match="non-model fixture"):
        build_reader_result(
            packet,
            CORRECTED_PREFERENCE,
            usage={"output_tokens": 1},
            provenance={"source": "canned"},
        )


def test_stale_use_irrelevant_disclosure_and_unsupported_answers_are_distinct() -> None:
    positive = _packet("changed_preference")
    negative = _packet("unknown_deployment_region", source="maintained_context_file")

    stale = build_reader_result(
        positive,
        PREFERENCE,
        provenance={"source": "canned"},
    )
    disclosure = build_reader_result(
        negative,
        CORRECTED_PREFERENCE,
        provenance={"source": "canned"},
    )
    unsupported = build_reader_result(
        positive, "A different answer.", provenance={"source": "canned"}
    )
    abstention = build_reader_result(positive, "UNKNOWN", provenance={"source": "canned"})

    assert (
        grade_reader_result(
            positive,
            stale,
            GROUND_TRUTHS["changed_preference"],
            stale_preference=PREFERENCE,
        )["classification"]
        == "stale_preference_use"
    )
    assert (
        grade_reader_result(
            negative,
            disclosure,
            GROUND_TRUTHS["unknown_deployment_region"],
            irrelevant_context=[CORRECTED_PREFERENCE],
        )["classification"]
        == "inappropriate_irrelevant_disclosure"
    )
    assert (
        grade_reader_result(
            positive,
            unsupported,
            GROUND_TRUTHS["changed_preference"],
        )["classification"]
        == "unsupported_answer"
    )
    abstention_outcome = grade_reader_result(
        positive,
        abstention,
        GROUND_TRUTHS["changed_preference"],
    )
    assert abstention_outcome["classification"] == "safe_abstention"
    assert abstention_outcome["safe_abstention_not_comparative_evidence"] is True


def test_public_packet_and_result_objects_revalidate_hashes() -> None:
    packet = _packet("changed_preference")
    result = build_reader_result(packet, CORRECTED_PREFERENCE, provenance={"source": "canned"})
    forged_packet = replace(packet, packet_sha256="0" * 64)
    with pytest.raises(ReceiptValidationError, match="packet hash"):
        grade_reader_result(forged_packet, result, GROUND_TRUTHS["changed_preference"])

    nested_config = {"reader": {"temperature": 0}}
    nested_result = build_reader_result(
        packet,
        CORRECTED_PREFERENCE,
        evidence_kind="external_reader",
        config=nested_config,
        provenance={"source": "external_receipt"},
    )
    nested_config["reader"]["temperature"] = 1
    with pytest.raises(ReceiptValidationError, match="result hash"):
        grade_reader_result(packet, nested_result, GROUND_TRUTHS["changed_preference"])


def test_ground_truth_must_match_packet_task() -> None:
    packet = _packet("changed_preference")
    result = build_reader_result(packet, CORRECTED_PREFERENCE, provenance={"source": "canned"})

    with pytest.raises(ReceiptValidationError, match="ground truth"):
        grade_reader_result(
            packet,
            result,
            replace(
                GROUND_TRUTHS["changed_preference"],
                task_key="unknown_deployment_region",
            ),
        )


def test_six_cell_grading_keeps_common_instruction_limits_and_raw_outcomes() -> None:
    packets = {}
    results = {}
    for task_key in TASK_IDS:
        for arm_id in ARM_IDS:
            source = "no_memory" if arm_id == ARM_IDS[0] else arm_id
            packets[(arm_id, task_key)] = _packet(task_key, source=source)
            answer = (
                "UNKNOWN"
                if task_key == "unknown_deployment_region" or arm_id == ARM_IDS[0]
                else CORRECTED_PREFERENCE
            )
            results[(arm_id, task_key)] = build_reader_result(
                packets[(arm_id, task_key)],
                answer,
                provenance={"source": "canned"},
            )

    report = grade_six_cells(
        packets,
        results,
        construction_units_by_arm={ARM_IDS[0]: 0, ARM_IDS[1]: 3, ARM_IDS[2]: 0},
        stale_preference=PREFERENCE,
        irrelevant_context_by_task={"unknown_deployment_region": [CORRECTED_PREFERENCE]},
    )

    assert report["schema"] == "atc.cross_client_reader_outcomes.v1"
    assert report["denominators"] == {
        "cell_count": 6,
        "task_count": 2,
        "arm_count": 3,
        "success_count": 5,
    }
    assert report["classification_counts"] == {"exact_match": 5, "safe_abstention": 1}
    assert report["comparative_interpretation"]["superiority_claim_permitted"] is False
    assert all(item["evidence_kind"] == "non_model_fixture" for item in report["outcomes"])
    assert all("answer" not in item for item in report["outcomes"])
    assert all(packet.construction_units == 0 for packet in packets.values())
    assert report["construction_access_retrieval_accounting"][ARM_IDS[1]]["construction_units"] == 3

    reused = dict(packets)
    reused[(ARM_IDS[1], TASK_IDS[0])] = packets[(ARM_IDS[0], TASK_IDS[0])]
    with pytest.raises(ReceiptValidationError, match="maintained_context_file"):
        grade_six_cells(
            reused,
            results,
            construction_units_by_arm={ARM_IDS[0]: 0, ARM_IDS[1]: 3, ARM_IDS[2]: 0},
            stale_preference=PREFERENCE,
            irrelevant_context_by_task={"unknown_deployment_region": [CORRECTED_PREFERENCE]},
        )


def test_reader_limits_reject_overflow_before_provider_or_as_result_failure() -> None:
    packet = _packet("changed_preference")
    with pytest.raises(ReceiptValidationError, match="before provider"):
        build_packet(
            TASK_PROMPTS["changed_preference"],
            (),
            context_source="test",
            construction_units=0,
            access_units=0,
            retrieval_units=0,
            input_token_count=2_049,
        )
    result = build_reader_result(
        packet,
        CORRECTED_PREFERENCE,
        usage={"output_tokens": 33},
        provenance={"source": "external_receipt"},
        evidence_kind="external_reader",
    )
    outcome = grade_reader_result(packet, result, GROUND_TRUTHS["changed_preference"])
    assert outcome["classification"] == "output_overflow"
    assert outcome["success"] is False


def test_result_hash_packet_binding_and_matrix_shape_fail_closed() -> None:
    packet = _packet("changed_preference")
    result = build_reader_result(packet, CORRECTED_PREFERENCE, provenance={"source": "canned"})
    tampered = copy.deepcopy(result.as_dict())
    tampered["answer"] = "UNKNOWN"
    with pytest.raises(ReceiptValidationError, match="hash"):
        reader_result_from_mapping(tampered)

    with pytest.raises(ReceiptValidationError, match="fixed arm/task matrix"):
        grade_six_cells({}, {}, construction_units_by_arm={})

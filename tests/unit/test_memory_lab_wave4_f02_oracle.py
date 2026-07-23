from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = (
    REPOSITORY_ROOT / "research" / "memory-lab" / "wave4-falsification-oracle.json"
)


def _oracle() -> dict[str, object]:
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


def test_f02_oracle_is_frozen_from_governance_base() -> None:
    oracle = _oracle()

    assert oracle["status"] == "frozen_preimplementation"
    assert (
        oracle["governance_base_commit"]
        == "f545c37157845f0bd402215719cb8c747b7fc21d"
    )
    assert oracle["evidence_level"] == "L0"


def test_m3_oracle_covers_frozen_contract() -> None:
    oracle = _oracle()
    m3 = oracle["m3"]
    assert isinstance(m3, dict)

    assert set(m3["derived_surfaces"]) == {
        "retrieval_selection",
        "issued_context",
        "procedure",
        "selection_cache",
        "working_state",
        "use_statistics",
    }
    assert set(m3["mutations"]) == {
        "correction",
        "scope_narrowing",
        "permission_revocation",
        "ordinary_delete",
        "terminal_purge",
        "policy_generation_change",
    }
    topologies = m3["topologies"]
    assert isinstance(topologies, list)
    assert {topology["kind"] for topology in topologies} == {
        "chain",
        "fan_out",
        "fan_in",
        "shared_descendant",
        "cycle_attempt",
        "cross_scope_edge_attempt",
    }

    case_ids = {case["case_id"] for case in m3["cases"]}
    assert case_ids == {
        "M3-C01-CORRECTION-CHAIN",
        "M3-C02-SCOPE-NARROWING-FANOUT",
        "M3-C03-PERMISSION-REVOCATION-FANIN",
        "M3-C04-ORDINARY-DELETE-RESTORE",
        "M3-C05-TERMINAL-PURGE-IDENTIFIER-REUSE",
        "M3-C06-POLICY-GENERATION-CHANGE",
        "M3-C07-PARTIAL-REPAIR-BARRIER",
        "M3-C08-STALE-WRITER",
        "M3-C09-CYCLE-EDGE-ATTEMPT",
        "M3-C10-CROSS-SCOPE-EDGE-ATTEMPT",
        "M3-C11-DUPLICATE-AND-CONFLICTING-REPLAY",
        "M3-C12-OUT-OF-ORDER-MUTATION",
        "M3-C13-SHARED-DESCENDANT-CORRECTION",
        "M3-C14-PURGE-DURING-ISSUE",
        "M3-C15-OPTIMIZATION-WORK-CONTROL",
    }

    metrics = m3["decisive_metrics"]
    assert isinstance(metrics, dict)
    for name, specification in metrics.items():
        if name.endswith("_count"):
            assert specification["required"] == 0


def test_m1_oracle_separates_observation_from_inference() -> None:
    oracle = _oracle()
    m1 = oracle["m1"]
    assert isinstance(m1, dict)

    assert m1["allowed_stages"] == [
        "assigned",
        "supplied",
        "acknowledged",
        "observed_use",
        "action",
        "outcome",
        "invalidated",
    ]
    forbidden = set(m1["receipt_forbidden_fields"])
    assert {
        "raw_context",
        "raw_prompt",
        "raw_response",
        "hidden_reasoning",
        "chain_of_thought",
        "credential",
        "secret",
        "stable_content_hash",
        "cross_transaction_tracking_id",
    } <= forbidden

    case_ids = {case["case_id"] for case in m1["cases"]}
    assert {
        "M1-C02-NONACKNOWLEDGEMENT-IS-UNKNOWN",
        "M1-C03-USE-WITHOUT-ACKNOWLEDGEMENT",
        "M1-C04-ACKNOWLEDGED-BUT-NOT-OBSERVED-USED",
        "M1-C05-DUPLICATE-REPLAY",
        "M1-C06-CONFLICTING-REPLAY",
        "M1-C07-OUT-OF-ORDER-AND-IMPOSSIBLE",
        "M1-C08-FABRICATED-OUTCOME",
        "M1-C11-DELETE-VERSUS-PURGE",
        "M1-C12-PURGED-IDENTIFIER-REUSE",
        "M1-C14-RAW-TRACE-AND-HIDDEN-REASONING-REJECTION",
        "M1-C15-RECEIPT-CORRELATION-NONINTERFERENCE",
        "M1-C16-PURGE-RACE-WITH-OBSERVED-USE",
    } <= case_ids

    metrics = m1["decisive_metrics"]
    assert isinstance(metrics, dict)
    for name, specification in metrics.items():
        if name.endswith("_count"):
            assert specification["required"] == 0


def test_failure_receipts_are_bounded_and_prior_art_is_primary() -> None:
    oracle = _oracle()
    contract = oracle["failure_receipt_contract"]
    assert isinstance(contract, dict)
    assert "hidden_reasoning" in contract["forbidden_fields"]
    assert "raw_record_id_after_purge" in contract["forbidden_fields"]
    assert "per_run_artifact_ref" in contract["required_fields"]

    sources = oracle["prior_art_boundary"]
    assert isinstance(sources, list)
    assert len(sources) >= 8
    assert all(source["accessed"] == "2026-07-23" for source in sources)
    assert all(
        source["url"].startswith(
            (
                "https://arxiv.org/",
                "https://bazel.build/",
                "https://www.vldb.org/",
                "https://www.w3.org/",
                "https://openlineage.io/",
                "https://learn.microsoft.com/",
            )
        )
        for source in sources
    )


def test_every_case_references_declared_oracle_objects() -> None:
    oracle = _oracle()

    for mechanism_name in ("m3", "m1"):
        mechanism = oracle[mechanism_name]
        assert isinstance(mechanism, dict)
        invariants = {
            invariant["invariant_id"] for invariant in mechanism["global_invariants"]
        }
        cases = mechanism["cases"]
        assert len({case["case_id"] for case in cases}) == len(cases)
        assert all(case["schedule"] and case["expected"] for case in cases)
        assert all(set(case["invariants"]) <= invariants for case in cases)

    m3 = oracle["m3"]
    assert isinstance(m3, dict)
    topology_ids = {topology["topology_id"] for topology in m3["topologies"]}
    assert all(case["topology_id"] in topology_ids for case in m3["cases"])

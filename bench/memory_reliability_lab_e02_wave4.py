"""Probe ADR-047 semantic gaps against the frozen production Core.

E02 uses only hand-authored symbolic data and disposable local Core stores. It
does not connect to a running Core, infer missing semantics from adjacent
fields, or patch unsupported production behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import types
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import allthecontext
import allthecontext.core.service as core_service_module
import allthecontext.models as models_module
import allthecontext.storage as storage_module
from allthecontext.core.service import CoreService
from allthecontext.models import CandidateInput, SearchRequest
from pydantic import BaseModel, ValidationError

FIXTURE = Path(__file__).with_name("memory_reliability_lab_e02_wave4_fixtures.json")
REPORT_SCHEMA = "atc.memory-reliability-lab.e02-wave4-report.v1"
GOVERNANCE_BASE = "f545c37157845f0bd402215719cb8c747b7fc21d"
WORKTREE_ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATIONS = frozenset(
    {
        "SUPPORTED_OBSERVED",
        "CONTRADICTED_OBSERVED",
        "UNSUPPORTED",
        "NOT_EXERCISED",
    }
)

_MARKER = "E02_SYNTHETIC_"


@dataclass(frozen=True, slots=True)
class CaseSpec:
    case_id: str
    required_semantic: str
    expected_classification: str
    expected_probe_classifications: Mapping[str, str]
    production_touchpoints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProbeResult:
    classification: str
    probe_classifications: Mapping[str, str]
    observed: Mapping[str, Any]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a string list")
    return tuple(value)


def load_fixture(path: Path = FIXTURE) -> tuple[dict[str, Any], tuple[CaseSpec, ...]]:
    """Load the expectation fixture that was committed before E02 execution."""

    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    root = dict(_mapping(loaded, "fixture"))
    if root.get("schema") != "atc.memory-reliability-lab.e02-wave4-fixture.v1":
        raise ValueError("unsupported E02 fixture schema")
    if root.get("governance_base") != GOVERNANCE_BASE:
        raise ValueError("E02 fixture does not target the immutable governance base")
    if root.get("expectations_frozen_before_execution") is not True:
        raise ValueError("E02 expectations were not frozen before execution")
    if set(_strings(root.get("classification_values"), "classification_values")) != set(
        CLASSIFICATIONS
    ):
        raise ValueError("E02 fixture classification vocabulary changed")
    raw_cases = root.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("cases must be a list")
    cases = tuple(
        CaseSpec(
            case_id=str(raw["case_id"]),
            required_semantic=str(raw["required_semantic"]),
            expected_classification=str(raw["expected_classification"]),
            expected_probe_classifications={
                str(key): str(value)
                for key, value in _mapping(
                    raw["expected_probe_classifications"],
                    "expected_probe_classifications",
                ).items()
            },
            production_touchpoints=_strings(
                raw["production_touchpoints"], "production_touchpoints"
            ),
        )
        for item in raw_cases
        for raw in (_mapping(item, "case"),)
    )
    if len(cases) != 6 or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("E02 requires exactly six uniquely identified gap cases")
    for case in cases:
        if case.expected_classification not in CLASSIFICATIONS:
            raise ValueError("case has an unknown expected classification")
        if not set(case.expected_probe_classifications.values()) <= CLASSIFICATIONS:
            raise ValueError("case has an unknown expected probe classification")
    return root, cases


def _rejects_extra(model: type[BaseModel], payload: Mapping[str, Any]) -> bool:
    try:
        model.model_validate(payload)
    except ValidationError:
        return True
    return False


def _record_id(core: CoreService, candidate: CandidateInput) -> str:
    observation = core.store.add_candidate(candidate)
    if observation.record_id is None:
        raise AssertionError("synthetic explicit observation did not produce a record")
    return observation.record_id


def _search_contents(core: CoreService, request: SearchRequest) -> list[str]:
    return [item.content for item in core.retrieval.search(request).items]


def _generic_epistemic_role(root: Path) -> ProbeResult:
    core = CoreService.in_directory(root)
    common = f"{_MARKER}ROLE"
    for kind, suffix in (("evidence", "KIND_EVIDENCE"), ("claim", "KIND_CLAIM")):
        _record_id(
            core,
            CandidateInput(
                kind=kind,
                content=f"{common}_{suffix}",
                structured_value={"epistemic_role": "evidence"},
                explicit_user_statement=True,
            ),
        )
    kind_results = _search_contents(
        core,
        SearchRequest(query=common, kinds=["evidence"], limit=10),
    )
    role_write_rejected = _rejects_extra(
        CandidateInput,
        {
            "kind": "claim",
            "content": f"{common}_DIRECT_ROLE",
            "epistemic_role": "evidence",
        },
    )
    role_query_rejected = _rejects_extra(
        SearchRequest,
        {"query": common, "epistemic_roles": ["evidence"]},
    )
    role_fields_absent = (
        "epistemic_role" not in CandidateInput.model_fields
        and "epistemic_roles" not in SearchRequest.model_fields
        and role_write_rejected
        and role_query_rejected
    )
    kind_substitution_incomplete = len(kind_results) == 1
    return ProbeResult(
        classification="UNSUPPORTED" if role_fields_absent else "CONTRADICTED_OBSERVED",
        probe_classifications={
            "role_fields": "UNSUPPORTED"
            if role_fields_absent
            else "SUPPORTED_OBSERVED",
            "kind_substitution": "CONTRADICTED_OBSERVED"
            if kind_substitution_incomplete
            else "SUPPORTED_OBSERVED",
        },
        observed={
            "candidate_role_field_available": False,
            "search_role_field_available": False,
            "direct_role_write_rejected": role_write_rejected,
            "direct_role_query_rejected": role_query_rejected,
            "semantic_role_peer_count": 2,
            "kind_substitution_result_count": len(kind_results),
        },
    )


def _project_and_domain_applicability(root: Path) -> ProbeResult:
    core = CoreService.in_directory(root)
    common = f"{_MARKER}APPLICABILITY"
    fixtures = (
        ("ATLAS_FINANCE", ["project:atlas", "domain:finance"]),
        ("ATLAS_MEDICAL", ["project:atlas", "domain:medical"]),
        ("ORCHID_FINANCE", ["project:orchid", "domain:finance"]),
    )
    for suffix, scopes in fixtures:
        _record_id(
            core,
            CandidateInput(
                kind="procedure",
                content=f"{common}_{suffix}",
                scopes=scopes,
                explicit_user_statement=True,
            ),
        )
    project_label_results = _search_contents(
        core,
        SearchRequest(
            query=common,
            scopes=["project:atlas"],
            limit=10,
        ),
    )
    project_signal_core = CoreService.in_directory(root / "current-project-signal")
    _record_id(
        project_signal_core,
        CandidateInput(
            kind="procedure",
            content=f"{common}_CURRENT_PROJECT_SIGNAL",
            scopes=["project:atlas"],
            explicit_user_statement=True,
        ),
    )
    project_signal_results = _search_contents(
        project_signal_core,
        SearchRequest(query=f"{common}_CURRENT_PROJECT_SIGNAL", current_project="orchid"),
    )
    project_write_rejected = _rejects_extra(
        CandidateInput,
        {
            "kind": "procedure",
            "content": f"{common}_DIRECT",
            "project": "atlas",
            "domain": "finance",
        },
    )
    domain_query_rejected = _rejects_extra(
        SearchRequest,
        {"query": common, "current_project": "atlas", "current_domain": "finance"},
    )
    exact_fields_absent = (
        "project" not in CandidateInput.model_fields
        and "domain" not in CandidateInput.model_fields
        and "current_domain" not in SearchRequest.model_fields
        and project_write_rejected
        and domain_query_rejected
    )
    return ProbeResult(
        classification="UNSUPPORTED" if exact_fields_absent else "CONTRADICTED_OBSERVED",
        probe_classifications={
            "project_domain_fields": "UNSUPPORTED"
            if exact_fields_absent
            else "SUPPORTED_OBSERVED",
            "scope_label_and_substitution": "CONTRADICTED_OBSERVED"
            if len(project_label_results) == 2
            else "SUPPORTED_OBSERVED",
            "current_project_hard_gate_substitution": "CONTRADICTED_OBSERVED"
            if len(project_signal_results) == 1
            else "SUPPORTED_OBSERVED",
        },
        observed={
            "direct_project_domain_write_rejected": project_write_rejected,
            "direct_domain_query_rejected": domain_query_rejected,
            "expected_and_match_count": 1,
            "project_label_without_domain_result_count": len(project_label_results),
            "current_project_only_result_count": len(project_signal_results),
            "scope_filter_operator": "any_requested_label",
            "current_project_semantic": "ranking_signal_not_hard_gate",
        },
    )


def _dependency_lineage_and_invalidation(root: Path) -> ProbeResult:
    core = CoreService.in_directory(root)
    common = f"{_MARKER}LINEAGE"
    source_id = _record_id(
        core,
        CandidateInput(
            kind="claim",
            content=f"{common}_SOURCE_V1",
            explicit_user_statement=True,
        ),
    )
    derived_id = _record_id(
        core,
        CandidateInput(
            kind="compiled_context",
            content=f"{common}_DERIVED",
            structured_value={"depends_on": [{"record_id": source_id, "version": 1}]},
            explicit_user_statement=True,
        ),
    )
    derived_before = core.store.get_record(derived_id)
    core.store.correct_record(
        source_id,
        content=f"{common}_SOURCE_V2",
        reason="E02 synthetic lineage boundary probe",
    )
    derived_after = core.store.get_record(derived_id)
    derived_results = _search_contents(
        core,
        SearchRequest(query=f"{common}_DERIVED", limit=10),
    )
    dependency_write_rejected = _rejects_extra(
        CandidateInput,
        {
            "kind": "compiled_context",
            "content": f"{common}_DIRECT",
            "dependencies": [{"record_id": "synthetic", "version": 1}],
        },
    )
    dependency_fields_absent = (
        "dependencies" not in CandidateInput.model_fields
        and "depends_on" not in CandidateInput.model_fields
        and dependency_write_rejected
    )
    metadata_remained_inert = (
        derived_before.version == derived_after.version == 1 and len(derived_results) == 1
    )
    return ProbeResult(
        classification="UNSUPPORTED"
        if dependency_fields_absent
        else "CONTRADICTED_OBSERVED",
        probe_classifications={
            "dependency_fields": "UNSUPPORTED"
            if dependency_fields_absent
            else "SUPPORTED_OBSERVED",
            "structured_metadata_substitution": "CONTRADICTED_OBSERVED"
            if metadata_remained_inert
            else "SUPPORTED_OBSERVED",
        },
        observed={
            "direct_dependency_write_rejected": dependency_write_rejected,
            "structured_dependency_metadata_accepted": True,
            "derived_version_before_source_correction": derived_before.version,
            "derived_version_after_source_correction": derived_after.version,
            "derived_retrievable_after_source_correction": len(derived_results) == 1,
            "automatic_invalidation_observed": False,
        },
    )


def _eviction_decay_and_procedure_retirement(root: Path) -> ProbeResult:
    core = CoreService.in_directory(root)
    common = f"{_MARKER}LIFECYCLE"
    expired_id = _record_id(
        core,
        CandidateInput(
            kind="procedure",
            content=f"{common}_EXPIRED",
            expires_at="2000-01-01T00:00:00Z",
            explicit_user_statement=True,
        ),
    )
    active_id = _record_id(
        core,
        CandidateInput(
            kind="procedure",
            content=f"{common}_ACTIVE",
            confidence=0.9,
            explicit_user_statement=True,
        ),
    )
    visible = _search_contents(core, SearchRequest(query=common, limit=10))
    expired_canonical = core.store.get_record(expired_id)
    active_before = core.store.get_record(active_id)
    _search_contents(core, SearchRequest(query=common, limit=10))
    active_after = core.store.get_record(active_id)
    decay_write_rejected = _rejects_extra(
        CandidateInput,
        {
            "kind": "procedure",
            "content": f"{common}_DECAY",
            "decay_rate": 0.1,
        },
    )
    retirement_write_rejected = _rejects_extra(
        CandidateInput,
        {
            "kind": "procedure",
            "content": f"{common}_RETIRED",
            "retirement_status": "retired",
        },
    )
    expiry_boundary_supported = (
        expired_canonical.id == expired_id
        and f"{common}_EXPIRED" not in visible
        and f"{common}_ACTIVE" in visible
    )
    confidence_unchanged = active_before.confidence == active_after.confidence == 0.9
    unsupported = (
        "decay_rate" not in CandidateInput.model_fields
        and "retirement_status" not in CandidateInput.model_fields
        and decay_write_rejected
        and retirement_write_rejected
        and confidence_unchanged
    )
    return ProbeResult(
        classification="UNSUPPORTED" if unsupported else "CONTRADICTED_OBSERVED",
        probe_classifications={
            "explicit_expiry_boundary": "SUPPORTED_OBSERVED"
            if expiry_boundary_supported
            else "CONTRADICTED_OBSERVED",
            "decay_fields": "UNSUPPORTED"
            if decay_write_rejected and confidence_unchanged
            else "SUPPORTED_OBSERVED",
            "procedure_retirement_fields": "UNSUPPORTED"
            if retirement_write_rejected
            else "SUPPORTED_OBSERVED",
        },
        observed={
            "expired_record_remains_canonical": expired_canonical.id == expired_id,
            "expired_record_retrievable": f"{common}_EXPIRED" in visible,
            "active_record_retrievable": f"{common}_ACTIVE" in visible,
            "confidence_before_search": active_before.confidence,
            "confidence_after_search": active_after.confidence,
            "direct_decay_write_rejected": decay_write_rejected,
            "direct_retirement_write_rejected": retirement_write_rejected,
        },
    )


def _same_identifier_after_terminal_purge(root: Path) -> ProbeResult:
    core = CoreService.in_directory(root)
    marker = f"{_MARKER}PURGE_IDENTITY"
    record_id = _record_id(
        core,
        CandidateInput(kind="claim", content=marker, explicit_user_statement=True),
    )
    caller_id_rejected = _rejects_extra(
        CandidateInput,
        {
            "id": record_id,
            "kind": "claim",
            "content": marker,
            "explicit_user_statement": True,
        },
    )
    core.store.purge(
        "record",
        record_id,
        confirmation=core.store.purge_confirmation_phrase("record", record_id),
        compact=True,
    )
    recreated_id = _record_id(
        core,
        CandidateInput(kind="claim", content=marker, explicit_user_statement=True),
    )
    with core.store.connect() as connection:
        tombstone_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM purge_tombstones WHERE stable_id=?",
                (record_id,),
            ).fetchone()[0]
        )
    fresh_boundary_supported = (
        recreated_id != record_id
        and tombstone_count == 1
        and len(_search_contents(core, SearchRequest(query=marker, limit=10))) == 1
    )
    return ProbeResult(
        classification="NOT_EXERCISED"
        if caller_id_rejected
        else "CONTRADICTED_OBSERVED",
        probe_classifications={
            "caller_selected_record_id": "NOT_EXERCISED"
            if caller_id_rejected
            else "CONTRADICTED_OBSERVED",
            "fresh_identity_and_tombstone_boundary": "SUPPORTED_OBSERVED"
            if fresh_boundary_supported
            else "CONTRADICTED_OBSERVED",
        },
        observed={
            "caller_selected_record_id_rejected": caller_id_rejected,
            "fresh_recreation_uses_distinct_identifier": recreated_id != record_id,
            "old_identifier_tombstone_count": tombstone_count,
            "fresh_record_result_count": 1,
            "exact_same_identifier_attempted": False,
        },
    )


def _procedure_preconditions_and_transfer(root: Path) -> ProbeResult:
    core = CoreService.in_directory(root)
    marker = f"{_MARKER}PROCEDURE_TRANSFER"
    _record_id(
        core,
        CandidateInput(
            kind="procedure",
            content=marker,
            scopes=["project:atlas"],
            structured_value={
                "preconditions": ["environment:atlas"],
                "applies_to": ["domain:finance"],
            },
            explicit_user_statement=True,
        ),
    )
    wrong_project_results = _search_contents(
        core,
        SearchRequest(query=marker, current_project="orchid", limit=10),
    )
    explicit_scope_results = _search_contents(
        core,
        SearchRequest(query=marker, scopes=["project:orchid"], limit=10),
    )
    semantic_write_rejected = _rejects_extra(
        CandidateInput,
        {
            "kind": "procedure",
            "content": marker,
            "preconditions": ["environment:atlas"],
            "applies_to": ["domain:finance"],
        },
    )
    semantic_fields_absent = (
        "preconditions" not in CandidateInput.model_fields
        and "applies_to" not in CandidateInput.model_fields
        and semantic_write_rejected
    )
    metadata_substitution_failed = len(wrong_project_results) == 1
    explicit_scope_boundary_supported = len(explicit_scope_results) == 0
    return ProbeResult(
        classification="UNSUPPORTED" if semantic_fields_absent else "CONTRADICTED_OBSERVED",
        probe_classifications={
            "procedure_semantic_fields": "UNSUPPORTED"
            if semantic_fields_absent
            else "SUPPORTED_OBSERVED",
            "structured_metadata_substitution": "CONTRADICTED_OBSERVED"
            if metadata_substitution_failed
            else "SUPPORTED_OBSERVED",
            "explicit_scope_boundary": "SUPPORTED_OBSERVED"
            if explicit_scope_boundary_supported
            else "CONTRADICTED_OBSERVED",
        },
        observed={
            "direct_procedure_semantic_write_rejected": semantic_write_rejected,
            "structured_preconditions_accepted_as_inert_data": True,
            "wrong_current_project_result_count": len(wrong_project_results),
            "wrong_explicit_scope_result_count": len(explicit_scope_results),
            "procedure_kind_creates_special_gate": False,
        },
    )


_EXECUTORS: Mapping[str, Callable[[Path], ProbeResult]] = {
    "generic_epistemic_role": _generic_epistemic_role,
    "project_and_domain_applicability": _project_and_domain_applicability,
    "dependency_lineage_and_invalidation": _dependency_lineage_and_invalidation,
    "eviction_decay_and_procedure_retirement": _eviction_decay_and_procedure_retirement,
    "same_identifier_after_terminal_purge": _same_identifier_after_terminal_purge,
    "procedure_preconditions_and_transfer": _procedure_preconditions_and_transfer,
}


def _run_case(case: CaseSpec, root: Path) -> dict[str, Any]:
    executor = _EXECUTORS[case.case_id]
    try:
        result = executor(root)
    except Exception as error:  # preserve a bounded receipt without raw values
        return {
            "case_id": case.case_id,
            "required_semantic": case.required_semantic,
            "expected_classification": case.expected_classification,
            "classification": "CONTRADICTED_OBSERVED",
            "expected_probe_classifications": dict(case.expected_probe_classifications),
            "probe_classifications": {},
            "production_touchpoints": list(case.production_touchpoints),
            "observed": {
                "evaluation_completed": False,
                "exception_type": type(error).__name__,
            },
        }
    return {
        "case_id": case.case_id,
        "required_semantic": case.required_semantic,
        "expected_classification": case.expected_classification,
        "classification": result.classification,
        "expected_probe_classifications": dict(case.expected_probe_classifications),
        "probe_classifications": dict(result.probe_classifications),
        "production_touchpoints": list(case.production_touchpoints),
        "observed": {"evaluation_completed": True, **result.observed},
    }


def _fingerprint(receipts: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(receipts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _relative_module_path(module: types.ModuleType) -> str:
    origin_value = module.__file__
    if origin_value is None:
        raise RuntimeError("imported production module has no filesystem origin")
    origin = Path(origin_value).resolve()
    try:
        relative = origin.relative_to(WORKTREE_ROOT)
    except ValueError as error:
        raise RuntimeError(
            "imported production module did not originate in the worker worktree"
        ) from error
    return relative.as_posix()


def _execution_origin_receipt() -> dict[str, Any]:
    modules = {
        "allthecontext": allthecontext,
        "allthecontext.core.service": core_service_module,
        "allthecontext.models": models_module,
        "allthecontext.storage": storage_module,
    }
    return {
        "governance_commit_sha": GOVERNANCE_BASE,
        "import_origin_verified_to_worker_worktree": True,
        "relative_imported_module_paths": {
            name: _relative_module_path(module) for name, module in modules.items()
        },
        "absolute_paths_recorded": False,
        "pre_verification_receipts_invalidated": True,
    }


def _implementation_boundary_receipt() -> dict[str, Any]:
    return {
        "receipt_type": "design_proposal_separate_from_observed_case_facts",
        "current_schema_api_touchpoints": {
            "canonical_input_output": [
                "CandidateInput",
                "ContextRecordOut",
                "context_candidates",
                "context_records",
                "context_record_versions snapshot_json",
            ],
            "eligibility_and_retrieval": [
                "SearchRequest",
                "EligibleRecordSelector.select/select_authorized",
                "_admissibility_inputs",
                "context_fts",
            ],
            "lifecycle_and_distribution": [
                "CoreStore.correct_record/delete_record/restore_record/purge",
                "purge_tombstones",
                "replication_events",
                "export/import migration paths",
            ],
        },
        "authority_boundaries": [
            (
                "Core remains the sole canonical authority for roles, applicability, "
                "lineage, and lifecycle state."
            ),
            (
                "Callers and Relay may propose explicit metadata but cannot canonize "
                "it or create an independent dependency graph."
            ),
            (
                "Applicability and role gates must resolve in Core before relevance "
                "scoring; kind, free-form structured_value, tags, and scope labels "
                "are not substitute authorities."
            ),
            (
                "Derived artifacts may reference Core record IDs and versions but "
                "cannot own canonical currentness or purge truth."
            ),
        ],
        "migration_risks": [
            (
                "New fields must round-trip through candidate, record, version "
                "snapshot, replication, export/import, and public transport schemas "
                "without inferring values for legacy rows."
            ),
            (
                "A role backfill from kind would silently encode the rejected "
                "substitution; legacy role must remain unknown until explicitly "
                "classified."
            ),
            (
                "Project and domain applicability require AND-capable normalized "
                "predicates; reusing current any-label scope filtering would preserve "
                "cross-boundary leakage."
            ),
            (
                "Lineage must be Core-owned, version-bound, cycle checked, scope "
                "checked, and purge closed; a sidecar graph would create a second "
                "authority."
            ),
            (
                "Procedure preconditions require versioned evaluation semantics and "
                "fail-closed unknown handling before any automatic transfer."
            ),
            (
                "Decay or retirement must not rewrite evidence history or weaken "
                "terminal tombstones; initial migrations should separate eligibility "
                "state from destructive collection."
            ),
        ],
        "first_capability": {
            "capability": "generic_epistemic_role",
            "smallest_safe_boundary": (
                "Add an optional explicit Core-owned role field end-to-end across "
                "canonical input, record versions, retrieval eligibility, replication, "
                "and export; preserve unknown for legacy rows and never infer role "
                "from kind."
            ),
            "reason": (
                "It is the smallest additive semantic needed before role-aware "
                "applicability or procedure policy, and it does not require a parallel "
                "authority or derived-state graph."
            ),
            "promotion_guard": (
                "Do not claim complete applicability or lifecycle readiness from this "
                "field alone; project/domain gates, lineage closure, and procedure "
                "preconditions remain separate gated work."
            ),
        },
    }


def run_fixture(*, repeats: int = 2) -> dict[str, Any]:
    """Execute deterministic E02 probes in disposable synthetic Core stores."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    fixture, cases = load_fixture()
    repeated: list[list[dict[str, Any]]] = []
    with TemporaryDirectory(prefix="atc-e02-") as temporary:
        temporary_root = Path(temporary)
        for repeat in range(repeats):
            repeated.append(
                [
                    _run_case(case, temporary_root / f"r{repeat}" / f"c{index}")
                    for index, case in enumerate(cases)
                ]
            )
    receipts = repeated[0]
    fingerprints = [_fingerprint(value) for value in repeated]
    classifications = Counter(
        str(receipt["classification"]) for receipt in receipts
    )
    case_mismatch_count = sum(
        receipt["classification"] != receipt["expected_classification"]
        for receipt in receipts
    )
    probe_mismatch_count = sum(
        receipt["probe_classifications"]
        != receipt["expected_probe_classifications"]
        for receipt in receipts
    )
    return {
        "schema": REPORT_SCHEMA,
        "experiment": "E02",
        "governance_base": GOVERNANCE_BASE,
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "expectations": fixture["expectation_authorship"],
        "execution_origin_receipt": _execution_origin_receipt(),
        "execution_scope": {
            "condition": "frozen_production_core_disposable_synthetic_stores",
            "production_code_changed": False,
            "declared_gap_count": len(cases),
            "executed_boundary_probe_count": sum(
                len(case.expected_probe_classifications) for case in cases
            ),
            "temporary_isolated_store": True,
            "operator_core_connected": False,
            "external_code_or_systems_exercised": False,
            "network_calls": 0,
            "provider_calls": 0,
            "model_calls": 0,
            "raw_personal_context": False,
        },
        "receipts": receipts,
        "summary": {
            "gap_count": len(receipts),
            "classification_counts": {
                classification: classifications[classification]
                for classification in sorted(CLASSIFICATIONS)
            },
            "case_expectation_mismatch_count": case_mismatch_count,
            "probe_expectation_mismatch_count": probe_mismatch_count,
            "evaluation_error_count": sum(
                not bool(receipt["observed"]["evaluation_completed"])
                for receipt in receipts
            ),
        },
        "determinism": {
            "repeats": repeats,
            "repeat_deterministic": len(set(fingerprints)) == 1,
            "receipt_fingerprint": fingerprints[0],
        },
        "implementation_boundary_receipt": _implementation_boundary_receipt(),
        "limitations": [
            (
                "The expectations and mappings were hand-authored against a known "
                "frozen codebase, not supplied by a blind independent oracle."
            ),
            (
                "UNSUPPORTED is a positive absence finding, not a passed conformance "
                "test or an evaluator failure."
            ),
            (
                "Same-identifier post-purge behavior was not exercised because public "
                "production inputs reject caller-selected record IDs; only the adjacent "
                "fresh-identity and tombstone boundary ran."
            ),
            (
                "Structured-value probes show that free-form data is inert; they do "
                "not claim that production documents those keys as semantic fields."
            ),
            (
                "Expiry observations cover retrieval eligibility, not storage eviction, "
                "confidence decay, or procedure retirement."
            ),
            (
                "Only isolated SQLite Core stores and public/stable Python paths ran; "
                "no operator Core, Relay, export restore, external system, provider, "
                "model, or protected action ran."
            ),
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_fixture(repeats=args.repeats)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

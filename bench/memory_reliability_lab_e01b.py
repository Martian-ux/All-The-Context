"""Run E01b against production Core at the frozen coordinator base.

The experiment uses only synthetic symbolic values and temporary stores. It
does not connect to a running Core, change production behavior, or emulate
semantics that the production schema does not expose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, NoReturn

from allthecontext.core.service import CoreService
from allthecontext.models import (
    CandidateInput,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
    SearchRequest,
)
from allthecontext.storage import NotFoundError

FIXTURES = Path(__file__).with_name("memory_reliability_lab_e01b_fixtures.json")
REFERENCE_FIXTURES = Path(__file__).with_name("memory_reliability_lab_e01_fixtures.json")
REPORT_SCHEMA = "atc.memory-reliability-lab.e01b-report.v1"
COORDINATOR_BASE = "950f649d9e3cc106fb8ff4febbe38919f8e00d11"

_MARKER_PREFIX = "E01B_SYNTHETIC_"


@dataclass(frozen=True, slots=True)
class CaseSpec:
    case_id: str
    reference_scenarios: tuple[str, ...]
    capability: str
    stage: str
    expected_status: str
    expected_classification: str
    pass_reason_code: str
    failure_reason_codes: tuple[str, ...]
    production_path: str


@dataclass(frozen=True, slots=True)
class _ObservedFailure(Exception):
    stage: str
    reason_code: str
    check: str


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a string list")
    return tuple(value)


def load_fixture(path: Path = FIXTURES) -> tuple[dict[str, Any], tuple[CaseSpec, ...]]:
    """Load and validate the E01b production-conformance fixture."""

    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    root = dict(_mapping(loaded, "fixture"))
    if root.get("schema") != "atc.memory-reliability-lab.e01b-fixture.v1":
        raise ValueError("unsupported E01b fixture schema")
    if root.get("coordinator_base") != COORDINATOR_BASE:
        raise ValueError("E01b fixture does not target the coordinator base")
    reference = _mapping(root.get("reference_e01_fixture"), "reference_e01_fixture")
    if hashlib.sha256(REFERENCE_FIXTURES.read_bytes()).hexdigest() != reference.get("sha256"):
        raise ValueError("frozen E01 reference fixture digest changed")
    raw_cases = root.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("cases must be a list")
    cases = tuple(
        CaseSpec(
            case_id=str(raw["case_id"]),
            reference_scenarios=_strings(raw["reference_scenarios"], "reference_scenarios"),
            capability=str(raw["capability"]),
            stage=str(raw["stage"]),
            expected_status=str(raw["expected_status"]),
            expected_classification=str(raw["expected_classification"]),
            pass_reason_code=str(raw["pass_reason_code"]),
            failure_reason_codes=_strings(
                raw["failure_reason_codes"], "failure_reason_codes"
            ),
            production_path=str(raw["production_path"]),
        )
        for item in raw_cases
        for raw in (_mapping(item, "case"),)
    )
    allowed_statuses = set(_strings(root["status_values"], "status_values"))
    allowed_classifications = set(
        _strings(root["classification_values"], "classification_values")
    )
    allowed_stages = set(_strings(root["stage_values"], "stage_values"))
    allowed_codes = set(_strings(root["reason_codes"], "reason_codes"))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    for case in cases:
        if case.expected_status not in allowed_statuses:
            raise ValueError("case has an unknown expected status")
        if case.expected_classification not in allowed_classifications:
            raise ValueError("case has an unknown expected classification")
        if case.stage not in allowed_stages:
            raise ValueError("case has an unknown stage")
        if case.pass_reason_code not in allowed_codes:
            raise ValueError("case has an unknown pass reason code")
        if not set(case.failure_reason_codes) <= allowed_codes:
            raise ValueError("case has an unknown failure reason code")
    return root, cases


def _fail(stage: str, reason_code: str, check: str) -> NoReturn:
    raise _ObservedFailure(stage, reason_code, check)


def _search_contents(core: CoreService, request: SearchRequest) -> list[str]:
    return [item.content for item in core.retrieval.search(request).items]


def _authority_source_admission(root: Path) -> dict[str, Any]:
    core = CoreService.in_directory(root)
    local_value = f"{_MARKER_PREFIX}AUTHORITY_LOCAL"
    imported_value = f"{_MARKER_PREFIX}AUTHORITY_IMPORT"
    local = core.store.add_candidate(
        CandidateInput(
            kind="claim",
            content=local_value,
            explicit_user_statement=True,
        )
    )
    if local.disposition != ObservationDisposition.APPLIED or local.record_id is None:
        _fail("capture", "CAPTURE_MISS", "eligible_local_observation_not_applied")

    source = core.store.add_source(
        imported_value.encode(),
        source_service="e01b",
        source_type="text",
        filename="synthetic.txt",
        media_type="text/plain",
    )
    session = core.store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["synthetic_archive"],
        unavailable_sources=[],
    )
    session_id = str(session["session_id"])
    submitted = core.store.submit_batch(
        session_id,
        "e01b-authority-batch",
        [
            CandidateInput(
                kind="claim",
                content=imported_value,
                source_id=source.id,
                source_type="generic_document",
                explicit_user_statement=True,
            )
        ],
    )
    observation_id = str(submitted["candidate_ids"][0])
    staged = core.store.get_candidate(observation_id)
    if staged.disposition != ObservationDisposition.STAGED:
        _fail("capture", "CAPTURE_MISS", "archive_observation_not_staged")
    core.store.finish_ingestion(
        session_id,
        CoverageReport(available=["synthetic_archive"], complete=True),
    )
    imported = core.store.get_candidate(observation_id)
    imported_results = _search_contents(core, SearchRequest(query=imported_value, limit=10))
    if imported.disposition != ObservationDisposition.TENTATIVE:
        _fail("canonicalize", "WITNESS_COLLAPSE", "generic_import_not_tentative")
    if imported.record_id is not None or imported_value in imported_results:
        _fail("canonicalize", "WITNESS_COLLAPSE", "generic_import_became_current")
    return {
        "local_disposition": "applied",
        "archive_initial_disposition": "staged",
        "archive_final_disposition": "tentative",
        "archive_current_result_count": 0,
    }


def _correction_currentness(root: Path) -> dict[str, Any]:
    core = CoreService.in_directory(root)
    common = f"{_MARKER_PREFIX}CURRENTNESS"
    first = core.store.add_candidate(
        CandidateInput(
            kind="claim",
            content=f"{common}_FIRST",
            entity_key="e01b:subject",
            attribute_key="e01b:state",
            observed_at="2035-01-01T00:00:00Z",
            explicit_user_statement=True,
        )
    )
    if first.record_id is None:
        _fail("capture", "CAPTURE_MISS", "initial_slot_observation_not_applied")
    newer = core.store.add_candidate(
        CandidateInput(
            kind="claim",
            content=f"{common}_NEWER",
            entity_key="e01b:subject",
            attribute_key="e01b:state",
            observed_at="2035-01-03T00:00:00Z",
            explicit_user_statement=True,
        )
    )
    older = core.store.add_candidate(
        CandidateInput(
            kind="claim",
            content=f"{common}_OLDER",
            entity_key="e01b:subject",
            attribute_key="e01b:state",
            observed_at="2035-01-02T00:00:00Z",
            explicit_user_statement=True,
        )
    )
    correction = core.store.add_candidate(
        CandidateInput(
            kind="correction",
            content=f"{common}_CORRECTED",
            supersedes=first.record_id,
            explicit_user_statement=True,
        )
    )
    current = core.store.get_record(first.record_id)
    if (
        newer.record_id != first.record_id
        or older.disposition != ObservationDisposition.IGNORED
        or correction.record_id != first.record_id
        or current.content != f"{common}_CORRECTED"
    ):
        _fail("canonicalize", "CANONICAL_WRONG_CURRENT", "slot_did_not_converge")
    results = _search_contents(
        core,
        SearchRequest(query=common, kinds=["claim"], limit=10),
    )
    if results != [f"{common}_CORRECTED"]:
        _fail("correct_forget", "CORRECTION_NONCONVERGENCE", "search_exposed_stale_value")
    if len(core.store.record_history(first.record_id)) != 3:
        _fail("correct_forget", "CORRECTION_NONCONVERGENCE", "version_history_incomplete")
    return {
        "stable_record_identity": True,
        "older_observation_disposition": "ignored",
        "current_result_count": 1,
        "stale_result_count": 0,
        "preserved_version_count": 3,
    }


def _kind_constrained_retrieval(root: Path) -> dict[str, Any]:
    core = CoreService.in_directory(root)
    common = f"{_MARKER_PREFIX}ROLE"
    evidence = f"{common}_EVIDENCE"
    preference = f"{common}_PREFERENCE"
    for kind, content in (("evidence", evidence), ("preference", preference)):
        core.store.add_candidate(
            CandidateInput(
                kind=kind,
                content=content,
                explicit_user_statement=True,
            )
        )
    results = _search_contents(
        core,
        SearchRequest(query=common, kinds=["evidence"], limit=10),
    )
    if results != [evidence]:
        _fail("applicability", "EPISTEMIC_ROLE_MISUSE", "kind_filter_not_enforced")
    return {
        "requested_kind_count": 1,
        "matching_result_count": 1,
        "nonmatching_result_count": 0,
        "semantic_boundary": "kind_filter_only",
    }


def _explicit_scope_applicability(root: Path) -> dict[str, Any]:
    core = CoreService.in_directory(root)
    common = f"{_MARKER_PREFIX}SCOPE"
    atlas = f"{common}_ATLAS"
    orchid = f"{common}_ORCHID"
    for scope, content in (("project:atlas", atlas), ("project:orchid", orchid)):
        core.store.add_candidate(
            CandidateInput(
                kind="procedure",
                content=content,
                scopes=[scope],
                explicit_user_statement=True,
            )
        )
    results = _search_contents(
        core,
        SearchRequest(
            query=common,
            scopes=["project:atlas"],
            kinds=["procedure"],
            limit=10,
        ),
    )
    if results != [atlas]:
        _fail(
            "applicability",
            "CROSS_PROJECT_DOMAIN_LEAKAGE",
            "explicit_scope_filter_not_enforced",
        )
    return {
        "requested_scope_count": 1,
        "matching_result_count": 1,
        "cross_scope_result_count": 0,
        "semantic_boundary": "explicit_scope_filter",
    }


def _reversible_delete_restore(root: Path) -> dict[str, Any]:
    core = CoreService.in_directory(root)
    marker = f"{_MARKER_PREFIX}REVERSIBLE"
    observation = core.store.add_candidate(
        CandidateInput(kind="claim", content=marker, explicit_user_statement=True)
    )
    if observation.record_id is None:
        _fail("capture", "CAPTURE_MISS", "reversible_record_not_created")
    record_id = observation.record_id
    core.store.delete_record(record_id, reason="e01b synthetic reversible deletion")
    deleted_results = _search_contents(core, SearchRequest(query=marker, limit=10))
    if deleted_results:
        _fail("retrieve", "RETRIEVAL_STALE", "soft_deleted_record_was_retrieved")
    restored = core.store.restore_record(
        record_id,
        reason="e01b synthetic reversible restoration",
    )
    restored_results = _search_contents(core, SearchRequest(query=marker, limit=10))
    if restored.content != marker or restored_results != [marker]:
        _fail("retrieve", "RETRIEVAL_MISS", "restored_record_was_not_retrieved")
    return {
        "visible_before_delete": True,
        "visible_after_delete": False,
        "visible_after_restore": True,
        "stable_record_identity": restored.id == record_id,
    }


def _table_contains_marker(connection: sqlite3.Connection, marker: str) -> bool:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return any(
        marker in repr(tuple(row))
        for table in tables
        for row in connection.execute(f'SELECT * FROM "{table}"')
    )


def _purge_terminality_residue(root: Path) -> dict[str, Any]:
    core = CoreService.in_directory(root)
    marker = f"{_MARKER_PREFIX}PURGE_RESIDUE_PROBE"
    source = core.store.add_source(
        marker.encode(),
        source_service="e01b",
        source_type="text",
        filename="purge-probe.txt",
        media_type="text/plain",
    )
    observation = core.store.add_candidate(
        CandidateInput(
            kind="claim",
            content=marker,
            source_id=source.id,
            source_reference="synthetic#probe",
            explicit_user_statement=True,
        )
    )
    if observation.record_id is None:
        _fail("capture", "CAPTURE_MISS", "purge_record_not_created")
    record_id = observation.record_id
    core.store.correct_record(
        record_id,
        content=f"{marker}_CURRENT",
        reason="e01b synthetic pre-purge correction",
    )
    job = core.store.purge(
        "record",
        record_id,
        confirmation=core.store.purge_confirmation_phrase("record", record_id),
        compact=True,
    )
    restore_blocked = False
    try:
        core.store.restore_record(record_id, reason="e01b terminality probe")
    except NotFoundError:
        restore_blocked = True
    if not restore_blocked:
        _fail("invalidate_rebuild", "PURGE_RESIDUE", "restore_resurrected_purged_record")
    if core.store.record_history(record_id):
        _fail("invalidate_rebuild", "PURGE_RESIDUE", "purged_version_history_remained")
    if _search_contents(core, SearchRequest(query=marker, limit=10)):
        _fail("invalidate_rebuild", "PURGE_RESIDUE", "purged_record_remained_retrievable")
    source_removed = False
    try:
        core.store.get_source_content(source.id)
    except NotFoundError:
        source_removed = True
    if not source_removed:
        _fail("invalidate_rebuild", "PURGE_RESIDUE", "unshared_source_blob_remained")
    with core.store.connect() as connection:
        logical_residue = _table_contains_marker(connection, marker)
        tombstone_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM purge_tombstones WHERE stable_id=?",
                (record_id,),
            ).fetchone()[0]
        )
        secure_delete = int(connection.execute("PRAGMA secure_delete").fetchone()[0])
    if logical_residue or tombstone_count != 1:
        _fail("invalidate_rebuild", "PURGE_RESIDUE", "logical_derived_residue_remained")
    database = core.config.database_path
    paths = (
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
    )
    physical_residue = any(path.exists() and marker.encode() in path.read_bytes() for path in paths)
    if physical_residue or secure_delete != 1 or job["phase"] != "completed":
        _fail("invalidate_rebuild", "PURGE_RESIDUE", "physical_residue_or_pending_compaction")
    return {
        "restore_after_purge_blocked": True,
        "current_result_count": 0,
        "version_history_count": 0,
        "unshared_source_removed": True,
        "logical_marker_residue": False,
        "physical_marker_residue": False,
        "opaque_tombstone_count": 1,
        "secure_delete_enabled": True,
        "compaction_phase": "completed",
    }


_EXECUTORS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "authority_source_admission": _authority_source_admission,
    "correction_currentness": _correction_currentness,
    "kind_constrained_retrieval": _kind_constrained_retrieval,
    "explicit_scope_applicability": _explicit_scope_applicability,
    "reversible_delete_restore": _reversible_delete_restore,
    "purge_terminality_residue": _purge_terminality_residue,
}


def _run_case(case: CaseSpec, root: Path) -> dict[str, Any]:
    executor = _EXECUTORS.get(case.case_id)
    if executor is None:
        return {
            "status": "not_exercised",
            "classification": case.expected_classification,
            "stage": case.stage,
            "reason_code": case.pass_reason_code,
            "observed": {"production_path_available": False},
        }
    try:
        observed = executor(root)
    except _ObservedFailure as failure:
        return {
            "status": "fail",
            "classification": "defect",
            "stage": failure.stage,
            "reason_code": failure.reason_code,
            "observed": {"failed_check": failure.check},
        }
    except Exception as error:  # preserve a failing receipt without leaking raw data
        return {
            "status": "fail",
            "classification": "evaluation_error",
            "stage": case.stage,
            "reason_code": "EVALUATOR_ERROR",
            "observed": {"exception_type": type(error).__name__},
        }
    return {
        "status": "pass",
        "classification": "conformance",
        "stage": case.stage,
        "reason_code": case.pass_reason_code,
        "observed": observed,
    }


def _receipt_fingerprint(receipts: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(receipts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def run_fixture(*, repeats: int = 2) -> dict[str, Any]:
    """Run deterministic repeats in disposable production Core stores."""

    if repeats < 1:
        raise ValueError("repeats must be positive")
    fixture, cases = load_fixture()
    repeated: list[list[dict[str, Any]]] = []
    with TemporaryDirectory(prefix="atc-e01b-") as temporary:
        temporary_root = Path(temporary)
        for repeat in range(repeats):
            receipts: list[dict[str, Any]] = []
            for case_index, case in enumerate(cases):
                result = _run_case(case, temporary_root / f"r{repeat}" / f"c{case_index}")
                receipts.append(
                    {
                        "case_index": case_index,
                        "capability": case.capability,
                        "reference_scenario_count": len(case.reference_scenarios),
                        "expected_status": case.expected_status,
                        "expected_classification": case.expected_classification,
                        **result,
                    }
                )
            repeated.append(receipts)

    fingerprints = [_receipt_fingerprint(receipts) for receipts in repeated]
    receipts = repeated[0]
    status_counts = Counter(str(receipt["status"]) for receipt in receipts)
    classification_counts = Counter(str(receipt["classification"]) for receipt in receipts)
    expectation_mismatch_count = sum(
        receipt["status"] != receipt["expected_status"]
        or receipt["classification"] != receipt["expected_classification"]
        for receipt in receipts
    )
    return {
        "schema": REPORT_SCHEMA,
        "experiment": "E01b",
        "coordinator_base": COORDINATOR_BASE,
        "fixture_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        "reference_e01_fixture_sha256": hashlib.sha256(
            REFERENCE_FIXTURES.read_bytes()
        ).hexdigest(),
        "execution_scope": {
            "condition": "production_core_at_frozen_coordinator_base",
            "frozen_base": COORDINATOR_BASE,
            "production_code_changed": False,
            "declared_capability_count": len(cases),
            "production_path_exercised_capability_count": len(_EXECUTORS),
            "not_exercised_capability_count": len(cases) - len(_EXECUTORS),
            "complete_capability_coverage": False,
            "temporary_isolated_store": True,
            "operator_core_connected": False,
            "external_systems_exercised": False,
            "network_calls": 0,
            "model_calls": 0,
            "python": "3.12",
        },
        "production_paths": sorted(
            case.production_path
            for case in cases
            if case.case_id in _EXECUTORS
        ),
        "receipts": receipts,
        "summary": {
            "case_count": len(receipts),
            "pass_count": status_counts["pass"],
            "fail_count": status_counts["fail"],
            "not_exercised_count": status_counts["not_exercised"],
            "classification_counts": dict(sorted(classification_counts.items())),
            "expectation_mismatch_count": expectation_mismatch_count,
        },
        "determinism": {
            "repeats": repeats,
            "repeat_deterministic": len(set(fingerprints)) == 1,
            "receipt_fingerprint": fingerprints[0],
        },
        "privacy": {
            **fixture["content_policy"],
            "raw_context_in_report": False,
            "record_or_source_ids_in_report": False,
            "temporary_stores_removed": True,
            "marker_scan_after_compacted_purge": True,
        },
        "limitations": [
            "kind filtering is narrower than a generic epistemic-role model",
            "explicit scope filtering does not establish a project-and-domain hard gate",
            "production has no explicit derived dependency-lineage relation",
            "automatic-v1 has no eviction, decay, or procedure-retirement semantics",
            "public observation paths do not accept caller-selected stable record IDs",
            "production has no procedure-precondition or applies-to field",
            "residue inspection covers the isolated SQLite store and its local side files only",
            "no answer model, protected action, Relay, export restore, or external system ran",
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

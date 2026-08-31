"""Run the disposable Packet H-B Memory Truth proof.

The proof exercises the real local workspace adapter, capture coordinator, and
registered-source sink, then observes Core only through its public Memory
Truth APIs.  The report is bounded content-free aggregate JSON; workspace text, paths,
source identities, account labels, and provider item identifiers never cross
the report boundary.
"""

from __future__ import annotations

# The checkout source path is inserted before third-party imports so a stale
# editable install cannot silently satisfy this proof.
# ruff: noqa: E402, I001

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

# Force this checkout ahead of any stale editable allthecontext install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SOURCE = _REPO_ROOT / "packages" / "allthecontext" / "src"
if not (_LOCAL_SOURCE / "allthecontext" / "__init__.py").is_file():
    raise RuntimeError("packet H proof requires the repository source tree")
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_LOCAL_SOURCE))

from allthecontext.capture import (
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureEvent,
    CaptureRunResult,
    CaptureSource,
)
from allthecontext.capture_runtime import _workspace_state_reader
from allthecontext.experimental_local_git_workspace_connector import (
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_PROVIDER,
    REGISTERED_SOURCE_TYPE,
    ObservationOrigin,
    registered_source_reference,
)
from allthecontext.models import Availability, MemoryTruthStatus, Sensitivity
from allthecontext.registered_source_admission import RegisteredSourceCaptureApplicationSink
from allthecontext.storage import CoreStore, NotFoundError

from bench.packet_h import (
    _assert_disposable_root,
    _close_core_stores,
    _CrashAfterFirstAdmission,
    _create_source,
    _DisposableRootCapability,
    _require_checkout_allthecontext,
    _runner_owned_temporary_root,
)

from tests.fixtures.local_git_workspace import create_sanitized_workspace

_require_checkout_allthecontext()

_DISPOSABLE_PREFIX = "atc-packet-h-truth-"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9.:-]+$")


def _run_summary(result: CaptureRunResult) -> dict[str, object]:
    """Keep capture outcomes to deterministic, identifier-safe fields."""

    return {
        "status": result.status,
        "pages": result.pages,
        "events": result.events,
        "applied_events": result.applied_events,
        "duplicate_events": result.duplicate_events,
        "failures": result.failures,
    }


def _fact_class(item: Any) -> str | None:
    structured = item.record.structured_value
    if not isinstance(structured, dict):
        return None
    value = structured.get("fact_class")
    return value if isinstance(value, str) else None


def _public_truth_state(item: Any) -> dict[str, Any]:
    """Return the complete public item projection for internal comparison only."""

    state = item.model_dump(mode="json")
    if not isinstance(state, dict):
        raise AssertionError("Memory Truth item projection is malformed")
    return cast(dict[str, Any], state)


def _truth_collection_signature(
    items: Sequence[Any],
) -> tuple[tuple[str, dict[str, Any]], ...] | None:
    """Keep complete public state and stable identities internal to the proof."""

    signature: list[tuple[str, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for item in items:
        state = _public_truth_state(item)
        record_state = state.get("record")
        if not isinstance(record_state, Mapping):
            return None
        record_id = record_state.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in seen_ids:
            return None
        seen_ids.add(record_id)
        signature.append((record_id, state))
    return tuple(signature)


def _truth_collections_match(left: Sequence[Any], right: Sequence[Any]) -> bool:
    """Compare full public state and record identity without reporting either."""

    left_signature = _truth_collection_signature(left)
    right_signature = _truth_collection_signature(right)
    return left_signature is not None and left_signature == right_signature


def _withdrawal_state_is_exact(before: Any, after: Any) -> bool:
    """Allow only the public fields intentionally changed by source withdrawal."""

    before_state = _public_truth_state(before)
    after_state = _public_truth_state(after)
    before_record = before_state.get("record")
    after_record = after_state.get("record")
    if not isinstance(before_record, Mapping) or not isinstance(after_record, Mapping):
        return False

    if (
        before.status is not MemoryTruthStatus.CURRENT
        or after.status is not MemoryTruthStatus.DELETED
        or before.record.status is not MemoryTruthStatus.CURRENT
        or after.record.status is not MemoryTruthStatus.DELETED
        or before.record.deleted_at is not None
        or after.record.deleted_at is None
        or before.record.expires_at is not None
        or after.record.expires_at is None
        or before.record.updated_at == after.record.updated_at
        or before.status_reason == after.status_reason
        or before.record.version + 1 != after.record.version
        or before.history_count + 1 != after.history_count
    ):
        return False

    # The full public projection deliberately includes source metadata,
    # source references, scopes/ACL, structured values, and evidence.  Only
    # deletion/status/version-history fields and their timestamps may differ.
    preserved_before = dict(before_state)
    preserved_after = dict(after_state)
    for field in ("status", "status_reason", "history_count"):
        preserved_before.pop(field, None)
        preserved_after.pop(field, None)
    preserved_before_record = dict(before_record)
    preserved_after_record = dict(after_record)
    for field in ("deleted_at", "expires_at", "status", "updated_at", "version"):
        preserved_before_record.pop(field, None)
        preserved_after_record.pop(field, None)
    preserved_before["record"] = preserved_before_record
    preserved_after["record"] = preserved_after_record
    return preserved_before == preserved_after


def _apply_manifest_override(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    override: CaptureCapabilityManifest | None,
) -> None:
    """Apply a test-controlled posture declaration to a disposable adapter."""

    if override is None:
        return
    if override.provider != adapter.capability_manifest.provider:
        raise ValueError("packet_h_manifest_override_provider_mismatch")
    object.__setattr__(adapter, "_capability_manifest", override)


def _truth_summary(items: Sequence[Any]) -> dict[str, object]:
    fact_classes: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    evidence_count = 0
    all_core_available = True
    all_normal_sensitivity = True
    all_registered_source_provenance = True
    all_registered_capture_type = True
    all_registered_provider = True
    all_applied_evidence = True
    for item in items:
        record = item.record
        status_counts[item.status.value] += 1
        evidence_count += len(item.evidence)
        fact_class = _fact_class(item)
        if fact_class is not None:
            fact_classes[fact_class] += 1
        all_core_available = all_core_available and record.availability is Availability.CORE
        all_normal_sensitivity = all_normal_sensitivity and record.sensitivity is Sensitivity.NORMAL
        all_registered_source_provenance = (
            all_registered_source_provenance
            and record.observation_origin == ObservationOrigin.REGISTERED_SOURCE.value
            and all(
                evidence_item.observation_origin == ObservationOrigin.REGISTERED_SOURCE.value
                for evidence_item in item.evidence
            )
        )
        all_registered_capture_type = (
            all_registered_capture_type
            and record.source_type == REGISTERED_SOURCE_TYPE
            and all(
                evidence_item.source_type == REGISTERED_SOURCE_TYPE
                for evidence_item in item.evidence
            )
        )
        all_registered_provider = (
            all_registered_provider
            and record.source_service == REGISTERED_SOURCE_PROVIDER
            and all(
                evidence_item.source_service == REGISTERED_SOURCE_PROVIDER
                for evidence_item in item.evidence
            )
        )
        all_applied_evidence = all_applied_evidence and all(
            evidence_item.disposition.value in {"applied", "reinforced"}
            for evidence_item in item.evidence
        )
    return {
        "item_count": len(items),
        "status_counts": dict(sorted(status_counts.items())),
        "fact_class_counts": dict(sorted(fact_classes.items())),
        "evidence_count": evidence_count,
        "all_core_available": all_core_available,
        "all_normal_sensitivity": all_normal_sensitivity,
        "all_registered_source_provenance": all_registered_source_provenance,
        "all_registered_capture_type": all_registered_capture_type,
        "all_registered_provider": all_registered_provider,
        "all_applied_evidence": all_applied_evidence,
    }


def _coverage_summary(coverage: Any) -> dict[str, object]:
    return {
        "source_count": coverage.source_count,
        "deleted_source_count": coverage.deleted_source_count,
        "observation_count": coverage.observation_count,
        "observations_by_disposition": dict(sorted(coverage.observations_by_disposition.items())),
        "record_count": coverage.record_count,
        "records_by_status": dict(sorted(coverage.records_by_status.items())),
        "conflict_group_count": coverage.conflict_group_count,
        "ingestion_session_count": coverage.ingestion_session_count,
        "incomplete_ingestion_session_count": coverage.incomplete_ingestion_session_count,
        "sessions_with_unavailable_sources": coverage.sessions_with_unavailable_sources,
    }


def _stable_truth_signature(summary: Mapping[str, object]) -> tuple[object, ...]:
    return (
        summary["item_count"],
        summary["status_counts"],
        summary["fact_class_counts"],
        summary["evidence_count"],
        summary["all_core_available"],
        summary["all_normal_sensitivity"],
        summary["all_registered_source_provenance"],
        summary["all_registered_capture_type"],
        summary["all_registered_provider"],
        summary["all_applied_evidence"],
    )


def _path_leak_forms(path: Path) -> frozenset[str]:
    """Native, resolved, POSIX, and JSON-escaped forms of one path."""

    resolved = path.resolve()
    forms = {
        str(path),
        str(resolved),
        path.as_posix(),
        resolved.as_posix(),
    }
    escaped = {json.dumps(form)[1:-1] for form in forms}
    return frozenset(form for form in forms | escaped if form)


def _walk_public_strings(value: object) -> Iterator[str]:
    if type(value) is str:
        yield value
        return
    if type(value) is dict:
        for key, nested in value.items():
            if type(key) is str:
                yield key
            yield from _walk_public_strings(nested)
        return
    if type(value) is list:
        for nested in value:
            yield from _walk_public_strings(nested)


def _public_truth_has_no_raw_material(
    public_items: Sequence[Any],
    *,
    workspace: Path,
    source: Any,
    target_event: CaptureEvent,
) -> bool:
    """Check public model-dumped string fields without returning identifiers."""

    dumped = [item.model_dump(mode="json") for item in public_items]
    texts = list(_walk_public_strings(dumped))
    forbidden = {
        "# Sample workspace",
        "Use deterministic local fixtures",
        "def answer()",
        "metadata-only",
        "not-for-capture",
        "AKIAIOSFODNN7EXAMPLE",
        "FIXTURE_SECRET=not-for-capture",
        "src/app.py",
        "docs/decision.md",
        "scripts/build.sh",
        "README.md",
        str(source.id),
        source.account_label,
        str(source.account_fingerprint),
        target_event.provider_item_id,
    }
    forbidden.update(_path_leak_forms(workspace))
    for value in target_event.payload.values():
        if type(value) is str:
            forbidden.add(value)
    return not any(fragment in text for text in texts for fragment in forbidden if fragment)


def _identifier_safe_report(report: Mapping[str, object]) -> bool:
    def safe(value: object) -> bool:
        if value is None or isinstance(value, (bool, int)):
            return True
        if isinstance(value, str):
            return _IDENTIFIER.fullmatch(value) is not None
        if isinstance(value, Mapping):
            return all(safe(nested) for nested in value.values())
        return False

    return safe(report)


def _stable_digest(material: Mapping[str, object]) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target_event(
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
    source: CaptureSource,
) -> CaptureEvent:
    page = adapter.fetch_page(source, None, 0)
    for event in page.events:
        if event.operation == "upsert" and event.payload.get("relative_path") == "README.md":
            return event
    raise AssertionError("sanitized target item was not emitted")


def _run_disposable(
    root: Path,
    *,
    ownership: _DisposableRootCapability | None = None,
    capability_manifest_override: CaptureCapabilityManifest | None = None,
) -> dict[str, object]:
    root = _assert_disposable_root(root, ownership=ownership)
    store: CoreStore | None = None
    restarted_store: CoreStore | None = None
    post_delete_store: CoreStore | None = None
    try:
        store = CoreStore(root / "core.sqlite3")
        workspace = create_sanitized_workspace(root / "workspace")
        store.initialize_vault()

        adapter = LocalGitWorkspaceCaptureProviderAdapter((workspace,))
        _apply_manifest_override(adapter, capability_manifest_override)
        real_sink = RegisteredSourceCaptureApplicationSink(store)
        first_coordinator = CaptureCoordinator(store, sink=_CrashAfterFirstAdmission(real_sink))
        source = _create_source(first_coordinator, adapter)
        target_event = _target_event(adapter, source)
        first = first_coordinator.run(source.id)

        restarted_store = CoreStore(root / "core.sqlite3")
        restarted_coordinator = CaptureCoordinator(
            restarted_store,
            sink=RegisteredSourceCaptureApplicationSink(restarted_store),
        )
        restarted_adapter = type(adapter)(
            (workspace,),
            state_reader=_workspace_state_reader(restarted_store),
        )
        _apply_manifest_override(restarted_adapter, capability_manifest_override)
        restarted_coordinator.register_adapter(source.provider, restarted_adapter)
        restarted_coordinator.resume(source.id)
        recovery = restarted_coordinator.run(source.id)
        scan_before_withdrawal = restarted_adapter.last_scan_report
        if scan_before_withdrawal is None:
            raise AssertionError("Memory Truth probe did not produce an initial scan report")

        current_before_response = restarted_store.list_memory_truth(
            status=MemoryTruthStatus.CURRENT,
            limit=500,
        )
        coverage_before = restarted_store.memory_truth_coverage()
        current_before = current_before_response.items
        details_before = [
            restarted_store.get_memory_truth(item.record.id, include_deleted=True)
            for item in current_before
        ]

        replay = restarted_coordinator.run(source.id)
        replay_response = restarted_store.list_memory_truth(
            status=MemoryTruthStatus.CURRENT, limit=500
        )
        replay_coverage = restarted_store.memory_truth_coverage()

        relative_path = target_event.payload.get("relative_path")
        if not isinstance(relative_path, str):
            raise AssertionError("target event path is malformed")
        relative_target = Path(relative_path)
        if relative_target.is_absolute() or ".." in relative_target.parts:
            raise AssertionError("target event escaped the disposable workspace")
        target_path = workspace / relative_target
        target_path.unlink()
        deletion = restarted_coordinator.run(source.id)

        current_after_response = restarted_store.list_memory_truth(
            status=MemoryTruthStatus.CURRENT,
            limit=500,
        )
        deleted_response = restarted_store.list_memory_truth(
            status=MemoryTruthStatus.DELETED,
            limit=500,
        )
        coverage_after = restarted_store.memory_truth_coverage()
        current_after = current_after_response.items
        deleted_items = deleted_response.items
        before_ids = {item.record.id for item in current_before}
        after_ids = {item.record.id for item in current_after}
        withdrawn_ids = before_ids - after_ids
        expected_source_reference = registered_source_reference(
            source.id, target_event.provider_item_id
        )
        bound_items = [
            item
            for item in details_before
            if item.record.source_reference == expected_source_reference
            and any(
                evidence_item.source_reference == expected_source_reference
                for evidence_item in item.evidence
            )
        ]
        if len(bound_items) != 1:
            raise AssertionError("Memory Truth probe could not bind the target source reference")
        target_before = bound_items[0]
        target_record_id = target_before.record.id
        exact_source_reference_withdrawn = withdrawn_ids == {target_record_id}
        deleted_detail = restarted_store.get_memory_truth(target_record_id, include_deleted=True)
        excluded_from_current = target_record_id not in after_ids
        try:
            restarted_store.get_memory_truth(target_record_id, include_deleted=False)
        except NotFoundError:
            excluded_from_non_deleted_detail = True
        else:
            excluded_from_non_deleted_detail = False

        post_delete_store = CoreStore(root / "core.sqlite3")
        post_delete_coordinator = CaptureCoordinator(
            post_delete_store,
            sink=RegisteredSourceCaptureApplicationSink(post_delete_store),
        )
        post_delete_adapter = type(adapter)(
            (workspace,),
            state_reader=_workspace_state_reader(post_delete_store),
        )
        _apply_manifest_override(post_delete_adapter, capability_manifest_override)
        post_delete_coordinator.register_adapter(source.provider, post_delete_adapter)
        post_delete_replay = post_delete_coordinator.run(source.id)
        post_delete_current_response = post_delete_store.list_memory_truth(
            status=MemoryTruthStatus.CURRENT,
            limit=500,
        )
        post_delete_coverage = post_delete_store.memory_truth_coverage()

        public_items = [
            *current_before,
            *replay_response.items,
            *current_after,
            *deleted_items,
            deleted_detail,
            *post_delete_current_response.items,
        ]
        public_content_free = _public_truth_has_no_raw_material(
            public_items,
            workspace=workspace,
            source=source,
            target_event=target_event,
        )

        before_summary = _truth_summary(current_before)
        details_summary = _truth_summary(details_before)
        replay_summary = _truth_summary(replay_response.items)
        after_summary = _truth_summary(current_after)
        deleted_summary = _truth_summary(deleted_items)
        post_delete_summary = _truth_summary(post_delete_current_response.items)
        before_coverage = _coverage_summary(coverage_before)
        replay_coverage_summary = _coverage_summary(replay_coverage)
        after_coverage = _coverage_summary(coverage_after)
        post_delete_coverage_summary = _coverage_summary(post_delete_coverage)

        target_unchanged = _withdrawal_state_is_exact(target_before, deleted_detail)
        no_ordinary_tombstone = deleted_detail.status_reason == "record is soft-deleted"
        adapter_manifest = restarted_adapter.capability_manifest

        list_detail_identity_exact = _truth_collections_match(current_before, details_before)
        replay_identity_exact = _truth_collections_match(current_before, replay_response.items)
        deleted_list_detail_identity_exact = _truth_collections_match(
            deleted_items,
            [deleted_detail],
        )
        post_delete_replay_identity_exact = _truth_collections_match(
            current_after,
            post_delete_current_response.items,
        )

        capture = {
            "manifest_partial": adapter_manifest.coverage == "partial"
            and adapter_manifest.availability == "partial",
            "network_denied": adapter_manifest.network_access == "denied",
            "data_egress_empty": adapter_manifest.data_egress == (),
            "files_considered": scan_before_withdrawal.files_considered,
            "items_emitted": scan_before_withdrawal.items_emitted,
            "initial_run": _run_summary(first),
            "recovery_run": _run_summary(recovery),
            "replay_run": _run_summary(replay),
            "delete_run": _run_summary(deletion),
            "post_delete_replay_run": _run_summary(post_delete_replay),
        }
        truth = {
            "before_withdrawal": before_summary,
            "details_match_list": _stable_truth_signature(before_summary)
            == _stable_truth_signature(details_summary)
            and list_detail_identity_exact,
            "list_detail_identity_exact": list_detail_identity_exact,
            "coverage_before_withdrawal": before_coverage,
            "coverage_matches_list": before_coverage
            == _coverage_summary(current_before_response.coverage),
            "replay_stable": _stable_truth_signature(before_summary)
            == _stable_truth_signature(replay_summary)
            and before_coverage == replay_coverage_summary
            and replay_identity_exact,
            "replay_identity_exact": replay_identity_exact,
        }
        withdrawal = {
            "after_withdrawal": after_summary,
            "deleted_items": deleted_summary,
            "coverage_after_withdrawal": after_coverage,
            "deleted_status_observed": deleted_detail.status is MemoryTruthStatus.DELETED,
            "exact_source_reference_withdrawn": exact_source_reference_withdrawn,
            "exact_untouched_record": target_unchanged,
            "without_ordinary_tombstone": no_ordinary_tombstone,
            "excluded_from_current_list": excluded_from_current,
            "excluded_from_non_deleted_detail": excluded_from_non_deleted_detail,
            "listed_as_deleted": target_record_id in {item.record.id for item in deleted_items},
            "post_delete_replay_stable": _stable_truth_signature(after_summary)
            == _stable_truth_signature(post_delete_summary)
            and after_coverage == post_delete_coverage_summary
            and post_delete_replay_identity_exact,
            "deleted_list_detail_identity_exact": deleted_list_detail_identity_exact,
            "post_delete_replay_identity_exact": post_delete_replay_identity_exact,
        }
        acceptance = {
            "four_current_records": (
                before_summary["item_count"] == 4
                and before_summary["status_counts"] == {"current": 4}
                and before_summary["fact_class_counts"]
                == {"markdown_documentation": 2, "python_source": 1, "shell_script": 1}
            ),
            "registered_source_truth": (
                before_summary["all_core_available"]
                and before_summary["all_normal_sensitivity"]
                and before_summary["all_registered_source_provenance"]
                and before_summary["all_registered_capture_type"]
                and before_summary["all_registered_provider"]
                and before_summary["all_applied_evidence"]
            ),
            "capture_capability_posture": (
                capture["manifest_partial"]
                and capture["network_denied"]
                and capture["data_egress_empty"]
            ),
            "memory_truth_identity_exact": (
                truth["list_detail_identity_exact"]
                and truth["replay_identity_exact"]
                and withdrawal["deleted_list_detail_identity_exact"]
                and withdrawal["post_delete_replay_identity_exact"]
            ),
            "capture_admission_reconciles_with_truth": (
                recovery.events == 4
                and recovery.applied_events + recovery.duplicate_events == recovery.events
                and before_summary["evidence_count"] == 4
                and before_coverage["observation_count"] == 4
                and before_coverage["record_count"] == 4
                and current_before_response.total == len(current_before) == 4
                and cast(dict[str, int], before_coverage["records_by_status"])["current"] == 4
            ),
            "deletion_reconciles_without_new_observation": (
                deletion.events == 1
                and deletion.applied_events == 1
                and after_coverage["observation_count"] == 4
                and after_coverage["record_count"] == 4
                and cast(dict[str, int], after_coverage["records_by_status"])["current"] == 3
                and cast(dict[str, int], after_coverage["records_by_status"])["deleted"] == 1
                and current_after_response.total == len(current_after) == 3
                and deleted_response.total == len(deleted_items) == 1
            ),
            "withdrawal_is_exact_and_publicly_excluded": (
                bool(withdrawal["exact_source_reference_withdrawn"])
                and bool(withdrawal["exact_untouched_record"])
                and bool(withdrawal["without_ordinary_tombstone"])
                and bool(withdrawal["excluded_from_current_list"])
                and bool(withdrawal["excluded_from_non_deleted_detail"])
            ),
            "deleted_status_observed": bool(withdrawal["deleted_status_observed"]),
            "listed_as_deleted": bool(withdrawal["listed_as_deleted"]),
            "restart_replay_stable": (
                first.status == "failed"
                and recovery.status == "completed"
                and replay.status == "completed"
                and post_delete_replay.status == "completed"
                and replay.events == 0
                and post_delete_replay.events == 0
                and bool(truth["replay_stable"])
                and bool(withdrawal["post_delete_replay_stable"])
            ),
            "content_free_identifier_safe": public_content_free,
        }
        aggregate_material = {
            "capture": capture,
            "truth": truth,
            "withdrawal": withdrawal,
            "acceptance": acceptance,
        }
        report: dict[str, object] = {
            "schema_version": 1,
            "boundary": "packet-h-b-memory-truth",
            "capture": capture,
            "truth": truth,
            "withdrawal": withdrawal,
            "acceptance": acceptance,
            "aggregate_receipt": {
                "receipt_type": "packet-h-b-aggregate",
                "status": "pass" if all(value is True for value in acceptance.values()) else "fail",
                "identifier_digest": _stable_digest(aggregate_material),
            },
        }
        if not _identifier_safe_report(report):
            raise AssertionError("packet H-B report is not identifier-safe")
        return report
    finally:
        _close_core_stores(post_delete_store, restarted_store, store)


def run() -> dict[str, object]:
    """Execute H-B entirely inside a temporary, runner-owned Core vault."""

    with _runner_owned_temporary_root(_DISPOSABLE_PREFIX) as (root, ownership):
        report = _run_disposable(root, ownership=ownership)
    if root.exists():
        raise RuntimeError("packet_h_truth_temporary_state_not_removed")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    receipt = report.get("aggregate_receipt")
    return 0 if isinstance(receipt, dict) and receipt.get("status") == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

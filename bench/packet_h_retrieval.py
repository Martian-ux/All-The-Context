"""Run the disposable Packet H-C Retrieval V3 evidence check.

This runner composes the existing Packet H-A admission proof inside a fresh,
runner-owned temporary Core vault.  It exercises only Core-owned structural
sentences through the public Retrieval V3 facade, then removes one uniquely
classified workspace item through the real local adapter so retrieval can
prove withdrawal exclusion.  The report is disposable local evidence only;
it does not change ranking, wire a provider into production, or claim provider
support.
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
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

# Force this checkout ahead of any stale editable allthecontext install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SOURCE = _REPO_ROOT / "packages" / "allthecontext" / "src"
if not (_LOCAL_SOURCE / "allthecontext" / "__init__.py").is_file():
    raise RuntimeError("packet H proof requires the repository source tree")
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_LOCAL_SOURCE))

from allthecontext.capture import CaptureCoordinator
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_CODE_OWNED_SCOPES,
    REGISTERED_SOURCE_EXTRACTOR_ID,
    REGISTERED_SOURCE_EXTRACTOR_VERSION,
    REGISTERED_SOURCE_FACT_KIND,
    REGISTERED_SOURCE_FACT_SCHEMA,
    REGISTERED_SOURCE_FACT_SENTENCES,
    REGISTERED_SOURCE_PROVIDER,
    REGISTERED_SOURCE_TYPE,
)
from allthecontext.models import (
    BootstrapRequest,
    ContextPackMetadata,
    ContextRecordOut,
    SearchRequest,
)
from allthecontext.registered_source_admission import RegisteredSourceCaptureApplicationSink
from allthecontext.retrieval import RetrievalEngine
from allthecontext.storage import CoreStore

from bench.packet_h import (
    _assert_disposable_root,
    _close_core_stores,
    _DisposableRootCapability,
    _require_checkout_allthecontext,
    _runner_owned_temporary_root,
)
from bench.packet_h import (
    _packet_h_a_report_ready as _validate_h_a_report,
)
from bench.packet_h import (
    _run_disposable as _run_admission_disposable,
)

_require_checkout_allthecontext()

_DISPOSABLE_PREFIX = "atc-packet-h-c-"
_SCOPE = REGISTERED_SOURCE_CODE_OWNED_SCOPES[0]
_BOOTSTRAP_BUDGET = 256
_SOURCE_REFERENCE = re.compile(r"^registered-source-item-[0-9a-f]{64}$")
_BINDING_HASH = re.compile(r"binding=[0-9a-f]{64}$")
_EXPECTED_FACT_COUNTS = {
    "markdown_documentation": 2,
    "python_source": 1,
    "shell_script": 1,
}
_NEGATIVE_QUERY_LABELS = (
    "path",
    "source_text",
    "secret_content",
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]+$")
_FORBIDDEN_RAW_MATERIAL = (
    "# Sample workspace",
    "Use deterministic local fixtures",
    "def answer()",
    "This file is inert imported text.",
    "AKIAIOSFODNN7EXAMPLE",
    "FIXTURE_SECRET",
    "not-for-capture",
    "workspace-source-",
    "README.md",
    "docs/decision.md",
    "src/app.py",
    "scripts/build.sh",
)


def _packet_h_a_report_ready(report: object) -> bool:
    """Use the authoritative H-A validator before starting retrieval."""

    return _validate_h_a_report(report)


def _stable_digest(material: Mapping[str, object]) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _structural_record(item: ContextRecordOut, fact_class: str) -> bool:
    structured = item.structured_value
    return (
        item.kind == REGISTERED_SOURCE_FACT_KIND
        and item.content == REGISTERED_SOURCE_FACT_SENTENCES[fact_class]
        and item.scopes == list(REGISTERED_SOURCE_CODE_OWNED_SCOPES)
        and item.source_id is None
        and item.source_service == REGISTERED_SOURCE_PROVIDER
        and item.source_type == REGISTERED_SOURCE_TYPE
        and isinstance(item.source_reference, str)
        and _SOURCE_REFERENCE.fullmatch(item.source_reference) is not None
        and isinstance(item.evidence, str)
        and item.evidence.startswith(
            "Core registered-source structural fact; "
            f"schema={REGISTERED_SOURCE_FACT_SCHEMA}; "
            f"fact_class={fact_class}; "
        )
        and _BINDING_HASH.search(item.evidence) is not None
        and isinstance(structured, dict)
        and set(structured)
        == {
            "binding_hash",
            "extractor",
            "extractor_version",
            "fact_class",
            "schema",
        }
        and structured.get("binding_hash") == item.evidence.rsplit("binding=", 1)[-1]
        and structured.get("extractor") == REGISTERED_SOURCE_EXTRACTOR_ID
        and structured.get("extractor_version") == REGISTERED_SOURCE_EXTRACTOR_VERSION
        and structured.get("fact_class") == fact_class
        and structured.get("schema") == REGISTERED_SOURCE_FACT_SCHEMA
    )


def _provenance_packaged(item: ContextRecordOut) -> bool:
    fact_class = item.structured_value.get("fact_class") if item.structured_value else None
    return isinstance(fact_class, str) and _structural_record(item, fact_class)


def _safe_record_signature(items: Sequence[ContextRecordOut]) -> tuple[str, ...]:
    """Keep IDs transient while checking exact repeat ordering."""

    return tuple(item.id for item in items)


def _select_admitted_source(coordinator: CaptureCoordinator) -> Any:
    sources, _total = coordinator.list_sources()
    candidates: list[tuple[int, Any]] = []
    for source in sources:
        status = coordinator.status(source.id)
        checkpoint = status.get("checkpoint")
        generation = checkpoint.get("generation") if isinstance(checkpoint, dict) else 0
        if type(generation) is int and generation > 0:
            candidates.append((generation, source))
    if len(candidates) != 1:
        raise AssertionError("packet H-C could not identify the admitted capture source")
    return max(candidates, key=lambda item: item[0])[1]


def _bootstrap(
    engine: RetrievalEngine,
    *,
    query: str,
    budget: int = _BOOTSTRAP_BUDGET,
) -> tuple[list[ContextRecordOut], ContextPackMetadata | None, int]:
    response = engine.bootstrap(
        BootstrapRequest(
            task_description=query,
            requested_scopes=[_SCOPE],
            character_budget=budget,
        )
    )
    return response.items, response.pack_metadata, response.used_chars


def _negative_query_checks(engine: RetrievalEngine) -> dict[str, dict[str, object]]:
    # These are deliberately untrusted-looking query values.  None of them is
    # admitted as Core fact text, so the public result must remain empty.
    queries = {
        "path": "README.md",
        "source_text": "deterministic local fixtures",
        "secret_content": "AKIAIOSFODNN7EXAMPLE",
    }
    checks: dict[str, dict[str, object]] = {}
    for label in _NEGATIVE_QUERY_LABELS:
        response = engine.search(SearchRequest(query=queries[label], scopes=[_SCOPE], limit=10))
        checks[label] = {
            "total": response.total,
            "returned_count": len(response.items),
            "passed": response.total == 0 and not response.items,
        }
    return checks


def _run_disposable(
    root: Path,
    *,
    ownership: _DisposableRootCapability | None = None,
) -> dict[str, object]:
    """Run H-A admission plus bounded Retrieval V3 evidence in one vault."""

    disposable_root = _assert_disposable_root(root, ownership=ownership)
    admission = _run_admission_disposable(disposable_root, ownership=ownership)
    admission_state_ready = _packet_h_a_report_ready(admission)
    if not admission_state_ready:
        raise AssertionError("packet H-C requires a complete H-A admission state")

    store = CoreStore(disposable_root / "core.sqlite3")
    try:
        coordinator = CaptureCoordinator(
            store,
            sink=RegisteredSourceCaptureApplicationSink(store),
        )
        source = _select_admitted_source(coordinator)
        workspace = disposable_root / "workspace"
        adapter = LocalGitWorkspaceCaptureProviderAdapter((workspace,))
        coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
        engine = RetrievalEngine(store)

        aggregate = engine.search(SearchRequest(query="workspace item", scopes=[_SCOPE], limit=10))
        expected_sentences = set(REGISTERED_SOURCE_FACT_SENTENCES.values())
        aggregate_structural = [
            item
            for item in aggregate.items
            if item.content in expected_sentences and _provenance_packaged(item)
        ]
        aggregate_all_structural = len(aggregate.items) == len(aggregate_structural) == 4 and all(
            _provenance_packaged(item) for item in aggregate.items
        )
        fact_counts: dict[str, int] = {}
        fact_recall = aggregate_all_structural
        exact_items: dict[str, ContextRecordOut] = {}
        for fact_class, expected_count in _EXPECTED_FACT_COUNTS.items():
            sentence = REGISTERED_SOURCE_FACT_SENTENCES[fact_class]
            response = engine.search(SearchRequest(query=sentence, scopes=[_SCOPE], limit=10))
            matches = [item for item in response.items if _structural_record(item, fact_class)]
            fact_counts[fact_class] = len(matches)
            fact_recall = (
                fact_recall
                and len(matches) == expected_count
                and all(_provenance_packaged(item) for item in response.items)
            )
            for item in matches:
                exact_items[item.id] = item

        exact_get_matches = 0
        for item in exact_items.values():
            fetched = engine.get(item.id)
            if fetched is not None and fetched.model_dump(mode="json") == item.model_dump(
                mode="json"
            ):
                exact_get_matches += 1

        first_repeat = engine.search(
            SearchRequest(query="workspace item", scopes=[_SCOPE], limit=10)
        )
        second_repeat = engine.search(
            SearchRequest(query="workspace item", scopes=[_SCOPE], limit=10)
        )
        repeat_search_equal = first_repeat.total == second_repeat.total and _safe_record_signature(
            first_repeat.items
        ) == _safe_record_signature(second_repeat.items)

        bootstrap_items, bootstrap_metadata, bootstrap_used = _bootstrap(
            engine,
            query="workspace item",
        )
        bootstrap_repeat_items, bootstrap_repeat_metadata, bootstrap_repeat_used = _bootstrap(
            engine,
            query="workspace item",
        )
        bootstrap_repeat_equal = (
            _safe_record_signature(bootstrap_items)
            == _safe_record_signature(bootstrap_repeat_items)
            and bootstrap_used == bootstrap_repeat_used
            and (
                (bootstrap_metadata is None and bootstrap_repeat_metadata is None)
                or (
                    bootstrap_metadata is not None
                    and bootstrap_repeat_metadata is not None
                    and bootstrap_metadata.model_dump(mode="json")
                    == bootstrap_repeat_metadata.model_dump(mode="json")
                )
            )
        )
        bootstrap_budget_ok = (
            bootstrap_metadata is not None
            and bootstrap_used <= _BOOTSTRAP_BUDGET
            and bootstrap_metadata.budget_chars == _BOOTSTRAP_BUDGET
            and bootstrap_metadata.used_chars == bootstrap_used
            and bootstrap_metadata.selected_count == len(bootstrap_items)
            and bootstrap_metadata.omitted_count
            == bootstrap_metadata.candidate_count - bootstrap_metadata.selected_count
            and bootstrap_metadata.provenance_backed_count == len(bootstrap_items)
            and bool(bootstrap_items)
            and all(_provenance_packaged(item) for item in bootstrap_items)
            and all(_provenance_packaged(item) for item in bootstrap_repeat_items)
        )

        negative = _negative_query_checks(engine)

        python_sentence = REGISTERED_SOURCE_FACT_SENTENCES["python_source"]
        python_record = next(
            item for item in aggregate_structural if item.content == python_sentence
        )
        initial_page = adapter.fetch_page(source, None, 0)
        python_events = [
            event
            for event in initial_page.events
            if event.operation == "upsert"
            and event.payload.get("kind") == "text"
            and isinstance(event.payload.get("relative_path"), str)
            and PurePosixPath(str(event.payload["relative_path"])).suffix.casefold()
            in {".py", ".pyw"}
        ]
        if len(python_events) != 1:
            raise AssertionError("packet H-C expected one unique Python structural item")
        relative_path = str(python_events[0].payload["relative_path"])
        posix_target = PurePosixPath(relative_path)
        relative_target = Path(relative_path)
        if (
            relative_target.is_absolute()
            or posix_target.is_absolute()
            or ".." in relative_target.parts
            or ".." in posix_target.parts
        ):
            raise AssertionError("target event escaped the disposable workspace")
        target_path = workspace.joinpath(*posix_target.parts)
        target_path.unlink()
        deletion = coordinator.run(source.id)

        after_delete_search = engine.search(
            SearchRequest(query="python source", scopes=[_SCOPE], limit=10)
        )
        after_delete_get = engine.get(python_record.id)
        after_delete_bootstrap, _after_delete_metadata, _after_delete_used = _bootstrap(
            engine,
            query="python source",
        )
        withdrawal_excluded = (
            deletion.status == "completed"
            and deletion.applied_events == 1
            and after_delete_search.total == 0
            and not after_delete_search.items
            and after_delete_get is None
            and all(_provenance_packaged(item) for item in after_delete_bootstrap)
            and all(item.content != python_sentence for item in after_delete_bootstrap)
        )

        provenance_count = sum(_provenance_packaged(item) for item in aggregate_structural)
        acceptance = {
            "structural_fact_recall": fact_recall and aggregate_all_structural,
            "provenance_packaging": provenance_count == 4 and len(exact_items) == 4,
            "exact_get_consistency": exact_get_matches == len(exact_items) == 4,
            "bootstrap_budget_compliance": bootstrap_budget_ok,
            "repeat_determinism": repeat_search_equal and bootstrap_repeat_equal,
            "negative_query_exclusion": all(bool(check["passed"]) for check in negative.values()),
            "withdrawal_exclusion": withdrawal_excluded,
        }
        scorecard: dict[str, object] = {
            "structural_fact_recall": {
                "expected_count": 4,
                "retrieved_count": len(aggregate_structural),
                "fact_class_counts": dict(sorted(fact_counts.items())),
                "passed": acceptance["structural_fact_recall"],
            },
            "provenance": {
                "checked_count": len(aggregate_structural),
                "packaged_count": provenance_count,
                "bootstrap_selected_count": len(bootstrap_items),
                "bootstrap_provenance_backed_count": (
                    bootstrap_metadata.provenance_backed_count if bootstrap_metadata else 0
                ),
                "passed": acceptance["provenance_packaging"],
            },
            "exact_get": {
                "checked_count": len(exact_items),
                "matched_count": exact_get_matches,
                "passed": acceptance["exact_get_consistency"],
            },
            "bootstrap": {
                "budget_chars": _BOOTSTRAP_BUDGET,
                "used_chars": bootstrap_used,
                "candidate_count": bootstrap_metadata.candidate_count if bootstrap_metadata else 0,
                "selected_count": len(bootstrap_items),
                "omitted_count": bootstrap_metadata.omitted_count if bootstrap_metadata else 0,
                "truncation_reason_families": (
                    sorted(bootstrap_metadata.truncation_reasons) if bootstrap_metadata else []
                ),
                "passed": acceptance["bootstrap_budget_compliance"],
            },
            "repeat": {
                "search_equal": repeat_search_equal,
                "bootstrap_equal": bootstrap_repeat_equal,
                "passed": acceptance["repeat_determinism"],
            },
            "negative_queries": negative,
            "withdrawal": {
                "adapter_delete_completed": deletion.status == "completed",
                "adapter_delete_applied_events": deletion.applied_events,
                "post_delete_search_total": after_delete_search.total,
                "post_delete_get_is_none": after_delete_get is None,
                "post_delete_bootstrap_excludes": all(
                    item.content != python_sentence for item in after_delete_bootstrap
                ),
                "reason_families": ["adapter_delete", "deleted_record_excluded"],
                "passed": acceptance["withdrawal_exclusion"],
            },
        }
        report: dict[str, object] = {
            "schema_version": 1,
            "boundary": "packet-h-c-retrieval-v3",
            "evidence_scope": "disposable-local-evidence-only",
            "admission_state_ready": admission_state_ready,
            "acceptance": acceptance,
            "scorecard": scorecard,
            "aggregate_receipt": {
                "receipt_type": "packet-h-c-aggregate",
                "status": "pass" if all(acceptance.values()) else "fail",
            },
        }
        receipt = cast(dict[str, object], report["aggregate_receipt"])
        digest_material = dict(report)
        digest_material.pop("aggregate_receipt", None)
        receipt["stable_digest"] = _stable_digest(digest_material)
        _assert_public_report_safe(report)
        return report
    finally:
        _close_core_stores(store)


def _identifier_safe_report(report: Mapping[str, object]) -> bool:
    def safe(value: object) -> bool:
        if value is None or type(value) in {bool, int}:
            return True
        if type(value) is str:
            return _IDENTIFIER.fullmatch(value) is not None
        if type(value) is dict:
            return all(
                type(key) is str and _IDENTIFIER.fullmatch(key) is not None and safe(nested)
                for key, nested in value.items()
            )
        if type(value) is list:
            return all(safe(nested) for nested in value)
        return False

    return safe(report)


def _assert_public_report_safe(report: Mapping[str, object]) -> None:
    if not _identifier_safe_report(report):
        raise AssertionError("packet H-C report is not identifier-safe")
    rendered = json.dumps(report, sort_keys=True)
    if any(value in rendered for value in _FORBIDDEN_RAW_MATERIAL):
        raise AssertionError("packet H-C report contains unbounded fixture material")
    receipt = report.get("aggregate_receipt")
    if not isinstance(receipt, dict) or not re.fullmatch(
        r"[0-9a-f]{64}", str(receipt.get("stable_digest"))
    ):
        raise AssertionError("packet H-C report does not contain a stable digest")


def run() -> dict[str, object]:
    """Execute H-C entirely inside a temporary, runner-owned Core vault."""

    with _runner_owned_temporary_root(_DISPOSABLE_PREFIX) as (root, ownership):
        report = _run_disposable(root, ownership=ownership)
    if root.exists():
        raise RuntimeError("packet_h_c_temporary_state_not_removed")
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

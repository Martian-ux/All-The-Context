"""First executable ATC Memory Lab slice.

This module supplies the current ATC retrieval adapter, deterministic fixture
loading, and a small CLI. It installs no external memory system and performs no
network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from allthecontext.memory_lab import (
    AdapterManifest,
    BenchmarkMetadata,
    MemoryObject,
    NoMemoryBaseline,
    PreparationReceipt,
    RankedMemory,
    RetrievalReceipt,
    RetrievalTask,
    run_memory_lab,
)
from allthecontext.memory_lab_baselines import (
    BoundedLocalFileSearchBaseline,
    FixedBudgetHistoryBaseline,
    RawAppendLogSearchBaseline,
    StableObservationLogBaseline,
    StaticProfileBaseline,
)
from allthecontext.models import SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

FIXTURES = Path(__file__).with_name("memory_lab_fixtures.json")
LADDER_CONFIG = Path(__file__).with_name("memory_lab_baseline_ladder_config.json")
LADDER_ORDER = (
    "no-memory",
    "fixed-budget-long-history",
    "static-profile",
    "raw-append-log-search",
    "stable-observation-current-state",
    "bounded-local-file-search",
    "atc-retrieval-v3",
)


@dataclass(frozen=True, slots=True)
class BaselineLadderConfig:
    """Frozen config-owned controls that contain no task answer labels."""

    context_budget_chars: int
    static_profile_object_ids: tuple[str, ...]
    file_search_max_files: int
    file_search_max_bytes: int

    def __post_init__(self) -> None:
        if (
            min(
                self.context_budget_chars,
                self.file_search_max_files,
                self.file_search_max_bytes,
            )
            < 1
        ):
            raise ValueError("baseline ladder limits must be positive")
        if not self.static_profile_object_ids:
            raise ValueError("the static profile must contain at least one object")


def _load_object(value: Any) -> MemoryObject:
    if not isinstance(value, dict):
        raise ValueError("memory objects must be JSON objects")
    return MemoryObject(
        object_id=str(value["object_id"]),
        kind=str(value["kind"]),
        content=str(value["content"]),
        scopes=tuple(str(item) for item in value.get("scopes", ())),
        tags=tuple(str(item) for item in value.get("tags", ())),
        valid_from=(str(value["valid_from"]) if value.get("valid_from") is not None else None),
        expires_at=(str(value["expires_at"]) if value.get("expires_at") is not None else None),
        supersedes=(str(value["supersedes"]) if value.get("supersedes") is not None else None),
        explicit_user_statement=bool(value.get("explicit_user_statement", False)),
        schema=str(value.get("schema", "atc.memory-object.v1")),
    )


def _load_task(value: Any, *, context_budget_chars: int | None) -> RetrievalTask:
    if not isinstance(value, dict):
        raise ValueError("retrieval tasks must be JSON objects")
    raw_groups = value.get("evidence_groups", ())
    if not isinstance(raw_groups, list):
        raise ValueError("evidence_groups must be a list")
    raw_context_budget = value.get("context_budget_chars", context_budget_chars)
    return RetrievalTask(
        task_id=str(value["task_id"]),
        query=str(value["query"]),
        evaluated_at=str(value["evaluated_at"]),
        limit=int(value["limit"]),
        evidence_groups=tuple(
            frozenset(str(object_id) for object_id in group) for group in raw_groups
        ),
        forbidden_ids=frozenset(str(item) for item in value.get("forbidden_ids", ())),
        scopes=tuple(str(item) for item in value.get("scopes", ())),
        current_project=(
            str(value["current_project"]) if value.get("current_project") is not None else None
        ),
        context_budget_chars=(int(raw_context_budget) if raw_context_budget is not None else None),
    )


def _load_fixture_document(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema") != "atc.memory-lab.fixture.v1":
        raise ValueError("unsupported Memory Lab fixture schema")
    return loaded


def _load_baseline_config(path: Path = LADDER_CONFIG) -> BaselineLadderConfig:
    raw_config: Any = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw_config, dict)
        or raw_config.get("schema") != "atc.memory-lab.baseline-ladder-config.v1"
    ):
        raise ValueError("unsupported baseline ladder configuration schema")
    raw_profile_ids = raw_config.get("static_profile_object_ids")
    if not isinstance(raw_profile_ids, list):
        raise ValueError("static_profile_object_ids must be a list")
    return BaselineLadderConfig(
        context_budget_chars=int(raw_config["context_budget_chars"]),
        static_profile_object_ids=tuple(str(item) for item in raw_profile_ids),
        file_search_max_files=int(raw_config["file_search_max_files"]),
        file_search_max_bytes=int(raw_config["file_search_max_bytes"]),
    )


def load_fixture_bundle(
    path: Path = FIXTURES,
    config_path: Path = LADDER_CONFIG,
) -> tuple[
    tuple[MemoryObject, ...],
    tuple[RetrievalTask, ...],
    BaselineLadderConfig,
]:
    """Load the deterministic objects, tasks, and frozen baseline configuration."""

    loaded = _load_fixture_document(path)
    config = _load_baseline_config(config_path)
    objects, tasks = _load_fixture_records(
        loaded,
        context_budget_chars=config.context_budget_chars,
    )
    object_ids = {item.object_id for item in objects}
    if not set(config.static_profile_object_ids) <= object_ids:
        raise ValueError("static profile references objects outside the fixture")
    return objects, tasks, config


def _load_fixture_records(
    loaded: dict[str, Any],
    *,
    context_budget_chars: int | None,
) -> tuple[tuple[MemoryObject, ...], tuple[RetrievalTask, ...]]:
    raw_objects = loaded.get("objects")
    raw_tasks = loaded.get("tasks")
    if not isinstance(raw_objects, list) or not isinstance(raw_tasks, list):
        raise ValueError("fixture objects and tasks must be lists")
    objects = tuple(_load_object(item) for item in raw_objects)
    return (
        objects,
        tuple(_load_task(item, context_budget_chars=context_budget_chars) for item in raw_tasks),
    )


def load_fixture(
    path: Path = FIXTURES,
) -> tuple[tuple[MemoryObject, ...], tuple[RetrievalTask, ...]]:
    """Load and validate the deterministic, sanitized M0 fixture."""

    return _load_fixture_records(
        _load_fixture_document(path),
        context_budget_chars=None,
    )


class AtcRetrievalAdapter:
    """Read-only lab adapter over the current production RetrievalEngine."""

    manifest = AdapterManifest(
        adapter_id="atc-retrieval-v3",
        name="ATC Retrieval V3",
        version="current-worktree",
    )

    def __init__(self, work_dir: Path, *, context_budget_chars: int) -> None:
        self._work_dir = work_dir
        self._context_budget_chars = context_budget_chars
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._database_path: Path | None = None
        self._store: CoreStore | None = None
        self._engine: RetrievalEngine | None = None
        self._object_count = 0
        self._principal = ClientPrincipal(
            "memory-lab-reader",
            "Synthetic Memory Lab reader",
            frozenset({"context:read"}),
        )
        self.benchmark_metadata = BenchmarkMetadata(
            source_representation="isolated_synthetic_core_snapshot",
            selection_mode="production_retrieval_v3_then_fixed_character_budget",
            context_budget_chars=context_budget_chars,
            validity_limitations=(
                "direct_synthetic_table_load",
                "retrieval_only_no_reader",
                "no_operator_core_connection",
            ),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        if self._store is not None:
            raise RuntimeError("ATC adapter may only be prepared once")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="atc-memory-core-",
            dir=self._work_dir,
        )
        self._database_path = Path(self._temporary.name) / "atc-memory-lab.sqlite3"
        store = CoreStore(self._database_path)
        store.migrate()
        vault_id = store.initialize_vault("Synthetic ATC Memory Lab", "UTC")
        insert_sql = (
            "INSERT INTO context_records("
            "id,vault_id,kind,content,scopes_json,tags_json,allowed_clients_json,"
            "denied_clients_json,valid_from,expires_at,supersedes,content_hash,created_at,"
            "updated_at,deleted_at,confidence,sensitivity,availability,approval_status,version,"
            "schema_version,explicit_user_statement) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "'normal','core_available','approved',1,1,?)"
        )
        with store.transaction() as connection:
            for ordinal, item in enumerate(objects):
                timestamp = f"2026-01-01T00:00:{ordinal:02d}+00:00"
                values = (
                    item.object_id,
                    vault_id,
                    item.kind,
                    item.content,
                    json.dumps(item.scopes, separators=(",", ":")),
                    json.dumps(item.tags, separators=(",", ":")),
                    "[]",
                    "[]",
                    item.valid_from,
                    item.expires_at,
                    item.supersedes,
                    hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                    timestamp,
                    timestamp,
                    None,
                    1.0,
                    int(item.explicit_user_statement),
                )
                connection.execute(insert_sql, values)
                connection.execute(
                    "INSERT INTO context_fts(record_id,content,kind,tags,scopes) VALUES(?,?,?,?,?)",
                    (
                        item.object_id,
                        item.content,
                        item.kind,
                        " ".join(item.tags),
                        " ".join(item.scopes),
                    ),
                )
        self._store = store
        self._engine = RetrievalEngine(store)
        self._object_count = len(objects)
        storage_bytes = sum(
            path.stat().st_size
            for path in self._database_path.parent.glob(f"{self._database_path.name}*")
            if path.is_file()
        )
        return PreparationReceipt(storage_bytes=storage_bytes)

    def retrieve(self, task: RetrievalTask) -> RetrievalReceipt:
        if self._engine is None:
            raise RuntimeError("ATC adapter must be prepared before retrieval")
        response = self._engine.search(
            SearchRequest(
                query=task.query,
                scopes=list(task.scopes),
                as_of=task.evaluated_at,
                current_project=task.current_project,
                limit=max(task.limit, self._object_count),
            ),
            self._principal,
        )
        context_budget_chars = task.context_budget_chars or self._context_budget_chars
        selected: list[RankedMemory] = []
        disclosed_chars = 0
        for item in response.items:
            if disclosed_chars + len(item.content) > context_budget_chars:
                continue
            selected.append(RankedMemory(item.id))
            disclosed_chars += len(item.content)
            if len(selected) == task.limit:
                break
        items = tuple(selected)
        return RetrievalReceipt(items=items, abstained=not items)

    def close(self) -> None:
        self._engine = None
        self._store = None
        self._object_count = 0
        self._database_path = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def _assess_ladder(report: dict[str, Any]) -> dict[str, Any]:
    """Apply one frozen, identifier-safe evidence rule to the ordered conditions."""

    assessments: dict[str, Any] = {}
    strongest_eligible = LADDER_ORDER[0]
    for rung_index, adapter_id in enumerate(LADDER_ORDER):
        result = report["adapters"][adapter_id]
        metrics = result["metrics"]
        hard_gate_failures: list[str] = []
        if metrics["forbidden_output_count"]:
            hard_gate_failures.append("forbidden_output")
        if metrics["contract_violation_count"]:
            hard_gate_failures.append("adapter_contract_violation")
        if metrics["budget_violation_count"]:
            hard_gate_failures.append("context_budget_exceeded")
        if metrics["deterministic_task_rate"] != 1.0:
            hard_gate_failures.append("nondeterministic_output")
        if rung_index == 0:
            decision = "retain_control"
            reason_codes = ["negative_control"]
            comparator = None
        elif hard_gate_failures:
            decision = "not_earned_on_this_fixture"
            reason_codes = ["hard_gate_failure", *hard_gate_failures]
            comparator = strongest_eligible
        else:
            comparator = strongest_eligible
            comparator_metrics = report["adapters"][comparator]["metrics"]
            if metrics["task_success_rate"] > comparator_metrics["task_success_rate"]:
                decision = "advance_to_next_fixture"
                reason_codes = ["improves_task_success_rate"]
            elif (
                metrics["task_success_rate"] == comparator_metrics["task_success_rate"]
                and metrics["mean_evidence_group_recall"]
                > comparator_metrics["mean_evidence_group_recall"]
            ):
                decision = "advance_to_next_fixture"
                reason_codes = ["improves_evidence_group_recall_at_success_parity"]
            elif (
                metrics["task_success_rate"] == comparator_metrics["task_success_rate"]
                and metrics["mean_evidence_group_recall"]
                == comparator_metrics["mean_evidence_group_recall"]
                and metrics["mean_irrelevant_disclosure_chars"]
                < comparator_metrics["mean_irrelevant_disclosure_chars"]
            ):
                decision = "advance_to_next_fixture"
                reason_codes = ["reduces_irrelevant_disclosure_at_quality_parity"]
            else:
                decision = "not_earned_on_this_fixture"
                reason_codes = ["no_incremental_gain_over_strongest_lower_rung"]
            if decision == "advance_to_next_fixture":
                strongest_eligible = adapter_id
        assessments[adapter_id] = {
            "rung_index": rung_index,
            "decision": decision,
            "comparator_adapter_id": comparator,
            "reason_codes": reason_codes,
        }
    return {
        "policy": {
            "hard_gates": [
                "zero_forbidden_output",
                "zero_adapter_contract_violation",
                "zero_context_budget_violation",
                "full_repeat_determinism",
            ],
            "incremental_criteria_order": [
                "task_success_rate",
                "mean_evidence_group_recall",
                "mean_irrelevant_disclosure_chars",
            ],
            "scope": "fixture_bounded_evidence_not_implementation_acceptance",
            "required_next_tests": [
                "current_state_mutation",
                "poisoning_and_harmful_memory",
                "scale_and_budget",
                "action_grounding",
                "caos",
            ],
        },
        "rungs": assessments,
    }


def run_fixture(work_dir: Path, *, repeats: int = 3) -> dict[str, Any]:
    """Run every simple-baseline rung and current ATC on one frozen fixture."""

    objects, tasks, config = load_fixture_bundle()
    report = run_memory_lab(
        objects,
        tasks,
        (
            NoMemoryBaseline(),
            FixedBudgetHistoryBaseline(config.context_budget_chars),
            StaticProfileBaseline(
                config.static_profile_object_ids,
                config.context_budget_chars,
            ),
            RawAppendLogSearchBaseline(config.context_budget_chars),
            StableObservationLogBaseline(config.context_budget_chars),
            BoundedLocalFileSearchBaseline(
                work_dir,
                context_budget_chars=config.context_budget_chars,
                max_files_scanned=config.file_search_max_files,
                max_bytes_scanned=config.file_search_max_bytes,
            ),
            AtcRetrievalAdapter(
                work_dir,
                context_budget_chars=config.context_budget_chars,
            ),
        ),
        fixture_sha256=hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        repeats=repeats,
    )
    report["baseline_config_sha256"] = hashlib.sha256(LADDER_CONFIG.read_bytes()).hexdigest()
    report["baseline_ladder"] = _assess_ladder(report)
    return report


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render an identifier-safe compact evidence report."""

    lines = [
        "# ATC Memory Lab Wave 2 baseline ladder",
        "",
        (
            f"Fixture `{report['fixture_sha256']}`; {report['object_count']} objects; "
            f"{report['task_count']} tasks; {report['repeats']} repeats."
        ),
        f"Baseline config `{report['baseline_config_sha256']}`.",
        "",
        "| Rung | Success | Recall | Precision | Forbidden | Failures | p50 ms | p95 ms | "
        "Storage B | Cost USD | Evidence disposition |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for adapter_id in LADDER_ORDER:
        result = report["adapters"][adapter_id]
        metrics = result["metrics"]
        latency = metrics["retrieval_latency"]
        assessment = report["baseline_ladder"]["rungs"][adapter_id]
        lines.append(
            f"| `{adapter_id}` | {metrics['task_success_rate']:.3f} | "
            f"{metrics['mean_evidence_group_recall']:.3f} | "
            f"{metrics['mean_precision']:.3f} | "
            f"{metrics['forbidden_output_count']} | {len(result['failure_cases'])} | "
            f"{latency['p50_ms']:.6f} | {latency['p95_ms']:.6f} | "
            f"{result['preparation']['storage_bytes']} | "
            f"{metrics['usage']['monetary_cost_usd']:.6f} | "
            f"`{assessment['decision']}` |"
        )
    lines.extend(["", "## Failure cases", ""])
    for adapter_id in LADDER_ORDER:
        cases = report["adapters"][adapter_id]["failure_cases"]
        if not cases:
            lines.append(f"- `{adapter_id}`: none.")
            continue
        rendered_cases = "; ".join(
            f"task-index-{case['task_index']} ({', '.join(case['reason_codes'])})" for case in cases
        )
        lines.append(f"- `{adapter_id}`: {rendered_cases}.")
    lines.extend(["", "## Validity limitations", ""])
    lines.extend(f"- `{item}`" for item in report["validity_limitations"])
    lines.extend(
        [
            "- The bounded local file-search rung is an infrastructure/control baseline, "
            "not a reproduction of programmatic action-model log search.",
            "- The stable observation condition may be aligned to this small fixture. It "
            "must pass mutation, poisoning, scale, action-grounding, and CAOS tests.",
            "- Evidence dispositions apply only to this retrieval-stage fixture; they are "
            "not implementation acceptance or production-promotion decisions.",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Optional directory for the isolated synthetic ATC database.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.repeats < 1:
        raise ValueError("--repeats must be positive")
    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        report = run_fixture(args.work_dir, repeats=args.repeats)
    else:
        with tempfile.TemporaryDirectory(prefix="atc-memory-lab-") as temporary:
            report = run_fixture(Path(temporary), repeats=args.repeats)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8", newline="\n")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_markdown_report(report),
            encoding="utf-8",
            newline="\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

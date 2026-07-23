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
from pathlib import Path
from typing import Any

from allthecontext.memory_lab import (
    AdapterManifest,
    DeterministicLexicalBaseline,
    MemoryObject,
    NoMemoryBaseline,
    PreparationReceipt,
    RankedMemory,
    RetrievalReceipt,
    RetrievalTask,
    run_memory_lab,
)
from allthecontext.models import SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

FIXTURES = Path(__file__).with_name("memory_lab_fixtures.json")


def _load_object(value: Any) -> MemoryObject:
    if not isinstance(value, dict):
        raise ValueError("memory objects must be JSON objects")
    return MemoryObject(
        object_id=str(value["object_id"]),
        kind=str(value["kind"]),
        content=str(value["content"]),
        scopes=tuple(str(item) for item in value.get("scopes", ())),
        tags=tuple(str(item) for item in value.get("tags", ())),
        valid_from=(
            str(value["valid_from"]) if value.get("valid_from") is not None else None
        ),
        expires_at=(
            str(value["expires_at"]) if value.get("expires_at") is not None else None
        ),
        supersedes=(
            str(value["supersedes"]) if value.get("supersedes") is not None else None
        ),
        explicit_user_statement=bool(value.get("explicit_user_statement", False)),
        schema=str(value.get("schema", "atc.memory-object.v1")),
    )


def _load_task(value: Any) -> RetrievalTask:
    if not isinstance(value, dict):
        raise ValueError("retrieval tasks must be JSON objects")
    raw_groups = value.get("evidence_groups", ())
    if not isinstance(raw_groups, list):
        raise ValueError("evidence_groups must be a list")
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
            str(value["current_project"])
            if value.get("current_project") is not None
            else None
        ),
    )


def load_fixture(
    path: Path = FIXTURES,
) -> tuple[tuple[MemoryObject, ...], tuple[RetrievalTask, ...]]:
    """Load and validate the deterministic, sanitized M0 fixture."""

    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema") != "atc.memory-lab.fixture.v1":
        raise ValueError("unsupported Memory Lab fixture schema")
    raw_objects = loaded.get("objects")
    raw_tasks = loaded.get("tasks")
    if not isinstance(raw_objects, list) or not isinstance(raw_tasks, list):
        raise ValueError("fixture objects and tasks must be lists")
    return (
        tuple(_load_object(item) for item in raw_objects),
        tuple(_load_task(item) for item in raw_tasks),
    )


class AtcRetrievalAdapter:
    """Read-only lab adapter over the current production RetrievalEngine."""

    manifest = AdapterManifest(
        adapter_id="atc-retrieval-v3",
        name="ATC Retrieval V3",
        version="current-worktree",
    )

    def __init__(self, work_dir: Path) -> None:
        self._database_path = work_dir / "atc-memory-lab.sqlite3"
        self._store: CoreStore | None = None
        self._engine: RetrievalEngine | None = None
        self._principal = ClientPrincipal(
            "memory-lab-reader",
            "Synthetic Memory Lab reader",
            frozenset({"context:read"}),
        )

    def prepare(self, objects: Sequence[MemoryObject]) -> PreparationReceipt:
        if self._store is not None:
            raise RuntimeError("ATC adapter may only be prepared once")
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
                    "INSERT INTO context_fts(record_id,content,kind,tags,scopes) "
                    "VALUES(?,?,?,?,?)",
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
                limit=task.limit,
            ),
            self._principal,
        )
        items = tuple(RankedMemory(item.id) for item in response.items)
        return RetrievalReceipt(items=items, abstained=not items)

    def close(self) -> None:
        self._engine = None
        self._store = None


def run_fixture(work_dir: Path, *, repeats: int = 3) -> dict[str, Any]:
    """Compare the simple baseline and current ATC on the same fixture."""

    objects, tasks = load_fixture()
    return run_memory_lab(
        objects,
        tasks,
        (
            NoMemoryBaseline(),
            DeterministicLexicalBaseline(),
            AtcRetrievalAdapter(work_dir),
        ),
        fixture_sha256=hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        repeats=repeats,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
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
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

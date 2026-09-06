"""Contracts for the required sequential and parallel pytest coverage."""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_collection_script() -> ModuleType:
    path = ROOT / "scripts" / "check_test_collection.py"
    spec = importlib.util.spec_from_file_location("check_test_collection", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collection_parser_accepts_windows_nodeids_and_ignores_summary() -> None:
    checker = _load_collection_script()

    parsed = checker.parse_collection(
        "tests\\unit\\test_example.py::test_one\n"
        "tests/unit/test_example.py::test_two[param\\value]\n"
        "2 passed in 0.01s\n"
    )

    assert parsed == Counter(
        {
            "tests/unit/test_example.py::test_one": 1,
            "tests/unit/test_example.py::test_two[param/value]": 1,
        }
    )


def test_collection_commands_share_the_same_required_pytest_target() -> None:
    checker = _load_collection_script()

    sequential = checker.collection_command(workers=None)
    parallel = checker.collection_command(workers=4)

    assert sequential[:5] == parallel[:5]
    assert sequential[3:] == ["--collect-only", "-q"]
    assert parallel[3:] == ["--collect-only", "-q", "-n", "4", "--dist=loadfile"]


def test_shards_normalize_windows_paths_and_reject_escaping_targets() -> None:
    checker = _load_collection_script()

    assert checker.normalize_test_file(r".\tests\unit\test_example.py") == (
        "tests/unit/test_example.py"
    )
    for invalid in (
        "../tests/unit/test_example.py",
        "tests/../unit/test_example.py",
        "tests/unit/not_a_test.txt",
        "C:/tests/unit/test_example.py",
        "tests//unit/test_example.py",
    ):
        with pytest.raises(ValueError):
            checker.normalize_test_file(invalid)


def test_shards_assign_new_files_once_and_deterministically() -> None:
    checker = _load_collection_script()
    files = [
        r"tests\unit\test_windows_update_helper.py",
        "tests/unit/test_memory_reliability_spec.py",
        "tests/unit/test_new_unmeasured_feature.py",
        "tests/integration/test_core_api.py",
    ]

    first = checker.assign_test_files(files, shard_count=2)
    second = checker.assign_test_files(list(reversed(files)), shard_count=2)
    flattened = [path for shard in first for path in shard]

    assert first == second
    assert Counter(flattened) == Counter(
        {
            "tests/unit/test_windows_update_helper.py": 1,
            "tests/unit/test_memory_reliability_spec.py": 1,
            "tests/unit/test_new_unmeasured_feature.py": 1,
            "tests/integration/test_core_api.py": 1,
        }
    )
    assert all(first)
    with pytest.raises(ValueError, match="duplicate normalized paths"):
        checker.assign_test_files(
            ["tests/unit/test_case.py", "tests/unit/Test_Case.py"], shard_count=2
        )


def test_collection_receipt_is_order_independent_and_count_sensitive() -> None:
    checker = _load_collection_script()
    first = Counter(
        {
            "tests/unit/test_a.py::test_one": 1,
            "tests/unit/test_b.py::test_two": 1,
        }
    )
    reversed_order = Counter(
        {
            "tests/unit/test_b.py::test_two": 1,
            "tests/unit/test_a.py::test_one": 1,
        }
    )
    duplicated = first.copy()
    duplicated["tests/unit/test_a.py::test_one"] += 1

    assert checker.collection_digest(first) == checker.collection_digest(reversed_order)
    assert checker.collection_digest(first) != checker.collection_digest(duplicated)


def test_shard_union_rejects_missing_duplicate_and_empty_results() -> None:
    checker = _load_collection_script()
    complete = Counter(
        {
            "tests/unit/test_a.py::test_one": 1,
            "tests/unit/test_b.py::test_two": 1,
        }
    )

    checker.verify_complete_disjoint_union(
        complete,
        (
            Counter({"tests/unit/test_a.py::test_one": 1}),
            Counter({"tests/unit/test_b.py::test_two": 1}),
        ),
    )
    with pytest.raises(ValueError, match="differs"):
        checker.verify_complete_disjoint_union(
            complete,
            (Counter({"tests/unit/test_a.py::test_one": 1}),),
        )
    with pytest.raises(ValueError, match="overlap"):
        checker.verify_complete_disjoint_union(
            complete,
            (
                complete.copy(),
                Counter({"tests/unit/test_a.py::test_one": 1}),
            ),
        )
    with pytest.raises(ValueError, match="missing or empty"):
        checker.verify_complete_disjoint_union(complete, (Counter(), complete.copy()))


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", ""])
def test_required_windows_check_rejects_unsuccessful_or_missing_shards(result: str) -> None:
    checker = _load_collection_script()

    assert checker.require_successful_shards(result) == 1
    assert checker.require_successful_shards("success") == 0


def test_ci_runs_complete_linux_and_two_sharded_windows_suites() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python scripts/check_test_collection.py --workers 4" in workflow
    assert "python -m pytest -n 4 --dist=loadfile" in workflow
    assert "shard: [0, 1]" in workflow
    assert "--shard-count 2" in workflow
    assert "--shard-index ${{ matrix.shard }}" in workflow
    assert "--write-targets .pytest-shard-targets.txt" in workflow
    assert "needs: python-windows-shard" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "fail-fast: false" in workflow
    assert "WINDOWS_SHARD_RESULT: ${{ needs.python-windows-shard.result }}" in workflow
    assert '--require-shard-result "$WINDOWS_SHARD_RESULT"' in workflow
    assert workflow.count("name: Python 3.12 - windows-latest\n") == 1
    assert "--ignore" not in workflow
    assert "--deselect" not in workflow
    assert "-k " not in workflow

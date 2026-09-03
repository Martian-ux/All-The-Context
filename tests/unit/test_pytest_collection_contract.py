"""Contracts for the required sequential and parallel pytest coverage."""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType

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


def test_ci_runs_collection_proof_and_fixed_file_level_parallelism() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python scripts/check_test_collection.py --workers 4" in workflow
    assert "python -m pytest -n 4 --dist=loadfile" in workflow
    assert "--ignore" not in workflow
    assert "--deselect" not in workflow
    assert "-k " not in workflow

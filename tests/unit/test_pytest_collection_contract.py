"""Contracts for the required sequential and parallel pytest coverage."""

from __future__ import annotations

import importlib.util
import math
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
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
            "tests/unit/test_example.py::test_two[param\\value]": 1,
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


def test_exact_case_schedule_defaults_to_files_and_admits_new_nodes() -> None:
    checker = _load_collection_script()
    updater = "tests/unit/test_windows_update_helper.py::"
    packet = "tests/unit/test_memory_reliability_spec.py::"
    audited = updater + "test_adversarial_child_failure_report_values_reach_safe_rollback"
    block = packet + checker.PACKET_A_BLOCK_FUNCTION
    nodes = [
        "tests/unit/test_retrieval_m3_current_candidate.py::test_first",
        "tests/unit/test_retrieval_m3_current_candidate.py::test_second",
        "tests/unit/test_new_module.py::test_new",
        "tests/unit/test_new_module.py::test_literal[account@mail.test]",
        updater + "test_unreviewed[new-case]",
        updater + "test_unreviewed[another-case]",
        packet + "test_unreviewed[parameter]",
        block,  # The current unparameterized workload stays with its module.
        *(f"{block}[block-{index:02d}-of-08]" for index in range(1, 9)),
        *(f"{audited}[case-{index}]" for index in range(4)),
    ]
    complete = Counter(nodes)
    groups = checker.test_case_groups(nodes)
    assert groups[nodes[0]] == groups[nodes[1]]
    assert groups[nodes[2]] == groups[nodes[3]]
    assert groups[nodes[4]] != groups[nodes[5]]
    assert groups[nodes[6]] == groups[block]
    assert len({groups[f"{block}[block-{index:02d}-of-08]"] for index in range(1, 9)}) == 8
    assert len({groups[f"{audited}[case-{index}]"] for index in range(4)}) == 4
    assert groups == checker.test_case_groups(list(reversed(nodes)))
    assert checker.HISTORICAL_TEST_FILE_SECONDS[updater.removesuffix("::")] == 236.066
    assert checker.xdist_collection(complete) == complete - Counter(nodes[:2])
    shards = checker.assign_test_cases(complete, shard_count=9)
    assert shards == checker.assign_test_cases(Counter(reversed(nodes)), shard_count=9)
    checker.verify_complete_disjoint_union(complete, shards)
    group_shards = {}
    for index, shard in enumerate(shards):
        for node in shard:
            assert group_shards.setdefault(groups[node], index) == index
    assert len({group_shards[groups[f"{audited}[case-{index}]"]] for index in range(4)}) > 1
    assert (
        len({group_shards[groups[f"{block}[block-{index:02d}-of-08]"]] for index in range(1, 9)})
        > 1
    )
    with pytest.raises(ValueError, match="duplicate"):
        checker.assign_test_cases(Counter({nodes[0]: 2}), shard_count=1)


def test_windows_tail_preserves_placement_and_parameter_affinity() -> None:
    checker = _load_collection_script()
    witness_file = "tests/unit/test_windows_ci_witness.py"
    assert checker.WINDOWS_FILE_SECONDS[witness_file] == 88.20
    witness_nodes = [f"{witness_file}::test_case[{case}]" for case in ("first", "second")]
    weighted_collection = Counter(
        witness_nodes + [f"tests/unit/test_weight_filler_{i}.py::test_case" for i in range(30)]
    )
    for platform in ("windows", "linux"):
        groups = checker.test_case_groups(witness_nodes, platform=platform)
        assert len(set(groups.values())) == 1
        shards = checker.assign_test_cases(
            weighted_collection, shard_count=checker.CI_SHARD_COUNTS[platform], platform=platform
        )
        checker.verify_complete_disjoint_union(weighted_collection, shards)
        assert any(set(witness_nodes) <= set(shard) for shard in shards)
    split_files = (
        "tests/integration/test_agent_continuity_harness.py",
        "tests/integration/test_project_first_read.py",
        "tests/unit/test_capture_runtime.py",
        "tests/unit/test_memory_truth.py",
        "tests/unit/test_provider_ingestion.py",
    )
    families = [
        [f"{path}::test_family[allow]", f"{path}::test_family[deny]", f"{path}::test_other"]
        for path in split_files
    ]
    continuity = (
        "tests/integration/test_project_memory_continuity.py"
        "::test_project_decision_correction_and_forget_survive_core_recreation"
    )
    allow, deny = (f"{continuity}[{acl}]" for acl in ("allow", "deny"))
    ordinary = [f"{continuity}[other]", continuity.rsplit("::", 1)[0] + "::test_unrelated"]
    nodes = [case for family in families for case in family]
    nodes += [allow, deny, *ordinary, *(f"tests/test_{i}.py::test_other" for i in range(30))]
    complete = Counter(nodes)

    default_groups = checker.test_case_groups(nodes)
    windows_groups = checker.test_case_groups(nodes, platform="windows")
    assert checker.test_case_groups(nodes, platform="linux") == default_groups
    for sibling_a, sibling_b, other in families:
        assert default_groups[sibling_a] == default_groups[other]
        assert windows_groups[sibling_a] == windows_groups[sibling_b] != windows_groups[other]
    assert len({windows_groups[node] for node in (allow, deny, ordinary[0])}) == 3
    assert windows_groups[ordinary[0]] == windows_groups[ordinary[1]]
    assert all(windows_groups[node] == default_groups[node] for node in ordinary)

    count = checker.CI_SHARD_COUNTS["windows"]
    shards = checker.assign_test_cases(complete, shard_count=count, platform="windows")
    checker.verify_complete_disjoint_union(complete, shards)
    assert all(not {allow, deny} <= set(shard) for shard in shards)
    for sibling_a, sibling_b, _ in families:
        assert any(sibling_a in shard and sibling_b in shard for shard in shards)
    assert shards == checker.assign_test_cases(
        Counter(reversed(nodes)), shard_count=count, platform="windows"
    )
    weighted = checker.windows_groups_by_runtime(nodes)
    positions = {node: index for index, (_, cases, _) in enumerate(weighted) for node in cases}
    assert positions[allow] < positions[deny] < positions[ordinary[0]]

    rolling = f"{witness_file}::test_rolling_successive_real_depth_two_events"
    event_cases = ("none", "foreign-id", "fork", "non-main", "attempt")
    source_seconds = {
        "reversed": (5.87, 6.20),
        "policy": (6.13, 6.70),
        "inherited-recipe": (6.29, 6.81),
        "recipe": (6.16, 6.67),
        "lock": (6.31, 6.42),
        "new-module": (6.16, 6.69),
        "deleted-module": (5.99, 6.47),
        "mode": (6.29, 6.38),
        "no-product": (6.27, 6.59),
    }
    expected = {
        f"{rolling}[{case}-{successor}]": f"{rolling}::event-scope-{successor}"
        for successor in (1, 2)
        for case in event_cases
    }
    expected.update(
        {
            f"{rolling}[{case}-{successor}]": f"{rolling}[{case}-{successor}]"
            for successor in (1, 2)
            for case in source_seconds
        }
    )
    assert checker.WINDOWS_ROLLING_SCOPES == expected
    old = [f"{witness_file}::test_old[{index}]" for index in range(164)]
    unknown = [
        f"{rolling}[none-3]",
        f"{rolling}[new-defect-1]",
        f"{rolling}[none-1-extra]",
        f"{rolling}_similar[none-1]",
        f"tests/unit/test_other.py::test_rolling_successive_real_depth_two_events[none-1]",
    ]
    cases = [
        *expected,
        *old,
        *unknown,
        *(f"tests/unit/test_scope_control_{index}.py::test_other" for index in range(4)),
    ]
    windows = checker.test_case_groups(cases, platform="windows")
    linux = checker.test_case_groups(cases, platform="linux")
    linux_plan = checker.assign_test_cases(Counter(cases), shard_count=4, platform="linux")
    assert sorted(Counter(windows[node] for node in expected).values()) == [1] * 18 + [5, 5]
    assert len({windows[node] for node in old + unknown[:-1]}) == 1
    assert not {windows[node] for node in expected} & {windows[node] for node in old}
    for node, scope in expected.items():
        assert checker._case_scope(node, windows=True) == scope
        assert checker._case_scope(node) == witness_file
    costs = {scope: seconds for scope, _, seconds in checker.windows_groups_by_runtime(cases)}
    assert costs[witness_file] == 87.80
    assert costs[f"{rolling}::event-scope-1"] == 9.70
    assert costs[f"{rolling}::event-scope-2"] == 10.09
    for case, seconds in source_seconds.items():
        for successor in (1, 2):
            assert costs[f"{rolling}[{case}-{successor}]"] == seconds[successor - 1]
    placement = checker.assign_test_cases(Counter(cases), shard_count=10, platform="windows")
    checker.verify_complete_disjoint_union(Counter(cases), placement)
    assert placement == checker.assign_test_cases(
        Counter(reversed(cases)), shard_count=10, platform="windows"
    )
    inverse = checker.projected_ids(windows)
    assert Counter(inverse.values()) == Counter(cases)
    for group in set(windows.values()):
        members = {node for node in cases if windows[node] == group}
        assert sum(members <= set(shard) for shard in placement) == 1
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(checker, "WINDOWS_ROLLING_SCOPES", {})
        assert checker.test_case_groups(cases, platform="linux") == linux
        assert (
            checker.assign_test_cases(Counter(cases), shard_count=4, platform="linux") == linux_plan
        )


def test_exact_projection_preserves_literal_at_signs_and_rejects_collisions() -> None:
    checker = _load_collection_script()
    nodes = [
        "tests/test_a.py::test_case[literal@atc-group-000000]",
        "tests/test_a.py::test_case[inside@[brackets]@atc-group-000001]",
        "tests/test_a.py::test_case[account@mail.test]",
    ]
    groups = checker.test_case_groups(nodes)
    assert checker.projected_ids(groups) == {f"{node}@{groups[node]}": node for node in nodes}
    node = nodes[0]
    with pytest.raises(ValueError, match="canonical/scheduling-ID collision"):
        checker.projected_ids(
            {node: "atc-group-000000", node + "@atc-group-000000": "atc-group-000001"}
        )
    with pytest.raises(ValueError, match="malformed scheduling group"):
        checker.projected_ids({node: "atc-group-000000@extra"})
    with pytest.raises(ValueError, match="duplicate collection"):
        checker.test_case_groups([node, node])
    with pytest.raises(ValueError, match="duplicate normalized paths"):
        checker.test_case_groups([node, node.replace("test_a.py", "Test_A.py")])


def test_runner_rejects_collection_that_diverges_from_canonical_plan() -> None:
    checker = _load_collection_script()
    canonical = Counter(
        {
            "tests/unit/test_a.py::test_one": 1,
            "tests/unit/test_b.py::test_two": 1,
        }
    )
    divergent_runner = Counter(
        {
            "tests/unit/test_a.py::test_one": 1,
            "tests/unit/test_c.py::test_three": 1,
        }
    )

    with pytest.raises(ValueError, match="differs from canonical Windows plan"):
        checker.require_canonical_collection(
            divergent_runner,
            checker.collection_digest(canonical),
        )


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


def test_required_windows_check_requires_valid_canonical_plan() -> None:
    checker = _load_collection_script()
    digest = "a" * 64
    shard_zero = '["tests/unit/test_a.py"]'
    shard_one = '["tests/unit/test_b.py"]'

    assert (
        checker.require_successful_shards(
            "success",
            plan_result="success",
            complete_digest=digest,
            shard_targets_json=(shard_zero, shard_one),
        )
        == 0
    )
    assert checker.require_successful_shards("success") == 1
    assert checker.require_successful_shards("success", plan_result="failure") == 1
    assert (
        checker.require_successful_shards(
            "success",
            plan_result="success",
            complete_digest=digest,
            shard_targets_json=(shard_zero, shard_zero),
        )
        == 1
    )


def test_ci_starts_frozen_shards_without_a_planner_and_keeps_required_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python-windows-plan" not in workflow
    assert "--shard-index ${{ matrix.shard }}" in workflow
    jobs = workflow.split("  security:", 1)[0]
    linux, rest = jobs.split("  python-windows-shard:", 1)
    windows, rest = rest.split("  python-windows-discovery:\n", 1)
    discovery, aggregates = rest.split("  python-linux:\n", 1)
    assert all("needs:" not in block for block in (linux, windows))
    assert "    needs: dashboard\n" in discovery
    assert "    if: ${{ !cancelled() }}\n" in discovery
    assert "run: python scripts/run_test_shard.py --discovery" in discovery
    assert "windows-discovery/canonical.json" in discovery
    assert "windows-discovery/retrieval.json" in discovery
    assert "/canonical.json" not in windows and "/retrieval.json" not in windows
    checker = _load_collection_script()
    assert checker.CI_SHARD_COUNTS == {"linux": 4, "windows": 10}
    for platform, block in (("linux", linux), ("windows", windows)):
        indices = list(range(checker.CI_SHARD_COUNTS[platform]))
        assert f"shard: {indices}\n" in block
    for name in ("security", "dashboard-parity"):
        delayed = workflow.split(f"  {name}:\n", 1)[1].split("    steps:", 1)[0]
        assert "    needs: [dashboard, dashboard-node-20]\n" in delayed
        assert "    if: ${{ !cancelled() }}\n" in delayed
    assert "needs: python\n" in aggregates
    assert "needs: [python-windows-shard, python-windows-discovery]\n" in aggregates
    assert aggregates.count("--verify-receipts .test-runs/receipts") == 2
    assert aggregates.count("merge-multiple: false") == 2
    assert "--platform linux" in aggregates and "--platform windows" in aggregates
    assert "if: ${{ always() }}" in workflow
    assert "fail-fast: false" in workflow
    assert "SHARD_RESULT: ${{ needs.python-windows-shard.result }}" in workflow
    assert '--shard-result "$SHARD_RESULT"' in workflow
    assert "DISCOVERY_RESULT: ${{ needs.python-windows-discovery.result }}" in aggregates
    assert '--discovery-result "$DISCOVERY_RESULT"' in aggregates
    assert workflow.count("name: Python 3.12 - windows-latest\n") == 1
    assert workflow.count("name: Python 3.12 - ubuntu-latest\n") == 1
    for block in (linux, windows):
        for gate in (
            "ruff format",
            "ruff check",
            "mypy packages/",
            "check_docs.py",
            "python -m build",
            "diagnose_python_packages.py",
        ):
            assert gate in block
    assert "matrix.shard == 0 || matrix.shard == 1" in windows
    assert "include-hidden-files: true" in windows
    assert "windows-install-removal-*.lifecycle.*" in windows
    assert linux.index("dependency_audit.py") < linux.index("run_test_shard.py")
    assert "--ignore" not in workflow
    assert "--deselect" not in workflow
    assert "-k " not in workflow


@pytest.fixture(scope="module")
def small_suite(tmp_path_factory):
    """A real default-discovery suite with platform, runtime and xfail outcomes."""

    root = tmp_path_factory.mktemp("ci-collection-contract")
    (root / "scripts").mkdir()
    for name in ("check_test_collection.py", "pytest_shard_receipt.py"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text(
        "import json\nimport time\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.fixture(scope='module')\n"
        "def shared(worker_id):\n"
        "    time.sleep(0.02)\n    seen = set()\n    yield seen\n"
        "    time.sleep(0.02)\n"
        "    assert seen == {1, 2, 3, 4}\n"
        "    with Path('witness-a.json').open('x', encoding='utf-8') as out:\n"
        "        json.dump(dict(worker=worker_id, cases=sorted(seen)), out)\n"
        "@pytest.mark.parametrize('value', [1, 2, 3, 4], "
        "ids=[r'raw\\id', 'raw/id', 'literal@atc-group-000001', "
        "'inside@[brackets]@atc-group-000003'])\n"
        "def test_parameter(value, shared):\n"
        "    time.sleep(0.02)\n"
        "    assert value not in shared\n    shared.add(value)\n"
        "@pytest.mark.skipif(True, reason='explicit capability control')\n"
        "def test_marked_skip(): assert False\n"
        "def test_runtime_skip(): pytest.skip('runtime capability control')\n",
        encoding="utf-8",
    )
    (tests / "test_b.py").write_text(
        "import json\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.fixture(scope='module')\n"
        "def shared(worker_id):\n"
        "    seen = set()\n    yield seen\n"
        "    assert seen == {1, 2}\n"
        "    with Path('witness-b.json').open('x', encoding='utf-8') as out:\n"
        "        json.dump(dict(worker=worker_id, cases=sorted(seen)), out)\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_shared(value, shared):\n"
        "    assert value not in shared\n    shared.add(value)\n"
        "@pytest.mark.xfail(reason='expected failure')\n"
        "def test_xfail(): assert False\n"
        "@pytest.mark.xfail(reason='nonstrict XPASS control')\n"
        "def test_xpass(): pass\n",
        encoding="utf-8",
    )
    (tests / "unit").mkdir()
    for name in ("test_updater.py", "test_packet_h_retrieval.py"):
        (tests / "unit" / name).write_text(
            "import pytest\n"
            "@pytest.fixture\ndef own():\n    seen = []\n    yield seen\n    assert seen == [1]\n"
            "@pytest.mark.parametrize('value', ['first', 'literal[tail]@value'])\n"
            "def test_family(value, own):\n    own.append(1)\n"
            "def test_other(own):\n    own.append(1)\n",
            encoding="utf-8",
        )
    (tests / "unit/test_windows_update_helper.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', ['a', 'b', 'c', 'd'], "
        "ids=['bare@group', 'account@mail.test', 'x@atc-group-000002', 'tail@'])\n"
        "def test_adversarial_child_failure_report_values_reach_safe_rollback(value):\n"
        "    assert value in 'abcd'\n",
        encoding="utf-8",
    )
    (tests / "unit/test_retrieval_m3_current_candidate.py").write_text(
        "import time\nimport pytest\nfrom pathlib import Path\n"
        "@pytest.fixture(scope='module')\n"
        "def serial(request):\n"
        "    assert not hasattr(request.config, 'workerinput')\n"
        "    seen = []\n    yield seen\n"
        "    assert seen == [1, 2]\n"
        "    Path('retrieval-witness.txt').write_text('complete')\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_serial(value, serial):\n    time.sleep(0.02)\n    serial.append(value)\n",
        encoding="utf-8",
    )
    (tests / "unit/test_windows_ci_witness.py").write_text(
        "import json\nfrom pathlib import Path\nimport pytest\n"
        "EVENTS = {'none', 'foreign-id', 'fork', 'non-main', 'attempt'}\n"
        "@pytest.fixture(scope='module')\n"
        "def event_checkouts(worker_id):\n"
        "    seen = {}\n    yield seen\n"
        "    for successor, cases in seen.items():\n"
        "        assert set(cases) == EVENTS\n"
        "        with Path(f'rolling-{successor}.json').open('x') as out:\n"
        "            json.dump(dict(worker=worker_id, cases=sorted(cases)), out)\n"
        "@pytest.mark.parametrize('successor', [1, 2])\n"
        "@pytest.mark.parametrize('defect', ['none', 'reversed', 'policy', "
        "'inherited-recipe', 'recipe', 'lock', 'new-module', 'deleted-module', "
        "'mode', 'no-product', 'foreign-id', 'fork', 'non-main', 'attempt'])\n"
        "def test_rolling_successive_real_depth_two_events(successor, defect, event_checkouts):\n"
        "    if defect in EVENTS:\n"
        "        seen = event_checkouts.setdefault(successor, [])\n"
        "        assert defect not in seen\n        seen.append(defect)\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_old(value): assert value in (1, 2)\n",
        encoding="utf-8",
    )
    checker = _load_collection_script()
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location(
            "run_test_shard", ROOT / "scripts/run_test_shard.py"
        )
        assert spec is not None and spec.loader is not None
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
    platform = "windows" if sys.platform == "win32" else "linux"
    identity = dict(head="a" * 40, tree="b" * 40, platform=platform, run_id="42", run_attempt="1")

    def run(mode, shard=0, count=2):
        output = root / f"receipt-{mode}-{shard}"
        output.mkdir()
        metadata = dict(
            identity,
            schema=1,
            workers=3,
            shard_count=count,
            mode=mode,
            shard=shard,
            job=f"proof/{shard}",
        )
        code = runner.run_pytest(root, output, metadata)
        return code, checker.read_receipt(output / f"{mode}.json")

    with ThreadPoolExecutor(max_workers=4) as pool:
        canonical = pool.submit(run, "canonical")
        shards = [pool.submit(run, "shard", i) for i in range(2)]
        retrieval = pool.submit(run, "retrieval")
        canonical_code, canonical_receipt = canonical.result()
        results = [future.result() for future in shards]
        retrieval_code, _ = retrieval.result()
    assert canonical_code == retrieval_code == 0 and all(code == 0 for code, _ in results)
    assert (root / "retrieval-witness.txt").read_text() == "complete"
    return checker, runner, root, identity, canonical_receipt, [value for _, value in results]


def _verify_small(small_suite, canonical=None, receipts=None, retrieval=None):
    checker, _, root, identity, original_canonical, original_receipts = small_suite
    return checker.verify_execution_receipts(
        original_canonical if canonical is None else canonical,
        original_receipts if receipts is None else receipts,
        identity=identity,
        shard_count=2,
        workers=3,
        job="proof",
        retrieval=(
            checker.read_receipt(root / "receipt-retrieval-0/retrieval.json")
            if retrieval is None
            else retrieval
        ),
    )


def test_actual_workers_match_independent_collection_and_preserve_outcomes(small_suite):
    statuses = _verify_small(small_suite)
    parameters = [node for node in statuses if "::test_parameter[" in node]
    assert len(parameters) == 4
    assert any("\\" in node for node in parameters)
    assert "tests/test_a.py::test_parameter[raw/id]" in parameters
    assert all(statuses[node] == "passed" for node in parameters)
    assert statuses["tests/test_a.py::test_marked_skip"] == "skipped"
    assert statuses["tests/test_a.py::test_runtime_skip"] == "skipped"
    assert statuses["tests/test_b.py::test_xfail"] == "xfailed"
    assert statuses["tests/test_b.py::test_xpass"] == "xpassed"
    assert statuses["tests/unit/test_retrieval_m3_current_candidate.py::test_serial[1]"] == "passed"


def test_real_workers_keep_audited_function_parameter_affinity(small_suite):
    checker, _, _, _, _, _ = small_suite
    statuses = _verify_small(small_suite)
    groups = checker.test_case_groups(list(statuses))
    for filename in ("test_updater.py", "test_packet_h_retrieval.py"):
        prefix = f"tests/unit/{filename}::"
        family = [node for node in statuses if node.startswith(prefix + "test_family[")]
        assert len(family) == 2
        assert all(statuses[node] == "passed" for node in family)
        assert len({groups[node] for node in family}) == 1
        assert groups[family[0]] != groups[prefix + "test_other"]


def _assert_profile(receipt):
    elapsed = receipt["process_elapsed_seconds"]
    assert math.isfinite(elapsed) and elapsed > 0
    for reports in receipt.get("reports", {}).values():
        for report in reports:
            duration = report["duration_seconds"]
            assert math.isfinite(duration) and 0 <= duration <= elapsed


def test_real_worker_and_standalone_profiles_survive_in_receipts(small_suite):
    checker, _, root, _, canonical, receipts = small_suite
    retrieval = checker.read_receipt(root / "receipt-retrieval-0/retrieval.json")
    for receipt in (canonical, *receipts, retrieval):
        _assert_profile(receipt)
    delayed = [
        report
        for receipt in receipts
        for node, reports in receipt["reports"].items()
        if node.startswith("tests/test_a.py::")
        for report in reports
    ]
    for phase in ("setup", "call", "teardown"):
        assert any(
            report["phase"] == phase and report["duration_seconds"] >= 0.018 for report in delayed
        )
    retrieval_calls = [
        report
        for reports in retrieval["reports"].values()
        for report in reports
        if report["phase"] == "call"
    ]
    assert len(retrieval_calls) == 2
    assert all(
        report["duration_seconds"] >= 0.018 and report["worker"] is None
        for report in retrieval_calls
    )


def test_old_receipts_without_optional_profiling_keep_the_same_verdict(small_suite):
    checker, _, root, _, canonical, receipts = small_suite
    retrieval = checker.read_receipt(root / "receipt-retrieval-0/retrieval.json")
    old_canonical, old_receipts, old_retrieval = deepcopy((canonical, receipts, retrieval))
    for receipt in (old_canonical, *old_receipts, old_retrieval):
        receipt.pop("process_elapsed_seconds")
        for reports in receipt.get("reports", {}).values():
            for report in reports:
                report.pop("duration_seconds")
    assert _verify_small(small_suite, old_canonical, old_receipts, old_retrieval) == _verify_small(
        small_suite
    )
    # Profiling remains diagnostic: old failed evidence must still be rejected.
    old_receipts[0]["process_exit"] = 1
    with pytest.raises(ValueError):
        _verify_small(small_suite, old_canonical, old_receipts, old_retrieval)


@pytest.mark.parametrize(
    "corruption", ["missing", "duplicate", "failed", "worker", "source", "collection", "extra"]
)
def test_retrieval_receipt_is_required_and_bound_to_complete_union(small_suite, corruption):
    checker, _, root, identity, canonical, receipts = small_suite
    retrieval = checker.read_receipt(root / "receipt-retrieval-0/retrieval.json")
    node = next(iter(retrieval["reports"]))
    if corruption == "duplicate":
        retrieval["started"][node] = 2
    elif corruption == "failed":
        retrieval["reports"][node][-2]["outcome"] = "failed"
    elif corruption == "worker":
        retrieval["reports"][node][-2]["worker"] = "gw0"
    elif corruption == "source":
        retrieval["tree"] = "c" * 40
    elif corruption == "collection":
        retrieval["collection"].pop()
    elif corruption == "extra":
        retrieval["reports"]["tests/test_a.py::test_runtime_skip"] = retrieval["reports"][node]
    with pytest.raises(ValueError):
        checker.verify_execution_receipts(
            canonical,
            receipts,
            identity=identity,
            shard_count=2,
            workers=3,
            job="proof",
            retrieval=None if corruption == "missing" else retrieval,
        )


def test_discovery_and_matrix_bind_real_receipts_to_distinct_producers(small_suite):
    checker, _, root, identity, canonical, original_shards = small_suite
    retrieval = checker.read_receipt(root / "receipt-retrieval-0/retrieval.json")
    discovery_job = checker.WINDOWS_DISCOVERY_JOB
    canonical = dict(canonical, job=f"{discovery_job}/0")
    retrieval["job"] = f"{discovery_job}/0"
    shards = deepcopy(original_shards)

    def verify():
        return checker.verify_execution_receipts(
            canonical,
            shards,
            identity=identity,
            shard_count=2,
            workers=3,
            job="proof",
            retrieval=retrieval,
            discovery_job=discovery_job,
        )

    assert verify() == _verify_small(small_suite)
    for receipt in (canonical, retrieval, shards[0]):
        original_job = receipt["job"]
        receipt["job"] = "proof/0" if receipt is not shards[0] else f"{discovery_job}/0"
        with pytest.raises(ValueError, match="job or invocation identity"):
            verify()
        receipt["job"] = original_job


def _verify_fixture_witnesses(homes, witnesses):
    assert set(witnesses) == {"a", "b"}
    for letter, cases in (("a", [1, 2, 3, 4]), ("b", [1, 2])):
        witness = witnesses[letter]
        workers = {
            worker for node, worker in homes.items() if node.startswith(f"tests/test_{letter}.py::")
        }
        assert workers == {witness["worker"]}
        assert witness["cases"] == cases


def test_real_loadgroup_preserves_shared_fixtures_and_splits_audited_cases(small_suite):
    checker, _, root, identity, canonical, receipts = small_suite
    _verify_small(small_suite)
    platform = identity["platform"]
    cases = canonical["collection"]
    groups = checker.test_case_groups(cases, platform=platform)
    inverse = checker.projected_ids(groups)
    if platform == "windows":
        parallel = checker.xdist_collection(Counter(cases))
        ordered = checker.windows_groups_by_runtime(list(parallel))
        ranks = {node: rank for rank, (_, nodes, _) in enumerate(ordered) for node in nodes}
        for receipt in receipts:
            for worker_nodes in receipt["collections"].values():
                worker_order = [ranks[inverse[node]] for node in worker_nodes]
                assert worker_order == sorted(worker_order)
    homes = {}
    placements = {}
    audited = []
    for shard, receipt in enumerate(receipts):
        for raw, reports in receipt["reports"].items():
            node = inverse[raw]
            worker = reports[0]["worker"]
            homes[node] = worker
            placements[node] = (shard, worker)
            if node.startswith("tests/unit/test_windows_update_helper.py::"):
                audited.append((shard, groups[node]))
        assert all(
            flags == {"loadgroup": True, "dist": "no"}
            for flags in receipt["worker_schedule"].values()
        )
    # The platform weights can divide the four independent groups unevenly.
    assert len(audited) == len({group for _, group in audited}) == 4
    assert {shard for shard, _ in audited} == {0, 1}
    rolling = "tests/unit/test_windows_ci_witness.py::test_rolling_successive_real_depth_two_events"
    for successor in (1, 2):
        event_nodes = [
            f"{rolling}[{case}-{successor}]"
            for case in ("none", "foreign-id", "fork", "non-main", "attempt")
        ]
        witness = checker.read_receipt(root / f"rolling-{successor}.json")
        assert len({placements[node] for node in event_nodes}) == 1
        assert homes[event_nodes[0]] == witness["worker"]
        assert witness["cases"] == ["attempt", "foreign-id", "fork", "non-main", "none"]
    rolling_nodes = [node for node in cases if node.startswith(rolling + "[")]
    assert len(rolling_nodes) == 28
    assert len({groups[node] for node in rolling_nodes}) == (20 if platform == "windows" else 1)
    old_nodes = [
        node
        for node in cases
        if node.startswith("tests/unit/test_windows_ci_witness.py::test_old[")
    ]
    assert len(old_nodes) == 2 and len({placements[node] for node in old_nodes}) == 1
    witnesses = {letter: checker.read_receipt(root / f"witness-{letter}.json") for letter in "ab"}
    _verify_fixture_witnesses(homes, witnesses)


@pytest.mark.parametrize("corruption", ["missing fixture", "missing case", "wrong worker"])
def test_all_terminal_passes_cannot_replace_shared_fixture_witnesses(small_suite, corruption):
    checker, _, root, _, canonical, receipts = small_suite
    _verify_small(small_suite)
    inverse = checker.projected_ids(
        checker.test_case_groups(canonical["collection"], platform=small_suite[3]["platform"])
    )
    homes = {
        inverse[raw]: reports[0]["worker"]
        for receipt in receipts
        for raw, reports in receipt["reports"].items()
    }
    witnesses = {letter: checker.read_receipt(root / f"witness-{letter}.json") for letter in "ab"}
    if corruption == "missing fixture":
        del witnesses["a"]
    elif corruption == "missing case":
        witnesses["a"]["cases"].pop()
    else:
        witnesses["a"]["worker"] = "gw99"
    with pytest.raises(AssertionError):
        _verify_fixture_witnesses(homes, witnesses)


@pytest.mark.parametrize(
    "corruption",
    [
        "missing terminal",
        "changed discovery",
        "duplicate teardown",
        "failed call",
        "wrong tree",
        "extra skip",
        "wrong OS",
        "wrong run",
        "wrong attempt",
        "wrong job",
        "missing shard",
        "duplicate shard",
        "extra shard",
        "missing worker",
        "worker crash",
        "missing finish",
        "duplicate start",
        "partial phases",
        "wrong worker",
        "failed process",
        "missing suffix",
        "stacked suffix",
        "wrong group",
        "aliased reports",
        "split shared group",
        "wrong scheduling flag",
        "missing scheduling flags",
    ],
)
def test_execution_receipts_reject_corrupted_real_evidence(small_suite, corruption):
    _, _, _, _, canonical, originals = small_suite
    receipts = deepcopy(originals)
    receipt = receipts[0]
    node = next(iter(receipt["reports"]))
    reports = receipt["reports"][node]
    if corruption == "missing terminal":
        del receipt["reports"][node]
    elif corruption == "changed discovery":
        receipt["worker_full"]["gw0"].pop()
    elif corruption == "duplicate teardown":
        reports.append(reports[-1])
    elif corruption == "failed call":
        reports[-2]["outcome"] = "failed"
    elif corruption == "extra skip":
        receipt["reports"]["tests/test_a.py::test_extra"] = [
            dict(phase="setup", outcome="skipped", xfail=False, worker="gw0"),
            dict(phase="teardown", outcome="passed", xfail=False, worker="gw0"),
        ]
    elif corruption in {"wrong tree", "wrong OS", "wrong run", "wrong attempt", "wrong job"}:
        key = {
            "wrong tree": "tree",
            "wrong OS": "platform",
            "wrong run": "run_id",
            "wrong attempt": "run_attempt",
            "wrong job": "job",
        }[corruption]
        receipt[key] = "wrong"
    elif corruption == "missing shard":
        receipts.pop()
    elif corruption == "duplicate shard":
        receipts[1] = receipt
    elif corruption == "extra shard":
        receipts.append(receipt)
    elif corruption == "missing worker":
        del receipt["collections"]["gw1"]
    elif corruption == "worker crash":
        receipt["worker_errors"]["gw0"] = True
    elif corruption == "missing finish":
        del receipt["finished"][node]
    elif corruption == "duplicate start":
        receipt["started"][node] += 1
    elif corruption == "partial phases":
        reports.pop()
    elif corruption == "wrong worker":
        reports[-1]["worker"] = "gw99"
    elif corruption in {"missing suffix", "stacked suffix", "wrong group"}:
        checker = small_suite[0]
        inverse = checker.projected_ids(
            checker.test_case_groups(canonical["collection"], platform=small_suite[3]["platform"])
        )
        changed = {
            "missing suffix": inverse[node],
            "stacked suffix": node + "@atc-group-999999",
            "wrong group": inverse[node] + "@atc-group-999999",
        }[corruption]
        for name in ("started", "finished", "reports"):
            receipt[name][changed] = receipt[name].pop(node)
    elif corruption == "aliased reports":
        other = next(value for value in receipt["reports"] if value != node)
        receipt["reports"][other].extend(receipt["reports"].pop(node))
    elif corruption == "split shared group":
        receipt, shared = next(
            (source, value)
            for source in receipts
            for value in source["reports"]
            if "::test_parameter[" in value or "::test_shared[" in value
        )
        for report in receipt["reports"][shared]:
            report["worker"] = "gw1" if report["worker"] == "gw0" else "gw0"
    elif corruption == "wrong scheduling flag":
        receipt["worker_schedule"]["gw0"]["loadgroup"] = False
    elif corruption == "missing scheduling flags":
        del receipt["worker_schedule"]["gw0"]
    else:
        receipt["process_exit"] = 1
    with pytest.raises(ValueError):
        _verify_small(small_suite, canonical, receipts)


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "", "success"])
def test_aggregate_rejects_failed_matrices_and_missing_artifacts(tmp_path, result):
    checker = _load_collection_script()
    with pytest.raises(ValueError):
        checker.verify_receipt_directory(
            tmp_path,
            platform="windows",
            run_id="42",
            run_attempt="1",
            shard_result=result,
            discovery_result="success",
        )


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "", None])
def test_windows_aggregate_rejects_unsuccessful_discovery(tmp_path, result):
    checker = _load_collection_script()
    with pytest.raises(ValueError, match="pytest discovery did not succeed"):
        checker.verify_receipt_directory(
            tmp_path,
            platform="windows",
            run_id="42",
            run_attempt="1",
            shard_result="success",
            discovery_result=result,
        )


def test_windows_aggregate_requires_exact_discovery_artifact_paths(tmp_path, monkeypatch):
    checker = _load_collection_script()
    monkeypatch.setattr(checker, "source_identity", lambda: dict(head="a" * 40, tree="b" * 40))
    captured = []
    monkeypatch.setattr(checker, "verify_execution_receipts", lambda *a, **kw: captured.append(kw))
    discovery = tmp_path / "pytest-42-1-windows-discovery"
    discovery.mkdir()
    (discovery / "canonical.json").write_text("{}", encoding="utf-8")
    for shard in range(checker.CI_SHARD_COUNTS["windows"]):
        folder = tmp_path / f"pytest-42-1-windows-{shard}"
        folder.mkdir()
        (folder / "shard.json").write_text("{}", encoding="utf-8")

    def verify():
        checker.verify_receipt_directory(
            tmp_path,
            platform="windows",
            run_id="42",
            run_attempt="1",
            shard_result="success",
            discovery_result="success",
        )

    alias = tmp_path / "pytest-42-1-windows-0/retrieval.json"
    alias.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="missing, extra or ambiguous receipt artifacts"):
        verify()
    retrieval = discovery / "retrieval.json"
    alias.rename(retrieval)
    retrieval.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        verify()
    retrieval.write_text("{}", encoding="utf-8")
    verify()
    assert captured[0]["discovery_job"] == checker.WINDOWS_DISCOVERY_JOB


@pytest.mark.parametrize("failure", ["setup", "call", "teardown", "crash", "empty"])
def test_real_failed_protocols_cannot_be_green(small_suite, tmp_path, failure):
    checker, runner, root, identity, _, _ = small_suite
    isolated = tmp_path / "suite"
    isolated.mkdir()
    shutil.copytree(root / "scripts", isolated / "scripts")
    shutil.copyfile(root / "pytest.ini", isolated / "pytest.ini")
    (isolated / "tests").mkdir()
    cases = {
        "setup": "import time\nimport pytest\n@pytest.fixture\ndef case():\n"
        "    time.sleep(0.02)\n    raise RuntimeError('setup')\n"
        "def test_case(case): pass\n",
        "call": "import time\ndef test_case():\n    time.sleep(0.02)\n    assert False\n",
        "teardown": "import time\nimport pytest\n@pytest.fixture\ndef case():\n    yield\n"
        "    time.sleep(0.02)\n    raise RuntimeError('teardown')\ndef test_case(case): pass\n",
        "crash": "import os\ndef test_case(): os._exit(2)\n",
        "empty": "# no collected tests\n",
    }
    (isolated / "tests/test_case.py").write_text(cases[failure], encoding="utf-8")
    metadata = dict(
        identity, schema=1, mode="shard", shard=0, shard_count=1, workers=2, job="proof/0"
    )
    output = isolated / "output"
    output.mkdir()
    code = runner.run_pytest(isolated, output, metadata)
    receipt = checker.read_receipt(output / "shard.json")
    assert code != 0
    _assert_profile(receipt)
    assert (output / "shard.log").is_file()
    if failure in {"setup", "call", "teardown"}:
        assert code == receipt["process_exit"] == receipt["exit"] == 1
        reports = next(iter(receipt["reports"].values()))
        assert checker.terminal_outcome(reports) == "failed"
        failed = next(report for report in reports if report["phase"] == failure)
        assert failed["outcome"] == "failed" and failed["duration_seconds"] >= 0.018
        old_reports = [
            {key: value for key, value in report.items() if key != "duration_seconds"}
            for report in reports
        ]
        assert checker.terminal_outcome(old_reports) == "failed"
    elif failure == "crash":
        assert receipt["worker_errors"]
    else:
        assert not receipt["reports"]


def test_json_receipts_reject_duplicate_keys(tmp_path):
    checker = _load_collection_script()
    path = tmp_path / "receipt.json"
    path.write_text('{"exit": 1, "exit": 0}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        checker.read_receipt(path)

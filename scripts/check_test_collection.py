"""Prove that CI parallel pytest commands execute every required test exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_PREFIX = "tests/"
CI_SHARD_COUNTS = {"linux": 4, "windows": 9}
DEFAULT_TEST_FILE_SECONDS = 0.859

# The twenty slowest files in the exact d18b4b7 sequential JUnit receipt. These
# bounded historical weights (with the two-module update noted below) affect
# scheduling only. Unknown/new files receive
# the deterministic median default above and can never be omitted.
HISTORICAL_TEST_FILE_SECONDS = {
    "tests/unit/test_windows_update_helper.py": 236.066,
    "tests/unit/test_memory_reliability_spec.py": 82.009,
    "tests/unit/test_retrieval_m3_current_candidate.py": 42.628,
    "tests/unit/test_import_operations.py": 32.825,
    "tests/integration/test_cross_client_memory_acceptance.py": 29.074,
    "tests/unit/test_updater.py": 48.143,
    "tests/unit/test_export.py": 19.968,
    "tests/unit/test_packet_h_retrieval.py": 23.409,
    "tests/unit/test_recovery_admin.py": 19.221,
    "tests/integration/test_core_api.py": 19.190,
    "tests/unit/test_capture_runtime.py": 18.723,
    "tests/unit/test_registered_source_admission.py": 17.248,
    "tests/unit/test_memory_truth.py": 16.832,
    "tests/integration/test_mcp_stdio.py": 14.864,
    "tests/integration/test_claude_code_memory_core_api.py": 14.644,
    "tests/unit/test_capture.py": 13.483,
    "tests/unit/test_retrieval_usefulness.py": 13.407,
    "tests/unit/test_capture_scheduler_productization.py": 12.777,
    "tests/unit/test_windows_bootstrap_install.py": 11.590,
    "tests/unit/test_local_git_workspace_connector.py": 11.489,
}

# The complete 236-node proof-3db07e46 audit supports singleton groups only
# for this exact module. Other files retain fixture affinity by default.
RETRIEVAL_TEST_FILE = "tests/unit/test_retrieval_m3_current_candidate.py"
WINDOWS_DISCOVERY_JOB = "python-windows-discovery"
PACKET_A_TEST_FILE = "tests/unit/test_memory_reliability_spec.py"
PACKET_A_BLOCK_FUNCTION = (
    "test_packet_a_code_owned_digest_rejects_every_semantic_leaf_after_rebound"
)
PACKET_A_BUCKET_PREFIX = f"{PACKET_A_TEST_FILE}::{PACKET_A_BLOCK_FUNCTION}[bucket-"

# Aggregate worker seconds from the two verified Linux shards of PR #128.
LINUX_TEST_FILE_SECONDS = HISTORICAL_TEST_FILE_SECONDS | {
    PACKET_A_TEST_FILE: 188.832224,
    "tests/integration/test_project_first_read.py": 72.037753,
    "tests/integration/test_agent_continuity_harness.py": 66.305963,
    "tests/integration/test_project_memory_continuity.py": 49.546586,
    "tests/unit/test_import_operations.py": 40.619605,
    "tests/unit/test_windows_update_helper.py": 32.086386,
}
LINUX_PACKET_A_GROUP_SECONDS = {
    PACKET_A_TEST_FILE: 14.195611,
    f"{PACKET_A_BUCKET_PREFIX}01-of-08]": 24.841772,
    f"{PACKET_A_BUCKET_PREFIX}02-of-08]": 24.432115,
    f"{PACKET_A_BUCKET_PREFIX}03-of-08]": 25.203897,
    f"{PACKET_A_BUCKET_PREFIX}04-of-08]": 24.382830,
    f"{PACKET_A_BUCKET_PREFIX}05-of-08]": 24.938513,
    f"{PACKET_A_BUCKET_PREFIX}06-of-08]": 17.619955,
    f"{PACKET_A_BUCKET_PREFIX}07-of-08]": 19.316208,
    f"{PACKET_A_BUCKET_PREFIX}08-of-08]": 13.901322,
}
# Shard zero handles extra serial checks. Reserve four workers times its
# observed ~20 seconds of additional setup when assigning Linux work.
LINUX_FIRST_SHARD_RESERVE = 80.0
# Real four-worker proof-5abef068: 226 cases, identical terminal outcomes and
# intentional skips. Keep parameter siblings together in these two modules.
# Their measured aggregate work above is placement evidence, not hosted wall time.
FUNCTION_AFFINITY_FILES = frozenset(
    {
        "tests/unit/test_updater.py",
        "tests/unit/test_packet_h_retrieval.py",
    }
)


PROJECT_FIRST_READ_TEST_FILE = "tests/integration/test_project_first_read.py"
PROJECT_MEMORY_TEST_FILE = "tests/integration/test_project_memory_continuity.py"

# Native Windows parity covered every case and fixture in these two modules.
WINDOWS_FUNCTION_AFFINITY_FILES = frozenset(
    {
        "tests/integration/test_agent_continuity_harness.py",
        PROJECT_FIRST_READ_TEST_FILE,
    }
)
# Those two observation-ACL journeys have independent vaults and instances.
WINDOWS_SINGLETON_NODE_IDS = frozenset(
    f"{PROJECT_MEMORY_TEST_FILE}::test_project_decision_correction_and_forget_survive_core_recreation[{acl}]"
    for acl in ("allow", "deny")
)
# Hosted receipt replay places a few measured slow groups on lighter runners.
WINDOWS_FILE_SHARDS = {
    "tests/integration/test_mcp_stdio.py": 1,
    PROJECT_FIRST_READ_TEST_FILE: 2,
    PROJECT_MEMORY_TEST_FILE: 8,
    "tests/unit/test_memory_reliability_lab_e01b.py": 4,
}


def test_case_groups(nodeids: Sequence[str], *, platform: str | None = None) -> dict[str, str]:
    """Default to file locality; split only audited independent exact cases."""

    receipt_collection(list(nodeids))
    files = {test_file_for_nodeid(node) for node in nodeids}
    require(
        len({path.casefold() for path in files}) == len(files),
        "test file list contains duplicate normalized paths",
    )
    keys = {}
    windows_splits = {}
    for nodeid in nodeids:
        path, _, test = nodeid.partition("::")
        function, parameter, _ = test.partition("[")
        independent = (path == "tests/unit/test_windows_update_helper.py") or (
            path == PACKET_A_TEST_FILE
            and function == PACKET_A_BLOCK_FUNCTION
            and bool(parameter)
            and test.endswith("]")
        )
        if independent:
            keys[nodeid] = nodeid
        elif path in FUNCTION_AFFINITY_FILES:
            keys[nodeid] = f"{path}::{function}"
        else:
            keys[nodeid] = path
        if platform == "windows":
            if nodeid in WINDOWS_SINGLETON_NODE_IDS:
                windows_splits[nodeid] = nodeid
            elif path in WINDOWS_FUNCTION_AFFINITY_FILES:
                windows_splits[nodeid] = f"{path}::{function}"
    # Append Windows scopes so unaffected nodes retain their projected IDs.
    original = set(keys.values())
    order = sorted(original) + sorted(set(windows_splits.values()) - original)
    names = {key: f"atc-group-{index:06d}" for index, key in enumerate(order)}
    return {nodeid: names[windows_splits.get(nodeid, key)] for nodeid, key in keys.items()}


def projected_ids(canonical_to_group: dict[str, str]) -> dict[str, str]:
    """Return an exact inverse, never parse or strip an opaque parameter suffix."""

    receipt_collection(list(canonical_to_group))
    require(
        all(re.fullmatch(r"atc-group-[0-9]{6,9}", group) for group in canonical_to_group.values()),
        "malformed scheduling group",
    )
    inverse = {f"{node}@{group}": node for node, group in canonical_to_group.items()}
    require(len(inverse) == len(canonical_to_group), "projected scheduling-ID collision")
    require(not inverse.keys() & canonical_to_group.keys(), "canonical/scheduling-ID collision")
    return inverse


def assign_test_cases(
    complete: Counter[str], *, shard_count: int, platform: str | None = None
) -> tuple[Counter[str], ...]:
    """Partition actual canonical cases without breaking their approved groups."""

    require(
        all(type(count) is int and count == 1 for count in complete.values()),
        "duplicate or invalid canonical counts",
    )
    # Worker affinity is independent of the historical hosted shard placement.
    groups = test_case_groups(list(complete))
    members: dict[str, list[str]] = {}
    file_counts = Counter(test_file_for_nodeid(node) for node in complete)
    for node, group in groups.items():
        members.setdefault(group, []).append(node)
    require(0 < shard_count <= len(members), "shard count exceeds collected work groups")
    # File totals are historical aggregate work, not per-case or group wall time.
    # Apportion them equally within a file; unknown files keep the median default.
    linux = platform == "linux"
    file_seconds = LINUX_TEST_FILE_SECONDS if linux else HISTORICAL_TEST_FILE_SECONDS
    weights = {}
    for group, nodes in members.items():
        path = test_file_for_nodeid(nodes[0])
        file_work = file_seconds.get(path, DEFAULT_TEST_FILE_SECONDS)
        # Preserve the Windows floating-point operation order used for placement.
        seconds = (
            file_work * len(nodes) / file_counts[path]
            if linux
            else sum(file_work / file_counts[path] for _ in nodes)
        )
        if linux and path == PACKET_A_TEST_FILE:
            key = nodes[0] if nodes[0].startswith(PACKET_A_BUCKET_PREFIX) else path
            seconds = LINUX_PACKET_A_GROUP_SECONDS.get(key, seconds)
        weights[group] = seconds
    shards: tuple[Counter[str], ...] = tuple(Counter() for _ in range(shard_count))
    totals = [0.0] * shard_count
    if linux:
        totals[0] = LINUX_FIRST_SHARD_RESERVE
    ordered = sorted(members, key=lambda value: (-weights[value], value))
    unassigned = set(range(shard_count))
    for offset, group in enumerate(ordered):
        eligible = (
            unassigned if linux and len(ordered) - offset == len(unassigned) else range(shard_count)
        )
        shard = min(eligible, key=lambda index: (totals[index], len(shards[index]), index))
        shards[shard].update(members[group])
        totals[shard] += weights[group]
        unassigned.discard(shard)
    if platform == "windows" and shard_count == CI_SHARD_COUNTS["windows"]:
        for nodes in members.values():
            destination = WINDOWS_FILE_SHARDS.get(test_file_for_nodeid(nodes[0]))
            if destination is None:
                continue
            source = next(shard for shard in shards if nodes[0] in shard)
            target = shards[destination]
            if source is not target:
                target.update({node: source.pop(node) for node in nodes})
    verify_complete_disjoint_union(complete, shards)
    return shards


def xdist_collection(complete: Counter[str]) -> Counter[str]:
    """Reserve the retrieval module for its explicit non-xdist process pool."""

    return Counter(
        {
            node: count
            for node, count in complete.items()
            if test_file_for_nodeid(node) != RETRIEVAL_TEST_FILE
        }
    )


def normalize_test_file(path: str) -> str:
    """Return a stable repository-relative test path or fail closed."""

    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if (
        not normalized.startswith(COLLECTION_PREFIX)
        or not normalized.endswith(".py")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"invalid repository test path: {path!r}")
    return "/".join(parts)


def collection_command(*, workers: int | None, targets: Sequence[str] = ()) -> list[str]:
    """Build sequential or file-level parallel collection commands."""

    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    if workers is not None:
        command.extend(["-n", str(workers), "--dist=loadfile"])
    command.extend(normalize_test_file(target) for target in targets)
    return command


def parse_collection(output: str) -> Counter[str]:
    """Return normalized nodeids while ignoring pytest progress and summaries."""

    nodeids: Counter[str] = Counter()
    for line in output.splitlines():
        raw = line.strip()
        if raw.startswith((COLLECTION_PREFIX, "tests\\")) and "::" in raw:
            nodeids[normalize_nodeid(raw)] += 1
    return nodeids


def normalize_nodeid(nodeid: str) -> str:
    """Normalize only the owned path; parameter/class suffixes are opaque."""

    path, separator, suffix = nodeid.partition("::")
    if not separator or not suffix:
        raise ValueError(f"invalid pytest nodeid: {nodeid!r}")
    return normalize_test_file(path) + separator + suffix


def collect(*, workers: int | None, targets: Sequence[str] = ()) -> Counter[str]:
    result = subprocess.run(
        collection_command(workers=workers, targets=targets),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError(f"pytest collection failed with exit code {result.returncode}")
    nodeids = parse_collection(result.stdout)
    if not nodeids:
        raise RuntimeError("pytest collection produced no test nodeids")
    return nodeids


def test_file_for_nodeid(nodeid: str) -> str:
    path, separator, _ = nodeid.partition("::")
    if not separator:
        raise ValueError(f"invalid pytest nodeid: {nodeid!r}")
    return normalize_test_file(path)


def collection_digest(collection: Counter[str]) -> str:
    """Return an order-independent receipt for exact nodeids and multiplicity."""

    payload = "".join(f"{count} {nodeid}\n" for nodeid, count in sorted(collection.items())).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def assign_test_files(files: Sequence[str], *, shard_count: int) -> tuple[tuple[str, ...], ...]:
    """Assign every normalized test file to one deterministic weighted shard."""

    if shard_count < 1:
        raise ValueError("shard count must be positive")
    normalized = [normalize_test_file(path) for path in files]
    if len({path.casefold() for path in normalized}) != len(normalized):
        raise ValueError("test file list contains duplicate normalized paths")
    if len(normalized) < shard_count:
        raise ValueError("shard count exceeds collected test file count")

    weighted = sorted(
        normalized,
        key=lambda path: (
            -HISTORICAL_TEST_FILE_SECONDS.get(path, DEFAULT_TEST_FILE_SECONDS),
            path.casefold(),
            path,
        ),
    )
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    totals = [0.0] * shard_count
    for path in weighted:
        shard_index = min(
            range(shard_count),
            key=lambda index: (totals[index], len(shards[index]), index),
        )
        shards[shard_index].append(path)
        totals[shard_index] += HISTORICAL_TEST_FILE_SECONDS.get(path, DEFAULT_TEST_FILE_SECONDS)

    if any(not shard for shard in shards):
        raise ValueError("test file assignment produced an empty shard")
    return tuple(tuple(sorted(shard, key=lambda path: (path.casefold(), path))) for shard in shards)


def expected_shard_collections(
    complete: Counter[str], shards: Sequence[Sequence[str]]
) -> tuple[Counter[str], ...]:
    file_to_shard = {
        path: shard_index for shard_index, shard in enumerate(shards) for path in shard
    }
    expected: tuple[Counter[str], ...] = tuple(Counter() for _ in shards)
    for nodeid, count in complete.items():
        path = test_file_for_nodeid(nodeid)
        shard_index = file_to_shard.get(path)
        if shard_index is None:
            raise ValueError(f"collected test file was not assigned: {path}")
        expected[shard_index][nodeid] = count
    verify_complete_disjoint_union(complete, expected)
    return expected


def parse_shard_targets(payload: str) -> tuple[str, ...]:
    """Parse one canonical shard's JSON target list and fail closed."""

    decoded = json.loads(payload)
    if (
        not isinstance(decoded, list)
        or not decoded
        or not all(isinstance(item, str) for item in decoded)
    ):
        raise ValueError("canonical shard targets must be a non-empty JSON string list")
    targets = tuple(normalize_test_file(item) for item in decoded)
    if len({target.casefold() for target in targets}) != len(targets):
        raise ValueError("canonical shard targets contain duplicate normalized paths")
    return targets


def require_canonical_collection(complete: Counter[str], expected_digest: str) -> None:
    """Reject a runner whose complete collection differs from the planner's."""

    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("canonical complete collection digest is missing or invalid")
    actual_digest = collection_digest(complete)
    if actual_digest != expected_digest:
        raise ValueError(
            "runner complete collection differs from canonical Windows plan "
            f"(expected_sha256={expected_digest}, actual_sha256={actual_digest})"
        )


def write_github_shard_plan(
    path: Path, complete: Counter[str], shards: Sequence[Sequence[str]]
) -> None:
    """Publish a bounded canonical plan for all later Windows runner jobs."""

    lines = [f"complete_digest={collection_digest(complete)}\n"]
    lines.extend(
        f"shard_{index}_targets={json.dumps(list(targets), separators=(',', ':'))}\n"
        for index, targets in enumerate(shards)
    )
    with path.open("a", encoding="utf-8") as output:
        output.write("".join(lines))


def verify_complete_disjoint_union(
    complete: Counter[str], shard_collections: Sequence[Counter[str]]
) -> None:
    """Reject missing or multiply assigned nodeids before test execution."""

    if not shard_collections or any(not shard for shard in shard_collections):
        raise ValueError("shard collection is missing or empty")
    combined: Counter[str] = Counter()
    for shard in shard_collections:
        overlap = combined & shard
        if overlap:
            raise ValueError(f"shard collections overlap on {sum(overlap.values())} nodeids")
        combined.update(shard)
    if combined != complete:
        raise ValueError(
            "shard collection union differs from complete collection "
            f"(complete={sum(complete.values())}, union={sum(combined.values())}, "
            f"missing={sum((complete - combined).values())}, "
            f"extra={sum((combined - complete).values())})"
        )


def _collections_match(expected: Counter[str], actual: Counter[str], *, description: str) -> bool:
    if expected == actual:
        return True
    print(
        f"pytest collection contract failed: {description} "
        f"(expected={sum(expected.values())}, actual={sum(actual.values())}, "
        f"missing={sum((expected - actual).values())}, "
        f"extra={sum((actual - expected).values())})",
        file=sys.stderr,
    )
    return False


def verify_collection(
    *,
    workers: int,
    shard_count: int | None = None,
    shard_index: int | None = None,
    write_targets: Path | None = None,
    write_github_output: Path | None = None,
    canonical_complete_digest: str | None = None,
    canonical_targets_json: str | None = None,
) -> int:
    if workers < 1:
        raise ValueError("workers must be positive")
    sequential = collect(workers=None)
    parallel = collect(workers=workers)
    if not _collections_match(
        sequential,
        parallel,
        description="sequential and complete parallel nodeid sets differ",
    ):
        return 1

    if shard_count is None:
        if any(
            value is not None
            for value in (
                shard_index,
                write_targets,
                write_github_output,
                canonical_complete_digest,
                canonical_targets_json,
            )
        ):
            raise ValueError("shard index/output requires a shard count")
        print(
            "pytest collection contract passed: "
            f"{sum(sequential.values())} nodeids are identical for sequential and "
            f"{workers}-worker --dist=loadfile collection; "
            f"sha256={collection_digest(sequential)}"
        )
        return 0
    files = sorted({test_file_for_nodeid(nodeid) for nodeid in sequential})
    shards = assign_test_files(files, shard_count=shard_count)
    expected = expected_shard_collections(sequential, shards)

    if write_github_output is not None:
        if any(
            value is not None
            for value in (
                shard_index,
                write_targets,
                canonical_complete_digest,
                canonical_targets_json,
            )
        ):
            raise ValueError("canonical plan output cannot select or consume a shard")
        write_github_shard_plan(write_github_output, sequential, shards)
        print(
            "pytest canonical shard plan passed: "
            f"{sum(sequential.values())} nodeids partition into {shard_count} disjoint shards; "
            f"complete_sha256={collection_digest(sequential)}"
        )
        return 0

    if shard_index is None:
        raise ValueError("shard index is required when shard count is set")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index is outside the configured shard range")
    if canonical_complete_digest is None or canonical_targets_json is None:
        raise ValueError("shard execution requires the canonical digest and targets")
    require_canonical_collection(sequential, canonical_complete_digest)
    targets = parse_shard_targets(canonical_targets_json)
    if targets != shards[shard_index]:
        raise ValueError(f"shard {shard_index} targets differ from canonical assignment")
    shard_sequential = collect(workers=None, targets=targets)
    shard_parallel = collect(workers=workers, targets=targets)
    if not _collections_match(
        expected[shard_index],
        shard_sequential,
        description=f"shard {shard_index} sequential nodeids differ from assignment",
    ):
        return 1
    if not _collections_match(
        shard_sequential,
        shard_parallel,
        description=f"shard {shard_index} sequential and parallel nodeids differ",
    ):
        return 1

    if write_targets is not None:
        write_targets.write_text("".join(f"{target}\n" for target in targets), encoding="utf-8")
    print(
        "pytest sharding contract passed: "
        f"{sum(sequential.values())} complete nodeids partition into {shard_count} "
        f"disjoint shards; shard {shard_index} has {len(targets)} files and "
        f"{sum(shard_sequential.values())} nodeids identical for sequential and "
        f"{workers}-worker --dist=loadfile collection; "
        f"complete_sha256={collection_digest(sequential)}; "
        f"shard_sha256={collection_digest(shard_sequential)}"
    )
    return 0


def require_successful_shards(
    result: str,
    *,
    plan_result: str | None = None,
    complete_digest: str | None = None,
    shard_targets_json: Sequence[str] = (),
) -> int:
    """Propagate a matrix dependency result through the stable required check."""

    if plan_result != "success":
        print(
            f"Windows pytest shard plan did not succeed: {plan_result or 'missing'}",
            file=sys.stderr,
        )
        return 1
    if result == "success":
        try:
            if complete_digest is None or not re.fullmatch(r"[0-9a-f]{64}", complete_digest):
                raise ValueError("canonical complete collection digest is missing or invalid")
            targets = tuple(parse_shard_targets(payload) for payload in shard_targets_json)
            if len(targets) != 2:
                raise ValueError("exactly two canonical shard target lists are required")
            flattened = [path for shard in targets for path in shard]
            if len({path.casefold() for path in flattened}) != len(flattened):
                raise ValueError("canonical shard target lists overlap")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Windows pytest shard plan is invalid: {exc}", file=sys.stderr)
            return 1
        print("Windows pytest shards completed successfully from the canonical plan")
        return 0
    print(f"Windows pytest shards did not succeed: {result or 'missing'}", file=sys.stderr)
    return 1


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_json(entries: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in entries:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_receipt(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_json)
    require(isinstance(payload, dict), "receipt must be a JSON object")
    return payload


def source_identity(root: Path = REPOSITORY_ROOT) -> dict[str, str]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    require(not git("status", "--porcelain", "--untracked-files=no"), "tracked source is dirty")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    require(
        all(not path or path.startswith(".test-runs/") for path in untracked),
        "untracked files outside owned test scratch",
    )
    return {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}")}


def receipt_collection(values: list[str]) -> Counter[str]:
    require(isinstance(values, list) and bool(values), "missing or empty collection")
    require(all(isinstance(value, str) for value in values), "invalid node IDs")
    require(all(normalize_nodeid(value) == value for value in values), "noncanonical node IDs")
    counted = Counter(values)
    require(all(count == 1 for count in counted.values()), "duplicate collection node IDs")
    return counted


def terminal_outcome(reports: list[dict]) -> str:
    """Attest the complete pytest protocol, including setup/teardown errors."""

    require(isinstance(reports, list) and bool(reports), "missing terminal reports")
    for report in reports:
        require(report["outcome"] in {"passed", "skipped", "failed"}, "invalid report outcome")
        require(type(report["xfail"]) is bool, "invalid xfail evidence")
    expected = (
        ["setup", "call", "teardown"]
        if reports[0]["outcome"] == "passed"
        else ["setup", "teardown"]
    )
    require([report["phase"] for report in reports] == expected, "incomplete or duplicate phases")
    if any(report["outcome"] == "failed" for report in reports):
        return "failed"
    require(reports[-1]["outcome"] == "passed", "teardown did not pass")
    decisive = reports[-2]
    if decisive["outcome"] == "passed":
        return "xpassed" if decisive["xfail"] else "passed"
    return "xfailed" if decisive["xfail"] else "skipped"


def verify_execution_receipts(
    canonical: dict,
    receipts: Sequence[dict],
    *,
    identity: dict,
    shard_count: int,
    workers: int,
    job: str,
    retrieval: dict | None = None,
    discovery_job: str | None = None,
) -> dict[str, str]:
    """Compare every real worker and terminal node against independent discovery."""

    require(shard_count > 0 and workers > 0, "invalid schedule")
    base = dict(identity, schema=1, shard_count=shard_count, workers=workers)

    def check_identity(receipt: dict, *, mode: str, shard: int, producer: str) -> None:
        expected = dict(base, mode=mode, shard=shard, job=f"{producer}/{shard}")
        require(
            all(
                type(receipt.get(key)) is type(value) and receipt[key] == value
                for key, value in expected.items()
            ),
            "wrong source, OS, run, job or invocation identity",
        )
        require(type(receipt.get("exit")) is int and receipt["exit"] == 0, "pytest did not succeed")
        require(
            type(receipt.get("process_exit")) is int and receipt["process_exit"] == 0,
            "pytest subprocess did not succeed",
        )

    discovery_producer = job if discovery_job is None else discovery_job
    check_identity(canonical, mode="canonical", shard=0, producer=discovery_producer)
    complete = receipt_collection(canonical["collection"])
    require(canonical["collection_digest"] == collection_digest(complete), "bad collection digest")
    groups = test_case_groups(list(complete), platform=identity.get("platform"))
    inverse = projected_ids(groups)
    parallel = xdist_collection(complete)
    expected = assign_test_cases(
        parallel, shard_count=shard_count, platform=identity.get("platform")
    )
    require(len(receipts) == shard_count, "missing or extra shard receipts")
    by_shard = {receipt["shard"]: receipt for receipt in receipts}
    require(set(by_shard) == set(range(shard_count)), "duplicate or wrong shard receipts")
    statuses: dict[str, str] = {}
    executed: list[Counter[str]] = []
    for shard, nodes in enumerate(expected):
        receipt = by_shard[shard]
        check_identity(receipt, mode="shard", shard=shard, producer=job)
        worker_ids = {f"gw{index}" for index in range(workers)}
        require(set(receipt["collections"]) == worker_ids, "missing or extra real workers")
        require(set(receipt["worker_full"]) == worker_ids, "missing full worker discovery")
        require(set(receipt["worker_schedule"]) == worker_ids, "missing worker scheduling flags")
        require(not receipt["worker_errors"], "worker crashed")
        projected = Counter({f"{node}@{groups[node]}": count for node, count in nodes.items()})
        order = None
        for worker in sorted(worker_ids):
            selected = receipt["collections"][worker]
            require(receipt_collection(selected) == projected, "worker selection differs")
            flags = receipt["worker_schedule"][worker]
            require(
                isinstance(flags, dict)
                and flags == {"loadgroup": True, "dist": "no"}
                and flags["loadgroup"] is True,
                "worker did not use real loadgroup scheduling",
            )
            require(
                receipt_collection(receipt["worker_full"][worker]) == complete,
                "full worker discovery differs from canonical collection",
            )
            require(order is None or selected == order, "worker collection orders differ")
            order = selected
        for name in ("started", "finished"):
            counts = receipt[name]
            require(
                all(type(count) is int and count == 1 for count in counts.values()),
                f"duplicate or invalid {name} counts",
            )
            require(Counter(counts) == projected, f"missing or extra {name} nodes")
        require(Counter(receipt["reports"].keys()) == projected, "missing or extra terminal nodes")
        group_workers: dict[str, str] = {}
        for raw_nodeid, reports in receipt["reports"].items():
            nodeid = inverse[raw_nodeid]
            require(bool(reports), "missing terminal reports")
            actual_workers = {report["worker"] for report in reports}
            require(
                len(actual_workers) == 1 and actual_workers <= worker_ids,
                "terminal reports have wrong worker identity",
            )
            worker = next(iter(actual_workers))
            group = groups[nodeid]
            require(
                group_workers.setdefault(group, worker) == worker,
                "scheduling group split across workers",
            )
            outcome = terminal_outcome(reports)
            require(outcome != "failed", f"test failed: {nodeid}")
            statuses[nodeid] = outcome
        executed.append(Counter(inverse[node] for node in receipt["reports"]))
    standalone = complete - parallel
    require((retrieval is not None) == bool(standalone), "missing or extra retrieval receipt")
    if retrieval is not None:
        check_identity(retrieval, mode="retrieval", shard=0, producer=discovery_producer)
        require(
            receipt_collection(retrieval["collection"]) == standalone,
            "retrieval collection differs",
        )
        for name in ("started", "finished"):
            counts = retrieval[name]
            require(
                all(type(count) is int and count == 1 for count in counts.values()),
                f"duplicate or invalid retrieval {name} counts",
            )
            require(Counter(counts) == standalone, f"missing or extra retrieval {name} nodes")
        require(
            Counter(retrieval["reports"].keys()) == standalone, "retrieval terminal nodes differ"
        )
        for nodeid, reports in retrieval["reports"].items():
            require(all(report["worker"] is None for report in reports), "retrieval used xdist")
            outcome = terminal_outcome(reports)
            require(outcome != "failed", f"test failed: {nodeid}")
            statuses[nodeid] = outcome
        executed.append(Counter(retrieval["reports"].keys()))
    verify_complete_disjoint_union(complete, executed)
    return statuses


def verify_receipt_directory(
    directory: Path,
    *,
    platform: str,
    run_id: str,
    run_attempt: str,
    shard_result: str,
    discovery_result: str | None = None,
) -> None:
    """Validate current-run artifacts and their producers' actual job conclusions."""

    require(shard_result == "success", f"pytest matrix did not succeed: {shard_result}")
    require(run_id.isdecimal() and run_attempt.isdecimal(), "invalid run identity")
    count = CI_SHARD_COUNTS[platform]
    job = "python" if platform == "linux" else "python-windows-shard"
    discovery_job = WINDOWS_DISCOVERY_JOB if platform == "windows" else None
    stem = f"pytest-{run_id}-{run_attempt}-{platform}"
    discovery_directory = f"{stem}-0"
    if discovery_job is not None:
        require(
            discovery_result == "success", f"pytest discovery did not succeed: {discovery_result}"
        )
        discovery_directory = f"{stem}-discovery"
    shard_paths = [f"{stem}-{shard}/shard.json" for shard in range(count)]
    expected_paths = set(shard_paths)
    canonical = f"{discovery_directory}/canonical.json"
    expected_paths.add(canonical)
    require((directory / canonical).is_file(), "missing canonical receipt artifact")
    canonical_receipt = read_receipt(directory / canonical)
    has_retrieval = discovery_job is not None or any(
        test_file_for_nodeid(node) == RETRIEVAL_TEST_FILE
        for node in receipt_collection(canonical_receipt["collection"])
    )
    retrieval_path = f"{discovery_directory}/retrieval.json"
    if has_retrieval:
        expected_paths.add(retrieval_path)
    actual_paths = {
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    }
    require(actual_paths == expected_paths, "missing, extra or ambiguous receipt artifacts")
    identity = dict(source_identity(), platform=platform, run_id=run_id, run_attempt=run_attempt)
    verify_execution_receipts(
        canonical_receipt,
        [read_receipt(directory / path) for path in shard_paths],
        identity=identity,
        shard_count=count,
        workers=4,
        job=job,
        retrieval=read_receipt(directory / retrieval_path) if has_retrieval else None,
        discovery_job=discovery_job,
    )
    print(f"{platform} canonical collection and {count} real-worker execution receipts passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--write-targets", type=Path)
    parser.add_argument("--write-github-output", type=Path)
    parser.add_argument("--canonical-complete-digest")
    parser.add_argument("--canonical-targets-json")
    parser.add_argument("--require-shard-result")
    parser.add_argument("--require-plan-result")
    parser.add_argument("--require-complete-digest")
    parser.add_argument("--require-shard-targets-json", action="append", default=[])
    parser.add_argument("--verify-receipts", type=Path)
    parser.add_argument("--platform", choices=("linux", "windows"))
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--shard-result")
    parser.add_argument("--discovery-result")
    arguments = parser.parse_args()
    try:
        if arguments.verify_receipts is not None:
            require(
                all((arguments.platform, arguments.run_id, arguments.run_attempt)),
                "receipt verification requires platform and run identity",
            )
            verify_receipt_directory(
                arguments.verify_receipts,
                platform=arguments.platform,
                run_id=arguments.run_id,
                run_attempt=arguments.run_attempt,
                shard_result=arguments.shard_result,
                discovery_result=arguments.discovery_result,
            )
            return 0
        if arguments.require_shard_result is not None:
            if any(
                value is not None
                for value in (
                    arguments.workers,
                    arguments.shard_count,
                    arguments.shard_index,
                    arguments.write_targets,
                    arguments.write_github_output,
                    arguments.canonical_complete_digest,
                    arguments.canonical_targets_json,
                )
            ):
                raise ValueError("shard result propagation cannot collect tests")
            return require_successful_shards(
                arguments.require_shard_result,
                plan_result=arguments.require_plan_result,
                complete_digest=arguments.require_complete_digest,
                shard_targets_json=arguments.require_shard_targets_json,
            )
        if arguments.workers is None:
            raise ValueError("workers are required for collection verification")
        return verify_collection(
            workers=arguments.workers,
            shard_count=arguments.shard_count,
            shard_index=arguments.shard_index,
            write_targets=arguments.write_targets,
            write_github_output=arguments.write_github_output,
            canonical_complete_digest=arguments.canonical_complete_digest,
            canonical_targets_json=arguments.canonical_targets_json,
        )
    except (KeyError, TypeError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        print(f"pytest collection contract error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

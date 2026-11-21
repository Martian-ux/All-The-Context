"""Bounded ordinary-PR stock witness and complete cold Windows fallback.

Only provider-authenticated, completed producer steps can authorize a join.
Receipts contain hashes of full files, never normalized archive comparisons.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from scripts import native_build_provenance as provenance
    from scripts import windows_ci_pilot as pilot
else:
    import native_build_provenance as provenance
    import windows_ci_pilot as pilot

BASE = pilot.SOURCE
PROFILE = "windows-x86_64-stock-marshal2-v1"
JOB = "Windows independent stock witness"
UPLOAD = "Upload bounded Windows stock witness"
MARKER = "Record Windows stock witness upload identity"
RECEIPT = "windows-stock-witness-v1.json"
MAX_RECEIPT = 32_768
API_VERSION = "2026-03-10"
# The qualification policy itself is reviewed with this module. All other
# admitted CI/native implementation inputs are fixed Git blobs, not a broad
# permission to change the packaging recipe on an ordinary PR.
QUALIFIED_INPUT_BLOBS = {
    # Additional inputs from the separately reviewed protected-main -> prior CI
    # overlay. These exact blobs remain present in the current witness overlay.
    "bench/retrieval_m3_current_candidate.py": "c2dae5e8b9eb3914ea691b6fab079d2372e9389a",
    "scripts/check_test_collection.py": "8841cf2e6d4947008aca2208cdab599c2becc5e2",
    "scripts/install_locked_python.py": "04ae2b2ab8fef6b913a88f5c79a72598df39a920",
    "scripts/pytest_shard_receipt.py": "6df45981b5c323599d5926ceb447cc33ce9722e2",
    "scripts/run_test_shard.py": "520425b0af8f07dfa1bfb062a3608e9193777291",
    "scripts/smoke_packaged_first_run.py": "c6f033793246921182cb0300c10afe7b1148befc",
    "scripts/windows_ci_process_inventory.py": "fe88de2c31f1418912de42842a911f88c37d184e",
    "tests/unit/test_exact_lock_contracts.py": "27db2600dbe7f3bd4c2fcfd32593232b41d68e27",
    "tests/unit/test_memory_reliability_spec.py": "7ba9c78cccd36e1cf45f9ca96a1c8f0de5d2b781",
    "tests/unit/test_packaged_first_run_diagnostics.py": "24779103a5896ca10726b845ddf8df6b0dda08b7",
    "tests/unit/test_retrieval_m3_current_candidate.py": "07f4453ad2b43a0afafb6681da41ce04153ad11d",
    # Current reviewed witness inputs (including its changes to prior CI files).
    ".github/workflows/ci.yml": "c7e3ba2a64a3c8f02f06fd087431d1163e21a399",
    "scripts/build_desktop.py": "233b11b625c2f75aa17609cd8f7500d7d6930acf",
    "scripts/verify_reproducible_build.py": "91a6c93381f1245a69e8c94d1f6d8fbf98c7b8d4",
    "scripts/windows_ci_components.py": "f98c18518c68c0505317d19981daa1611341d320",
    "scripts/windows_ci_package_checks.py": "d2bbb50c719b19058faf4aef204a6d0a1235bf4f",
    "scripts/windows_ci_pilot.py": "a06c6f2b46f8bae744ccc61ede367aa1a37d54aa",
    "scripts/windows_ci_canonical.py": "5e47f71507e7ea9e36c3ee372361de08114a7d38",
}
MARKER_PATTERN = re.compile(
    r"^(?:\d{4}-\d\d-\d\dT[0-9:.]+Z )?"
    r"ATC_WINDOWS_STOCK_V1 artifact_id=([1-9][0-9]{0,19}) digest=sha256:([0-9a-f]{64})$",
    re.MULTILINE,
)
IMPLEMENTATION_FILES = {
    ".github/workflows/ci.yml",
    "scripts/build_desktop.py",
    "scripts/verify_reproducible_build.py",
    "scripts/windows_ci_pilot.py",
    "scripts/windows_ci_components.py",
    "scripts/windows_ci_package_checks.py",
    "scripts/windows_ci_witness.py",
    "scripts/windows_ci_canonical.py",
    "tests/unit/test_windows_ci_witness.py",
    "tests/unit/test_windows_ci_pilot.py",
    "tests/unit/test_native_build_provenance.py",
    "tests/unit/test_pytest_collection_contract.py",
    "tests/unit/test_release_workflow_contracts.py",
    "docs/STATUS.md",
    "docs/DECISIONS.md",
    "docs/REQUIREMENTS_TRACEABILITY.md",
}
require = pilot.require
git = pilot.git


def native_verifier():
    # Source qualification also runs before the locked environment is installed.
    # Keep that early donor-download path entirely standard-library-only.
    return importlib.import_module(
        "scripts.verify_reproducible_build" if __package__ else "verify_reproducible_build"
    )


def component_runner(*arguments, **options):
    module = importlib.import_module(
        "scripts.windows_ci_components" if __package__ else "windows_ci_components"
    )
    return module.component_runner(*arguments, **options)


def bounded_json(raw: bytes, limit: int = MAX_RECEIPT) -> dict:
    require(len(raw) <= limit, "oversized witness data")

    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "duplicate witness field")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("nonfinite witness data")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    require(isinstance(value, dict), "invalid witness data")
    return value


def run_identity(environment: dict[str, str]) -> tuple[int, int]:
    values = [environment.get(name, "") for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")]
    require(all(re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in values), "invalid run")
    return int(values[0]), int(values[1])


def packaging_venv(environment: dict[str, str]) -> str:
    run, attempt = run_identity(environment)
    # Deliberately independent of runner.temp and strategy.job-index.
    return f"C:/atc-ci-package-{run}-{attempt}"


def admit_source(root: Path, commit: str, *, producer: bool = False) -> dict:
    environment = dict(os.environ)
    event = bounded_json(Path(environment["GITHUB_EVENT_PATH"]).read_bytes(), 1_000_000)
    context = dict(environment, GITHUB_JOB="desktop-package")
    # Check the actual event against protected main; never substitute a private
    # implementation parent, which is not even available at provider depth two.
    pr = event.get("pull_request")
    require(isinstance(pr, dict), "not an ordinary PR")
    base = pr.get("base")
    require(isinstance(base, dict) and base.get("sha") == BASE, "unreviewed ordinary base")
    require(pilot.eligible_context(event, context), "unsupported ordinary context")
    require(
        environment.get("GITHUB_JOB")
        == ("windows-stock-witness" if producer else "desktop-package"),
        "wrong witness participant",
    )
    run_identity(environment)
    require(
        git(root, "rev-parse", "HEAD").decode().strip() == commit == environment["GITHUB_SHA"],
        "ordinary source mismatch",
    )
    require(not git(root, "status", "--porcelain", "--untracked-files=normal"), "dirty source")
    for name, blob in QUALIFIED_INPUT_BLOBS.items():
        require(
            git(root, "rev-parse", f"{commit}:{name}").decode().strip() == blob,
            "unreviewed CI/native input",
        )
    parents = git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()
    require(parents == [commit, BASE, pr["head"]["sha"]], "unsupported merge parents")
    rows = (
        git(root, "diff", "--name-status", "--no-renames", "-z", BASE, commit).decode().split("\0")
    )
    require(rows[-1] == "" and len(rows) % 2 == 1, "invalid source delta")
    ordinary = False
    for status, name in zip(rows[:-1:2], rows[1:-1:2], strict=True):
        require(status in {"A", "M"}, "unsupported source delta")
        for revision in (BASE, commit) if status == "M" else (commit,):
            row = git(root, "ls-tree", revision, "--", name).decode()
            require(
                row.startswith("100644 blob ") and row.endswith(f"\t{name}\n"),
                "unsupported ordinary file",
            )
        if name in IMPLEMENTATION_FILES or name in QUALIFIED_INPUT_BLOBS:
            continue
        product = name.startswith("packages/allthecontext/src/allthecontext/") and name.endswith(
            ".py"
        )
        supporting = (name.startswith("tests/") and name.endswith(".py")) or (
            name.startswith("docs/") and name.endswith(".md")
        )
        require(status == "M" and (product or supporting), "uncovered ordinary input")
        ordinary |= product
    require(ordinary, "no ordinary shipped Python edit")
    prefix = "packages/allthecontext/src/allthecontext/"
    tracked = set(
        git(root, "ls-tree", "-r", "--name-only", "-z", commit, "--", prefix).decode().split("\0")
    ) - {""}
    for count, path in enumerate((root / prefix).rglob("*")):
        require(count < 10_000, "oversized covered source inventory")
        relative = path.relative_to(root).as_posix()
        information = path.lstat()
        require(
            not path.is_symlink() and not getattr(information, "st_file_attributes", 0) & 0x400,
            "linked covered source input",
        )
        if path.is_dir() or ("__pycache__" in path.parts and path.suffix == ".pyc"):
            continue
        require(
            relative in tracked and stat.S_ISREG(information.st_mode), "uncovered checkout input"
        )
    require(
        pilot.profile(git(root, "show", f"{BASE}:scripts/build_desktop.py"))
        == pilot.profile(git(root, "show", f"{commit}:scripts/build_desktop.py")),
        "packaging graph policy changed",
    )
    return {
        "repository": pilot.REPOSITORY,
        "run_id": int(environment["GITHUB_RUN_ID"]),
        "run_attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
        "head": commit,
        "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "pr_head": pr["head"]["sha"],
    }


def expected_receipt(root: Path, identity: dict, tools: dict, locks: dict) -> dict:
    verifier = native_verifier()
    require(
        str(Path(sys.prefix).resolve()).casefold().replace("\\", "/")
        == packaging_venv(dict(os.environ)).casefold(),
        "packaging venv mismatch",
    )
    return {
        "schema_version": 1,
        "producer_job": JOB,
        **identity,
        "profile": PROFILE,
        "platform": "windows",
        "architecture": "x86_64",
        "packaging_venv": packaging_venv(dict(os.environ)),
        "toolchain": tools,
        "locks": locks,
        "version": verifier.project_version(root),
    }


def artifact_name(identity: dict) -> str:
    return f"windows-stock-witness-{identity['run_id']}-{identity['run_attempt']}"


def api_request(route: str):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{pilot.REPOSITORY}/{route}",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    return urllib.request.build_opener(pilot.NoRedirect).open(request, timeout=20)


def provider_bytes(route: str, limit: int) -> bytes:
    """Follow one recognized provider storage redirect without forwarding credentials."""
    try:
        response = api_request(route)
    except urllib.error.HTTPError as redirect:
        require(redirect.code == 302, "provider evidence unavailable")
        location = redirect.headers.get("Location", "")
        parsed = urllib.parse.urlsplit(location)
        require(
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.endswith(".blob.core.windows.net")
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment,
            "unrecognized provider redirect",
        )
        response = urllib.request.build_opener(pilot.NoRedirect).open(location, timeout=20)
    with response:
        raw = response.read(limit + 1)
    require(len(raw) <= limit, "oversized provider evidence")
    return raw


def api_json(route: str) -> dict:
    with api_request(route) as response:
        return bounded_json(response.read(1_000_001), 1_000_000)


def producer_job(run: dict, jobs: dict, identity: dict) -> dict | None:
    """Require the exact attempt endpoint and one immutable, successful producer."""
    provider_head = run.get("head_sha")
    require(
        run.get("id") == identity["run_id"]
        and run.get("run_attempt") == identity["run_attempt"]
        and run.get("event") == "pull_request"
        and run.get("path") == ".github/workflows/ci.yml"
        and provider_head in {identity["head"], identity["pr_head"]}
        and run.get("repository", {}).get("id") == pilot.REPOSITORY_ID
        and run.get("repository", {}).get("full_name") == pilot.REPOSITORY
        and run.get("head_repository", {}).get("id") == pilot.REPOSITORY_ID,
        "wrong provider run",
    )
    rows = jobs.get("jobs")
    require(
        isinstance(rows, list)
        and type(jobs.get("total_count")) is int
        and jobs["total_count"] == len(rows) <= 100,
        "incomplete provider jobs",
    )
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == JOB]
    require(len(matches) <= 1, "ambiguous producer job")
    if not matches:
        return None
    job = matches[0]
    job_id = job.get("id")
    require(
        type(job_id) is int
        and job_id > 0
        and job.get("run_id") == identity["run_id"]
        and job.get("head_sha") == provider_head
        and job.get("url")
        == f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/jobs/{job_id}"
        and job.get("run_url")
        == f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/runs/{identity['run_id']}",
        "wrong producer identity",
    )
    if job.get("status") != "completed":
        require(job.get("status") in {"queued", "in_progress", "waiting"}, "unknown producer state")
        return None
    require(job.get("conclusion") == "success", "unsuccessful producer")
    steps = job.get("steps")
    require(isinstance(steps, list), "missing producer steps")
    numbers = [step.get("number") for step in steps if isinstance(step, dict)]
    require(
        len(numbers) == len(steps)
        and all(type(n) is int and n > 0 for n in numbers)
        and len(set(numbers)) == len(numbers),
        "invalid provider step numbers",
    )
    selected = []
    for name in (UPLOAD, MARKER):
        matching = [step for step in steps if step.get("name") == name]
        require(
            len(matching) == 1
            and matching[0].get("status") == "completed"
            and matching[0].get("conclusion") == "success",
            "unsuccessful producer step",
        )
        selected.append(matching[0])
    require(selected[0]["number"] < selected[1]["number"], "invalid producer step order")
    return {"id": job_id, "step": selected[1]["number"], "provider_head": provider_head}


def validate_artifact(
    artifact: dict, identity: dict, job: dict, artifact_id: int, digest: str
) -> None:
    workflow = artifact.get("workflow_run", {})
    require(
        artifact.get("id") == artifact_id
        and artifact.get("expired") is False
        and artifact.get("name") == artifact_name(identity)
        and artifact.get("digest") == f"sha256:{digest}"
        and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= MAX_RECEIPT + 4096
        and workflow.get("id") == identity["run_id"]
        and workflow.get("head_sha") == job["provider_head"]
        and workflow.get("repository_id") == pilot.REPOSITORY_ID
        and workflow.get("head_repository_id") == pilot.REPOSITORY_ID,
        "wrong witness artifact",
    )


def receipt_from_archive(raw: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(len(rows) == 1, "invalid witness inventory")
        row = rows[0]
        require(
            row.filename == RECEIPT
            and 0 < row.file_size <= MAX_RECEIPT
            and not row.is_dir()
            and not row.flag_bits & 1
            and not stat.S_ISLNK(row.external_attr >> 16)
            and row.compress_type in (0, 8),
            "invalid witness archive member",
        )
        return bounded_json(archive.read(row))


def validate_receipt(value: dict, expected: dict) -> tuple:
    require(
        set(value) == {*expected, "components"}
        and provenance.canonical_json({key: value[key] for key in expected})
        == provenance.canonical_json(expected),
        "wrong stock witness receipt",
    )
    return provenance._validate_components(value["components"])


def acquire(expected: dict) -> tuple:
    identity = expected
    run, attempt = identity["run_id"], identity["run_attempt"]
    deadline = time.monotonic() + 150
    while True:
        job = producer_job(
            api_json(f"actions/runs/{run}/attempts/{attempt}"),
            api_json(f"actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"),
            identity,
        )
        if job is not None:
            break
        require(time.monotonic() < deadline, "stock witness unavailable")
        time.sleep(2)
    # Never read full job logs or search arbitrary step output for identity.
    log = provider_bytes(f"actions/jobs/{job['id']}/steps/{job['step']}/logs", 64_000)
    matches = MARKER_PATTERN.findall(log.decode("utf-8").replace("\r\n", "\n"))
    require(len(matches) == 1, "missing or ambiguous exact-step marker")
    artifact_id, digest = int(matches[0][0]), matches[0][1]
    artifact = api_json(f"actions/artifacts/{artifact_id}")
    validate_artifact(artifact, identity, job, artifact_id, digest)
    raw = provider_bytes(f"actions/artifacts/{artifact_id}/zip", MAX_RECEIPT + 4096)
    require(
        len(raw) == artifact["size_in_bytes"] and hashlib.sha256(raw).hexdigest() == digest,
        "witness archive digest mismatch",
    )
    return validate_receipt(receipt_from_archive(raw), expected)


def donor_runner(root: Path, commit: str, tools: dict, locks: dict):
    folder = pilot.download_directory()
    deadline = time.monotonic() + 125
    while not (folder / "complete.json").is_file():
        require(folder.is_dir() and time.monotonic() < deadline, "donor unavailable")
        time.sleep(0.1)
    require(
        bounded_json((folder / "complete.json").read_bytes()) == {"available": True},
        "donor unavailable",
    )
    paths = pilot.extract_donor(folder / "donor.zip", folder / "ordinary-verified")
    doc = provenance.load_provenance(folder / "ordinary-verified" / Path(pilot.MANIFEST).name)
    require(
        doc["contract"]["locks"] == locks and doc["contract"]["toolchain"] == tools,
        "donor inputs changed",
    )
    return component_runner(root, commit, paths, witness_profile=True)


def same_components(left: tuple, right: tuple) -> bool:
    # ComponentDigest includes role and both exact names as well as full-file size/SHA.
    return left == right


def provisional_checks(root: Path, candidate: Path) -> int:
    verifier = native_verifier()
    command = [
        sys.executable,
        str(root / "scripts/windows_ci_package_checks.py"),
        "--artifact-root",
        str(candidate),
        "--overlap-first-run",
    ]
    environment = verifier.reproducible_environment(config_root=candidate / "checks")
    # Provisional success must not leak into the normal publication step's outputs.
    environment.pop("GITHUB_OUTPUT", None)
    return subprocess.run(command, cwd=root, env=environment, check=False).returncode


def promote(candidate: Path, build: Path, dist: Path, output: Path) -> None:
    targets = [
        (candidate / "build/desktop", build),
        (candidate / "dist/desktop", dist),
        (candidate / "dist/native-build-provenance", output),
        (candidate / "dist/platform", dist.parent / "platform"),
        (candidate / "dist/release", dist.parent / "release"),
    ]
    # Preflight every destination before copying any provisional bytes.
    for source, destination in targets:
        require(source.is_dir(), "missing provisional output")
        require(not destination.exists(), "publication destination already exists")
    for source, destination in targets:
        shutil.copytree(source, destination)


def try_ordinary(
    root: Path,
    commit: str,
    *,
    version: str,
    build: Path,
    dist: Path,
    output: Path,
    identity: dict,
    tools: dict,
    locks: dict,
) -> tuple[dict, dict] | None:
    """Drain all provisional work before deciding stock fallback or publication."""
    verifier = native_verifier()
    with tempfile.TemporaryDirectory(prefix="atc-ci-provisional-") as temporary:
        candidate = Path(temporary)
        try:
            expected = expected_receipt(root, identity, tools, locks)
            runner = donor_runner(root, commit, tools, locks)
            payload, paths = verifier.generate_provenance(
                repository_root=root,
                version=version,
                source_commit=commit,
                final_build_root=candidate / "build/desktop",
                final_dist_root=candidate / "dist/desktop",
                output_dir=candidate / "dist/native-build-provenance",
                toolchain=tools,
                locks=locks,
                build_runner=runner,
                parallel_windows=True,
            )
        except Exception:
            print("Windows ordinary witness: complete stock fallback (provisional miss)")
            return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            smoke = pool.submit(provisional_checks, root, candidate)
            witness = pool.submit(acquire, expected)
            try:
                components = witness.result()
                observed = provenance.collect_snapshot(paths, label="provisional")
                require(same_components(components, observed.components), "stock role mismatch")
                # Provenance validated the isolated A/B join; recheck its exact rows too.
                require(
                    same_components(
                        components, provenance._validate_components(payload["components"])
                    ),
                    "retained arm mismatch",
                )
            except Exception:
                # The context manager waits for all actual lifecycle work before disposal.
                print("Windows ordinary witness: complete stock fallback (witness miss)")
                return None
            # A proved candidate's real lifecycle failure remains a required failure.
            require(smoke.result() == 0, "proved Windows candidate lifecycle failed")
        promote(candidate, build, dist, output)
    final_paths = verifier.build_desktop.component_paths(build_root=build, dist_root=dist)
    provenance.verify_provenance(
        output / provenance.PROVENANCE_FILE_NAME, final_paths, version=version, source_commit=commit
    )
    return payload, final_paths


def produce(root: Path, receipt_path: Path) -> bool:
    """An independent full stock graph: no donor or retained inputs on this route."""
    verifier = native_verifier()
    commit = verifier.checked_out_commit(root)
    try:
        identity = admit_source(root, commit, producer=True)
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        print("Windows stock witness: ordinary source not qualified")
        return False
    tools, locks = verifier.current_toolchain(), verifier.lock_digests(root)
    expected = expected_receipt(root, identity, tools, locks)
    with tempfile.TemporaryDirectory(prefix="atc-ci-stock-witness-") as temporary:
        work = Path(temporary)
        snapshot = verifier._run_clean_build(
            root,
            work / "build",
            work / "dist",
            version=expected["version"],
            source_commit=commit,
            parallel_windows=True,
            ci_witness=True,
        )
    payload = {
        **expected,
        "components": [provenance._component_descriptor(c) for c in snapshot.components],
    }
    validate_receipt(payload, expected)
    raw = provenance.canonical_json(payload)
    require(len(raw) <= MAX_RECEIPT, "oversized stock receipt")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with receipt_path.open("xb") as stream:
        stream.write(raw)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    available = produce(Path(__file__).resolve().parents[1], args.receipt)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"available={'true' if available else 'false'}\n")


if __name__ == "__main__":
    main()

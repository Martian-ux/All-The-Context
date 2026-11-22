"""Bounded ordinary-PR stock witness and complete cold Windows fallback.

Only provider-authenticated, completed producer steps can authorize a join.
Receipts contain hashes of full files, never normalized archive comparisons.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib
import io
import json
import os
import re
import shutil
import socket
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
PRODUCER_WAIT = 150
RECEIPT_WAIT = 8
IO_TIMEOUT = 5
PROVIDER_RETRIES = 4
TRANSIENT_HTTP = {500, 502, 503, 504}
ACQUISITION_STAGES = ("attempt", "jobs", "marker", "artifact", "archive", "receipt")
MISS_CODES = (
    frozenset(
        f"{stage}-{reason}"
        for stage in ACQUISITION_STAGES
        for reason in (
            "deadline",
            "authorization",
            "http",
            "transport",
            "invalid",
            "indexing-exhausted",
            "server-exhausted",
            "transport-exhausted",
        )
    )
    | {"acquisition-invalid", "archive-size-or-sha256"}
    | frozenset(
        f"{stage}-{role}-{reason}"
        for stage in ("candidate", "retained")
        for role, _, _ in provenance.COMPONENTS
        for reason in ("size", "sha256", "size-sha256", "identity")
    )
    | frozenset(
        f"{stage}-{reason}"
        for stage in ("candidate", "retained")
        for reason in ("invalid", "inventory")
    )
)
# The qualification policy itself is reviewed with this module. All other
# admitted CI/native implementation inputs are fixed Git blobs, not a broad
# permission to change the packaging recipe on an ordinary PR.
QUALIFIED_INPUT_BLOBS = {
    # Inputs from the separately reviewed protected-main -> prior CI overlay.
    # Contract tests retain historical mappings; reviewed follow-up pins bind
    # the scheduler weight and early donor preparation below.
    "bench/retrieval_m3_current_candidate.py": "c2dae5e8b9eb3914ea691b6fab079d2372e9389a",
    "scripts/check_test_collection.py": "01de4bf97745a53edddacdf9ef7fa2011ad56a8e",
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
    ".github/workflows/ci.yml": "5b6c9715c25f3a4392ca9984a5de2116f1cc5d36",
    "scripts/build_desktop.py": "233b11b625c2f75aa17609cd8f7500d7d6930acf",
    "scripts/verify_reproducible_build.py": "91a6c93381f1245a69e8c94d1f6d8fbf98c7b8d4",
    "scripts/windows_ci_components.py": "5aa48eb7e2c0a4ff054d73b3a2ef6437a385e4c6",
    "scripts/windows_ci_package_checks.py": "d2bbb50c719b19058faf4aef204a6d0a1235bf4f",
    "scripts/windows_ci_pilot.py": "66786c2ee977f1695c815e7d39957a3d2d3bc55e",
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
# SHA-256 of the reviewed inventory, with only existing shipped Python,
# tests/docs content and this policy's own blob omitted. Names and modes remain
# covered. Generated offline from the reviewed source, never from a PR claim.
ROLLING_INVENTORY_SHA256 = "109411f333215767400f4d8cb66fe0eba30ca204049ed496132c5e65ff539227"
POLICY = "scripts/windows_ci_witness.py"


def ordinary_name(name: str) -> tuple[bool, bool]:
    safe = re.fullmatch(r"[A-Za-z0-9_./-]+", name) is not None
    product = (
        safe
        and name.startswith("packages/allthecontext/src/allthecontext/")
        and name.endswith(".py")
    )
    supporting = safe and (
        (name.startswith("tests/") and name.endswith(".py"))
        or (name.startswith("docs/") and name.endswith(".md"))
    )
    return product, supporting


def rolling_inventory(files: dict) -> str:
    rows = []
    for name, (mode, kind, blob) in sorted(files.items()):
        text = name.decode("utf-8")
        flexible = any(ordinary_name(text)) or text == POLICY
        rows.append([text, mode.decode(), kind.decode(), "*" if flexible else blob.decode()])
    return hashlib.sha256(provenance.canonical_json({"files": rows})).hexdigest()


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


def _git_tree_files(root: Path, revision: str) -> dict[bytes, tuple[bytes, bytes, bytes]]:
    # Keep Git's configured quoting, just as the original per-path ls-tree did.
    # Delta names are unquoted: a quoted tree name must still fail exact lookup.
    records = git(root, "ls-tree", "-r", "--full-tree", revision).split(b"\n")
    require(records[-1] == b"", "invalid source tree")
    files = {}
    for record in records[:-1]:
        header, name = record.split(b"\t", 1)
        mode, kind, object_id = header.split(b" ")
        require(name not in files, "duplicate source tree path")
        files[name] = (mode, kind, object_id)
    return files


def admit_source(root: Path, commit: str, *, producer: bool = False) -> dict:
    environment = dict(os.environ)
    event = bounded_json(Path(environment["GITHUB_EVENT_PATH"]).read_bytes(), 1_000_000)
    context = dict(environment, GITHUB_JOB="desktop-package")
    # Check the actual event against protected main; never substitute a private
    # implementation parent, which is not even available at provider depth two.
    pr = event.get("pull_request")
    require(isinstance(pr, dict), "not an ordinary PR")
    base = pr.get("base")
    require(isinstance(base, dict), "unreviewed ordinary base")
    event_base = base.get("sha")
    require(
        isinstance(event_base, str) and pilot.COMMIT_SHA.fullmatch(event_base),
        "unreviewed ordinary base",
    )
    rolling = event_base != BASE
    # Reuse the fixed context checks without changing the fixed pilot authority.
    checked_event = {**event, "pull_request": {**pr, "base": {**base, "sha": pilot.SOURCE}}}
    require(pilot.eligible_context(checked_event, context), "unsupported ordinary context")
    if rolling:
        for repo in (event["repository"], base["repo"], pr["head"]["repo"]):
            require(
                type(repo.get("id")) is int
                and repo["id"] == pilot.REPOSITORY_ID
                and repo.get("fork") is False,
                "unsupported ordinary context",
            )
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
    candidate_files = _git_tree_files(root, commit)
    for name, blob in QUALIFIED_INPUT_BLOBS.items():
        require(
            candidate_files.get(name.encode()) == (b"100644", b"blob", blob.encode()),
            "unreviewed CI/native input",
        )
    parents = git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()
    require(parents == [commit, event_base, pr["head"]["sha"]], "unsupported merge parents")
    base_files = _git_tree_files(root, event_base)
    if rolling:
        require(
            base_files.get(POLICY.encode()) == candidate_files.get(POLICY.encode())
            and base_files.get(POLICY.encode(), ())[:2] == (b"100644", b"blob"),
            "changed rolling policy",
        )
        for files in (base_files, candidate_files):
            require(
                rolling_inventory(files) == ROLLING_INVENTORY_SHA256, "unreviewed rolling inventory"
            )
            for name, blob in QUALIFIED_INPUT_BLOBS.items():
                require(
                    files.get(name.encode()) == (b"100644", b"blob", blob.encode()),
                    "unreviewed CI/native input",
                )
    rows = (
        git(root, "diff", "--name-status", "--no-renames", "-z", event_base, commit)
        .decode()
        .split("\0")
    )
    require(rows[-1] == "" and len(rows) % 2 == 1, "invalid source delta")
    ordinary = False
    for status, name in zip(rows[:-1:2], rows[1:-1:2], strict=True):
        require(status in {"A", "M"}, "unsupported source delta")
        for files in (base_files, candidate_files) if status == "M" else (candidate_files,):
            entry = files.get(name.encode())
            require(
                entry is not None and entry[:2] == (b"100644", b"blob"),
                "unsupported ordinary file",
            )
        if not rolling and (name in IMPLEMENTATION_FILES or name in QUALIFIED_INPUT_BLOBS):
            continue
        if rolling:
            require(
                status == "M"
                and base_files.get(name.encode())[:2] == candidate_files.get(name.encode())[:2],
                "unsupported source delta",
            )
        product, supporting = (
            ordinary_name(name)
            if rolling
            else (
                name.startswith("packages/allthecontext/src/allthecontext/")
                and name.endswith(".py"),
                (name.startswith("tests/") and name.endswith(".py"))
                or (name.startswith("docs/") and name.endswith(".md")),
            )
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
    if rolling:
        for directory in ("scripts", "apps/dashboard/src", "apps/dashboard/public"):
            for count, path in enumerate((root / directory).rglob("*")):
                require(count < 10_000, "oversized recipe input inventory")
                info = path.lstat()
                require(
                    not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "linked recipe input",
                )
                if path.is_dir() or ("__pycache__" in path.parts and path.suffix == ".pyc"):
                    continue
                require(
                    stat.S_ISREG(info.st_mode)
                    and path.relative_to(root).as_posix().encode() in candidate_files,
                    "uncovered recipe checkout input",
                )
    require(
        pilot.profile(git(root, "show", f"{event_base}:scripts/build_desktop.py"))
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


class WitnessMiss(ValueError):
    """A bounded diagnostic code, with no provider data or credentials."""

    def __init__(self, code: str):
        super().__init__(code if code in MISS_CODES else "acquisition-invalid")


def api_request(route: str, *, timeout: float):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{pilot.REPOSITORY}/{route}",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    return urllib.request.build_opener(pilot.NoRedirect).open(request, timeout=timeout)


def provider_bytes(route: str, limit: int, *, deadline: float) -> bytes:
    """Follow one recognized provider storage redirect without forwarding credentials."""
    try:
        response = api_request(route, timeout=io_timeout(deadline))
    except urllib.error.HTTPError as redirect:
        if redirect.code != 302:
            raise
        with redirect:
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
        try:
            response = urllib.request.build_opener(pilot.NoRedirect).open(
                location, timeout=io_timeout(deadline)
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                # Only the bound API endpoint can be temporarily unindexed.
                # A storage miss must not spend the provider indexing budget.
                with error:
                    raise ValueError("storage evidence unavailable") from None
            raise
    with response:
        raw = read_provider(response, limit, deadline=deadline)
    return raw


def api_json(route: str, *, deadline: float) -> dict:
    with api_request(route, timeout=io_timeout(deadline)) as response:
        return bounded_json(read_provider(response, 1_000_000, deadline=deadline), 1_000_000)


def read_provider(response, limit: int, *, deadline: float) -> bytes:
    # read1 performs at most one underlying socket read. Recheck between reads
    # so a trickle cannot extend read-all indefinitely; each read retains the
    # short socket timeout established by the owned request.
    raw = bytearray()
    while len(raw) <= limit:
        io_timeout(deadline)
        chunk = response.read1(min(65_536, limit + 1 - len(raw)))
        io_timeout(deadline)
        if not chunk:
            return bytes(raw)
        raw.extend(chunk)
    raise ValueError("oversized provider evidence")


def validate_attempt(run: dict, identity: dict) -> str:
    """Bind the attempt before asking for any producer evidence."""
    provider_head = run.get("head_sha")
    require(
        type(run.get("id")) is int
        and run["id"] == identity["run_id"]
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] == identity["run_attempt"]
        and run.get("event") == "pull_request"
        and run.get("path") == ".github/workflows/ci.yml"
        and provider_head in {identity["head"], identity["pr_head"]}
        and run.get("repository", {}).get("id") == pilot.REPOSITORY_ID
        and run.get("repository", {}).get("full_name") == pilot.REPOSITORY
        and run.get("head_repository", {}).get("id") == pilot.REPOSITORY_ID,
        "wrong provider run",
    )
    return provider_head


def producer_job(
    jobs: dict,
    identity: dict,
    provider_head: str,
    *,
    job_name: str = JOB,
    upload_name: str = UPLOAD,
    marker_name: str = MARKER,
) -> dict | None:
    """Find exactly one successful producer in the validated attempt."""
    rows = jobs.get("jobs")
    require(
        isinstance(rows, list)
        and type(jobs.get("total_count")) is int
        and jobs["total_count"] == len(rows) <= 100,
        "incomplete provider jobs",
    )
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == job_name]
    require(len(matches) <= 1, "ambiguous producer job")
    if not matches:
        return None
    job = matches[0]
    job_id = job.get("id")
    require(
        type(job_id) is int
        and job_id > 0
        and type(job.get("run_id")) is int
        and job["run_id"] == identity["run_id"]
        and type(job.get("run_attempt")) is int
        and job["run_attempt"] == identity["run_attempt"]
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
    for name in (upload_name, marker_name):
        matching = [step for step in steps if step.get("name") == name]
        require(
            len(matching) == 1
            and matching[0].get("status") == "completed"
            and matching[0].get("conclusion") == "success",
            "unsuccessful producer step",
        )
        selected.append(matching[0])
    require(selected[0]["number"] < selected[1]["number"], "invalid producer step order")
    return {
        "id": job_id,
        "step": selected[1]["number"],
        "upload_step": selected[0]["number"],
        "provider_head": provider_head,
    }


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


def io_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return min(IO_TIMEOUT, remaining)


def transient_transport(error: Exception) -> bool:
    if isinstance(error, urllib.error.URLError):
        error = error.reason
    return isinstance(
        error,
        (TimeoutError, ConnectionResetError, ConnectionAbortedError, http.client.IncompleteRead),
    ) or (isinstance(error, socket.gaierror) and error.errno == socket.EAI_AGAIN)


def acquire(expected: dict) -> tuple:
    attempt_route = f"actions/runs/{expected['run_id']}/attempts/{expected['run_attempt']}"
    deadline = time.monotonic() + PRODUCER_WAIT
    retries = PROVIDER_RETRIES
    stage = "attempt"

    def wait(delay):
        remaining = deadline - time.monotonic()
        if remaining <= delay:
            raise WitnessMiss(f"{stage}-deadline")
        time.sleep(delay)

    def fetch(operation, *arguments, indexing=False):
        nonlocal retries
        while time.monotonic() < deadline:
            try:
                value = operation(*arguments, deadline=deadline)
            except urllib.error.HTTPError as error:
                with error:
                    status = error.code
                if status in (401, 403):
                    raise WitnessMiss(f"{stage}-authorization") from None
                if status not in TRANSIENT_HTTP and (status != 404 or not indexing):
                    raise WitnessMiss(f"{stage}-http") from None
                outcome = "indexing" if status == 404 else "server"
            except (OSError, http.client.HTTPException) as error:
                if not transient_transport(error):
                    raise WitnessMiss(f"{stage}-transport") from None
                outcome = "transport"
            else:
                if time.monotonic() < deadline:
                    return value
                break
            if retries == 0:
                raise WitnessMiss(f"{stage}-{outcome}-exhausted")
            retries -= 1
            wait(1)
        raise WitnessMiss(f"{stage}-deadline")

    try:
        provider_head = validate_attempt(fetch(api_json, attempt_route), expected)
        stage = "jobs"
        while True:
            # A missing endpoint can reflect indexing only after verifying the attempt.
            jobs = fetch(api_json, f"{attempt_route}/jobs?per_page=100", indexing=True)
            job = producer_job(jobs, expected, provider_head)
            if job is not None:
                break
            wait(2)
        deadline = min(deadline, time.monotonic() + RECEIPT_WAIT)
        stage = "marker"
        # Never search the complete job log or other steps for upload identity.
        log = fetch(
            provider_bytes,
            f"actions/jobs/{job['id']}/steps/{job['step']}/logs",
            64_000,
            indexing=True,
        )
        matches = MARKER_PATTERN.findall(log.decode("utf-8").replace("\r\n", "\n"))
        require(len(matches) == 1, "missing or ambiguous exact-step marker")
        artifact_id, digest = int(matches[0][0]), matches[0][1]
        stage = "artifact"
        artifact_route = f"actions/artifacts/{artifact_id}"
        artifact = fetch(api_json, artifact_route, indexing=True)
        validate_artifact(artifact, expected, job, artifact_id, digest)
        stage = "archive"
        raw = fetch(provider_bytes, f"{artifact_route}/zip", MAX_RECEIPT + 4096, indexing=True)
        if len(raw) != artifact["size_in_bytes"] or hashlib.sha256(raw).hexdigest() != digest:
            raise WitnessMiss("archive-size-or-sha256")
        stage = "receipt"
        return validate_receipt(receipt_from_archive(raw), expected)
    except WitnessMiss:
        raise
    except Exception:
        raise WitnessMiss(f"{stage}-invalid") from None


DONOR_JOB = "Desktop artifact - windows-latest"
DONOR_UPLOAD = "Upload desktop package"
DONOR_MARKER = "Record Windows main donor upload identity"
DONOR_PATTERN = re.compile(
    r"^(?:\d{4}-\d\d-\d\dT[0-9:.]+Z )?"
    r"ATC_WINDOWS_DONOR_V1 artifact_id=([1-9][0-9]{0,19}) digest=sha256:([0-9a-f]{64})$",
    re.MULTILINE,
)


def protected_donor(root: Path, commit: str) -> pilot.RollingDonor:
    """One bounded exact-base lookup; no ancestor search or indexing wait."""
    admit_source(root, commit)
    event = bounded_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes(), 1_000_000)
    base = event["pull_request"]["base"]["sha"]
    require(base != BASE, "fixed bootstrap donor required")
    deadline = time.monotonic() + 20
    workflow = api_json("actions/workflows/ci.yml", deadline=deadline)
    workflow_id = workflow.get("id")
    require(
        type(workflow_id) is int
        and workflow_id > 0
        and workflow.get("path") == ".github/workflows/ci.yml"
        and workflow.get("state") == "active"
        and workflow.get("url")
        == f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/workflows/{workflow_id}",
        "wrong donor workflow",
    )
    runs = api_json(
        f"actions/workflows/{workflow_id}/runs?event=push&branch=main&head_sha={base}&per_page=100",
        deadline=deadline,
    )
    rows = runs.get("workflow_runs")
    require(
        isinstance(rows, list)
        and type(runs.get("total_count")) is int
        and runs["total_count"] == len(rows) <= 100,
        "incomplete donor runs",
    )
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("head_sha") == base
        and row.get("status") == "completed"
        and row.get("conclusion") == "success"
    ]
    require(len(matches) == 1, "absent or ambiguous donor run")
    selected = matches[0]
    run_id, attempt = selected.get("id"), selected.get("run_attempt")
    require(
        type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
        "invalid donor attempt",
    )
    route = f"actions/runs/{run_id}/attempts/{attempt}"
    run = api_json(route, deadline=deadline)
    for value in (selected, run):
        require(
            value.get("id") == run_id
            and value.get("run_attempt") == attempt
            and value.get("workflow_id") == workflow_id
            and value.get("path") == ".github/workflows/ci.yml"
            and value.get("event") == "push"
            and value.get("head_branch") == "main"
            and value.get("head_sha") == base
            and value.get("status") == "completed"
            and value.get("conclusion") == "success"
            and value.get("url")
            == f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/runs/{run_id}"
            and all(
                value.get(key, {}).get("id") == pilot.REPOSITORY_ID
                and value.get(key, {}).get("full_name") == pilot.REPOSITORY
                for key in ("repository", "head_repository")
            ),
            "wrong donor run",
        )
    job = producer_job(
        api_json(f"{route}/jobs?per_page=100", deadline=deadline),
        {"run_id": run_id, "run_attempt": attempt},
        base,
        job_name=DONOR_JOB,
        upload_name=DONOR_UPLOAD,
        marker_name=DONOR_MARKER,
    )
    require(job is not None, "missing successful donor job")
    log = provider_bytes(
        f"actions/jobs/{job['id']}/steps/{job['step']}/logs", 64_000, deadline=deadline
    )
    matches = DONOR_PATTERN.findall(log.decode("utf-8").replace("\r\n", "\n"))
    require(len(matches) == 1, "missing exact donor marker")
    artifact_id, digest = int(matches[0][0]), matches[0][1]
    artifact = api_json(f"actions/artifacts/{artifact_id}", deadline=deadline)
    origin = artifact.get("workflow_run", {})
    expiry = artifact.get("expires_at")
    pilot.require_future_expiry(expiry)
    require(
        artifact.get("id") == artifact_id
        and artifact.get("name") == "all-the-context-windows-x86_64"
        and artifact.get("expired") is False
        and artifact.get("digest") == f"sha256:{digest}"
        and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= pilot.MAX_ROLLING_ARCHIVE
        and artifact.get("url")
        == f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/artifacts/{artifact_id}"
        and artifact.get("archive_download_url")
        == f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/artifacts/{artifact_id}/zip"
        and origin.get("id") == run_id
        and origin.get("head_sha") == base
        and origin.get("head_branch") == "main"
        and origin.get("repository_id") == pilot.REPOSITORY_ID
        and origin.get("head_repository_id") == pilot.REPOSITORY_ID,
        "wrong donor artifact",
    )
    version = pilot.git(root, "show", f"{base}:pyproject.toml")
    import tomllib

    return pilot.RollingDonor(
        source=base,
        workflow_id=workflow_id,
        run_id=run_id,
        attempt=attempt,
        job_id=job["id"],
        upload_step=job["upload_step"],
        marker_step=job["step"],
        artifact_id=artifact_id,
        size=artifact["size_in_bytes"],
        digest=digest,
        expires_at=expiry,
        version=tomllib.loads(version.decode())["project"]["version"],
    )


def donor_runner(root: Path, commit: str, tools: dict, locks: dict):
    paths, descriptor = pilot.prepared_donor_contract(tools, locks)
    if descriptor is None:
        return component_runner(root, commit, paths, witness_profile=True)
    return component_runner(root, commit, paths, witness_profile=True, donor_descriptor=descriptor)


def require_components(witnessed: tuple, observed: tuple, stage: str) -> None:
    # Equality includes both names, the role and exact full-file size and SHA.
    if witnessed == observed:
        return
    for (role, _, _), expected, actual in zip(
        provenance.COMPONENTS, witnessed, observed, strict=False
    ):
        if expected != actual:
            fields = "-".join(
                field
                for field in ("size", "sha256")
                if getattr(expected, field) != getattr(actual, field)
            )
            raise WitnessMiss(f"{stage}-{role}-{fields or 'identity'}")
    raise WitnessMiss(f"{stage}-inventory")


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
            reason = "acquisition-invalid"
            try:
                components = witness.result()
                reason = "candidate-invalid"
                observed = provenance.collect_snapshot(paths, label="provisional")
                require_components(components, observed.components, "candidate")
                # Provenance validated the isolated A/B join; recheck its exact rows too.
                reason = "retained-invalid"
                require_components(
                    components, provenance._validate_components(payload["components"]), "retained"
                )
            except WitnessMiss as miss:
                # Reapply the allowlist at the output boundary as well.
                reason = str(WitnessMiss(str(miss)))
                print(f"Windows ordinary witness: complete stock fallback ({reason})")
                return None
            except Exception:
                # The context manager waits for all actual lifecycle work before disposal.
                print(f"Windows ordinary witness: complete stock fallback ({reason})")
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

"""Exact provider custody, full-file joins and ordinary Windows cold fallback."""

from __future__ import annotations

import copy
import hashlib
import http.client
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts import native_build_provenance as provenance
from scripts import verify_reproducible_build as verifier
from scripts import windows_ci_components as components
from scripts import windows_ci_package_checks as checks
from scripts import windows_ci_pilot as pilot
from scripts import windows_ci_witness as witness

ROOT = Path(__file__).resolve().parents[2]
TOOLS = {"python": "3.12.10", "pyinstaller": "6.21.0", "uv": "0.11.32"}
LOCKS = {name: {"sha256": "d" * 64} for name in provenance.LOCK_FILE_NAMES}
IDENTITY = {
    "repository": pilot.REPOSITORY,
    "run_id": 42,
    "run_attempt": 2,
    "head": "a" * 40,
    "tree": "b" * 40,
    "pr_head": "c" * 40,
}


def expected():
    return {
        "schema_version": 1,
        "producer_job": witness.JOB,
        **IDENTITY,
        "profile": witness.PROFILE,
        "platform": "windows",
        "architecture": "x86_64",
        "packaging_venv": "C:/atc-ci-package-42-2",
        "toolchain": TOOLS,
        "locks": LOCKS,
        "version": "0.1.0-beta.8",
    }


def rows():
    return [
        {
            "role": role,
            "filename": filename,
            "build_filename": build_filename,
            "sha256": hashlib.sha256(role.encode()).hexdigest(),
            "size": len(role),
        }
        for role, filename, build_filename in provenance.COMPONENTS
    ]


def archive(value):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(witness.RECEIPT, provenance.canonical_json(value))
    return stream.getvalue()


@pytest.fixture
def provider(monkeypatch):
    receipt = {**expected(), "components": rows()}
    raw = archive(receipt)
    digest = hashlib.sha256(raw).hexdigest()
    run = {
        "id": 42,
        "run_attempt": 2,
        "head_sha": IDENTITY["pr_head"],
        "event": "pull_request",
        "path": ".github/workflows/ci.yml",
        "repository": {"id": pilot.REPOSITORY_ID, "full_name": pilot.REPOSITORY},
        "head_repository": {"id": pilot.REPOSITORY_ID},
    }
    job = {
        "id": 73,
        "run_id": 42,
        "run_attempt": 2,
        "head_sha": IDENTITY["pr_head"],
        "name": witness.JOB,
        "status": "completed",
        "conclusion": "success",
        "url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/jobs/73",
        "run_url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/runs/42",
        "steps": [
            {"name": witness.UPLOAD, "number": 8, "status": "completed", "conclusion": "success"},
            {"name": witness.MARKER, "number": 9, "status": "completed", "conclusion": "success"},
        ],
    }
    jobs = {"total_count": 1, "jobs": [job]}
    artifact = {
        "id": 99,
        "name": witness.artifact_name(IDENTITY),
        "expired": False,
        "size_in_bytes": len(raw),
        "digest": f"sha256:{digest}",
        "workflow_run": {
            "id": 42,
            "head_sha": IDENTITY["pr_head"],
            "repository_id": pilot.REPOSITORY_ID,
            "head_repository_id": pilot.REPOSITORY_ID,
        },
    }
    log = f"2026-11-21T00:00:00.123Z ATC_WINDOWS_STOCK_V1 artifact_id=99 digest=sha256:{digest}\n"
    data = {
        "run": run,
        "jobs": jobs,
        "job": job,
        "artifact": artifact,
        "raw": raw,
        "log": log.encode(),
        "receipt": receipt,
        "calls": [],
        "provider_bytes": witness.provider_bytes,
    }

    def api(route, *, deadline):
        data["calls"].append(route)
        return {
            "actions/runs/42/attempts/2": run,
            "actions/runs/42/attempts/2/jobs?per_page=100": jobs,
            "actions/artifacts/99": artifact,
        }[route]

    def fetch(route, _limit, *, deadline):
        data["calls"].append(route)
        return {
            "actions/jobs/73/steps/9/logs": data["log"],
            "actions/artifacts/99/zip": data["raw"],
        }[route]

    monkeypatch.setattr(witness, "api_json", api)
    monkeypatch.setattr(witness, "provider_bytes", fetch)
    return data


def test_exact_attempt_successful_producer_and_exact_marker_log(provider):
    assert witness.acquire(expected()) == provenance._validate_components(rows())
    assert provider["calls"] == [
        "actions/runs/42/attempts/2",
        "actions/runs/42/attempts/2/jobs?per_page=100",
        "actions/jobs/73/steps/9/logs",
        "actions/artifacts/99",
        "actions/artifacts/99/zip",
    ]


@pytest.mark.parametrize(
    "field",
    [
        "repository",
        "run_id",
        "run_attempt",
        "head",
        "tree",
        "pr_head",
        "profile",
        "platform",
        "architecture",
        "packaging_venv",
        "producer_job",
        "locks",
        "toolchain",
        "version",
        "schema_version",
    ],
)
def test_receipt_is_bound_to_every_input(field):
    value = {**expected(), "components": rows()}
    value[field] = True if field == "schema_version" else "wrong"
    with pytest.raises(ValueError, match="receipt"):
        witness.validate_receipt(value, expected())


@pytest.mark.parametrize(
    "defect",
    [
        "run",
        "attempt",
        "repository",
        "head",
        "path",
        "job-run",
        "job-attempt",
        "job-head",
        "job-url",
        "failed",
        "upload",
        "marker",
        "marker-running",
        "step-number",
        "step-order",
        "duplicate-job",
        "duplicate-step",
        "incomplete",
    ],
)
def test_provider_custody_rejects_wrong_or_incomplete_identity(provider, defect):
    run, jobs, job = provider["run"], provider["jobs"], provider["job"]
    if defect in {"run", "attempt", "head", "path"}:
        key = {"run": "id", "attempt": "run_attempt", "head": "head_sha", "path": "path"}[defect]
        run[key] = "wrong"
    elif defect == "repository":
        run["repository"]["id"] += 1
    elif defect.startswith("job-"):
        job[
            {
                "job-run": "run_id",
                "job-attempt": "run_attempt",
                "job-head": "head_sha",
                "job-url": "url",
            }[defect]
        ] = "wrong"
    elif defect == "failed":
        job["conclusion"] = "failure"
    elif defect in {"upload", "marker"}:
        job["steps"][0 if defect == "upload" else 1]["conclusion"] = "failure"
    elif defect == "marker-running":
        job["steps"][1]["status"] = "in_progress"
    elif defect == "step-number":
        job["steps"][1]["number"] = True
    elif defect == "step-order":
        job["steps"][1]["number"] = 7
    elif defect == "duplicate-job":
        jobs["jobs"].append(copy.deepcopy(job))
        jobs["total_count"] = 2
    elif defect == "duplicate-step":
        job["steps"].append({**job["steps"][1], "number": 10})
    else:
        jobs["total_count"] = 2
    with pytest.raises(ValueError):
        witness.acquire(expected())
    assert not any("/logs" in route for route in provider["calls"])
    if defect in {"run", "attempt", "repository", "head", "path"}:
        assert provider["calls"] == ["actions/runs/42/attempts/2"]
    else:
        assert provider["calls"] == [
            "actions/runs/42/attempts/2",
            "actions/runs/42/attempts/2/jobs?per_page=100",
        ]


@pytest.mark.parametrize("state", ["queued", "in_progress", "waiting"])
def test_unfinished_producer_never_authorizes_upload(provider, state):
    provider["job"]["status"] = state
    head = witness.validate_attempt(provider["run"], IDENTITY)
    assert witness.producer_job(provider["jobs"], IDENTITY, head) is None


@pytest.fixture
def witness_clock(monkeypatch):
    current = [0]

    def advance(seconds):
        current[0] += seconds

    monkeypatch.setattr(witness.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(witness.time, "sleep", advance)
    return current


def provider_error(status):
    return urllib.error.HTTPError(
        "https://example.invalid/private?signature=secret", status, "private", {}, None
    )


def test_transient_acquisition_recovers_across_exact_provider_endpoints(
    provider, witness_clock, monkeypatch
):
    attempt = "actions/runs/42/attempts/2"
    jobs = f"{attempt}/jobs?per_page=100"
    marker = "actions/jobs/73/steps/9/logs"
    artifact = "actions/artifacts/99"
    failures = {
        attempt: urllib.error.URLError(ConnectionResetError("private")),
        jobs: provider_error(404),
        marker: provider_error(503),
        artifact: provider_error(404),
    }
    attempts = []
    original_json = witness.api_json
    original_bytes = witness.provider_bytes

    def try_route(operation, route, *arguments, deadline):
        attempts.append((route, deadline))
        if route in failures:
            raise failures.pop(route)
        if route == jobs and attempts.count((route, deadline)) == 2:
            return {"jobs": [], "total_count": 0}
        return operation(route, *arguments, deadline=deadline)

    monkeypatch.setattr(
        witness, "api_json", lambda *args, **kwargs: try_route(original_json, *args, **kwargs)
    )
    monkeypatch.setattr(
        witness,
        "provider_bytes",
        lambda *args, **kwargs: try_route(original_bytes, *args, **kwargs),
    )
    assert witness.acquire(expected()) == provenance._validate_components(rows())
    assert attempts == [
        (attempt, 150),
        (attempt, 150),
        (jobs, 150),
        (jobs, 150),
        (jobs, 150),
        (marker, 12),
        (marker, 12),
        (artifact, 12),
        (artifact, 12),
        (f"{artifact}/zip", 12),
    ]
    assert witness_clock == [6]


@pytest.mark.parametrize(
    ("endpoint", "error", "reason"),
    [
        ("attempt", provider_error(404), "attempt-http"),
        ("jobs", provider_error(403), "jobs-authorization"),
        ("marker", provider_error(401), "marker-authorization"),
        ("marker", provider_error(429), "marker-http"),
        (
            "marker",
            urllib.error.URLError(ssl.SSLCertVerificationError("private")),
            "marker-transport",
        ),
    ],
)
def test_terminal_provider_errors_stop_at_first_response(
    provider, witness_clock, monkeypatch, endpoint, error, reason
):
    targets = {
        "attempt": "actions/runs/42/attempts/2",
        "jobs": "actions/runs/42/attempts/2/jobs?per_page=100",
        "marker": "actions/jobs/73/steps/9/logs",
    }
    failed = []
    original_json = witness.api_json
    original_bytes = witness.provider_bytes

    def fail_at_route(operation, route, *arguments, deadline):
        if route == targets[endpoint]:
            failed.append(route)
            raise error
        return operation(route, *arguments, deadline=deadline)

    monkeypatch.setattr(
        witness, "api_json", lambda *args, **kwargs: fail_at_route(original_json, *args, **kwargs)
    )
    monkeypatch.setattr(
        witness,
        "provider_bytes",
        lambda *args, **kwargs: fail_at_route(original_bytes, *args, **kwargs),
    )
    with pytest.raises(witness.WitnessMiss, match=f"^{reason}$"):
        witness.acquire(expected())
    assert failed == [targets[endpoint]] and witness_clock == [0]


def test_transient_budget_is_shared_between_routes(provider, witness_clock, monkeypatch):
    original = witness.api_json
    errors = []

    def flaky(route, *, deadline):
        if "/jobs?" in route and len(errors) < 3:
            errors.append(route)
            raise provider_error(502)
        return original(route, deadline=deadline)

    def not_indexed(route, _limit, *, deadline):
        errors.append(route)
        raise provider_error(404)

    monkeypatch.setattr(witness, "api_json", flaky)
    monkeypatch.setattr(witness, "provider_bytes", not_indexed)
    with pytest.raises(witness.WitnessMiss, match=r"^marker-indexing-exhausted$"):
        witness.acquire(expected())
    assert len(errors) == 5 and witness_clock == [4]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("private"),
        ConnectionAbortedError("private"),
        http.client.IncompleteRead(b"private"),
        urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "private")),
    ],
    ids=["timeout", "aborted", "incomplete-read", "temporary-dns"],
)
def test_eligible_transport_recovers_exact_receipt(provider, witness_clock, monkeypatch, error):
    original = witness.api_json
    failed = []

    def transient(route, *, deadline):
        if not failed:
            failed.append(route)
            raise error
        return original(route, deadline=deadline)

    monkeypatch.setattr(witness, "api_json", transient)
    assert witness.acquire(expected()) == provenance._validate_components(rows())
    assert failed == ["actions/runs/42/attempts/2"] and witness_clock == [1]


@pytest.mark.parametrize("endpoint", ["marker", "archive"])
def test_storage_404_is_terminal_not_provider_indexing(
    provider, witness_clock, monkeypatch, endpoint
):
    storage = "https://productionresultssa14.blob.core.windows.net/private?signature=secret"
    target = "actions/jobs/73/steps/9/logs" if endpoint == "marker" else "actions/artifacts/99/zip"
    calls = []
    closed = io.BytesIO(b"private provider error")

    def request(route, *, timeout):
        calls.append(route)
        assert 0 < timeout <= 5
        if route == target:
            raise urllib.error.HTTPError("private", 302, "private", {"Location": storage}, None)
        return io.BytesIO(provider["log"])

    class Storage:
        def open(self, location, *, timeout):
            assert location == storage
            calls.append("storage")
            raise urllib.error.HTTPError(storage, 404, "private", {}, closed)

    monkeypatch.setattr(witness, "api_request", request)
    monkeypatch.setattr(witness.urllib.request, "build_opener", lambda *_args: Storage())
    monkeypatch.setattr(witness, "provider_bytes", provider["provider_bytes"])
    with pytest.raises(witness.WitnessMiss, match=f"^{endpoint}-invalid$"):
        witness.acquire(expected())
    assert calls.count(target) == 1 and calls.count("storage") == 1
    assert witness_clock == [0] and closed.closed


def test_slow_provider_read_closes_owned_response_at_deadline(witness_clock, monkeypatch):
    class Trickle(io.BytesIO):
        def read1(self, size):
            witness_clock[0] += 1
            return super().read1(min(size, 1))

    response = Trickle(b'{"private":"slow response"}')
    monkeypatch.setattr(witness, "api_request", lambda *_args, **_kwargs: response)
    with pytest.raises(TimeoutError):
        witness.api_json("actions/runs/42/attempts/2", deadline=2)
    assert response.closed and witness_clock == [2]


def test_request_deadline_is_capped_and_expiry_prevents_open(witness_clock, monkeypatch):
    calls = []

    class Opener:
        def open(self, request, *, timeout):
            calls.append(timeout)
            return io.BytesIO(b"{}")

    monkeypatch.setenv("GH_TOKEN", "fictional-secret")
    monkeypatch.setattr(witness.urllib.request, "build_opener", lambda *_args: Opener())
    assert witness.api_json("actions/runs/42/attempts/2", deadline=0.25) == {}
    with pytest.raises(TimeoutError):
        witness.api_json("actions/runs/42/attempts/2", deadline=0)
    assert calls == [0.25]


@pytest.mark.parametrize("mode", ["producer-pending", "slow-receipt"])
def test_witness_deadlines_skip_further_provider_reads(provider, witness_clock, monkeypatch, mode):
    original_json = witness.api_json
    original_bytes = witness.provider_bytes

    def slow_json(route, *, deadline):
        result = original_json(route, deadline=deadline)
        if "/jobs?" in route and mode == "producer-pending":
            witness_clock[0] = deadline - 1
            result = {"jobs": [], "total_count": 0}
        return result

    def slow_marker(route, limit, *, deadline):
        result = original_bytes(route, limit, deadline=deadline)
        assert deadline == 8
        witness_clock[0] = deadline
        return result

    monkeypatch.setattr(witness, "api_json", slow_json)
    monkeypatch.setattr(witness, "provider_bytes", slow_marker)
    stage = "jobs" if mode == "producer-pending" else "marker"
    with pytest.raises(witness.WitnessMiss, match=f"^{stage}-deadline$"):
        witness.acquire(expected())
    assert len(provider["calls"]) == (2 if stage == "jobs" else 3)


@pytest.mark.parametrize(
    "defect",
    [
        "absent",
        "duplicate",
        "echo",
        "wrong-id",
        "wrong-digest",
        "metadata-digest",
        "wrong-artifact-run",
        "expired",
        "archive",
        "archive-size",
    ],
)
def test_upload_identity_and_downloaded_bytes_are_jointly_required(provider, defect):
    if defect == "absent":
        provider["log"] = b"unrelated output\n"
    elif defect == "duplicate":
        provider["log"] *= 2
    elif defect == "echo":
        provider["log"] = b'Write-Output "' + provider["log"].strip() + b'"\n'
    elif defect == "wrong-id":
        provider["artifact"]["id"] = 100
    elif defect == "wrong-digest":
        provider["log"] = provider["log"].replace(b"digest=sha256:", b"digest=sha256:0")
    elif defect == "metadata-digest":
        provider["artifact"]["digest"] = "sha256:" + "0" * 64
    elif defect == "wrong-artifact-run":
        provider["artifact"]["workflow_run"]["id"] = 43
    elif defect == "expired":
        provider["artifact"]["expired"] = True
    elif defect == "archive-size":
        provider["raw"] += b"!"
    else:
        provider["raw"] = provider["raw"][:-1] + b"!"
    with pytest.raises(witness.WitnessMiss):
        witness.acquire(expected())
    assert (
        len(provider["calls"])
        == {
            "absent": 3,
            "duplicate": 3,
            "echo": 3,
            "wrong-digest": 3,
            "wrong-id": 4,
            "metadata-digest": 4,
            "wrong-artifact-run": 4,
            "expired": 4,
            "archive": 5,
            "archive-size": 5,
        }[defect]
    )


@pytest.mark.parametrize("field", ["head", "run_attempt"])
def test_downloaded_receipt_source_or_attempt_mismatch_is_terminal(provider, witness_clock, field):
    provider["receipt"][field] = "f" * 40 if field == "head" else 3
    provider["raw"] = archive(provider["receipt"])
    digest = hashlib.sha256(provider["raw"]).hexdigest()
    provider["log"] = f"ATC_WINDOWS_STOCK_V1 artifact_id=99 digest=sha256:{digest}\n".encode()
    provider["artifact"]["size_in_bytes"] = len(provider["raw"])
    provider["artifact"]["digest"] = f"sha256:{digest}"
    with pytest.raises(witness.WitnessMiss, match=r"^receipt-invalid$"):
        witness.acquire(expected())
    assert len(provider["calls"]) == 5 and witness_clock == [0]


@pytest.mark.parametrize("defect", ["extra", "duplicate", "path", "oversized", "not-zip"])
def test_only_bounded_exact_receipt_archive_member_is_read(defect):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        name = "../" + witness.RECEIPT if defect == "path" else witness.RECEIPT
        bundle.writestr(name, b" " * (witness.MAX_RECEIPT + 1) if defect == "oversized" else b"{}")
        if defect in {"extra", "duplicate"}:
            bundle.writestr("extra" if defect == "extra" else name, b"{}")
    raw = b"not ZIP" if defect == "not-zip" else stream.getvalue()
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        witness.receipt_from_archive(raw)


@pytest.mark.parametrize(
    "location",
    [
        "http://host.blob.core.windows.net/x",
        "https://blob.core.windows.net.evil.invalid/x",
        "https://api.github.com/x",
        "https://user:pass@host.blob.core.windows.net/x",
        "https://host.blob.core.windows.net:444/x",
    ],
)
def test_unrecognized_redirects_cannot_receive_credentials(monkeypatch, location):
    def request(_route, *, timeout):
        raise urllib.error.HTTPError(
            "https://api.github.com/private", 302, "redirect", {"Location": location}, None
        )

    monkeypatch.setattr(witness, "api_request", request)
    with pytest.raises(ValueError, match="redirect"):
        witness.provider_bytes(
            "actions/jobs/73/steps/9/logs", 100, deadline=witness.time.monotonic() + 6
        )


def test_storage_redirect_has_no_authorization_header(monkeypatch):
    location = "https://productionresultssa14.blob.core.windows.net/private?signed=private"

    def request(_route, *, timeout):
        assert timeout == 5
        raise urllib.error.HTTPError(
            "https://api.github.com/private", 302, "redirect", {"Location": location}, None
        )

    class Opener:
        def open(self, target, *, timeout):
            assert target == location and isinstance(target, str) and timeout == 5
            return io.BytesIO(b"exact step bytes")

    monkeypatch.setattr(witness, "api_request", request)
    monkeypatch.setattr(witness.urllib.request, "build_opener", lambda *_args: Opener())
    assert (
        witness.provider_bytes(
            "actions/jobs/73/steps/9/logs", 100, deadline=witness.time.monotonic() + 6
        )
        == b"exact step bytes"
    )


@pytest.fixture
def ordinary_checkout(tmp_path, monkeypatch):
    """A real synthetic merge, existing names, and an ordinary shipped-code edit."""
    root = tmp_path / "source"
    root.mkdir()

    def git(*args):
        return (
            subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )

    git("init")
    git("config", "user.name", "Contract Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "commit.gpgsign", "false")
    files = {
        "scripts/build_desktop.py": (ROOT / "scripts/build_desktop.py").read_text(encoding="utf-8"),
        "packages/allthecontext/src/allthecontext/client.py": "value = 1\n",
        "tests/unit/test_client.py": "def test_client(): pass\n",
        "docs/fixture.md": "Fixture\n",
        "docs/café.md": "Quoted fixture\n",
        "uv.lock": "frozen\n",
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-b", "ordinary")
    (root / "packages/allthecontext/src/allthecontext/client.py").write_text(
        "value = 2\n", encoding="utf-8"
    )
    git("commit", "-am", "ordinary edit")
    head = git("rev-parse", "HEAD")
    git("checkout", "-b", "synthetic", base)
    git("merge", "--no-ff", "ordinary", "-m", "synthetic merge")
    commit = git("rev-parse", "HEAD")
    repo = {"full_name": pilot.REPOSITORY}
    event = {
        "number": 1,
        "repository": repo,
        "pull_request": {
            "base": {"sha": base, "ref": "main", "repo": repo},
            "head": {"sha": head, "repo": repo},
            "merge_commit_sha": commit,
        },
    }
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event), encoding="utf-8")
    environment = {
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_SHA": commit,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/1/merge",
        "RUNNER_OS": "Windows",
        "GITHUB_REPOSITORY": pilot.REPOSITORY,
        "GITHUB_JOB": "desktop-package",
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "2",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(witness, "BASE", base)
    monkeypatch.setattr(pilot, "SOURCE", base)
    monkeypatch.setattr(
        witness,
        "QUALIFIED_INPUT_BLOBS",
        {"scripts/build_desktop.py": git("rev-parse", f"{base}:scripts/build_desktop.py")},
    )
    return root, commit, event, event_file, git


def test_real_ordinary_merge_qualifies_only_existing_checked_names(ordinary_checkout):
    root, commit, _, _, _ = ordinary_checkout
    identity = witness.admit_source(root, commit)
    assert identity["head"] == commit and len(identity["tree"]) == 40
    (root / "packages/allthecontext/src/allthecontext/client.py").write_text(
        "dirty\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="dirty"):
        witness.admit_source(root, commit)


@pytest.mark.parametrize("quote_path", ["true", "false"])
def test_ordinary_admission_preserves_configured_git_quoting(
    ordinary_checkout, monkeypatch, quote_path
):
    root, _, event, event_file, git = ordinary_checkout
    git("config", "core.quotePath", quote_path)
    (root / "docs/café.md").write_text("Modified supporting doc\n", encoding="utf-8")
    git("add", ".")
    git("commit", "--amend", "--no-edit")
    commit = git("rev-parse", "HEAD")
    event["pull_request"]["merge_commit_sha"] = commit
    event_file.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_SHA", commit)
    original = pilot.git(root, "ls-tree", commit, "--", "docs/café.md")
    accepted = original.startswith(b"100644 blob ") and original.endswith(
        "\tdocs/café.md\n".encode()
    )
    assert accepted == (quote_path == "false")
    for job, producer in (("desktop-package", False), ("windows-stock-witness", True)):
        monkeypatch.setenv("GITHUB_JOB", job)
        if accepted:
            assert witness.admit_source(root, commit, producer=producer)["head"] == commit
        else:
            with pytest.raises(ValueError, match="unsupported ordinary file"):
                witness.admit_source(root, commit, producer=producer)


@pytest.mark.parametrize("quote_path", ["true", "false"])
def test_batched_tree_matches_original_literal_path_mode_and_quoting(tmp_path, quote_path):
    # Git objects permit quoted names even when the host cannot check them out.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.quotePath", quote_path], check=True)

    def git_input(arguments, raw):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *arguments],
            input=raw,
            check=True,
            capture_output=True,
        ).stdout.strip()

    blob = git_input(["hash-object", "-w", "--stdin"], b"fixture\n")
    names = [
        "plain.md",
        "with space.md",
        'double"quote.md',
        "back\\slash.md",
        "tab\tname.md",
        "line\nname.md",
        "café.md",
        "[literal].md",
        "star*.md",
    ]
    rows = [b"100644 blob " + blob + b"\t" + name.encode() + b"\0" for name in names]
    rows += [
        mode + b" blob " + blob + b"\t" + name + b"\0"
        for mode, name in (
            (b"100755", b"executable.md"),
            (b"120000", b"linked.md"),
        )
    ]
    subtree = git_input(["mktree", "-z"], b"".join(rows))
    tree = git_input(["mktree", "-z"], b"040000 tree " + subtree + b"\tdocs\0").decode()
    files = witness._git_tree_files(tmp_path, tree)
    for name in [
        *(f"docs/{name}" for name in names),
        "docs/executable.md",
        "docs/linked.md",
        "docs",
        "doc",
        "docs/plain.md/child",
        "docs/plain",
        "docs/missing.md",
    ]:
        original = pilot.git(tmp_path, "ls-tree", tree, "--", name)
        accepted = original.startswith(b"100644 blob ") and original.endswith(
            b"\t" + name.encode() + b"\n"
        )
        entry = files.get(name.encode())
        assert (entry is not None and entry[:2] == (b"100644", b"blob")) == accepted, name
        if accepted:
            assert entry == (b"100644", b"blob", blob)


@pytest.mark.parametrize("launch_error", [False, True])
def test_ordinary_start_prepares_only_fresh_background_downloader(
    ordinary_checkout, tmp_path, monkeypatch, launch_error
):
    root, _, _, _, _ = ordinary_checkout
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(pilot.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    launches = []
    real_launch = subprocess.Popen

    def launch(command, **options):
        if command[0] != sys.executable:
            return real_launch(command, **options)
        assert command == [
            sys.executable,
            "-B",
            "-S",
            str(Path(pilot.__file__).resolve()),
            "download",
        ]
        assert options["cwd"] == root and options["stdin"] == subprocess.DEVNULL
        assert options["stdout"] is options["stderr"]
        assert not (pilot.download_directory() / "complete.json").exists()
        launches.append(command)
        if launch_error:
            raise OSError("launch unavailable")

    monkeypatch.setattr(pilot.subprocess, "Popen", launch)
    pilot.start_download(root)
    folder = pilot.download_directory()
    assert len(launches) == 1 and folder.is_dir()
    assert not (folder / "complete.part").exists()
    if launch_error:
        assert (folder / "complete.json").read_bytes() == b'{"available": false}'
    else:
        assert not (folder / "complete.json").exists()
    # The same run/attempt never reuses a prior preparation directory.
    pilot.start_download(root)
    assert len(launches) == 1


@pytest.mark.parametrize(
    "defect", ["lock", "new-module", "deleted-module", "recipe", "ignored-input"]
)
def test_unknown_inputs_fail_closed_even_with_an_ordinary_python_edit(
    ordinary_checkout, monkeypatch, defect
):
    root, commit, event, event_file, git = ordinary_checkout
    if defect == "ignored-input":
        # Git ignores .pyc; stock data/code discovery must not gain other hidden inputs.
        (root / "packages/allthecontext/src/allthecontext/unknown.pyc").write_bytes(b"unknown")
        git("config", "core.excludesFile", str(root / "ignore-patterns"))
        (root / "ignore-patterns").write_text("*.pyc\nignore-patterns\n", encoding="utf-8")
    else:
        path = {
            "lock": root / "uv.lock",
            "new-module": root / "packages/allthecontext/src/allthecontext/new_module.py",
            "deleted-module": root / "packages/allthecontext/src/allthecontext/client.py",
            "recipe": root / "scripts/build_desktop.py",
        }[defect]
        if defect == "deleted-module":
            path.unlink()
        else:
            path.write_text("changed\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "--amend", "--no-edit")
        commit = git("rev-parse", "HEAD")
        event["pull_request"]["merge_commit_sha"] = commit
        event_file.write_text(json.dumps(event), encoding="utf-8")
        monkeypatch.setenv("GITHUB_SHA", commit)
    with pytest.raises(ValueError):
        witness.admit_source(root, commit)


@pytest.mark.parametrize("defect", ["fork", "push", "release", "base", "job"])
def test_nonordinary_context_keeps_stock_route(ordinary_checkout, monkeypatch, defect):
    root, commit, event, event_file, _ = ordinary_checkout
    if defect == "fork":
        event["pull_request"]["head"]["repo"] = {"full_name": "fork/project"}
    elif defect == "base":
        event["pull_request"]["base"]["sha"] = "f" * 40
    else:
        monkeypatch.setenv("GITHUB_JOB" if defect == "job" else "GITHUB_EVENT_NAME", defect)
    event_file.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ValueError):
        witness.admit_source(root, commit)


# Separately bound reviewed overlays. These are source evidence, not references
# to a live scratch checkout. The provider checkout below must not need PRIOR_CI.
PROTECTED_MAIN = "e9bdaab8afcc6265802d690834fbb7fa60cd1a0e"
PRIOR_CI = "2681c5ec9ced8aa15de77d82fcffc0730cf85731"
REVIEWED_WITNESS = "bcb67566232ca3e778fd39f2596522c813599da0"
APP_FIX = "b450e9048770d66d81ab3194960febc608c56c46"
INTEGRATION = "2021a37fba1de91343048734b3b369c695e6724e"
PRIOR_OVERLAY_BLOBS = {
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
}
WITNESS_OVERLAY_BLOBS = {
    ".github/workflows/ci.yml": "c7e3ba2a64a3c8f02f06fd087431d1163e21a399",
    "scripts/build_desktop.py": "233b11b625c2f75aa17609cd8f7500d7d6930acf",
    "scripts/verify_reproducible_build.py": "91a6c93381f1245a69e8c94d1f6d8fbf98c7b8d4",
    "scripts/windows_ci_components.py": "f98c18518c68c0505317d19981daa1611341d320",
    "scripts/windows_ci_package_checks.py": "d2bbb50c719b19058faf4aef204a6d0a1235bf4f",
    "scripts/windows_ci_pilot.py": "a06c6f2b46f8bae744ccc61ede367aa1a37d54aa",
    "scripts/windows_ci_canonical.py": "5e47f71507e7ea9e36c3ee372361de08114a7d38",
}
CURRENT_WITNESS_UPDATES = {
    "scripts/check_test_collection.py": "01de4bf97745a53edddacdf9ef7fa2011ad56a8e",
    ".github/workflows/ci.yml": "5b6c9715c25f3a4392ca9984a5de2116f1cc5d36",
    "scripts/windows_ci_pilot.py": "7cec3559b00402a01bdf676575261774ffb224e2",
    "scripts/windows_ci_components.py": "eccc6705208166773e796fcb1e0312d0e6dec80d",
}


def test_protected_main_admission_binds_both_reviewed_overlays():
    def git(*args):
        return pilot.git(ROOT, *args).decode().strip()

    assert witness.BASE == pilot.SOURCE == PROTECTED_MAIN
    assert git("rev-list", "--parents", "-n", "1", INTEGRATION).split() == [
        INTEGRATION,
        REVIEWED_WITNESS,
        APP_FIX,
    ]
    prior_paths = set(git("diff", "--name-only", PROTECTED_MAIN, PRIOR_CI).splitlines())
    witness_paths = set(git("diff", "--name-only", PRIOR_CI, REVIEWED_WITNESS).splitlines())
    assert prior_paths - witness.IMPLEMENTATION_FILES == PRIOR_OVERLAY_BLOBS.keys()
    assert witness.QUALIFIED_INPUT_BLOBS == (
        PRIOR_OVERLAY_BLOBS | WITNESS_OVERLAY_BLOBS | CURRENT_WITNESS_UPDATES
    )
    # Only the policy module and the existing passive checked test/doc paths
    # have no blob pin. Do not silently expand this exemption to executable inputs.
    unpinned = (prior_paths | witness_paths) - witness.QUALIFIED_INPUT_BLOBS.keys()
    assert unpinned == {
        "scripts/windows_ci_witness.py",
        "tests/unit/test_windows_ci_witness.py",
        "tests/unit/test_windows_ci_pilot.py",
        "tests/unit/test_native_build_provenance.py",
        "tests/unit/test_pytest_collection_contract.py",
        "tests/unit/test_release_workflow_contracts.py",
        "docs/STATUS.md",
        "docs/DECISIONS.md",
        "docs/REQUIREMENTS_TRACEABILITY.md",
    }
    for revision, pins in (
        (PRIOR_CI, PRIOR_OVERLAY_BLOBS),
        (REVIEWED_WITNESS, WITNESS_OVERLAY_BLOBS),
        ("HEAD", witness.QUALIFIED_INPUT_BLOBS),
    ):
        for name, blob in pins.items():
            assert git("rev-parse", f"{revision}:{name}") == blob, (revision, name)
    # The current witness must also retain every prior-overlay pin unchanged.
    for name, blob in PRIOR_OVERLAY_BLOBS.items():
        assert git("rev-parse", f"{REVIEWED_WITNESS}:{name}") == blob


@pytest.fixture(scope="module")
def protected_main_objects(tmp_path_factory):
    """Build provider-shaped commits locally, using the actual frozen history."""
    root = tmp_path_factory.mktemp("protected-main-objects") / "objects"
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(ROOT), str(root)],
        check=True,
        capture_output=True,
    )
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Admission fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Admission fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }

    def git(*args, data=None):
        return (
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "-C", str(root), *args],
                input=data,
                env=environment,
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )

    def branch(source, edits):
        git("read-tree", source)
        for name, contents in edits.items():
            blob = git("hash-object", "-w", "--stdin", data=contents)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        tree = git("write-tree")
        head = git("commit-tree", tree, "-p", source, "-m", "Local admission branch")
        # A real merge with protected main has exactly this candidate tree.
        assert git("merge-tree", "--write-tree", PROTECTED_MAIN, head) == tree
        return head, tree

    policy = "scripts/windows_ci_witness.py"
    assert git("rev-parse", f"{INTEGRATION}:{policy}") == git(
        "rev-parse", f"{REVIEWED_WITNESS}:{policy}"
    )
    corrected = {
        name: subprocess.check_output(["git", "-C", str(ROOT), "show", f"HEAD:{name}"])
        for name in (policy, *CURRENT_WITNESS_UPDATES)
    }
    head, tree = branch(INTEGRATION, corrected)
    ci_head, ci_tree = branch(REVIEWED_WITNESS, corrected)
    return root, git, branch, corrected, head, tree, ci_head, ci_tree


@pytest.mark.parametrize(
    "case,error",
    [
        ("original", "unreviewed ordinary base"),
        ("corrected", None),
        ("private-base", "unreviewed ordinary base"),
        ("wrong-branch", "unsupported ordinary context"),
        ("fork", "unsupported ordinary context"),
        ("event-head", "unsupported merge parents"),
        ("event-merge", "unsupported ordinary context"),
        ("checkout-head", "ordinary source mismatch"),
        ("reversed-parents", "unsupported merge parents"),
        ("extra-parent", "unsupported merge parents"),
        ("prior-input", "unreviewed CI/native input"),
        ("witness-input", "unreviewed CI/native input"),
        ("new-module", "uncovered ordinary input"),
        ("dashboard", "uncovered ordinary input"),
        ("history", "uncovered ordinary input"),
        ("ci-only", "no ordinary shipped Python edit"),
    ],
)
def test_actual_protected_main_depth_two_both_participants(
    protected_main_objects, tmp_path, case, error
):
    objects, git, branch, corrected, head, tree, ci_head, ci_tree = protected_main_objects
    if case == "original":
        head, tree = INTEGRATION, git("rev-parse", f"{INTEGRATION}^{{tree}}")
    elif case == "ci-only":
        head, tree = ci_head, ci_tree
    elif case == "prior-input":
        name = "scripts/check_test_collection.py"
        contents = pilot.git(ROOT, "show", f"{PRIOR_CI}:{name}")
        head, tree = branch(INTEGRATION, {**corrected, name: contents})
    elif case in {"witness-input", "new-module", "dashboard", "history"}:
        name = {
            "witness-input": "scripts/build_desktop.py",
            "new-module": "packages/allthecontext/src/allthecontext/unreviewed.py",
            "dashboard": "apps/dashboard/unreviewed.txt",
            "history": "scripts/unreviewed-history.json",
        }[case]
        contents = (ROOT / name).read_bytes() if (ROOT / name).is_file() else b""
        head, tree = branch(INTEGRATION, {**corrected, name: contents + b"\n# unreviewed\n"})
    parents = [PROTECTED_MAIN, head]
    if case == "reversed-parents":
        parents.reverse()
    elif case == "extra-parent":
        parents.append(APP_FIX)
    arguments = ["commit-tree", tree]
    for parent in parents:
        arguments.extend(["-p", parent])
    merge = git(*arguments, "-m", f"Local provider merge: {case}")

    # file:// forces Git's upload-pack transport to honor depth; a plain local
    # clone would ignore --depth. This checkout has no alternates or private base.
    root = tmp_path / "provider"
    root.mkdir()

    def provider_git(*args):
        return (
            subprocess.run(
                ["git", "-c", "core.autocrlf=false", "-C", str(root), *args],
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )

    provider_git("init", "-q")
    provider_git("config", "core.autocrlf", "false")
    provider_git("fetch", "--no-tags", "--depth=2", objects.as_uri(), merge)
    provider_git("checkout", "--detach", "FETCH_HEAD")
    assert provider_git("rev-parse", "--is-shallow-repository") == "true"
    assert provider_git("rev-list", "--parents", "-n", "1", "HEAD").split() == [merge, *parents]
    assert not (root / ".git/objects/info/alternates").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{PRIOR_CI}^{{commit}}"],
            capture_output=True,
        ).returncode
        != 0
    )
    repo = {"id": pilot.REPOSITORY_ID, "full_name": pilot.REPOSITORY, "fork": False}
    event = {
        "action": "opened",
        "number": 133,
        "repository": repo,
        "pull_request": {
            "base": {"sha": PROTECTED_MAIN, "ref": "main", "repo": repo},
            "head": {"sha": head, "ref": "codex/ordinary-ci-test", "repo": repo},
            "merge_commit_sha": merge,
        },
    }
    pr = event["pull_request"]
    if case == "private-base":
        pr["base"]["sha"] = PRIOR_CI
    elif case == "wrong-branch":
        pr["base"]["ref"] = "unreviewed"
    elif case == "fork":
        pr["head"]["repo"] = {"full_name": "fork/project"}
    elif case == "event-head":
        pr["head"]["sha"] = "f" * 40
    elif case == "event-merge":
        pr["merge_commit_sha"] = "f" * 40
    event_file = tmp_path / "event.json"
    event_bytes = json.dumps(event).encode()
    event_file.write_bytes(event_bytes)
    process = """
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from scripts import windows_ci_witness as witness
try:
    result = witness.admit_source(root, sys.argv[2], producer=sys.argv[3] == 'true')
except ValueError as exc:
    result = {'error': str(exc)}
print(json.dumps(result))
"""
    for role, producer in (("desktop-package", False), ("windows-stock-witness", True)):
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                process,
                str(root),
                "f" * 40 if case == "checkout-head" else merge,
                str(producer).lower(),
            ],
            env={
                **os.environ,
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_SHA": merge,
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REF": "refs/pull/133/merge",
                "RUNNER_OS": "Windows",
                "GITHUB_REPOSITORY": pilot.REPOSITORY,
                "GITHUB_JOB": role,
                "GITHUB_RUN_ID": "42",
                "GITHUB_RUN_ATTEMPT": "2",
            },
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        if error:
            assert result == {"error": error}, (case, role, result)
        else:
            assert result == {
                "repository": pilot.REPOSITORY,
                "run_id": 42,
                "run_attempt": 2,
                "head": merge,
                "tree": tree,
                "pr_head": head,
            }
        assert event_file.read_bytes() == event_bytes
        assert provider_git("status", "--porcelain", "--untracked-files=normal") == ""
        if case == "ci-only" and not producer:
            pilot_process = process.replace(
                "result = witness.admit_source(root, sys.argv[2], producer=sys.argv[3] == 'true')",
                "from scripts import windows_ci_pilot; "
                "windows_ci_pilot.admit_source(root, sys.argv[2]); "
                "result = {'fixed_pilot': True}",
            )
            fixed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", pilot_process, str(root), merge, "false"],
                env={
                    **os.environ,
                    "GITHUB_EVENT_PATH": str(event_file),
                    "GITHUB_SHA": merge,
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_REF": "refs/pull/133/merge",
                    "RUNNER_OS": "Windows",
                    "GITHUB_REPOSITORY": pilot.REPOSITORY,
                    "GITHUB_JOB": "desktop-package",
                    "GITHUB_RUN_ID": "42",
                    "GITHUB_RUN_ATTEMPT": "2",
                },
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            assert json.loads(fixed.stdout) == {"fixed_pilot": True}


@pytest.fixture
def candidate_flow(tmp_path, monkeypatch):
    """Real production double-build/stage/provenance control flow, fictional bytes."""
    root = tmp_path / "checkout"
    root.mkdir()
    state = {"arms": [], "stock": [], "smokes": [], "drained": threading.Event()}

    def build(_root, build, dist):
        paths = verifier.build_desktop.component_paths(build_root=build, dist_root=dist)
        for role, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(role.encode())
        state["arms"].append(build)
        return provenance.collect_snapshot(paths)

    def smoke(_root, candidate):
        state["smokes"].append(candidate)
        for name in ("platform", "release"):
            directory = candidate / "dist" / name
            directory.mkdir()
            (directory / "package").write_bytes(b"verified package")
        state["drained"].set()
        return state.get("smoke_result", 0)

    monkeypatch.setattr(witness, "expected_receipt", lambda *_args: expected())
    monkeypatch.setattr(witness, "donor_runner", lambda *_args: build)
    monkeypatch.setattr(
        witness, "acquire", lambda _expected: provenance._validate_components(rows())
    )
    monkeypatch.setattr(witness, "provisional_checks", smoke)
    monkeypatch.setattr(verifier, "project_version", lambda _root: "0.1.0-beta.8")
    args = dict(
        root=root,
        commit=IDENTITY["head"],
        version="0.1.0-beta.8",
        build=root / "build/desktop",
        dist=root / "dist/desktop",
        output=root / "dist/native-build-provenance",
        identity=IDENTITY,
        tools=TOOLS,
        locks=LOCKS,
    )
    return state, args


def test_full_four_role_join_promotes_only_smoked_candidate(candidate_flow):
    state, args = candidate_flow
    result = witness.try_ordinary(**args)
    assert result is not None and len(state["arms"]) == 2
    assert state["arms"][0] != state["arms"][1]
    assert state["drained"].is_set()
    payload, paths = result
    assert payload["components"] == rows()
    assert {role: path.read_bytes() for role, path in paths.items()} == {
        role: role.encode() for role in paths
    }
    assert (args["root"] / "dist/platform/package").is_file()
    assert not state["smokes"][0].exists()


@pytest.mark.parametrize(
    "miss", ["provider", "one-role", "one-size", "graph", "donor", "retained-build"]
)
def test_provisional_miss_drains_then_runs_original_complete_stock_route(
    candidate_flow, monkeypatch, capsys, miss
):
    state, args = candidate_flow
    if miss == "provider":

        def acquire(_expected):
            raise witness.WitnessMiss("marker-indexing-exhausted") from OSError("private")

        monkeypatch.setattr(witness, "acquire", acquire)
    elif miss in {"one-role", "one-size", "graph"}:
        changed = rows()
        if miss == "one-size":
            changed[2]["size"] += 1
        else:
            changed[3 if miss == "one-role" else 0]["sha256"] = "f" * 64
        monkeypatch.setattr(
            witness, "acquire", lambda _expected: provenance._validate_components(changed)
        )
    else:

        def fail(*_args):
            raise ValueError("unavailable provisional input")

        if miss == "donor":
            monkeypatch.setattr(witness, "donor_runner", fail)
        else:
            monkeypatch.setattr(witness, "donor_runner", lambda *_args: fail)
    assert witness.try_ordinary(**args) is None
    emitted = capsys.readouterr().out
    reason = {
        "provider": "marker-indexing-exhausted",
        "one-role": "candidate-updater-sha256",
        "one-size": "candidate-recovery-size",
        "graph": "candidate-main-sha256",
        "donor": "provisional miss",
        "retained-build": "provisional miss",
    }[miss]
    assert f"Windows ordinary witness: complete stock fallback ({reason})" in emitted
    assert "private" not in emitted
    assert not args["build"].exists() and not args["dist"].exists()
    assert not args["output"].exists()
    if miss not in {"donor", "retained-build"}:
        assert state["drained"].is_set() and not state["smokes"][0].exists()
    # Execute the actual CLI's stock continuation, then the unchanged lifecycle entrypoint.
    monkeypatch.setattr(verifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verifier, "checked_out_commit", lambda _root: IDENTITY["head"])
    monkeypatch.setattr(verifier, "current_toolchain", lambda: TOOLS)
    monkeypatch.setattr(verifier, "lock_digests", lambda _root: LOCKS)
    monkeypatch.setattr(witness, "admit_source", lambda *_args: IDENTITY)
    monkeypatch.setattr(witness, "try_ordinary", lambda *_args, **_kwargs: None)

    def stock(_root, build, dist, **kwargs):
        assert kwargs["parallel_windows"] is True and not kwargs.get("ci_witness")
        state["stock"].append(build)
        paths = verifier.build_desktop.component_paths(build_root=build, dist_root=dist)
        for role, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cold-" + role.encode())
        return provenance.collect_snapshot(paths)

    monkeypatch.setattr(verifier, "_run_clean_build", stock)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--repository-root",
            str(args["root"]),
            "--source-commit",
            IDENTITY["head"],
            "--build-root",
            str(args["build"]),
            "--dist-root",
            str(args["dist"]),
            "--output-dir",
            str(args["output"]),
            "--windows-ci-donor-pilot",
            "--parallel-windows",
        ],
    )
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert verifier.main() == 0
    assert len(state["stock"]) == 2 and not set(state["stock"]) & set(state["arms"])
    calls = []
    monkeypatch.setattr(checks, "run", lambda *args: calls.append(args))
    monkeypatch.setattr(checks, "project_version", lambda _root: "0.1.0-beta.8")
    assert checks.main() == 0
    assert {call[0] for call in calls} == {
        "smoke_desktop_artifact.py",
        "smoke_packaged_recovery.py",
        "smoke_packaged_first_run.py",
        "package_desktop.py",
        "smoke_platform_package.py",
        "build_release_assets.py",
    }
    assert (args["dist"] / "AllTheContextSetup.exe").read_bytes() == b"cold-main"


def test_matching_witness_preserves_genuine_lifecycle_failure(candidate_flow):
    state, args = candidate_flow
    state["smoke_result"] = 1
    with pytest.raises(ValueError, match="lifecycle failed"):
        witness.try_ordinary(**args)
    assert state["drained"].is_set() and not args["dist"].exists()


@pytest.mark.parametrize("matching", [True, False], ids=["proved-failure", "unproved-fallback"])
def test_public_verifier_preserves_reached_lifecycle_failure_and_fallback(
    candidate_flow, monkeypatch, capsys, matching
):
    state, args = candidate_flow
    calls = []
    github_output = args["root"] / "github-output"

    def lifecycle_command(script, *arguments):
        calls.append((script, arguments))
        if script == "smoke_packaged_first_run.py" and arguments[1] != ".":
            raise subprocess.CalledProcessError(1, [script])

    def reached_checks(_root, candidate):
        state["smokes"].append(candidate)
        result = checks.candidate_checks(candidate)
        state["drained"].set()
        return result

    def stock(_root, build, dist, **kwargs):
        assert kwargs["parallel_windows"] and not kwargs.get("ci_witness")
        state["stock"].append(build)
        paths = verifier.build_desktop.component_paths(build_root=build, dist_root=dist)
        for role, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cold-" + role.encode())
        return provenance.collect_snapshot(paths)

    monkeypatch.setattr(checks, "run", lifecycle_command)
    monkeypatch.setattr(checks, "project_version", lambda _root: "0.1.0-beta.8")
    monkeypatch.setattr(witness, "provisional_checks", reached_checks)
    monkeypatch.setattr(verifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verifier, "checked_out_commit", lambda _root: IDENTITY["head"])
    monkeypatch.setattr(verifier, "current_toolchain", lambda: TOOLS)
    monkeypatch.setattr(verifier, "lock_digests", lambda _root: LOCKS)
    monkeypatch.setattr(witness, "admit_source", lambda *_args: IDENTITY)
    monkeypatch.setattr(verifier, "_run_clean_build", stock)
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    if not matching:
        changed = rows()
        changed[0]["sha256"] = "f" * 64
        monkeypatch.setattr(
            witness, "acquire", lambda _expected: provenance._validate_components(changed)
        )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--repository-root",
            str(args["root"]),
            "--source-commit",
            IDENTITY["head"],
            "--build-root",
            str(args["build"]),
            "--dist-root",
            str(args["dist"]),
            "--output-dir",
            str(args["output"]),
            "--windows-ci-donor-pilot",
            "--parallel-windows",
        ],
    )
    assert verifier.main() == (1 if matching else 0)
    assert state["drained"].is_set() and not state["smokes"][0].exists()
    # The real candidate-check coordinator drained both branches after the
    # first-run command failed at the subprocess boundary. No fake return code.
    assert {script for script, _ in calls} == {
        "smoke_packaged_first_run.py",
        "smoke_desktop_artifact.py",
        "smoke_packaged_recovery.py",
        "package_desktop.py",
        "smoke_platform_package.py",
        "build_release_assets.py",
    }
    if matching:
        assert not state["stock"] and not github_output.exists()
        assert not any(args[key].exists() for key in ("build", "dist", "output"))
        assert "proved Windows candidate lifecycle failed" in capsys.readouterr().err
    else:
        assert len(state["stock"]) == 2 and not set(state["stock"]) & set(state["arms"])
        assert github_output.read_text() == "route=cold\n"
        calls.clear()
        assert checks.main(overlap_first_run=True) == 0
        assert len(calls) == 6
        assert (args["dist"] / "AllTheContextSetup.exe").read_bytes() == b"cold-main"


@pytest.mark.parametrize("typed", [True, False], ids=["unknown-code", "raw-provider-error"])
def test_fallback_output_never_echoes_untrusted_errors(candidate_flow, monkeypatch, capsys, typed):
    _state, args = candidate_flow

    def unavailable(_expected):
        error = "https://private.invalid/path?credential=fictional-secret"
        raise witness.WitnessMiss(error) if typed else OSError(error)

    monkeypatch.setattr(witness, "acquire", unavailable)
    assert witness.try_ordinary(**args) is None
    assert capsys.readouterr().out.splitlines()[-1] == (
        "Windows ordinary witness: complete stock fallback (acquisition-invalid)"
    )


def test_retained_output_mismatch_has_fixed_role_code(candidate_flow, monkeypatch, capsys):
    _state, args = candidate_flow
    original = verifier.generate_provenance

    def changed_retained(**kwargs):
        payload, paths = original(**kwargs)
        payload["components"][1]["sha256"] = "f" * 64
        return payload, paths

    monkeypatch.setattr(verifier, "generate_provenance", changed_retained)
    assert witness.try_ordinary(**args) is None
    assert capsys.readouterr().out.splitlines()[-1] == (
        "Windows ordinary witness: complete stock fallback (retained-mcp-sha256)"
    )
    assert not args["dist"].exists()


def test_witness_miss_waits_for_running_smoke_before_scratch_disposal(candidate_flow, monkeypatch):
    state, args = candidate_flow
    entered, rejected, release = threading.Event(), threading.Event(), threading.Event()
    candidates = []

    def slow_smoke(_root, candidate):
        candidates.append(candidate)
        entered.set()
        assert release.wait(timeout=5)
        assert candidate.is_dir()
        state["drained"].set()
        return 1

    def unavailable(_expected):
        assert entered.wait(timeout=5)
        rejected.set()
        raise OSError("optional provider unavailable")

    monkeypatch.setattr(witness, "provisional_checks", slow_smoke)
    monkeypatch.setattr(witness, "acquire", unavailable)
    with ThreadPoolExecutor(max_workers=1) as worker:
        outcome = worker.submit(witness.try_ordinary, **args)
        try:
            assert rejected.wait(timeout=5)
            with pytest.raises(TimeoutError):
                outcome.result(timeout=0.05)
            assert candidates[0].is_dir() and not args["dist"].exists()
        finally:
            release.set()
        assert outcome.result(timeout=5) is None
    assert state["drained"].is_set() and not candidates[0].exists()


def test_provisional_package_commands_use_only_explicit_candidate_bytes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(checks, "run", lambda *args: calls.append(args))
    monkeypatch.setattr(checks, "project_version", lambda _root: "0.1.0-beta.8")
    assert checks.candidate_checks(tmp_path) == 0
    for call in calls:
        if "--artifact-root" in call:
            assert call[call.index("--artifact-root") + 1] == str(tmp_path.resolve())
        if "--source" in call:
            assert call[call.index("--source") + 1] == str(
                tmp_path / "dist/desktop/AllTheContextSetup.exe"
            )
    assert len(calls) == 6


def test_compiler_environment_excludes_provider_credentials(tmp_path, monkeypatch):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN"):
        monkeypatch.setenv(name, "fictional-secret")
    env = verifier.reproducible_environment(config_root=tmp_path)
    assert not {"GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN"} & env.keys()


def test_workflow_keeps_witness_independent_and_required_publication_gated():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    producer = workflow.split("  windows-stock-witness:\n", 1)[1].split("  desktop-package:\n", 1)[
        0
    ]
    desktop = workflow.split("  desktop-package:\n", 1)[1].split("  desktop-package-linux:\n", 1)[0]
    assert f"name: {witness.JOB}" in producer and "needs:" not in producer
    assert "windows_ci_pilot" not in producer and "donor" not in producer
    assert f"name: {witness.UPLOAD}" in producer and f"name: {witness.MARKER}" in producer
    assert producer.index(f"name: {witness.UPLOAD}") < producer.index(f"name: {witness.MARKER}")
    assert "steps.stock_upload.outputs.artifact-id" in producer
    assert "steps.stock_upload.outputs.artifact-digest" in producer
    path = "C:/atc-ci-package-${{ github.run_id }}-${{ github.run_attempt }}"
    assert path in producer and path in desktop
    assert "steps.windows_native.outputs.route != 'witnessed'" in desktop
    assert desktop.index("Prepare Windows packaged first-run diagnostics") < desktop.index(
        "id: windows_native"
    )
    assert "continue-on-error" not in desktop
    assert "actions: read" in desktop and "actions: write" not in workflow
    assert "--ci-witness" not in (ROOT / ".github/workflows/release-candidate.yml").read_text(
        encoding="utf-8"
    )


def test_exact_metadata_member_name_refreshes_locked_current_bytes(tmp_path, monkeypatch):
    site = tmp_path / "venv/Lib/site-packages"
    directory = site / "fixture_package-1.2.3.dist-info"
    directory.mkdir(parents=True)
    (directory / "METADATA").write_text("Name: fixture-package\nVersion: 1.2.3\n", encoding="utf-8")
    (directory / "INSTALLER").write_bytes(b"uv\n")
    (directory / "RECORD").write_bytes(b"exact launcher path and digest\n")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="fixture-package"\nversion="1.2.3"\n', encoding="utf-8"
    )
    monkeypatch.setattr(components.sysconfig, "get_path", lambda _name: str(site))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    entries = {
        f"{directory.name}\\{leaf}": components.Entry(
            f"{directory.name}\\{leaf}", "b", 1, b"", b"old pip bytes"
        )
        for leaf in ("INSTALLER", "RECORD")
    }
    replacement = components.installed_metadata(tmp_path, entries)
    assert replacement == {
        f"{directory.name}\\INSTALLER": b"uv\n",
        f"{directory.name}\\RECORD": b"exact launcher path and digest\n",
    }
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="fixture-package"\nversion="9.0"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unlocked"):
        components.installed_metadata(tmp_path, entries)


def test_independent_stock_producer_has_no_donor_or_retained_inputs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(verifier, "checked_out_commit", lambda _root: IDENTITY["head"])
    monkeypatch.setattr(
        witness,
        "admit_source",
        lambda *_args, **kwargs: (
            IDENTITY if kwargs == {"producer": True} else pytest.fail("not a stock producer")
        ),
    )
    monkeypatch.setattr(verifier, "current_toolchain", lambda: TOOLS)
    monkeypatch.setattr(verifier, "lock_digests", lambda _root: LOCKS)
    monkeypatch.setattr(witness, "expected_receipt", lambda *_args: expected())
    monkeypatch.setattr(witness, "donor_runner", lambda *_args: pytest.fail("stock consumed donor"))
    monkeypatch.setattr(
        witness, "component_runner", lambda *_args: pytest.fail("stock retained input")
    )

    def stock(root, build, dist, **kwargs):
        calls.append((root, build, dist, kwargs))
        return provenance.BuildSnapshot("stock", provenance._validate_components(rows()))

    monkeypatch.setattr(verifier, "_run_clean_build", stock)
    path = tmp_path / witness.RECEIPT
    assert witness.produce(tmp_path, path)
    assert len(calls) == 1 and calls[0][3] == {
        "version": "0.1.0-beta.8",
        "source_commit": IDENTITY["head"],
        "parallel_windows": True,
        "ci_witness": True,
    }
    assert witness.validate_receipt(witness.bounded_json(path.read_bytes()), expected()) == (
        provenance._validate_components(rows())
    )


def test_absolute_packaging_path_is_shared_across_independent_runners():
    common = {"GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2"}
    assert witness.packaging_venv(
        {**common, "RUNNER_TEMP": "D:/one", "GITHUB_JOB": "desktop-package"}
    ) == (
        witness.packaging_venv(
            {**common, "RUNNER_TEMP": "E:/two", "GITHUB_JOB": "windows-stock-witness"}
        )
    )
    assert witness.packaging_venv(common) == "C:/atc-ci-package-42-2"


def test_marker_looking_output_from_unrelated_step_is_never_consulted(provider):
    provider["job"]["steps"].append(
        {"name": "Unrelated output", "number": 10, "status": "completed", "conclusion": "success"}
    )
    provider["log"] = b"dedicated marker step emitted no upload identity\n"
    with pytest.raises(ValueError, match="marker"):
        witness.acquire(expected())
    assert "actions/jobs/73/steps/10/logs" not in provider["calls"]


def test_scope_miss_preserves_existing_ci_only_pilot_in_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verifier, "checked_out_commit", lambda _root: IDENTITY["head"])
    monkeypatch.setattr(verifier, "project_version", lambda _root: "0.1.0-beta.8")
    monkeypatch.setattr(verifier, "current_toolchain", lambda: TOOLS)
    monkeypatch.setattr(verifier, "lock_digests", lambda _root: LOCKS)

    def miss(*_args):
        raise ValueError("CI-only delta")

    monkeypatch.setattr(witness, "admit_source", miss)
    sentinel = object()
    monkeypatch.setattr(pilot, "pilot_runner", lambda *_args: sentinel)
    received = []

    def generate(**kwargs):
        received.append(kwargs["build_runner"])
        return {"components": rows()}, {}

    monkeypatch.setattr(verifier, "generate_provenance", generate)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--repository-root",
            str(tmp_path),
            "--source-commit",
            IDENTITY["head"],
            "--windows-ci-donor-pilot",
        ],
    )
    assert verifier.main() == 0 and received == [sentinel]


def test_early_source_qualification_import_needs_no_installed_dependencies():
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; import scripts.windows_ci_witness; import scripts.windows_ci_components; "
            "assert 'scripts.build_desktop' not in sys.modules; "
            "assert 'scripts.verify_reproducible_build' not in sys.modules; "
            "assert 'cryptography' not in sys.modules",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("successor", [1, 2])
@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "reversed",
        "policy",
        "inherited-recipe",
        "recipe",
        "lock",
        "new-module",
        "deleted-module",
        "mode",
        "no-product",
        "foreign-id",
        "fork",
        "non-main",
        "attempt",
    ],
)
def test_rolling_successive_real_depth_two_events(tmp_path, monkeypatch, successor, defect):
    """Real Git objects and shallow transport; synthetic provider events, no hosted claim."""
    objects = tmp_path / "objects"
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(ROOT), str(objects)],
        check=True,
        capture_output=True,
    )
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Rolling fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Rolling fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }

    def git(*args, data=None):
        return (
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "-C", str(objects), *args],
                input=data,
                env=environment,
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )

    product = "packages/allthecontext/src/allthecontext/http_client.py"
    policy = witness.POLICY
    base = git(
        "commit-tree",
        git("rev-parse", "HEAD^{tree}"),
        "-p",
        PROTECTED_MAIN,
        "-m",
        "Synthetic protected deployment of clean candidate",
    )
    for number in range(1, successor + 1):
        if number == successor and defect == "inherited-recipe":
            git("read-tree", base)
            blob = git("hash-object", "-w", "--stdin", data=b"unreviewed inherited recipe")
            git("update-index", "--cacheinfo", f"100644,{blob},scripts/build_release_assets.py")
            base = git("commit-tree", git("write-tree"), "-p", base, "-m", "Inherited drift")
        git("read-tree", base)
        edits = {
            product: pilot.git(objects, "show", f"{base}:{product}")
            + f"\n# ordinary {number}\n".encode()
        }
        if number == successor:
            if defect == "no-product":
                edits = {
                    "docs/STATUS.md": pilot.git(objects, "show", f"{base}:docs/STATUS.md")
                    + b"\nDoc edit\n"
                }
            for key, name in {
                "policy": policy,
                "recipe": "scripts/build_release_assets.py",
                "lock": "uv.lock",
                "new-module": product.replace("http_client", "unreviewed"),
            }.items():
                if defect == key:
                    edits[name] = (
                        pilot.git(objects, "show", f"{base}:{name}") + b"\n# drift\n"
                        if key != "new-module"
                        else b"pass\n"
                    )
        for name, raw in edits.items():
            blob = git("hash-object", "-w", "--stdin", data=raw)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        if number == successor and defect == "deleted-module":
            git("update-index", "--force-remove", product)
        if number == successor and defect == "mode":
            git("update-index", "--chmod=+x", product)
        tree = git("write-tree")
        head = git("commit-tree", tree, "-p", base, "-m", f"Ordinary shipped edit {number}")
        parents = [base, head]
        if number == successor and defect == "reversed":
            parents.reverse()
        merge = git(
            "commit-tree", tree, "-p", parents[0], "-p", parents[1], "-m", "Synthetic PR merge"
        )
        if number < successor:
            base = merge
    # A later main tip must not replace the event base, even in a shallow checkout.
    later = git("commit-tree", tree, "-p", base, "-m", "Protected main advanced after event")
    root = tmp_path / "provider"
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    for args in (
        ("config", "core.autocrlf", "false"),
        ("fetch", "--no-tags", "--depth=2", objects.as_uri(), merge),
        ("checkout", "--detach", "FETCH_HEAD"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    assert pilot.git(root, "rev-parse", "--is-shallow-repository").strip() == b"true"
    assert pilot.git(root, "rev-list", "--parents", "-n", "1", "HEAD").decode().split() == [
        merge,
        *parents,
    ]
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "fetch",
            "--no-tags",
            "--depth=1",
            objects.as_uri(),
            f"{later}:refs/remotes/origin/main",
        ],
        check=True,
        capture_output=True,
    )
    assert (
        pilot.git(root, "rev-parse", "refs/remotes/origin/main").decode().strip() == later != base
    )
    repo = {"id": pilot.REPOSITORY_ID, "full_name": pilot.REPOSITORY, "fork": False}
    event = {
        "number": 137,
        "repository": repo,
        "pull_request": {
            "base": {"sha": base, "ref": "main", "repo": dict(repo)},
            "head": {"sha": head, "repo": dict(repo)},
            "merge_commit_sha": merge,
        },
    }
    if defect == "foreign-id":
        event["pull_request"]["head"]["repo"]["id"] += 1
    if defect == "fork":
        event["pull_request"]["head"]["repo"]["fork"] = True
    if defect == "non-main":
        event["pull_request"]["base"]["ref"] = "feature"
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    process = """
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root))
from scripts import windows_ci_witness as witness
try:
    result = witness.admit_source(root, sys.argv[2], producer=sys.argv[3] == 'true')
except (ValueError, TypeError) as exc:
    result = {'error': str(exc)}
print(json.dumps(result))
"""
    for role, producer in (("desktop-package", False), ("windows-stock-witness", True)):
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", process, str(root), merge, str(producer).lower()],
            env={
                **os.environ,
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_SHA": merge,
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_REF": "refs/pull/137/merge",
                "RUNNER_OS": "Windows",
                "GITHUB_REPOSITORY": pilot.REPOSITORY,
                "GITHUB_JOB": role,
                "GITHUB_RUN_ID": "43",
                "GITHUB_RUN_ATTEMPT": "0" if defect == "attempt" else "3",
            },
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)
        if defect == "none":
            assert result == {
                "repository": pilot.REPOSITORY,
                "run_id": 43,
                "run_attempt": 3,
                "head": merge,
                "tree": tree,
                "pr_head": head,
            }
        else:
            assert "error" in result, (defect, role, result)
        assert not pilot.git(root, "status", "--porcelain", "--untracked-files=normal")


@pytest.fixture
def rolling_provider(ordinary_checkout, monkeypatch):
    root, commit, event, _event_path, _git = ordinary_checkout
    base = event["pull_request"]["base"]["sha"]
    monkeypatch.setattr(witness, "BASE", PROTECTED_MAIN)
    monkeypatch.setattr(witness, "admit_source", lambda *_args: IDENTITY)
    workflow = {
        "id": 901,
        "path": ".github/workflows/ci.yml",
        "state": "active",
        "url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/workflows/901",
    }
    repo = {"id": pilot.REPOSITORY_ID, "full_name": pilot.REPOSITORY}
    run = {
        "id": 82,
        "run_attempt": 3,
        "workflow_id": 901,
        "head_sha": base,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "path": workflow["path"],
        "repository": repo,
        "head_repository": dict(repo),
        "url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/runs/82",
    }
    job = {
        "id": 83,
        "name": witness.DONOR_JOB,
        "run_id": 82,
        "run_attempt": 3,
        "head_sha": base,
        "status": "completed",
        "conclusion": "success",
        "url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/jobs/83",
        "run_url": run["url"],
        "steps": [
            {"name": name, "number": number, "status": "completed", "conclusion": "success"}
            for name, number in ((witness.DONOR_UPLOAD, 8), (witness.DONOR_MARKER, 9))
        ],
    }
    artifact = {
        "id": 84,
        "name": "all-the-context-windows-x86_64",
        "size_in_bytes": 1234,
        "expired": False,
        "expires_at": "2099-01-01T00:00:00Z",
        "digest": "sha256:" + "a" * 64,
        "url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/artifacts/84",
        "archive_download_url": f"https://api.github.com/repos/{pilot.REPOSITORY}/actions/artifacts/84/zip",
        "workflow_run": {
            "id": 82,
            "head_sha": base,
            "head_branch": "main",
            "repository_id": pilot.REPOSITORY_ID,
            "head_repository_id": pilot.REPOSITORY_ID,
        },
    }
    listing = {"total_count": 1, "workflow_runs": [run]}
    jobs = {"total_count": 1, "jobs": [job]}
    marker = b"ATC_WINDOWS_DONOR_V1 artifact_id=84 digest=sha256:" + b"a" * 64 + b"\n"
    routes = {
        "actions/workflows/ci.yml": workflow,
        f"actions/workflows/901/runs?event=push&branch=main&head_sha={base}&per_page=100": listing,
        "actions/runs/82/attempts/3": run,
        "actions/runs/82/attempts/3/jobs?per_page=100": jobs,
        "actions/artifacts/84": artifact,
        "actions/jobs/83/steps/9/logs": marker,
    }
    calls = []

    def fetch(route, *_args, **_kwargs):
        calls.append(route)
        return copy.deepcopy(routes[route])

    monkeypatch.setattr(witness, "api_json", fetch)
    monkeypatch.setattr(witness, "provider_bytes", fetch)
    monkeypatch.setattr(pilot, "git", lambda *_args: b'[project]\nversion = "0.1.0-beta.8"\n')
    return root, commit, workflow, run, job, artifact, listing, jobs, routes, calls


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "workflow",
        "repo",
        "head-repo",
        "head",
        "event",
        "branch",
        "running",
        "failed",
        "absent",
        "ambiguous",
        "incomplete",
        "attempt",
        "job",
        "job-attempt",
        "upload",
        "marker",
        "step-order",
        "artifact-id",
        "marker-log",
        "ambiguous-marker",
        "name",
        "digest",
        "origin",
        "expired",
        "expiry",
        "size",
        "url",
        "archive-url",
    ],
)
def test_exact_protected_push_descriptor_fails_closed(rolling_provider, defect):
    root, commit, workflow, run, job, artifact, listing, _jobs, routes, calls = rolling_provider
    if defect == "workflow":
        workflow["id"] = 902
    elif defect == "repo":
        run["repository"] = {"id": 1, "full_name": pilot.REPOSITORY}
    elif defect == "head-repo":
        run["head_repository"]["full_name"] = "foreign/repo"
    elif defect in {"head", "event", "branch", "running", "failed"}:
        field, value = {
            "head": ("head_sha", "f" * 40),
            "event": ("event", "pull_request"),
            "branch": ("head_branch", "feature"),
            "running": ("status", "in_progress"),
            "failed": ("conclusion", "failure"),
        }[defect]
        run[field] = value
    elif defect == "absent":
        listing.update(total_count=0, workflow_runs=[])
    elif defect == "ambiguous":
        listing["workflow_runs"].append(dict(run))
        listing["total_count"] = 2
    elif defect == "incomplete":
        listing["total_count"] = 101
    elif defect == "attempt":
        run["run_attempt"] = 0
    elif defect == "job":
        job["name"] = "unrelated"
    elif defect == "job-attempt":
        job["run_attempt"] = 2
    elif defect in {"upload", "marker"}:
        job["steps"][0 if defect == "upload" else 1]["conclusion"] = "skipped"
    elif defect == "step-order":
        job["steps"][0]["number"] = 10
    elif defect == "artifact-id":
        artifact["id"] = 85
    elif defect == "marker-log":
        routes["actions/jobs/83/steps/9/logs"] = b"unrelated log\n"
    elif defect == "ambiguous-marker":
        routes["actions/jobs/83/steps/9/logs"] *= 2
    elif defect == "name":
        artifact["name"] = "other"
    elif defect == "digest":
        artifact["digest"] = "sha256:" + "b" * 64
    elif defect == "origin":
        artifact["workflow_run"]["head_sha"] = "f" * 40
    elif defect == "expired":
        artifact["expired"] = True
    elif defect == "expiry":
        artifact["expires_at"] = "2000-01-01T00:00:00Z"
    elif defect == "size":
        artifact["size_in_bytes"] = pilot.MAX_ROLLING_ARCHIVE + 1
    elif defect in {"url", "archive-url"}:
        artifact["url" if defect == "url" else "archive_download_url"] = (
            "https://foreign.invalid/archive"
        )
    if defect == "none":
        descriptor = witness.protected_donor(root, commit)
        assert (
            descriptor.run_id,
            descriptor.attempt,
            descriptor.job_id,
            descriptor.upload_step,
            descriptor.marker_step,
            descriptor.artifact_id,
        ) == (82, 3, 83, 8, 9, 84)
        assert pilot.RollingDonor.parse(descriptor.raw()) == descriptor
        assert calls[-1] == "actions/artifacts/84"
    else:
        with pytest.raises((ValueError, KeyError)):
            witness.protected_donor(root, commit)


def test_main_upload_marker_is_scoped_to_existing_stock_writer():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    marker = workflow.split("      - name: Record Windows main donor upload identity", 1)[1].split(
        "\n  desktop-package-linux:", 1
    )[0]
    assert (
        "runner.os == 'Windows' && github.event_name == 'push' && github.ref == 'refs/heads/main'"
        in marker
    )
    assert "steps.desktop_upload.outcome == 'success'" in marker
    assert "steps.desktop_upload.outputs.artifact-id" in marker
    assert "steps.desktop_upload.outputs.artifact-digest" in marker
    assert "ATC_WINDOWS_DONOR_V1" in marker
    assert workflow.count("id: desktop_upload") == 1

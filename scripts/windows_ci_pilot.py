"""Bounded CI-only Windows donor pilot; every admission miss uses cold builds.

The pinned archive is a protected-main input, never an executable to launch.
Only the Actions job gets read access; download URLs and credentials stay private.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import re
import stat
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
REPOSITORY = "Martian-ux/All-The-Context"
REPOSITORY_ID = 1307248106
SOURCE = "e9bdaab8afcc6265802d690834fbb7fa60cd1a0e"
# Reviewed packager with the PR witness opt-in. The CI-only route leaves that
# opt-in off, and the graph/identity methods below must still match the donor.
PACKAGER_BLOB = "233b11b625c2f75aa17609cd8f7500d7d6930acf"
RUN = 44945160616
ARTIFACT = 14723830372
ARCHIVE_SHA256 = "5e05a475e623c7e6b4c7a0d2d9a2454ee08dde60d6f4e02efd3ff7507e57d591"
MANIFEST_SHA256 = "19c4a18a32fef287d4c1c359a08ca057bb2a3639db2566e06ee2d6c5defa3b8f"
ARCHIVE_SIZE = 198_230_093
STEM = "all-the-context-0.1.0-beta.8-windows-x86_64"
INSTALLER = f"platform/{STEM}-unsigned.exe"
MANIFEST = "native-build-provenance/native-build-provenance-v1.json"
OUTER_SIZES = {
    INSTALLER: 99_434_301,
    f"{INSTALLER}.sha256": 123,
    f"platform/{STEM}-unsigned.IMPORTANT-UNSIGNED.txt": 428,
    f"platform/{STEM}-unsigned.package.json": 569,
    f"release/{STEM}.zip": 99_088_585,
    f"release/{STEM}.zip.sha256": 114,
    f"release/{STEM}.zip.spdx.json": 2_248,
    MANIFEST: 4_058,
    f"{MANIFEST}.sha256": 98,
}
GRAPH_PATHS = (
    "packages/allthecontext/src",
    "pyproject.toml",
    "uv.lock",
    "apps/dashboard/package-lock.json",
    "scripts/mcp_entry.py",
    "scripts/recovery_entry.py",
    "scripts/update_helper_entry.py",
    "scripts/desktop_entry.py",
    "scripts/macos_artifact_layout.py",
    "scripts/native_build_provenance.py",
)
PROFILE_FUNCTIONS = {
    "common_arguments",
    "helper_arguments",
    "recovery_helper_arguments",
    "update_helper_arguments",
    "desktop_arguments",
    "_identity_data_argument",
    "_version_file_argument",
    "_write_build_identity",
    "_windows_version_file",
}
# This pilot deliberately excludes product, lock, release and unrelated changes.
CI_FILES = {
    ".github/workflows/ci.yml",
    "scripts/build_desktop.py",
    "scripts/verify_reproducible_build.py",
    "scripts/check_test_collection.py",
    "scripts/run_test_shard.py",
    "scripts/pytest_shard_receipt.py",
    "scripts/install_locked_python.py",
    "scripts/windows_ci_pilot.py",
    "scripts/windows_ci_components.py",
    "scripts/windows_ci_witness.py",
    "scripts/windows_ci_canonical.py",
    "scripts/windows_ci_package_checks.py",
    "scripts/windows_ci_process_inventory.py",
    "scripts/smoke_packaged_first_run.py",
    "tests/unit/test_packaged_first_run_diagnostics.py",
    "tests/unit/test_windows_ci_pilot.py",
    "tests/unit/test_windows_ci_witness.py",
    "tests/unit/test_pytest_collection_contract.py",
    "tests/unit/test_release_workflow_contracts.py",
    "tests/unit/test_native_build_provenance.py",
    "tests/unit/test_exact_lock_contracts.py",
    "tests/unit/test_desktop_packaging.py",
    "tests/unit/test_memory_reliability_spec.py",
    "tests/unit/test_retrieval_m3_current_candidate.py",
    "bench/retrieval_m3_current_candidate.py",
    "docs/STATUS.md",
    "docs/DECISIONS.md",
    "docs/REQUIREMENTS_TRACEABILITY.md",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *arguments], stderr=subprocess.DEVNULL)


def eligible_context(event: dict, environment: dict[str, str]) -> bool:
    if not isinstance(event, dict):
        return False
    pr = event.get("pull_request", {})
    if not isinstance(pr, dict):
        return False
    base, head = pr.get("base", {}), pr.get("head", {})
    if not isinstance(base, dict) or not isinstance(head, dict):
        return False
    if not all(
        isinstance(value, dict)
        for value in (
            event.get("repository"),
            base.get("repo"),
            head.get("repo"),
        )
    ):
        return False
    number = event.get("number")
    source_sha = environment.get("GITHUB_SHA", "")
    head_sha = head.get("sha")
    return (
        environment.get("GITHUB_EVENT_NAME") == "pull_request"
        and environment.get("RUNNER_OS") == "Windows"
        and environment.get("GITHUB_REPOSITORY") == REPOSITORY
        and environment.get("GITHUB_JOB") == "desktop-package"
        and type(number) is int
        and number > 0
        and environment.get("GITHUB_REF") == f"refs/pull/{number}/merge"
        and COMMIT_SHA.fullmatch(source_sha) is not None
        and isinstance(head_sha, str)
        and COMMIT_SHA.fullmatch(head_sha) is not None
        and event.get("repository", {}).get("full_name") == REPOSITORY
        and base.get("repo", {}).get("full_name") == REPOSITORY
        and head.get("repo", {}).get("full_name") == REPOSITORY
        and base.get("sha") == SOURCE
        and base.get("ref") == "main"
        and pr.get("merge_commit_sha") in (None, source_sha)
    )


def profile(source: bytes) -> dict[str, str]:
    functions = {
        node.name: ast.dump(node)
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in PROFILE_FUNCTIONS
    }
    require(set(functions) == PROFILE_FUNCTIONS, "incomplete packager profile")
    return functions


def admit_source(root: Path, source_commit: str) -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    require(eligible_context(event, dict(os.environ)), "unsupported pilot context")
    require(
        git(root, "rev-parse", "HEAD").decode().strip()
        == source_commit
        == os.environ["GITHUB_SHA"],
        "pilot source mismatch",
    )
    require(
        not git(root, "status", "--porcelain", "--untracked-files=normal"), "dirty pilot source"
    )
    parents = git(root, "rev-list", "--parents", "-n", "1", source_commit).decode().split()
    head = event["pull_request"]["head"]["sha"]
    require(parents == [source_commit, SOURCE, head], "unsupported merge parents")
    changed = git(root, "diff", "--name-only", "-z", SOURCE, source_commit).decode().split("\0")
    require(set(changed) - {""} <= CI_FILES, "non-CI change")
    for path in GRAPH_PATHS:
        require(
            git(root, "rev-parse", f"{SOURCE}:{path}")
            == git(root, "rev-parse", f"{source_commit}:{path}"),
            "graph input changed",
        )
    path = "scripts/build_desktop.py"
    require(
        git(root, "rev-parse", f"{source_commit}:{path}").decode().strip() == PACKAGER_BLOB,
        "packager input changed",
    )
    require(
        profile(git(root, "show", f"{SOURCE}:{path}"))
        == profile(git(root, "show", f"{source_commit}:{path}")),
        "packager profile changed",
    )


def download_directory() -> Path:
    run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    require(run.isdecimal() and attempt.isdecimal(), "invalid run identity")
    return Path(os.environ["RUNNER_TEMP"]) / f"atc-ci-donor-{run}-{attempt}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_request(suffix: str):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{suffix}",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.build_opener(NoRedirect).open(request, timeout=20)


def api_json(suffix: str) -> dict:
    with api_request(suffix) as response:
        raw = response.read(1_000_001)
    require(len(raw) <= 1_000_000, "oversized API response")
    return json.loads(raw)


def validate_remote(run: dict, artifact: dict) -> None:
    require(
        run.get("id") == RUN
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("head_sha") == SOURCE
        and run.get("head_branch") == "main"
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("repository", {}).get("id") == REPOSITORY_ID
        and run.get("head_repository", {}).get("id") == REPOSITORY_ID,
        "untrusted donor run",
    )
    workflow = artifact.get("workflow_run", {})
    require(
        artifact.get("id") == ARTIFACT
        and artifact.get("expired") is False
        and artifact.get("name") == "all-the-context-windows-x86_64"
        and artifact.get("size_in_bytes") == ARCHIVE_SIZE
        and artifact.get("digest") == f"sha256:{ARCHIVE_SHA256}"
        and workflow.get("id") == RUN
        and workflow.get("head_sha") == SOURCE
        and workflow.get("head_branch") == "main"
        and workflow.get("repository_id") == REPOSITORY_ID
        and workflow.get("head_repository_id") == REPOSITORY_ID,
        "untrusted donor artifact",
    )


def download() -> None:
    """Use only fixed API routes. Never forward the token to artifact storage."""
    import urllib.error

    folder = download_directory()
    available = False
    try:
        validate_remote(api_json(f"actions/runs/{RUN}"), api_json(f"actions/artifacts/{ARTIFACT}"))
        try:
            response = api_request(f"actions/artifacts/{ARTIFACT}/zip")
        except urllib.error.HTTPError as redirect:
            require(redirect.code == 302, "artifact unavailable")
            location = redirect.headers["Location"]
            parsed = urllib.parse.urlsplit(location)
            require(
                parsed.scheme == "https"
                and parsed.hostname is not None
                and parsed.hostname.endswith(".blob.core.windows.net")
                and parsed.username is None
                and parsed.password is None,
                "invalid artifact storage",
            )
            # The signed location remains in memory. This request has no token.
            response = urllib.request.build_opener(NoRedirect).open(location, timeout=20)
        started = time.monotonic()
        size, digest = 0, hashlib.sha256()
        with response, (folder / "donor.zip.part").open("xb") as target:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                require(
                    size <= ARCHIVE_SIZE and time.monotonic() - started < 100,
                    "donor download exceeded bounds",
                )
                digest.update(chunk)
                target.write(chunk)
        require(
            size == ARCHIVE_SIZE and digest.hexdigest() == ARCHIVE_SHA256, "donor archive mismatch"
        )
        (folder / "donor.zip.part").rename(folder / "donor.zip")
        available = True
    except Exception:
        # Network errors can contain signed URLs. Record only availability.
        pass
    finally:
        marker = folder / "complete.part"
        marker.write_text(json.dumps({"available": available}), encoding="utf-8")
        marker.rename(folder / "complete.json")


def start_download(root: Path) -> None:
    try:
        try:
            admit_source(root, os.environ["GITHUB_SHA"])
        except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
            if __package__:
                from scripts.windows_ci_witness import admit_source as admit_ordinary
            else:
                from windows_ci_witness import admit_source as admit_ordinary
            admit_ordinary(root, os.environ["GITHUB_SHA"])
        folder = download_directory()
        folder.mkdir(exist_ok=False)
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        print("Windows CI donor pilot: cold-build fallback")
        return
    try:
        with (folder / "download.log").open("xb") as log:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "download"],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
    except OSError:
        (folder / "complete.json").write_text('{"available": false}', encoding="utf-8")
        print("Windows CI donor pilot: cold-build fallback")


def extract_donor(bundle: Path, destination: Path) -> dict[str, Path]:
    """Select only three known ZIP members, then the setup's three known helpers."""
    if __package__:
        from scripts import native_build_provenance as provenance
        from scripts.windows_ci_components import ROLE_NAMES, Archive
    else:
        import native_build_provenance as provenance
        from windows_ci_components import ROLE_NAMES, Archive

    require(bundle.stat().st_size == ARCHIVE_SIZE, "donor archive size mismatch")
    with bundle.open("rb") as stream:
        require(
            hashlib.file_digest(stream, "sha256").hexdigest() == ARCHIVE_SHA256,
            "donor archive digest mismatch",
        )
    destination.mkdir(exist_ok=False)
    paths = {role: destination / name for role, name in ROLE_NAMES.items()}
    manifest = destination / Path(MANIFEST).name
    with zipfile.ZipFile(bundle) as archive:
        rows = archive.infolist()
        require(
            len(rows) == len(OUTER_SIZES)
            and {row.filename: row.file_size for row in rows} == OUTER_SIZES,
            "donor ZIP inventory mismatch",
        )
        for row in rows:
            require(
                not row.is_dir()
                and not stat.S_ISLNK(row.external_attr >> 16)
                and not row.flag_bits & 1
                and row.compress_type in (0, 8)
                and 0 <= row.header_offset < ARCHIVE_SIZE
                and 0 <= row.compress_size <= ARCHIVE_SIZE,
                "unsafe donor ZIP entry",
            )
        for name, path in (
            (INSTALLER, paths["main"]),
            (MANIFEST, manifest),
            (f"{MANIFEST}.sha256", manifest.with_suffix(".json.sha256")),
        ):
            data = archive.read(name)
            require(len(data) == OUTER_SIZES[name], "donor member size mismatch")
            path.write_bytes(data)
    require(
        hashlib.sha256(manifest.read_bytes()).hexdigest() == MANIFEST_SHA256,
        "donor provenance identity mismatch",
    )
    main = Archive(paths["main"].read_bytes())
    for role, path in paths.items():
        if role != "main":
            entry = main.entries[path.name]
            require(entry.kind == "b", "invalid embedded helper role")
            path.write_bytes(entry.raw)
    provenance.verify_provenance(manifest, paths, source_commit=SOURCE, version="0.1.0-beta.8")
    return paths


def pilot_runner(root: Path, source_commit: str, toolchain: dict, locks: dict):
    """Return a runner only after complete admission; admitted build errors propagate."""
    stage = "source admission"
    try:
        admit_source(root, source_commit)
        require(platform.machine().lower() in {"amd64", "x86_64"}, "unsupported pilot architecture")
        stage = "donor download"
        folder = download_directory()
        deadline = time.monotonic() + 125
        while not (folder / "complete.json").is_file():
            require(folder.is_dir() and time.monotonic() < deadline, "donor unavailable")
            time.sleep(0.1)
        require(
            json.loads((folder / "complete.json").read_bytes()) == {"available": True},
            "donor unavailable",
        )
        stage = "donor archive verification"
        paths = extract_donor(folder / "donor.zip", folder / "verified")
        if __package__:
            from scripts import native_build_provenance as provenance
            from scripts.windows_ci_components import component_runner
        else:
            import native_build_provenance as provenance
            from windows_ci_components import component_runner
        doc = provenance.load_provenance(folder / "verified" / Path(MANIFEST).name)
        require(
            doc["contract"]["locks"] == locks and doc["contract"]["toolchain"] == toolchain,
            "donor lock or toolchain mismatch",
        )
        stage = "local toolchain compatibility"
        runner = component_runner(root, source_commit, paths)
    except (
        ImportError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        EOFError,
        struct.error,
        zlib.error,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ):
        print(f"Windows CI donor pilot: cold-build fallback ({stage})")
        return None
    print("Windows CI donor pilot: retained verified dependency closure; fresh ATC A/B compilation")
    return runner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("start", "download"))
    args = parser.parse_args()
    if args.operation == "start":
        start_download(Path(__file__).resolve().parents[1])
    else:
        download()


if __name__ == "__main__":
    main()

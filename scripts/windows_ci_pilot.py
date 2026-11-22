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
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from scripts import native_build_provenance as provenance
else:
    import native_build_provenance as provenance

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
COMPLETE = b'{"available": true}'
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


MAX_ROLLING_ARCHIVE = 220_000_000
MAX_ROLLING_MEMBER = 110_000_000
DESCRIPTOR = "rolling-donor-v1.json"


def require_future_expiry(value: str) -> None:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9T:.+-]+Z", value) is not None,
        "invalid donor expiry",
    )
    require(
        datetime.fromisoformat(value.replace("Z", "+00:00")) > datetime.now(UTC),
        "expired donor",
    )


@dataclass(frozen=True)
class RollingDonor:
    source: str
    workflow_id: int
    run_id: int
    attempt: int
    job_id: int
    upload_step: int
    marker_step: int
    artifact_id: int
    size: int
    digest: str
    expires_at: str
    version: str
    repository: str = REPOSITORY
    repository_id: int = REPOSITORY_ID
    workflow_path: str = ".github/workflows/ci.yml"
    job_name: str = "Desktop artifact - windows-latest"
    artifact_name: str = "all-the-context-windows-x86_64"
    manifest_size: int = 0
    manifest_digest: str = ""

    def __post_init__(self):
        require(
            self.repository == REPOSITORY
            and type(self.repository_id) is int
            and self.repository_id == REPOSITORY_ID
            and self.workflow_path == ".github/workflows/ci.yml"
            and self.job_name == "Desktop artifact - windows-latest"
            and self.artifact_name == "all-the-context-windows-x86_64",
            "invalid donor origin",
        )
        require(COMMIT_SHA.fullmatch(self.source) is not None, "invalid donor source")
        require(
            all(
                type(value) is int and 0 < value < 10**20
                for value in (
                    self.workflow_id,
                    self.run_id,
                    self.attempt,
                    self.job_id,
                    self.upload_step,
                    self.marker_step,
                    self.artifact_id,
                )
            ),
            "invalid donor identity",
        )
        require(self.upload_step < self.marker_step, "invalid donor step order")
        require(
            type(self.size) is int and 0 < self.size <= MAX_ROLLING_ARCHIVE, "invalid donor size"
        )
        require(re.fullmatch(r"[0-9a-f]{64}", self.digest) is not None, "invalid donor digest")
        require(
            re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", self.version) is not None,
            "invalid donor version",
        )
        require_future_expiry(self.expires_at)
        require(
            type(self.manifest_size) is int
            and 0 <= self.manifest_size <= 32_768
            and (
                (self.manifest_size == 0 and self.manifest_digest == "")
                or (self.manifest_size > 0 and re.fullmatch(r"[0-9a-f]{64}", self.manifest_digest))
            ),
            "invalid donor manifest descriptor",
        )

    def raw(self) -> bytes:
        return provenance.canonical_json(asdict(self))

    @classmethod
    def parse(cls, raw: bytes):
        require(len(raw) <= 4096, "oversized donor descriptor")
        value = cls(**json.loads(raw))
        require(value.raw() == raw, "noncanonical donor descriptor")
        return value


def prepared_descriptor() -> RollingDonor | None:
    path = download_directory() / "verified" / DESCRIPTOR
    if not path.exists():
        return None
    regular = provenance._regular_file(path, label="donor descriptor")
    require(regular.stat().st_size <= 4096, "oversized donor descriptor")
    return RollingDonor.parse(regular.read_bytes())


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


def require_owned_directory(path: Path) -> None:
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
        "unsafe donor directory",
    )


def record_completion(folder: Path, available: bool) -> None:
    marker = folder / "complete.part"
    with marker.open("xb") as stream:
        stream.write(COMPLETE if available else b'{"available": false}')
    marker.rename(folder / "complete.json")


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
    """Prepare the donor before publishing readiness. Keep the token on fixed API routes."""
    import urllib.error

    folder = download_directory()
    require_owned_directory(folder)
    available = False
    try:
        if (folder / "rolling-request").exists():
            download_rolling(folder, Path(__file__).resolve().parents[1])
            available = True
            return
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
        extract_donor(folder / "donor.zip", folder / "verified")
        available = True
    except Exception:
        # Network errors can contain signed URLs. Record only availability.
        pass
    finally:
        record_completion(folder, available)


def start_download(root: Path) -> None:
    rolling = False
    try:
        try:
            admit_source(root, os.environ["GITHUB_SHA"])
        except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
            if __package__:
                from scripts.windows_ci_witness import admit_source as admit_ordinary
            else:
                from windows_ci_witness import admit_source as admit_ordinary
            admit_ordinary(root, os.environ["GITHUB_SHA"])
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
            rolling = event["pull_request"]["base"]["sha"] != SOURCE
        folder = download_directory()
        folder.mkdir(exist_ok=False)
        if rolling:
            (folder / "rolling-request").write_bytes(b"rolling-v1")
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError):
        print("Windows CI donor pilot: cold-build fallback")
        return
    try:
        with (folder / "download.log").open("xb") as log:
            subprocess.Popen(
                [sys.executable, "-B", "-S", str(Path(__file__).resolve()), "download"],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
    except OSError:
        record_completion(folder, False)
        print("Windows CI donor pilot: cold-build fallback")


def donor_component_paths(destination: Path) -> dict[str, Path]:
    return {role: destination / name for role, _, name in provenance.COMPONENTS}


def verify_donor(
    destination: Path, paths: dict[str, Path], descriptor: RollingDonor | None = None
) -> dict:
    require_owned_directory(destination)
    manifest = destination / Path(MANIFEST).name
    for name, path in (
        (MANIFEST, manifest),
        (f"{MANIFEST}.sha256", manifest.with_suffix(".json.sha256")),
    ):
        require(
            provenance._regular_file(path, label="donor provenance").stat().st_size
            == (
                descriptor.manifest_size
                if descriptor and name == MANIFEST
                else 98
                if descriptor
                else OUTER_SIZES[name]
            ),
            "donor provenance size mismatch",
        )
    require(
        hashlib.sha256(manifest.read_bytes()).hexdigest()
        == (descriptor.manifest_digest if descriptor else MANIFEST_SHA256),
        "donor provenance identity mismatch",
    )
    payload = provenance.verify_provenance(
        manifest,
        paths,
        source_commit=descriptor.source if descriptor else SOURCE,
        version=descriptor.version if descriptor else "0.1.0-beta.8",
    )
    require(
        hashlib.sha256(provenance.canonical_json(payload)).hexdigest()
        == (descriptor.manifest_digest if descriptor else MANIFEST_SHA256),
        "donor provenance changed during verification",
    )
    if descriptor:
        if __package__:
            from scripts.windows_ci_components import IDENTITY, Archive
        else:
            from windows_ci_components import IDENTITY, Archive
        expected = {
            "schema_version": 1,
            "version": descriptor.version,
            "channel": "beta" if "-" in descriptor.version else "stable",
            "platform": "windows",
            "architecture": "x86_64",
            "source_commit": descriptor.source,
        }
        components = {row["role"]: row for row in payload["components"]}
        for role, path in paths.items():
            # verify_provenance hashes the full snapshot first. Stable own reads
            # protect the parser too, including the worker's later verification.
            digest, size = provenance._stable_hash(path, label="donor identity")
            require(
                0 < size == components[role]["size"] <= MAX_ROLLING_MEMBER,
                "donor identity size mismatch",
            )
            with path.open("rb") as stream:
                data = stream.read(size + 1)
            require(
                len(data) == size == components[role]["size"]
                and hashlib.sha256(data).hexdigest() == digest == components[role]["sha256"],
                "donor changed before identity parsing",
            )
            require(
                json.loads(Archive(data).entries[IDENTITY].raw) == expected,
                "donor embedded identity mismatch",
            )
    return payload


def download_rolling(folder: Path, root: Path) -> None:
    if __package__:
        from scripts import windows_ci_witness as witness
    else:
        import windows_ci_witness as witness
    descriptor = witness.protected_donor(root, os.environ["GITHUB_SHA"])
    raw = witness.provider_bytes(
        f"actions/artifacts/{descriptor.artifact_id}/zip",
        descriptor.size,
        deadline=time.monotonic() + 60,
    )
    require(
        len(raw) == descriptor.size and hashlib.sha256(raw).hexdigest() == descriptor.digest,
        "donor archive mismatch",
    )
    bundle = folder / "donor.zip"
    with bundle.open("xb") as stream:
        stream.write(raw)
    extract_donor(bundle, folder / "verified", descriptor)


def rolling_members(version: str) -> dict[str, int]:
    stem = f"all-the-context-{version}-windows-x86_64"
    installer = f"platform/{stem}-unsigned.exe"
    return {
        installer: MAX_ROLLING_MEMBER,
        f"{installer}.sha256": 512,
        f"platform/{stem}-unsigned.IMPORTANT-UNSIGNED.txt": 4096,
        f"platform/{stem}-unsigned.package.json": 4096,
        f"release/{stem}.zip": MAX_ROLLING_MEMBER,
        f"release/{stem}.zip.sha256": 512,
        f"release/{stem}.zip.spdx.json": 1_000_000,
        MANIFEST: 32_768,
        f"{MANIFEST}.sha256": 98,
    }


def extract_donor(
    bundle: Path, destination: Path, descriptor: RollingDonor | None = None
) -> dict[str, Path]:
    """Select only three known ZIP members, then the setup's three known helpers."""
    if __package__:
        from scripts.windows_ci_components import Archive
    else:
        from windows_ci_components import Archive

    bundle = provenance._regular_file(bundle, label="donor archive")
    archive_size = descriptor.size if descriptor else ARCHIVE_SIZE
    archive_digest = descriptor.digest if descriptor else ARCHIVE_SHA256
    require(bundle.stat().st_size == archive_size, "donor archive size mismatch")
    before = provenance._stable_hash(bundle, label="donor archive")
    with bundle.open("rb") as stream:
        require(
            hashlib.file_digest(stream, "sha256").hexdigest() == archive_digest,
            "donor archive digest mismatch",
        )
    limits = rolling_members(descriptor.version) if descriptor else OUTER_SIZES
    installer = next(name for name in limits if name.endswith("-unsigned.exe"))
    destination.mkdir(exist_ok=False)
    paths = donor_component_paths(destination)
    manifest = destination / Path(MANIFEST).name
    with zipfile.ZipFile(bundle) as archive:
        rows = archive.infolist()
        require(
            len(rows) == len(limits)
            and len({row.filename for row in rows}) == len(rows)
            and {row.filename for row in rows} == set(limits)
            and all(0 < row.file_size <= limits[row.filename] for row in rows)
            and (descriptor is not None or {row.filename: row.file_size for row in rows} == limits),
            "donor ZIP inventory mismatch",
        )
        for row in rows:
            require(
                not row.is_dir()
                and not stat.S_ISLNK(row.external_attr >> 16)
                and (
                    descriptor is None
                    or (
                        stat.S_IFMT(row.external_attr >> 16) in (0, stat.S_IFREG)
                        and not row.external_attr & 0x10
                    )
                )
                and not row.flag_bits & 1
                and row.compress_type in (0, 8)
                and 0 <= row.header_offset < archive_size
                and 0 <= row.compress_size <= archive_size,
                "unsafe donor ZIP entry",
            )
        for name, path in (
            (installer, paths["main"]),
            (MANIFEST, manifest),
            (f"{MANIFEST}.sha256", manifest.with_suffix(".json.sha256")),
        ):
            data = archive.read(name)
            require(len(data) == archive.getinfo(name).file_size, "donor member size mismatch")
            path.write_bytes(data)
    main = Archive(paths["main"].read_bytes())
    for role, path in paths.items():
        if role != "main":
            entry = main.entries[path.name]
            require(entry.kind == "b", "invalid embedded helper role")
            path.write_bytes(entry.raw)
    require(
        provenance._stable_hash(bundle, label="donor archive") == before,
        "donor archive changed during extraction",
    )
    if descriptor:
        descriptor = replace(
            descriptor,
            manifest_size=manifest.stat().st_size,
            manifest_digest=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )
    verify_donor(destination, paths, descriptor)
    if descriptor:
        (destination / DESCRIPTOR).write_bytes(descriptor.raw())
    return paths


def prepared_donor_contract(
    toolchain: dict, locks: dict
) -> tuple[dict[str, Path], RollingDonor | None]:
    """Return the exact immutable descriptor validated with this snapshot."""
    folder = download_directory()
    require_owned_directory(folder)
    marker = folder / "complete.json"
    deadline = time.monotonic() + 125
    while not marker.exists():
        require(folder.is_dir() and time.monotonic() < deadline, "donor unavailable")
        time.sleep(0.1)
    with provenance._regular_file(marker, label="donor completion").open("rb") as stream:
        require(stream.read(len(COMPLETE) + 1) == COMPLETE, "donor unavailable")
    destination = folder / "verified"
    paths = donor_component_paths(destination)
    descriptor = prepared_descriptor()
    if descriptor:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        require(
            descriptor.source == event["pull_request"]["base"]["sha"], "wrong prepared donor base"
        )
    else:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if event_path:
            event = json.loads(Path(event_path).read_bytes())
            require(event["pull_request"]["base"]["sha"] == SOURCE, "missing rolling descriptor")
    doc = verify_donor(destination, paths, descriptor)
    require(
        doc["contract"]["locks"] == locks and doc["contract"]["toolchain"] == toolchain,
        "donor lock or toolchain mismatch",
    )
    return paths, descriptor


def prepared_donor(toolchain: dict, locks: dict) -> dict[str, Path]:
    """Recheck prepared bytes against the pinned provenance and current build inputs."""
    return prepared_donor_contract(toolchain, locks)[0]


def pilot_runner(root: Path, source_commit: str, toolchain: dict, locks: dict):
    """Return a runner only after complete admission; admitted build errors propagate."""
    stage = "source admission"
    try:
        admit_source(root, source_commit)
        require(platform.machine().lower() in {"amd64", "x86_64"}, "unsupported pilot architecture")
        stage = "donor verification"
        paths = prepared_donor(toolchain, locks)
        if __package__:
            from scripts.windows_ci_components import component_runner
        else:
            from windows_ci_components import component_runner
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

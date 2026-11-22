"""Admission, bounded codecs and failure joins for the frozen Windows CI pilot."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import threading
import zipfile
import zlib
from pathlib import Path

import pytest

from scripts import native_build_provenance as provenance
from scripts import verify_reproducible_build as verifier
from scripts import windows_ci_components as components
from scripts import windows_ci_package_checks as package_checks
from scripts import windows_ci_pilot as pilot
from scripts import windows_ci_witness as witness


def context():
    repo = {"full_name": pilot.REPOSITORY}
    event = {
        "number": 1,
        "repository": repo,
        "pull_request": {
            "base": {"repo": repo, "sha": pilot.SOURCE, "ref": "main"},
            "head": {"repo": repo, "sha": "c" * 40},
            "merge_commit_sha": "a" * 40,
        },
    }
    environment = {
        "GITHUB_EVENT_NAME": "pull_request",
        "RUNNER_OS": "Windows",
        "GITHUB_REPOSITORY": pilot.REPOSITORY,
        "GITHUB_JOB": "desktop-package",
        "GITHUB_REF": "refs/pull/1/merge",
        "GITHUB_SHA": "a" * 40,
    }
    return event, environment


@pytest.mark.parametrize("incompatible", ["push", "tag", "fork", "base", "job", "source"])
def test_pilot_context_has_one_supported_path(incompatible):
    event, environment = context()
    assert pilot.eligible_context(event, environment)
    if incompatible == "fork":
        event["pull_request"]["head"] = {"repo": {"full_name": "fork/project"}}
    elif incompatible == "base":
        event["pull_request"]["base"]["sha"] = "b" * 40
    else:
        key, value = {
            "push": ("GITHUB_EVENT_NAME", "push"),
            "tag": ("GITHUB_REF", "refs/tags/v1"),
            "job": ("GITHUB_JOB", "release"),
            "source": ("GITHUB_SHA", "b" * 40),
        }[incompatible]
        environment[key] = value
    assert not pilot.eligible_context(event, environment)


@pytest.mark.parametrize("include_null", [False, True])
def test_opening_event_without_populated_merge_commit_sha_is_supported(include_null):
    event, environment = context()
    if include_null:
        event["pull_request"]["merge_commit_sha"] = None
    else:
        del event["pull_request"]["merge_commit_sha"]
    assert pilot.eligible_context(event, environment)


def test_exact_graph_admission_and_cold_fallback(tmp_path, monkeypatch):
    event, environment = context()
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    for key, value in {**environment, "GITHUB_EVENT_PATH": str(path)}.items():
        monkeypatch.setenv(key, value)
    packager = (Path(pilot.__file__).parent / "build_desktop.py").read_bytes()
    changed_graph = False
    merge_head = "c" * 40

    def git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return environment["GITHUB_SHA"].encode()
        if args[0] == "status":
            return b""
        if args[0] == "rev-list":
            return f"{environment['GITHUB_SHA']} {pilot.SOURCE} {merge_head}".encode()
        if args[0] == "diff":
            return b".github/workflows/ci.yml\0scripts/windows_ci_pilot.py\0"
        if args[0] == "show":
            return packager
        assert args[0] == "rev-parse"
        if args[1].endswith(":scripts/build_desktop.py"):
            return pilot.PACKAGER_BLOB.encode()
        return b"different" if changed_graph and args[1].startswith("a" * 40) else b"same"

    monkeypatch.setattr(pilot, "git", git)
    pilot.admit_source(tmp_path, environment["GITHUB_SHA"])
    merge_head = "d" * 40
    with pytest.raises(ValueError, match="unsupported merge parents"):
        pilot.admit_source(tmp_path, environment["GITHUB_SHA"])
    merge_head = "c" * 40
    changed_graph = True
    with pytest.raises(ValueError, match="graph input changed"):
        pilot.admit_source(tmp_path, environment["GITHUB_SHA"])
    assert pilot.pilot_runner(tmp_path, environment["GITHUB_SHA"], {}, {}) is None


def test_packager_profile_detects_graph_argument_changes():
    source = (Path(pilot.__file__).parent / "build_desktop.py").read_bytes()
    changed = source.replace(b'"--collect-submodules",', b'"--hidden-import",', 1)
    assert pilot.profile(source) != pilot.profile(changed)


def remote_metadata():
    run = {
        "id": pilot.RUN,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": pilot.SOURCE,
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "repository": {"id": pilot.REPOSITORY_ID},
        "head_repository": {"id": pilot.REPOSITORY_ID},
    }
    artifact = {
        "id": pilot.ARTIFACT,
        "expired": False,
        "name": "all-the-context-windows-x86_64",
        "size_in_bytes": pilot.ARCHIVE_SIZE,
        "digest": f"sha256:{pilot.ARCHIVE_SHA256}",
        "workflow_run": {
            "id": pilot.RUN,
            "head_sha": pilot.SOURCE,
            "head_branch": "main",
            "repository_id": pilot.REPOSITORY_ID,
            "head_repository_id": pilot.REPOSITORY_ID,
        },
    }
    return run, artifact


@pytest.mark.parametrize("defect", ["expired", "hash", "run", "event", "failed", "fork"])
def test_only_successful_pinned_protected_main_donor_is_admitted(defect):
    run, artifact = remote_metadata()
    pilot.validate_remote(run, artifact)
    if defect == "expired":
        artifact["expired"] = True
    elif defect == "hash":
        artifact["digest"] = "sha256:" + "0" * 64
    elif defect == "run":
        artifact["workflow_run"]["id"] += 1
    elif defect == "event":
        run["event"] = "pull_request"
    elif defect == "failed":
        run["conclusion"] = "failure"
    else:
        run["head_repository"]["id"] += 1
    with pytest.raises(ValueError, match="untrusted donor"):
        pilot.validate_remote(run, artifact)


def miniature_archive(raw=b"retained dependency\0" * 10, *, members=None):
    prefix = bytearray(512)
    prefix[:2] = b"MZ"
    struct.pack_into("<I", prefix, 0x3C, 64)
    prefix[64:68] = b"PE\0\0"
    struct.pack_into("<H", prefix, 68, 0x8664)
    struct.pack_into("<H", prefix, 88, 0x20B)
    payload, table = b"", b""
    for filename, contents in (members or {"dependency.dll": raw}).items():
        stored = zlib.compress(contents, 9)
        name = filename.encode() + b"\0"
        row_size = ((components.ROW.size + len(name) + 15) // 16) * 16
        table += components.ROW.pack(row_size, len(payload), len(stored), len(contents), 1, b"b")
        table += name.ljust(row_size - components.ROW.size, b"\0")
        payload += stored
    cookie = components.COOKIE.pack(
        components.MAGIC,
        len(payload) + len(table) + components.COOKIE.size,
        len(payload),
        len(table),
        312,
        b"python312.dll",
    )
    return bytes(prefix) + payload + table + cookie


def test_retained_encoding_is_lossless_and_role_inputs_are_isolated():
    raw = b"retained dependency\0" * 10
    data = miniature_archive(raw)
    archive = components.Archive(data)
    entry = archive.entries["dependency.dll"]
    assert entry.raw == raw and components.encode(entry, bytes(raw)) == entry.stored
    assert zlib.decompress(components.encode(entry, raw)) == raw
    other = components.Archive(miniature_archive(b"different"))
    assert other.entries[entry.name].stored != entry.stored
    assert archive.entries[entry.name].raw == raw


@pytest.mark.parametrize(
    "defect", ["oversized", "trailing", "truncated", "method", "bounds", "overlap"]
)
def test_bounded_archive_and_codec_reject_incompatible_data(defect):
    data = bytearray(miniature_archive())
    cookie_at = len(data) - components.COOKIE.size
    cookie = list(components.COOKIE.unpack_from(data, cookie_at))
    toc = len(data) - cookie[1] + cookie[2]
    if defect == "oversized":
        struct.pack_into("!I", data, toc + 12, components.MAX_RAW + 1)
    elif defect == "trailing":
        with pytest.raises(ValueError):
            components.decode(zlib.compress(b"raw") + b"tail", 3, 1)
        return
    elif defect == "truncated":
        data.pop()
    elif defect == "method":
        data[toc + 16] = 2
    elif defect == "bounds":
        struct.pack_into("!I", data, toc + 4, len(data))
    else:
        # A second table row aliases the first payload under a different name.
        row = bytearray(data[toc:cookie_at])
        row[components.ROW.size : components.ROW.size + 1] = b"X"
        cookie[1] += len(row)
        cookie[3] += len(row)
        data[cookie_at:] = row + components.COOKIE.pack(*cookie)
    with pytest.raises((ValueError, struct.error, zlib.error)):
        components.Archive(bytes(data))


def test_wrong_archive_identity_never_extracts(tmp_path):
    bundle = tmp_path / "donor.zip"
    bundle.write_bytes(b"wrong donor")
    with pytest.raises(ValueError, match="size mismatch"):
        pilot.extract_donor(bundle, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.fixture
def donor_bundle(tmp_path, monkeypatch):
    """Small real ZIP, CArchives and maintained provenance; no executable launch."""
    root = tmp_path / "stock"
    root.mkdir()
    paths = {role: root / name for role, name in components.ROLE_NAMES.items()}
    helpers = {}
    for role, path in paths.items():
        if role != "main":
            helpers[path.name] = miniature_archive(role.encode())
            path.write_bytes(helpers[path.name])
    paths["main"].write_bytes(miniature_archive(members=helpers))
    snapshot = provenance.collect_snapshot(paths, label="clean-build-1")
    locks = {name: {"sha256": "0" * 64} for name in provenance.LOCK_FILE_NAMES}
    tools = {"python": "3.12.10", "pyinstaller": "6.21.0", "uv": "0.11.32"}
    payload = provenance.build_payload(
        version="0.1.0-beta.8",
        source_commit=pilot.SOURCE,
        toolchain=tools,
        locks=locks,
        first=snapshot,
        second=provenance.BuildSnapshot("clean-build-2", snapshot.components),
    )
    manifest, sidecar = provenance.write_provenance(root / Path(pilot.MANIFEST).name, payload)
    members = {
        pilot.INSTALLER: paths["main"].read_bytes(),
        pilot.MANIFEST: manifest.read_bytes(),
        f"{pilot.MANIFEST}.sha256": sidecar.read_bytes(),
    }
    bundle = tmp_path / "donor.zip"
    with zipfile.ZipFile(bundle, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    monkeypatch.setattr(pilot, "ARCHIVE_SIZE", bundle.stat().st_size)
    monkeypatch.setattr(pilot, "ARCHIVE_SHA256", hashlib.sha256(bundle.read_bytes()).hexdigest())
    monkeypatch.setattr(pilot, "MANIFEST_SHA256", hashlib.sha256(manifest.read_bytes()).hexdigest())
    monkeypatch.setattr(pilot, "OUTER_SIZES", {name: len(data) for name, data in members.items()})
    return bundle, paths, tools, locks


def test_selective_intake_verifies_all_four_roles_and_sidecar(donor_bundle, tmp_path):
    bundle, stock, _, _ = donor_bundle
    actual = pilot.extract_donor(bundle, tmp_path / "verified")
    assert {role: path.read_bytes() for role, path in actual.items()} == {
        role: path.read_bytes() for role, path in stock.items()
    }
    assert {path.name for path in (tmp_path / "verified").iterdir()} == {
        *components.ROLE_NAMES.values(),
        Path(pilot.MANIFEST).name,
        Path(pilot.MANIFEST).name + ".sha256",
    }


@pytest.mark.parametrize(
    "defect",
    [
        "archive",
        "main",
        "mcp",
        "recovery",
        "updater",
        "provenance",
        "hardlinked-role",
        "lock",
        "toolchain",
        "pending",
        "failed",
    ],
)
def test_unavailable_or_incompatible_donor_falls_back(donor_bundle, tmp_path, monkeypatch, defect):
    bundle, _, tools, locks = donor_bundle
    folder = tmp_path / "download"
    folder.mkdir()
    if defect == "archive":
        raw = bundle.read_bytes()
        bundle.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with pytest.raises(ValueError, match="digest mismatch"):
            pilot.extract_donor(bundle, folder / "verified")
        paths = {}
        pilot.record_completion(folder, False)
    else:
        paths = pilot.extract_donor(bundle, folder / "verified")
        pilot.record_completion(folder, True)
    monkeypatch.setattr(pilot, "admit_source", lambda *_args: None)
    monkeypatch.setattr(pilot.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(pilot, "download_directory", lambda: folder)
    monkeypatch.setattr(components, "component_runner", lambda *_args: pytest.fail("invalid donor"))
    if defect in paths:
        data = bytearray(paths[defect].read_bytes())
        data[10] ^= 1
        paths[defect].write_bytes(data)
    elif defect == "provenance":
        monkeypatch.setattr(pilot, "MANIFEST_SHA256", "0" * 64)
    elif defect == "hardlinked-role":
        (tmp_path / "hardlinked.exe").hardlink_to(paths["mcp"])
    elif defect == "lock":
        locks = {name: {"sha256": "1" * 64} for name in locks}
    elif defect == "toolchain":
        tools = {**tools, "python": "3.12.11"}
    elif defect == "pending":
        (folder / "complete.json").unlink()
        times = iter([0, 126])
        monkeypatch.setattr(pilot.time, "monotonic", lambda: next(times))
    elif defect == "failed":
        (folder / "complete.json").unlink()
        pilot.record_completion(folder, False)
    assert pilot.pilot_runner(tmp_path, "a" * 40, tools, locks) is None


def test_admitted_build_failure_is_not_a_fallback(donor_bundle, tmp_path, monkeypatch):
    bundle, _, tools, locks = donor_bundle
    folder = tmp_path / "download"
    folder.mkdir()
    pilot.extract_donor(bundle, folder / "verified")
    pilot.record_completion(folder, True)
    monkeypatch.setattr(pilot, "admit_source", lambda *_args: None)
    monkeypatch.setattr(pilot.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(pilot, "download_directory", lambda: folder)

    def admitted(*_args):
        def fail(*_args):
            raise ValueError("admitted divergence")

        return fail

    monkeypatch.setattr(components, "component_runner", admitted)
    runner = pilot.pilot_runner(tmp_path, "a" * 40, tools, locks)
    assert runner is not None
    with pytest.raises(ValueError, match="admitted divergence"):
        runner(None, None, None)


def test_download_completion_is_written_after_archive_closes(donor_bundle, tmp_path, monkeypatch):
    _download_completion_case(donor_bundle, tmp_path, monkeypatch, None)


@pytest.mark.parametrize("defect", ["archive", "provenance", "extraction"])
def test_failed_preparation_never_publishes_ready(donor_bundle, tmp_path, monkeypatch, defect):
    _download_completion_case(donor_bundle, tmp_path, monkeypatch, defect)


def _download_completion_case(donor_bundle, tmp_path, monkeypatch, defect):
    bundle, stock, tools, locks = donor_bundle
    run, artifact = remote_metadata()
    raw = bundle.read_bytes()
    if defect == "archive":
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
    if defect == "provenance":
        monkeypatch.setattr(pilot, "MANIFEST_SHA256", "0" * 64)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    folder = pilot.download_directory()
    folder.mkdir()
    monkeypatch.setattr(pilot, "api_json", lambda route: run if "/runs/" in route else artifact)

    def extract(bundle, destination):
        assert response.closed
        assert not (folder / "complete.json").exists()
        if defect == "extraction":
            destination.mkdir()
            (destination / "partial").write_bytes(b"incomplete")
            raise OSError("private diagnostic must not escape")
        result = real_extract(bundle, destination)
        assert not (folder / "complete.json").exists()
        return result

    class Response(io.BytesIO):
        def read(self, size=-1):
            assert not (folder / "complete.json").exists()
            return super().read(size)

    response = Response(raw)
    real_extract = pilot.extract_donor
    monkeypatch.setattr(pilot, "api_request", lambda _route: response)
    monkeypatch.setattr(pilot, "extract_donor", extract)
    monkeypatch.setattr("sys.argv", ["windows_ci_pilot.py", "download"])
    pilot.main()
    assert response.closed
    assert json.loads((folder / "complete.json").read_bytes()) == {"available": defect is None}
    assert not (folder / "complete.part").exists()
    if defect is None:
        assert (folder / "donor.zip").read_bytes() == raw
        consumed = pilot.prepared_donor(tools, locks)
        assert {role: path.read_bytes() for role, path in consumed.items()} == {
            role: path.read_bytes() for role, path in stock.items()
        }
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
        with pytest.raises(FileNotFoundError):
            pilot.prepared_donor(tools, locks)
    else:
        with pytest.raises(ValueError, match="donor unavailable"):
            pilot.prepared_donor(tools, locks)


@pytest.mark.parametrize("first", ["pilot", "ordinary"])
@pytest.mark.parametrize(
    "defect",
    [
        "ready",
        "main",
        "mcp",
        "recovery",
        "updater",
        "manifest",
        "checksum",
        "size",
        "missing-role",
        "lock",
        "toolchain",
        "incomplete",
        "failed",
        "malformed",
    ],
)
def test_both_consumers_revalidate_prepared_bytes(
    donor_bundle, tmp_path, monkeypatch, first, defect
):
    bundle, stock, tools, locks = donor_bundle
    folder = tmp_path / "download"
    folder.mkdir()
    paths = pilot.extract_donor(bundle, folder / "verified")
    pilot.record_completion(folder, True)
    monkeypatch.setattr(pilot, "download_directory", lambda: folder)
    monkeypatch.setattr(pilot, "admit_source", lambda *_args: None)
    monkeypatch.setattr(pilot.platform, "machine", lambda: "AMD64")
    calls = []

    def runner(_root, _commit, received, **options):
        assert {role: path.read_bytes() for role, path in received.items()} == {
            role: path.read_bytes() for role, path in stock.items()
        }
        calls.append(options)
        return sentinel

    sentinel = object()
    monkeypatch.setattr(components, "component_runner", runner)
    consumers = {
        "pilot": pilot.pilot_runner,
        "ordinary": witness.donor_runner,
    }
    assert consumers[first](tmp_path, "a" * 40, tools, locks) is sentinel
    assert calls == ([{}] if first == "pilot" else [{"witness_profile": True}])
    if defect in paths:
        raw = paths[defect].read_bytes()
        paths[defect].write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    elif defect in {"manifest", "checksum"}:
        name = Path(pilot.MANIFEST).name + (".sha256" if defect == "checksum" else "")
        path = folder / "verified" / name
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + b"!")
    elif defect == "size":
        paths["main"].write_bytes(paths["main"].read_bytes() + b"extra")
    elif defect == "missing-role":
        paths["updater"].unlink()
    elif defect == "lock":
        locks = {name: {"sha256": "1" * 64} for name in locks}
    elif defect == "toolchain":
        tools = {**tools, "python": "3.12.11"}
    elif defect == "incomplete":
        (folder / "complete.json").unlink()
        times = iter([0, 126])
        monkeypatch.setattr(pilot.time, "monotonic", lambda: next(times))
    elif defect in {"failed", "malformed"}:
        (folder / "complete.json").write_bytes(
            b'{"available": false}' if defect == "failed" else b'{"available": true}trailing'
        )
    second = "ordinary" if first == "pilot" else "pilot"
    if defect == "ready":
        assert consumers[second](tmp_path, "a" * 40, tools, locks) is sentinel
        assert len(calls) == 2
    elif second == "pilot":
        assert consumers[second](tmp_path, "a" * 40, tools, locks) is None
        assert len(calls) == 1
    else:
        with pytest.raises((ValueError, OSError)):
            consumers[second](tmp_path, "a" * 40, tools, locks)
        assert len(calls) == 1


@pytest.mark.parametrize("ordinary", [False, True])
@pytest.mark.parametrize("ready", [False, True])
def test_public_verifier_uses_ready_donor_or_complete_cold_builds(
    donor_bundle, tmp_path, monkeypatch, ordinary, ready
):
    bundle, stock, tools, locks = donor_bundle
    folder = tmp_path / "download"
    folder.mkdir()
    paths = pilot.extract_donor(bundle, folder / "verified")
    pilot.record_completion(folder, True)
    if not ready:
        paths["mcp"].write_bytes(b"altered")
    monkeypatch.setattr(pilot, "download_directory", lambda: folder)
    monkeypatch.setattr(pilot, "admit_source", lambda *_args: None)
    monkeypatch.setattr(pilot.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(verifier.platform, "system", lambda: "Windows")
    monkeypatch.setattr(verifier, "project_version", lambda _root: "0.1.0-beta.8")
    monkeypatch.setattr(verifier, "checked_out_commit", lambda _root: "a" * 40)
    monkeypatch.setattr(verifier, "current_toolchain", lambda: tools)
    monkeypatch.setattr(verifier, "lock_digests", lambda _root: locks)

    def admission(*_args):
        if not ordinary:
            raise ValueError("not an ordinary product edit")
        return {"head": "a" * 40}

    monkeypatch.setattr(witness, "admit_source", admission)
    monkeypatch.setattr(witness, "expected_receipt", lambda *_args: {})
    snapshot = provenance.collect_snapshot(stock, label="stock")
    monkeypatch.setattr(witness, "acquire", lambda _expected: snapshot.components)

    def checks(_root, candidate):
        for name in ("platform", "release"):
            (candidate / "dist" / name).mkdir()
        return 0

    monkeypatch.setattr(witness, "provisional_checks", checks)
    builds = []

    def build(_root, build, dist, *, retained=False, **_options):
        build.mkdir(parents=True)
        dist.mkdir(parents=True)
        outputs = verifier.build_desktop.component_paths(build_root=build, dist_root=dist)
        for role, path in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(stock[role].read_bytes())
        builds.append((retained, build, dist))
        return provenance.collect_snapshot(outputs, label="build")

    def retained(_root, _commit, received, **options):
        assert options == ({"witness_profile": True} if ordinary else {})
        assert {role: path.read_bytes() for role, path in received.items()} == {
            role: path.read_bytes() for role, path in stock.items()
        }
        return lambda root, build_root, dist_root: build(root, build_root, dist_root, retained=True)

    monkeypatch.setattr(components, "component_runner", retained)
    monkeypatch.setattr(verifier, "_run_clean_build", build)
    output = tmp_path / "result"
    github_output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_reproducible_build.py",
            "--repository-root",
            str(tmp_path),
            "--source-commit",
            "a" * 40,
            "--windows-ci-donor-pilot",
            "--build-root",
            str(output / "build/desktop"),
            "--dist-root",
            str(output / "dist/desktop"),
            "--output-dir",
            str(output / "dist/native-build-provenance"),
        ],
    )
    assert verifier.main() == 0
    assert len(builds) == 2 and all(retained == ready for retained, _, _ in builds)
    assert builds[0][1] != builds[1][1] and builds[0][2] != builds[1][2]
    assert not any(build.exists() or dist.exists() for _, build, dist in builds)
    route = "witnessed" if ready and ordinary else "retained" if ready else "cold"
    assert github_output.read_text() == f"route={route}\n" + (
        "lifecycle_success=true\n" if route == "witnessed" else ""
    )
    final = verifier.build_desktop.component_paths(
        build_root=output / "build/desktop", dist_root=output / "dist/desktop"
    )
    provenance.verify_provenance(
        output / "dist/native-build-provenance" / provenance.PROVENANCE_FILE_NAME,
        final,
        version="0.1.0-beta.8",
        source_commit="a" * 40,
    )


@pytest.mark.parametrize("overlap_first_run", [False, True])
def test_package_overlap_drains_lifecycle_and_fails_closed(monkeypatch, overlap_first_run):
    entered = threading.Barrier(2)
    finished = threading.Event()

    def lifecycle():
        entered.wait(timeout=5)
        finished.set()

    def fail_prepare():
        entered.wait(timeout=5)
        raise subprocess.CalledProcessError(1, ["package"])

    if overlap_first_run:
        monkeypatch.setattr(package_checks, "first_run", lifecycle)
        monkeypatch.setattr(package_checks, "packaged_checks_then_prepare", fail_prepare)
    else:
        monkeypatch.setattr(package_checks, "lifecycle", lifecycle)
        monkeypatch.setattr(package_checks, "prepare", fail_prepare)
    assert package_checks.main(overlap_first_run=overlap_first_run) == 1
    assert finished.is_set()


@pytest.mark.parametrize("overlap_first_run", [False, True])
def test_package_overlap_keeps_stock_commands_and_provenance(monkeypatch, overlap_first_run):
    calls = []
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(package_checks, "run", lambda *args: calls.append(args))
    monkeypatch.setattr(package_checks, "project_version", lambda _root: "0.1.0-beta.8")
    assert package_checks.main(overlap_first_run=overlap_first_run) == 0
    lifecycle = [
        call for call in calls if call[0].startswith("smoke_") and "--artifact-root" in call
    ]
    lineage = [call[0] for call in lifecycle]
    assert lineage.index("smoke_desktop_artifact.py") < lineage.index("smoke_packaged_recovery.py")
    if not overlap_first_run:
        assert lineage[-1] == "smoke_packaged_first_run.py"
    assert all(list(call[1:]) == package_checks.PROVENANCE for call in lifecycle)
    assert {call[0] for call in calls} == {
        "smoke_desktop_artifact.py",
        "smoke_packaged_recovery.py",
        "smoke_packaged_first_run.py",
        "package_desktop.py",
        "smoke_platform_package.py",
        "build_release_assets.py",
    }

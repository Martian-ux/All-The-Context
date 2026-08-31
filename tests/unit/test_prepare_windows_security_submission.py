"""Focused synthetic tests for the content-free Windows submission preparer."""

from __future__ import annotations

import ast
import hashlib
import json
import zipfile
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path

import pytest

from scripts import prepare_windows_security_submission as submission_module
from scripts.build_release_assets import build_archive
from scripts.installed_component_manifest import (
    MANIFEST_FILE_NAME,
    canonical_json,
    create_manifest,
)
from scripts.prepare_windows_security_submission import (
    BUNDLE_CHECKSUM_FILE_NAME,
    BUNDLE_FILE_NAME,
    OPERATOR_INSTRUCTIONS,
    WindowsSecuritySubmissionError,
    create_bundle,
)

SOURCE_COMMIT = "a" * 40
VERSION = "0.1.0-beta.7"


def _pe_image(*, certificate_offset: int = 0, certificate_size: int = 0) -> bytes:
    pe_offset = 128
    optional_size = 240
    header_size = pe_offset + 24 + optional_size
    image = bytearray(max(header_size, certificate_offset + certificate_size))
    image[:2] = b"MZ"
    image[60:64] = pe_offset.to_bytes(4, "little")
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    image[pe_offset + 20 : pe_offset + 22] = optional_size.to_bytes(2, "little")
    optional_offset = pe_offset + 24
    image[optional_offset : optional_offset + 2] = (0x20B).to_bytes(2, "little")
    image[optional_offset + 108 : optional_offset + 112] = (16).to_bytes(4, "little")
    certificate_entry = optional_offset + 112 + (4 * 8)
    image[certificate_entry : certificate_entry + 4] = certificate_offset.to_bytes(4, "little")
    image[certificate_entry + 4 : certificate_entry + 8] = certificate_size.to_bytes(
        4, "little"
    )
    return bytes(image)


@dataclass(frozen=True)
class _Stage:
    root: Path
    components: dict[str, Path]
    package: Path
    direct_package: Path
    manifest: Path
    manifest_checksum: Path
    archive: Path


def _stage(tmp_path: Path) -> _Stage:
    root = tmp_path / "source-root"
    root.mkdir()
    build = root / "build"
    build.mkdir()
    data = {
        "main": _pe_image(),
        "mcp": _pe_image(certificate_offset=392, certificate_size=16),
        "recovery": _pe_image(),
        "updater": _pe_image(),
    }
    names = {
        "main": "AllTheContextSetup.exe",
        "mcp": "AllTheContextMCP.exe",
        "recovery": "AllTheContextRecovery.exe",
        "updater": "AllTheContextUpdater.exe",
    }
    components: dict[str, Path] = {}
    for role, contents in data.items():
        path = build / names[role]
        path.write_bytes(contents)
        components[role] = path

    package_dir = root / "manifest"
    package_dir.mkdir()
    package = package_dir / "AllTheContextSetup.exe"
    package.write_bytes(components["main"].read_bytes())
    direct = root / "direct" / "all-the-context-0.1.0-beta.7-windows-x86_64-unsigned.exe"
    direct.parent.mkdir()
    direct.write_bytes(package.read_bytes())
    manifest, checksum = create_manifest(
        output_dir=package_dir,
        package_path=package,
        direct_package_path=direct,
        component_paths=components,
        source_root=root,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
    )
    archive = build_archive(
        package_dir,
        root / "release-assets",
        version=VERSION,
        platform_name="windows",
        architecture="x86_64",
    )
    return _Stage(root, components, package, direct, manifest, checksum, archive)


def _bundle_kwargs(stage: _Stage, output_dir: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "output_dir": output_dir,
        "archive_path": stage.archive,
        "package_path": stage.package,
        "direct_package_path": stage.direct_package,
        "main_path": stage.components["main"],
        "mcp_path": stage.components["mcp"],
        "recovery_path": stage.components["recovery"],
        "updater_path": stage.components["updater"],
        "manifest_path": stage.manifest,
        "manifest_checksum_path": stage.manifest_checksum,
        "source_root": stage.root,
        "role": "mcp",
        "source_commit": SOURCE_COMMIT,
        "version": VERSION,
    }
    values.update(overrides)
    return values


def _assert_no_bundle(output_dir: Path) -> None:
    assert not output_dir.exists() or not any(output_dir.iterdir())


def _rewrite_manifest(stage: _Stage, mutate) -> None:
    payload = json.loads(stage.manifest.read_bytes())
    mutate(payload)
    raw = canonical_json(payload)
    stage.manifest.write_bytes(raw)
    stage.manifest_checksum.write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {MANIFEST_FILE_NAME}\n",
        encoding="ascii",
        newline="\n",
    )


def test_bundle_is_content_free_and_binds_all_verified_inputs(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    output_dir = stage.root / "submission"

    bundle_path, checksum_path = create_bundle(**_bundle_kwargs(stage, output_dir))

    raw_bundle = bundle_path.read_bytes()
    payload = json.loads(raw_bundle)
    helper = stage.components["mcp"]
    helper_digest = hashlib.sha256(helper.read_bytes()).hexdigest()
    manifest_digest = hashlib.sha256(stage.manifest.read_bytes()).hexdigest()
    assert bundle_path.name == BUNDLE_FILE_NAME
    assert checksum_path.name == BUNDLE_CHECKSUM_FILE_NAME
    assert payload["candidate"] == {
        "authenticode_status": "present-unverified",
        "filename": "AllTheContextMCP.exe",
        "role": "mcp",
        "sha256": helper_digest,
        "size": helper.stat().st_size,
    }
    assert payload["manifest_binding"]["manifest"]["sha256"] == manifest_digest
    assert payload["manifest_binding"]["manifest"]["size"] == stage.manifest.stat().st_size
    assert payload["manifest_binding"]["component"] == payload["candidate"]
    assert payload["manifest_binding"]["archive"]["filename"] == stage.archive.name
    assert payload["manifest_binding"]["package"]["filename"] == stage.package.name
    assert payload["manifest_binding"]["direct_package"]["filename"] == stage.direct_package.name
    assert payload["manifest_binding"]["source_commit"] == SOURCE_COMMIT
    assert payload["manifest_binding"]["version"] == VERSION
    assert payload["source_commit"] == SOURCE_COMMIT
    assert payload["status"] == "hold"
    assert payload["detection"] == {
        "event_id": None,
        "observed_at": None,
        "product": None,
        "reassessment_id": None,
        "severity": None,
        "submission_id": None,
        "status": "not-provided",
        "threat_name": None,
        "vendor": None,
    }
    assert payload["trust"] == {
        "clearance": "not-claimed",
        "malware_status": "not-determined",
        "publisher_status": "not-asserted",
        "submission_status": "not-submitted",
    }
    assert payload["operator_instructions"] == list(OPERATOR_INSTRUCTIONS)
    assert str(stage.root) not in raw_bundle.decode("utf-8")
    assert b"password" not in raw_bundle.lower()
    assert checksum_path.read_text(encoding="ascii") == (
        f"{hashlib.sha256(raw_bundle).hexdigest()}  {BUNDLE_FILE_NAME}\n"
    )


def test_component_mutation_fails_closed_without_writing_output(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    stage.components["mcp"].write_bytes(_pe_image(certificate_offset=392, certificate_size=24))
    output_dir = stage.root / "submission"

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(**_bundle_kwargs(stage, output_dir))
    _assert_no_bundle(output_dir)


def test_manifest_checksum_and_source_binding_are_required(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    stage.manifest_checksum.write_text(
        "0" * 64 + f"  {MANIFEST_FILE_NAME}\n", encoding="ascii"
    )

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(
            **_bundle_kwargs(stage, stage.root / "bad-checksum", source_commit="b" * 40)
        )
    _assert_no_bundle(stage.root / "bad-checksum")

    stage.manifest_checksum.write_text(
        f"{hashlib.sha256(stage.manifest.read_bytes()).hexdigest()}  "
        f"{MANIFEST_FILE_NAME}\n",
        encoding="ascii",
        newline="\n",
    )
    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(
            **_bundle_kwargs(stage, stage.root / "bad-source", source_commit="b" * 40)
        )
    _assert_no_bundle(stage.root / "bad-source")


def test_manifest_version_binding_is_required_and_exact(tmp_path: Path) -> None:
    stage = _stage(tmp_path)

    with pytest.raises(WindowsSecuritySubmissionError, match="product version is invalid"):
        create_bundle(
            **_bundle_kwargs(stage, stage.root / "bad-format", version="not-a-version")
        )
    _assert_no_bundle(stage.root / "bad-format")

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(
            **_bundle_kwargs(stage, stage.root / "bad-match", version="0.1.0-beta.8")
        )
    _assert_no_bundle(stage.root / "bad-match")

    bundle_path, _checksum_path = create_bundle(
        **_bundle_kwargs(stage, stage.root / "valid")
    )
    payload = json.loads(bundle_path.read_bytes())
    assert payload["status"] == "hold"
    assert payload["trust"]["clearance"] == "not-claimed"
    assert str(stage.root) not in bundle_path.read_text(encoding="utf-8")


def test_malformed_manifest_fails_closed_through_canonical_verifier(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    raw = stage.manifest.read_bytes().replace(b'"component_count": 4', b'"component_count": NaN')
    stage.manifest.write_bytes(raw)
    stage.manifest_checksum.write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {MANIFEST_FILE_NAME}\n",
        encoding="ascii",
        newline="\n",
    )

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(**_bundle_kwargs(stage, stage.root / "submission"))
    _assert_no_bundle(stage.root / "submission")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["components"][1].update({"sha256": "0" * 64}),
        lambda payload: payload["components"][2].update({"sha256": "0" * 64}),
    ],
    ids=["self-authored-selected-manifest", "fabricated-unselected-component"],
)
def test_self_authored_or_fabricated_manifest_cannot_produce_bundle(
    tmp_path: Path, mutate
) -> None:
    stage = _stage(tmp_path)
    _rewrite_manifest(stage, mutate)

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(**_bundle_kwargs(stage, stage.root / "submission"))
    _assert_no_bundle(stage.root / "submission")


@pytest.mark.parametrize("field", ["package", "direct_package"])
def test_wrong_package_or_direct_package_cannot_produce_bundle(
    tmp_path: Path, field: str
) -> None:
    stage = _stage(tmp_path)
    replacement = _pe_image(certificate_offset=400, certificate_size=8)
    if field == "package":
        stage.package.write_bytes(replacement)
    else:
        stage.direct_package.write_bytes(replacement)

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(**_bundle_kwargs(stage, stage.root / "submission"))
    _assert_no_bundle(stage.root / "submission")


def test_wrong_archive_cannot_produce_bundle(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    wrong_archive = stage.root / "wrong-archive.zip"
    replacement = _pe_image(certificate_offset=400, certificate_size=8)
    with zipfile.ZipFile(stage.archive, "r") as source, zipfile.ZipFile(
        wrong_archive, "w", compression=zipfile.ZIP_DEFLATED
    ) as destination:
        for info in source.infolist():
            data = (
                replacement
                if Path(info.filename).name == stage.package.name
                else source.read(info)
            )
            destination.writestr(info, data)

    with pytest.raises(WindowsSecuritySubmissionError, match="verification cannot be verified"):
        create_bundle(
            **_bundle_kwargs(stage, stage.root / "submission", archive_path=wrong_archive)
        )
    _assert_no_bundle(stage.root / "submission")


def test_cli_requires_every_exact_input_and_candidate_version() -> None:
    parser = submission_module._parser()
    required = [
        "--source-root",
        "source-root",
        "--archive",
        "candidate.zip",
        "--package",
        "AllTheContextSetup.exe",
        "--direct-package",
        "direct.exe",
        "--main",
        "AllTheContextSetup.exe",
        "--mcp",
        "AllTheContextMCP.exe",
        "--recovery",
        "AllTheContextRecovery.exe",
        "--updater",
        "AllTheContextUpdater.exe",
        "--manifest",
        MANIFEST_FILE_NAME,
        "--manifest-checksum",
        f"{MANIFEST_FILE_NAME}.sha256",
        "--role",
        "mcp",
        "--source-commit",
        SOURCE_COMMIT,
        "--output-dir",
        "submission",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(required)
    parser.parse_args([*required, "--version", VERSION])


def test_existing_output_is_never_replaced(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    output_dir = stage.root / "submission"
    create_bundle(**_bundle_kwargs(stage, output_dir))
    original = (output_dir / BUNDLE_FILE_NAME).read_bytes()

    with pytest.raises(WindowsSecuritySubmissionError, match="output directory cannot be verified"):
        create_bundle(**_bundle_kwargs(stage, output_dir))
    assert (output_dir / BUNDLE_FILE_NAME).read_bytes() == original


def test_existing_empty_output_is_not_operation_owned(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    output_dir = stage.root / "submission"
    output_dir.mkdir()

    with pytest.raises(WindowsSecuritySubmissionError, match="output directory cannot be verified"):
        create_bundle(**_bundle_kwargs(stage, output_dir))
    assert not any(output_dir.iterdir())


def test_output_identity_change_blocks_exclusive_write(tmp_path: Path, monkeypatch) -> None:
    stage = _stage(tmp_path)
    output, identity = submission_module._create_owned_output_directory(
        stage.root / "submission", source_root=stage.root
    )
    replacement = dataclass_replace(identity, inode=identity.inode + 1)
    monkeypatch.setattr(submission_module, "_directory_identity", lambda _path: replacement)

    with pytest.raises(WindowsSecuritySubmissionError, match="identity changed"):
        submission_module._write_owned_new(
            output,
            identity,
            BUNDLE_FILE_NAME,
            b"synthetic bundle",
            label="security submission bundle",
            expected_names=set(),
        )
    assert not (output / BUNDLE_FILE_NAME).exists()


def test_cleanup_skips_unlink_when_output_identity_changes(tmp_path: Path, monkeypatch) -> None:
    stage = _stage(tmp_path)
    output, identity = submission_module._create_owned_output_directory(
        stage.root / "submission", source_root=stage.root
    )
    bundle_identity = submission_module._write_owned_new(
        output,
        identity,
        BUNDLE_FILE_NAME,
        b"synthetic bundle",
        label="security submission bundle",
        expected_names=set(),
    )
    replacement = dataclass_replace(identity, inode=identity.inode + 1)
    monkeypatch.setattr(submission_module, "_directory_identity", lambda _path: replacement)

    with pytest.raises(WindowsSecuritySubmissionError, match="identity changed"):
        submission_module._cleanup_owned_file(
            output,
            identity,
            BUNDLE_FILE_NAME,
            bundle_identity,
            expected_names={BUNDLE_FILE_NAME},
        )
    assert (output / BUNDLE_FILE_NAME).read_bytes() == b"synthetic bundle"


def test_cleanup_skips_unlink_when_output_file_identity_changes(
    tmp_path: Path, monkeypatch
) -> None:
    stage = _stage(tmp_path)
    output, identity = submission_module._create_owned_output_directory(
        stage.root / "submission", source_root=stage.root
    )
    bundle_identity = submission_module._write_owned_new(
        output,
        identity,
        BUNDLE_FILE_NAME,
        b"synthetic bundle",
        label="security submission bundle",
        expected_names=set(),
    )
    replacement = dataclass_replace(bundle_identity, inode=bundle_identity.inode + 1)
    monkeypatch.setattr(submission_module, "_output_file_identity", lambda _path: replacement)

    with pytest.raises(WindowsSecuritySubmissionError, match="file identity changed"):
        submission_module._cleanup_owned_file(
            output,
            identity,
            BUNDLE_FILE_NAME,
            bundle_identity,
            expected_names={BUNDLE_FILE_NAME},
        )
    assert (output / BUNDLE_FILE_NAME).read_bytes() == b"synthetic bundle"


def test_preparer_has_no_process_or_archive_execution_surface() -> None:
    source = Path(__file__).parents[2] / "scripts" / "prepare_windows_security_submission.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )
    assert "subprocess" not in imported
    assert "zipfile" not in imported
    text = source.read_text(encoding="utf-8").casefold()
    assert "subprocess" not in text
    assert "zipfile" not in text


def test_component_identity_is_revalidated_between_verification_phases(
    tmp_path: Path, monkeypatch
) -> None:
    stage = _stage(tmp_path)
    original = submission_module.manifest_module.verify_archive

    def swap_component(**kwargs):
        result = original(**kwargs)
        component = stage.components["mcp"]
        component.write_bytes(component.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(submission_module.manifest_module, "verify_archive", swap_component)
    with pytest.raises(WindowsSecuritySubmissionError, match="mcp executable"):
        create_bundle(**_bundle_kwargs(stage, stage.root / "submission"))
    assert not (stage.root / "submission").exists()


def test_source_root_identity_is_revalidated_between_verification_phases(
    tmp_path: Path, monkeypatch
) -> None:
    stage = _stage(tmp_path)
    original = submission_module.manifest_module.verify_manifest

    def rotate_root(**kwargs):
        result = original(**kwargs)
        stage.root.rename(tmp_path / "rotated-source-root")
        return result

    monkeypatch.setattr(submission_module.manifest_module, "verify_manifest", rotate_root)
    with pytest.raises(WindowsSecuritySubmissionError, match="source root"):
        create_bundle(**_bundle_kwargs(stage, stage.root / "submission"))
    assert not (tmp_path / "source-root" / "submission").exists()


def test_failed_exclusive_write_cleans_only_owned_output(tmp_path: Path, monkeypatch) -> None:
    stage = _stage(tmp_path)
    original = submission_module._write_all
    calls = 0

    def fail_checksum(stream, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            stream.write(content[:1])
            raise OSError("simulated checksum write failure")
        return original(stream, content)

    monkeypatch.setattr(submission_module, "_write_all", fail_checksum)
    output = stage.root / "submission"
    with pytest.raises(WindowsSecuritySubmissionError, match="security submission checksum"):
        create_bundle(**_bundle_kwargs(stage, output))
    assert not output.exists()

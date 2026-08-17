from __future__ import annotations

import json
import os
import plistlib
import shutil
import sys
from pathlib import Path

import pytest

from scripts.run_macos_native_supporting_checks import (
    PhaseObservation,
    SupportingCheckError,
    create_results_directory,
    run_subprocess_phase,
    run_supporting_checks,
    select_macos_artifact,
    stage_macos_app_from_dmg,
)


def _descriptor(name: str, digest: str = "a" * 64, size: int = 1) -> dict[str, object]:
    return {"name": name, "sha256": digest, "size": size}


def test_results_directory_must_be_new_and_outside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(SupportingCheckError, match="results_directory_inside_source"):
        create_results_directory(source / "results", project_root=source)

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(SupportingCheckError, match="results_directory_already_exists"):
        create_results_directory(existing, project_root=source)

    created = create_results_directory(tmp_path / "new-results", project_root=source)
    assert created.is_dir()


def test_select_macos_artifact_requires_one_matching_architecture() -> None:
    arm = {"platform": "macos", "architecture": "arm64"}
    intel = {"platform": "macos", "architecture": "x86_64"}
    windows = {"platform": "windows", "architecture": "x86_64"}

    assert select_macos_artifact({"artifacts": [arm, intel, windows]}, architecture="arm64") is arm
    with pytest.raises(SupportingCheckError, match="not_unique"):
        select_macos_artifact({"artifacts": [arm, arm]}, architecture="arm64")
    with pytest.raises(SupportingCheckError, match="not_unique"):
        select_macos_artifact({"artifacts": [intel]}, architecture="arm64")


def test_subprocess_phase_retains_only_counts(tmp_path: Path) -> None:
    observation = run_subprocess_phase(
        "content_free",
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(b'abc');sys.stderr.buffer.write(b'de')",
        ],
        project_root=tmp_path,
        environment=dict(os.environ),
    )

    assert observation == PhaseObservation(
        phase="content_free",
        status="pass",
        return_code=0,
        duration_ms=observation.duration_ms,
        stdout_bytes=3,
        stderr_bytes=2,
    )
    assert not hasattr(observation, "stdout")
    assert not hasattr(observation, "stderr")


def test_dmg_staging_uses_read_only_mount_and_detaches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "candidate.dmg"
    package.write_bytes(b"dmg")
    destination = tmp_path / "source" / "dist" / "desktop" / "AllTheContext.app"
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object):
        commands.append(command)
        if command[1] == "attach":
            mountpoint = Path(command[command.index("-mountpoint") + 1])
            source = mountpoint / "All The Context.app"
            source.mkdir()
            payload = {"system-entities": [{"mount-point": str(mountpoint)}]}
            return __import__("subprocess").CompletedProcess(
                command, 0, plistlib.dumps(payload), b""
            )
        if command[0] == "ditto":
            shutil.copytree(command[1], command[2])
            return __import__("subprocess").CompletedProcess(command, 0, b"", b"")
        if command[1] == "detach":
            shutil.rmtree(Path(command[2]) / "All The Context.app")
            return __import__("subprocess").CompletedProcess(command, 0, b"", b"")
        raise AssertionError(command)

    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.subprocess.run",
        run,
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.verify_macos_app",
        lambda *_args, **_kwargs: None,
    )

    stage_macos_app_from_dmg(
        package,
        destination=destination,
        architecture="arm64",
        version="0.1.0-beta.1",
    )

    assert destination.is_dir()
    attach = commands[0]
    assert "-readonly" in attach
    assert "-nobrowse" in attach
    assert "-mountpoint" in attach
    assert any(command[1] == "detach" for command in commands)


def test_supporting_run_is_candidate_bound_and_never_claims_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    release = tmp_path / "release"
    source.mkdir()
    release.mkdir()
    candidate_path = release / "release-candidate-v1.json"
    candidate_path.write_text("{}\n", encoding="utf-8")
    candidate = {
        "source_commit": "1" * 40,
        "version": "0.1.0-beta.1",
        "artifacts": [
            {
                "platform": "macos",
                "architecture": "arm64",
                "direct_package": _descriptor("candidate-arm64.dmg"),
                "ota_archive": _descriptor("candidate-arm64.zip", "b" * 64, 2),
            }
        ],
    }
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.verify_candidate",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.validate_source_checkout",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.validate_source_python_environment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks._git_text",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.collect_host_facts",
        lambda: object(),
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.evaluate_host_facts",
        lambda *_args, **_kwargs: {
            "kind": "macos_acceptance_preflight",
            "status": "pass",
            "native_acceptance_eligible": True,
        },
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.verify_package",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.stage_macos_app_from_dmg",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.run_macos_native_supporting_checks.run_subprocess_phase",
        lambda phase, *_args, **_kwargs: PhaseObservation(phase, "pass", 0, 1, 2, 3),
    )

    results = tmp_path / "results"
    report = run_supporting_checks(
        project_root=source,
        release_dir=release,
        candidate_path=candidate_path,
        expected_candidate_sha256="c" * 64,
        architecture="arm64",
        expected_os_version="26.0",
        results_dir=results,
        dedicated_clean_user_attested=True,
    )

    assert report["status"] == "pass"
    assert report["acceptance_claimed"] is False
    assert report["canonical_receipts_emitted"] is False
    assert report["candidate_sha256"] == "c" * 64
    assert report["source_commit"] == "1" * 40
    assert report["artifact_digests"] == {
        "direct_package": _descriptor("candidate-arm64.dmg"),
        "ota_archive": _descriptor("candidate-arm64.zip", "b" * 64, 2),
    }
    assert len(report["remaining_native_acceptance"]) == 8
    assert all(phase["status"] == "pass" for phase in report["phases"])
    assert report["cleanup"] == {
        "desktop_staging_removed": True,
        "ephemeral_run_root_removed": True,
        "source_checkout_clean": True,
    }
    persisted = json.loads(
        (results / "macos-native-supporting-checks.json").read_text(encoding="utf-8")
    )
    assert persisted == report
    assert not any(key.endswith("path") for key in persisted)

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
from allthecontext.repository_security import (
    SecurityScanError,
    require_clean,
    scan_artifact_directory,
    scan_committed_tree,
    scan_git_history,
    scan_tree,
)


def _synthetic(kind: str, body: str) -> str:
    """Assemble canary text at runtime so source files stay scan-clean."""

    return f"ATC_CANARY_{kind}_{body}"


def test_tree_scan_detects_synthetic_canaries_without_printing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = _synthetic("SECRET", "ABCDEF12")
    raw_context = _synthetic("RAW_CONTEXT", "ZZZZ9999")
    developer_path = "C:" + "\\Users\\" + "Developer\\vault.db"
    (tmp_path / "ok.txt").write_text("safe text\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(f"prefix {secret} suffix\n", encoding="utf-8")
    (tmp_path / "context.txt").write_text(f"{raw_context}\n", encoding="utf-8")
    (tmp_path / "path.txt").write_text(f"path is {developer_path}\n", encoding="utf-8")
    (tmp_path / "tool.exe").write_bytes(b"MZ")

    report = scan_tree(tmp_path, allow_absolute_paths=False)
    classes = {finding.finding_class for finding in report.findings}
    assert "credential_canary" in classes
    assert "raw_context_canary" in classes
    assert "absolute_developer_path" in classes
    assert "unexpected_executable" in classes
    serialized = str(report.as_dict())
    assert secret not in serialized
    # Path name may appear; matched secret body must not.
    assert "ATC_CANARY_SECRET_" not in serialized
    with pytest.raises(SecurityScanError, match="security scan failed"):
        require_clean(report)
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_tree_scan_detects_private_key_blocks(tmp_path: Path) -> None:
    begin = "-----BEGIN " + "PRIVATE KEY-----"
    end = "-----END " + "PRIVATE KEY-----"
    pem = f"{begin}\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC\n{end}\n"
    (tmp_path / "key.pem").write_text(pem, encoding="utf-8")
    report = scan_tree(tmp_path)
    assert any(item.finding_class == "private_key_marker" for item in report.findings)


def test_artifact_scan_reads_zip_entries(tmp_path: Path) -> None:
    import zipfile

    token = _synthetic("TOKEN", "PUBLICONLY1")
    archive = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("nested/note.txt", f"{token}\n")
    report = scan_artifact_directory(tmp_path)
    assert any(item.finding_class == "credential_canary" for item in report.findings)
    assert token not in str(report.as_dict())


def test_clean_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("# safe\n", encoding="utf-8")
    report = scan_tree(tmp_path)
    require_clean(report)
    assert report.ok


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, relative: str, value: bytes) -> str:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    _git(repository, "add", relative)
    _git(repository, "commit", "-m", relative)
    return _git(repository, "rev-parse", "HEAD")


def test_artifact_scan_reads_large_zip_members_and_complete_private_keys(tmp_path: Path) -> None:
    archive = tmp_path / "candidate.zip"
    begin = b"-----BEGIN " + b"PRIVATE KEY-----"
    end = b"-----END " + b"PRIVATE KEY-----"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "nested/key.pem",
            b"x" * (17 * 1024 * 1024) + begin + b"\nbody\n" + end,
        )

    report = scan_artifact_directory(tmp_path)

    assert any(item.finding_class == "private_key_marker" for item in report.findings)


def test_history_scans_deleted_archives_but_not_unrelated_branches(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "security@example.invalid")
    _git(repository, "config", "user.name", "Security Test")
    clean = _commit(repository, "readme.md", b"clean\n")
    _git(repository, "switch", "-c", "unrelated")
    unrelated = b"ATC_CANARY_" + b"SECRET_" + b"UNRELATED1"
    _commit(repository, "unrelated.txt", unrelated)
    _git(repository, "switch", "-c", "candidate", clean)
    archive = tmp_path / "history.zip"
    secret = b"ATC_CANARY_" + b"TOKEN_" + b"ARCHIVED1"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("deep/note.txt", b"x" * (300 * 1024) + secret)
    _commit(repository, "renamed.bin", archive.read_bytes())
    head = _commit(repository, "renamed.bin", b"removed\n")

    report = scan_git_history(repository, source_commit=head)

    assert any(item.finding_class == "credential_canary" for item in report.findings)
    assert all(item.path != "unrelated.txt" for item in report.findings)


def test_committed_tree_is_bound_to_source_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "security@example.invalid")
    _git(repository, "config", "user.name", "Security Test")
    commit = _commit(repository, "safe.txt", b"safe\n")
    (repository / "ignored.txt").write_bytes(b"ATC_CANARY_" + b"SECRET_" + b"WORKTREE1")

    report = scan_committed_tree(repository, source_commit=commit)

    require_clean(report)

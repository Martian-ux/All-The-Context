"""Content-free repository, history, and artifact security scanning.

Findings report only class, relative path, and safe coordinates. Matched
secret or personal-context bytes are never written to logs, receipts, or
stdout.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

FindingClass = Literal[
    "private_key_marker",
    "credential_canary",
    "raw_context_canary",
    "absolute_developer_path",
    "unexpected_executable",
]

SEVERITY = {
    "private_key_marker": "P0",
    "credential_canary": "P0",
    "raw_context_canary": "P0",
    "absolute_developer_path": "P1",
    "unexpected_executable": "P1",
}

PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
)

# Synthetic canaries only. Real secrets must never be committed as fixtures.
CREDENTIAL_CANARY_PATTERNS = (
    re.compile(rb"ATC_CANARY_SECRET_[A-Z0-9_]{8,}"),
    re.compile(rb"ATC_CANARY_TOKEN_[A-Z0-9_]{8,}"),
    re.compile(rb"ATC_CANARY_PASSWORD_[A-Z0-9_]{8,}"),
    re.compile(rb"ghp_[A-Za-z0-9]{20,}"),  # classic GitHub PAT shape
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
)

RAW_CONTEXT_CANARY_PATTERNS = (
    re.compile(rb"ATC_CANARY_RAW_CONTEXT_[A-Z0-9_]{8,}"),
    re.compile(rb"ATC_CANARY_PERSONAL_MEMORY_[A-Z0-9_]{8,}"),
)

# Absolute developer machine paths. Match Windows and POSIX home layouts.
ABSOLUTE_PATH_PATTERNS = (
    re.compile(rb"(?i)(?:^|[\s\"'=])C:\\Users\\[^\s\"']+"),
    re.compile(rb"(?i)(?:^|[\s\"'=])/Users/[^/\s\"']+/"),
    re.compile(rb"(?i)(?:^|[\s\"'=])/home/[^/\s\"']+/"),
    re.compile(rb"(?i)(?:^|[\s\"'=])/Users/Noah/"),
)

EXECUTABLE_SUFFIXES = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bat",
        ".cmd",
        ".ps1",
        ".com",
        ".scr",
        ".msi",
        ".appimage",
    }
)

# Text-like extensions scanned for path/credential markers.
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".py",
        ".pyi",
        ".md",
        ".txt",
        ".rst",
        ".toml",
        ".json",
        ".yml",
        ".yaml",
        ".ini",
        ".cfg",
        ".css",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".svg",
        ".sql",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".env",
        ".example",
        ".lock",
        ".csv",
        ".xml",
        ".spdx",
    }
)

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tox",
        ".eggs",
    }
)

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_HISTORY_BLOBS = 100_000
MAX_FINDINGS = 200


class SecurityScanError(Exception):
    """Raised when a security scan cannot complete fail-closed."""


@dataclass(frozen=True, order=True)
class SecurityFinding:
    finding_class: FindingClass
    path: str
    location: str
    severity: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityScanReport:
    scope: str
    source_commit: str | None
    files_examined: int
    findings: tuple[SecurityFinding, ...]
    truncated: bool

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": self.scope,
            "source_commit": self.source_commit,
            "files_examined": self.files_examined,
            "finding_count": len(self.findings),
            "truncated": self.truncated,
            "ok": self.ok,
            "findings": [finding.as_dict() for finding in self.findings],
        }


def contains_private_key_block(value: bytes) -> bool:
    for begin_marker in PRIVATE_KEY_MARKERS:
        end_marker = begin_marker.replace(b"BEGIN", b"END", 1)
        begin = value.find(begin_marker)
        if begin >= 0 and value.find(end_marker, begin + len(begin_marker)) >= 0:
            return True
    return False


def _relative_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _line_location(value: bytes, offset: int) -> str:
    line = value.count(b"\n", 0, offset) + 1
    return f"line:{line}"


def _scan_bytes(
    value: bytes,
    *,
    relative_path: str,
    findings: list[SecurityFinding],
    allow_absolute_paths: bool,
) -> None:
    if len(findings) >= MAX_FINDINGS:
        return
    if contains_private_key_block(value):
        findings.append(
            SecurityFinding(
                finding_class="private_key_marker",
                path=relative_path,
                location="binary-or-text-block",
                severity=SEVERITY["private_key_marker"],
            )
        )
    for pattern in CREDENTIAL_CANARY_PATTERNS:
        match = pattern.search(value)
        if match is not None:
            findings.append(
                SecurityFinding(
                    finding_class="credential_canary",
                    path=relative_path,
                    location=_line_location(value, match.start()),
                    severity=SEVERITY["credential_canary"],
                )
            )
            break
    for pattern in RAW_CONTEXT_CANARY_PATTERNS:
        match = pattern.search(value)
        if match is not None:
            findings.append(
                SecurityFinding(
                    finding_class="raw_context_canary",
                    path=relative_path,
                    location=_line_location(value, match.start()),
                    severity=SEVERITY["raw_context_canary"],
                )
            )
            break
    if not allow_absolute_paths:
        for pattern in ABSOLUTE_PATH_PATTERNS:
            match = pattern.search(value)
            if match is not None:
                findings.append(
                    SecurityFinding(
                        finding_class="absolute_developer_path",
                        path=relative_path,
                        location=_line_location(value, match.start()),
                        severity=SEVERITY["absolute_developer_path"],
                    )
                )
                break


def _should_scan_text(path: Path) -> bool:
    return path.suffix.casefold() in TEXT_SUFFIXES or path.name.casefold() in {
        "dockerfile",
        "makefile",
        "license",
        "notice",
        "authors",
        "agents.md",
        "security.md",
        "readme",
        "readme.md",
    }


def _is_unexpected_executable(path: Path, *, allow_packaged_binaries: bool) -> bool:
    suffix = path.suffix.casefold()
    if suffix not in EXECUTABLE_SUFFIXES:
        return False
    if allow_packaged_binaries:
        # Candidate artifact directories may intentionally contain native packages.
        return False
    # Repository source trees should not track unexpected binaries.
    allowed_names = {
        "allthecontextsetup.exe",  # never committed; defensive
    }
    return path.name.casefold() not in allowed_names


def _scan_zip(
    bundle: zipfile.ZipFile,
    *,
    relative_path: str,
    findings: list[SecurityFinding],
) -> None:
    infos = bundle.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise SecurityScanError(f"ZIP member count exceeds scan ceiling: {relative_path}")
    expanded = 0
    for info in infos:
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise SecurityScanError(f"unsafe ZIP member path in artifact: {relative_path}")
        if info.flag_bits & 0x1:
            raise SecurityScanError(f"encrypted ZIP member cannot be scanned: {relative_path}")
        if info.file_size > MAX_SCAN_BYTES:
            raise SecurityScanError(f"ZIP member exceeds scan ceiling: {relative_path}")
        expanded += info.file_size
        if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
            raise SecurityScanError(f"ZIP expanded size exceeds scan ceiling: {relative_path}")
        try:
            payload = bundle.read(info)
        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
            raise SecurityScanError(f"could not scan ZIP member: {relative_path}") from exc
        if len(payload) != info.file_size:
            raise SecurityScanError(f"ZIP member size mismatch: {relative_path}")
        member_path = f"{relative_path}:{name}"
        _scan_bytes(
            payload,
            relative_path=member_path,
            findings=findings,
            allow_absolute_paths=False,
        )


def _scan_complete_payload(
    value: bytes,
    *,
    relative_path: str,
    findings: list[SecurityFinding],
    allow_absolute_paths: bool,
) -> None:
    if len(value) > MAX_SCAN_BYTES:
        raise SecurityScanError(f"payload exceeds scan ceiling: {relative_path}")
    if value.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        try:
            with zipfile.ZipFile(io.BytesIO(value), "r") as bundle:
                _scan_zip(bundle, relative_path=relative_path, findings=findings)
        except zipfile.BadZipFile as exc:
            raise SecurityScanError(f"invalid ZIP payload: {relative_path}") from exc
    _scan_bytes(
        value,
        relative_path=relative_path,
        findings=findings,
        allow_absolute_paths=allow_absolute_paths,
    )


def _git(
    repository_root: Path,
    *args: str,
    text: bool = False,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository_root), *args],
        check=False,
        capture_output=True,
        text=text,
    )


def _resolve_commit(repository_root: Path, revision: str) -> str:
    completed = _git(repository_root, "rev-parse", "--verify", f"{revision}^{{commit}}", text=True)
    if completed.returncode != 0:
        raise SecurityScanError(f"could not resolve source commit: {revision}")
    assert isinstance(completed.stdout, str)
    commit = completed.stdout.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise SecurityScanError("resolved source commit is not a full SHA")
    return commit


def _read_git_blob(repository_root: Path, object_id: str, *, label: str) -> bytes:
    size_result = _git(repository_root, "cat-file", "-s", object_id, text=True)
    if size_result.returncode != 0:
        raise SecurityScanError(f"could not size git blob: {label}")
    assert isinstance(size_result.stdout, str)
    try:
        size = int(size_result.stdout.strip())
    except ValueError as exc:
        raise SecurityScanError(f"invalid git blob size: {label}") from exc
    if size > MAX_SCAN_BYTES:
        raise SecurityScanError(f"git blob exceeds scan ceiling: {label}")
    content = _git(repository_root, "cat-file", "blob", object_id)
    if content.returncode != 0:
        raise SecurityScanError(f"could not read git blob: {label}")
    assert isinstance(content.stdout, bytes)
    if len(content.stdout) != size:
        raise SecurityScanError(f"git blob size mismatch: {label}")
    return content.stdout


def scan_tree(
    root: Path,
    *,
    scope: str = "tree",
    source_commit: str | None = None,
    allow_packaged_binaries: bool = False,
    allow_absolute_paths: bool = False,
    extra_skip_dirs: Iterable[str] = (),
) -> SecurityScanReport:
    if not root.is_dir():
        raise SecurityScanError(f"scan root is not a directory: {root}")
    skip_dirs = SKIP_DIR_NAMES | {name.casefold() for name in extra_skip_dirs}
    findings: list[SecurityFinding] = []
    examined = 0
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.casefold() not in skip_dirs and not name.startswith(".")
        )
        for name in sorted(filenames):
            if len(findings) >= MAX_FINDINGS:
                break
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            relative = _relative_display(path, root)
            examined += 1
            if _is_unexpected_executable(path, allow_packaged_binaries=allow_packaged_binaries):
                findings.append(
                    SecurityFinding(
                        finding_class="unexpected_executable",
                        path=relative,
                        location="filename",
                        severity=SEVERITY["unexpected_executable"],
                    )
                )
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise SecurityScanError(f"could not stat path for scan: {relative}") from exc
            if size > MAX_FILE_BYTES:
                # Still check private-key markers at the head of large files.
                try:
                    head = path.read_bytes()[: min(size, 256 * 1024)]
                except OSError as exc:
                    raise SecurityScanError(f"could not read path for scan: {relative}") from exc
                if contains_private_key_block(head):
                    findings.append(
                        SecurityFinding(
                            finding_class="private_key_marker",
                            path=relative,
                            location="file-head",
                            severity=SEVERITY["private_key_marker"],
                        )
                    )
                continue
            if not _should_scan_text(path) and path.suffix.casefold() not in {
                ".pem",
                ".key",
                ".p12",
                ".pfx",
                ".crt",
                ".cer",
            }:
                # Binary non-executables: only private-key marker scan on small files.
                try:
                    value = path.read_bytes()
                except OSError as exc:
                    raise SecurityScanError(f"could not read path for scan: {relative}") from exc
                if contains_private_key_block(value):
                    findings.append(
                        SecurityFinding(
                            finding_class="private_key_marker",
                            path=relative,
                            location="binary-or-text-block",
                            severity=SEVERITY["private_key_marker"],
                        )
                    )
                continue
            try:
                value = path.read_bytes()
            except OSError as exc:
                raise SecurityScanError(f"could not read path for scan: {relative}") from exc
            _scan_bytes(
                value,
                relative_path=relative,
                findings=findings,
                allow_absolute_paths=allow_absolute_paths,
            )
    truncated = len(findings) >= MAX_FINDINGS
    return SecurityScanReport(
        scope=scope,
        source_commit=source_commit,
        files_examined=examined,
        findings=tuple(sorted(findings)),
        truncated=truncated,
    )


def scan_committed_tree(
    repository_root: Path,
    *,
    source_commit: str,
    scope: str = "tree",
    allow_absolute_paths: bool = False,
) -> SecurityScanReport:
    """Scan the committed blobs at the exact candidate SHA."""

    repository_root = repository_root.resolve()
    if not (repository_root / ".git").exists():
        raise SecurityScanError("committed tree scan requires a git repository")
    commit = _resolve_commit(repository_root, source_commit)
    listed = _git(repository_root, "ls-tree", "-r", "-z", commit)
    if listed.returncode != 0:
        raise SecurityScanError("could not enumerate committed source tree")
    assert isinstance(listed.stdout, bytes)
    entries = [entry for entry in listed.stdout.split(b"\0") if entry]
    findings: list[SecurityFinding] = []
    examined = 0
    for entry in entries:
        metadata, separator, raw_path = entry.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[1] != b"blob":
            raise SecurityScanError("unexpected committed tree entry")
        object_id = fields[2].decode("ascii", errors="strict")
        relative = raw_path.decode("utf-8", errors="replace").replace("\\", "/")
        value = _read_git_blob(repository_root, object_id, label=relative)
        _scan_complete_payload(
            value,
            relative_path=relative,
            findings=findings,
            allow_absolute_paths=allow_absolute_paths,
        )
        examined += 1
        if len(findings) >= MAX_FINDINGS:
            break
    return SecurityScanReport(
        scope=scope,
        source_commit=commit,
        files_examined=examined,
        findings=tuple(sorted(set(findings))),
        truncated=len(findings) >= MAX_FINDINGS,
    )


def scan_artifact_directory(
    directory: Path,
    *,
    source_commit: str | None = None,
) -> SecurityScanReport:
    """Scan release artifacts, including ZIP entry names and text sidecars."""

    if not directory.is_dir():
        raise SecurityScanError(f"artifact directory is missing: {directory}")
    findings: list[SecurityFinding] = []
    examined = 0
    directory = directory.resolve()
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = _relative_display(path, directory)
        examined += 1
        suffix = path.suffix.casefold()
        if suffix == ".zip":
            try:
                with zipfile.ZipFile(path, "r") as bundle:
                    _scan_zip(bundle, relative_path=relative, findings=findings)
            except zipfile.BadZipFile as exc:
                raise SecurityScanError(f"artifact is not a valid ZIP: {relative}") from exc
            continue
        size = path.stat().st_size
        if size > MAX_SCAN_BYTES:
            raise SecurityScanError(f"artifact exceeds scan ceiling: {relative}")
        if _should_scan_text(path) or suffix in {".sha256", ".json", ".txt", ".md"}:
            try:
                value = path.read_bytes()
            except OSError as exc:
                raise SecurityScanError(f"could not read artifact: {relative}") from exc
            _scan_complete_payload(
                value,
                relative_path=relative,
                findings=findings,
                allow_absolute_paths=False,
            )
        elif contains_private_key_block(path.read_bytes()):
            findings.append(
                SecurityFinding(
                    finding_class="private_key_marker",
                    path=relative,
                    location="file-head",
                    severity=SEVERITY["private_key_marker"],
                )
            )
    return SecurityScanReport(
        scope="artifacts",
        source_commit=source_commit,
        files_examined=examined,
        findings=tuple(sorted(findings)),
        truncated=len(findings) >= MAX_FINDINGS,
    )


def scan_git_history(
    repository_root: Path,
    *,
    source_commit: str | None = None,
) -> SecurityScanReport:
    """Scan every unique blob reachable from the exact source commit."""

    repository_root = repository_root.resolve()
    if not (repository_root / ".git").exists():
        raise SecurityScanError("git history scan requires a git repository")
    commit = _resolve_commit(repository_root, source_commit or "HEAD")
    listed = _git(repository_root, "rev-list", "--objects", commit)
    if listed.returncode != 0:
        raise SecurityScanError("could not enumerate reachable git history")
    assert isinstance(listed.stdout, bytes)
    object_lines = listed.stdout.splitlines()
    if len(object_lines) > MAX_HISTORY_BLOBS * 4:
        raise SecurityScanError("reachable git object count exceeds scan ceiling")
    findings: list[SecurityFinding] = []
    examined = 0
    seen: set[str] = set()
    for line in object_lines:
        if len(findings) >= MAX_FINDINGS:
            break
        parts = line.split(b" ", 1)
        object_id = parts[0].decode("ascii", errors="strict")
        if object_id in seen:
            continue
        object_type = _git(repository_root, "cat-file", "-t", object_id, text=True)
        if object_type.returncode != 0:
            raise SecurityScanError("could not inspect reachable git object")
        assert isinstance(object_type.stdout, str)
        if object_type.stdout.strip() != "blob":
            continue
        seen.add(object_id)
        if len(seen) > MAX_HISTORY_BLOBS:
            raise SecurityScanError("reachable git blob count exceeds scan ceiling")
        path = (
            parts[1].decode("utf-8", errors="replace").replace("\\", "/")
            if len(parts) == 2
            else f"blob:{object_id[:12]}"
        )
        value = _read_git_blob(repository_root, object_id, label=f"history:{object_id[:12]}")
        _scan_complete_payload(
            value,
            relative_path=path,
            findings=findings,
            allow_absolute_paths=True,
        )
        examined += 1
    unique = tuple(sorted(set(findings)))
    return SecurityScanReport(
        scope="history",
        source_commit=commit,
        files_examined=examined,
        findings=unique,
        truncated=len(unique) >= MAX_FINDINGS,
    )


def write_report(path: Path, report: SecurityScanReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SecurityScanError(f"refusing to replace existing scan report: {path.name}")
    path.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def require_clean(report: SecurityScanReport) -> None:
    if report.ok:
        return
    classes = sorted({finding.finding_class for finding in report.findings})
    raise SecurityScanError(
        f"security scan failed scope={report.scope} findings={len(report.findings)} "
        f"classes={','.join(classes)}"
    )

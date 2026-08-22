"""Bounded local archive parsing and resumable candidate ingestion."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

from .config import DEFAULT_MAX_IMPORT_BYTES, MAX_IMPORT_BYTES
from .import_boundary import (
    DEFAULT_CANCEL_REGISTRY,
    ImportCancelledError,
    ImportCancelRegistry,
    ImportProgress,
    ImportProgressTracker,
    durable_import_error_code,
    merge_progress_metadata,
    preflight_disk_space,
    refuse_if_over_boundary,
)
from .ingestion import IngestionService, archive_session_request
from .memory_policy import classify_sensitivity
from .models import (
    Availability,
    CandidateInput,
    CoverageReport,
    FinishIngestionRequest,
    SourceOut,
    SubmitBatchRequest,
)
from .provider_ingestion import (
    PARSER_VERSION,
    ArchiveProvider,
    ProviderArchiveBuilder,
    ProviderExtraction,
    normalize_provider,
    parser_identity_for,
)
from .storage import CoreStore, InvalidStateError

DEFAULT_MAX_EXPANDED_TEXT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_JSON_ITEM_CHARS = 128 * 1024 * 1024
DEFAULT_MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ATTACHMENT_TEXT_BYTES = 8 * 1024 * 1024
_OPERATION_COOPERATIVE_YIELD_SECONDS = 0.001

_KIND_MAP = {
    "preference": "interaction_preference",
    "preferences": "interaction_preference",
    "decision": "project_decision",
    "decisions": "project_decision",
    "project": "project",
    "projects": "project",
    "goal": "goal",
    "goals": "goal",
    "constraint": "constraint",
    "constraints": "constraint",
    "workflow": "workflow",
    "workflows": "workflow",
    "fact": "fact",
    "facts": "fact",
    "task": "open_task",
    "tasks": "open_task",
}
_LABELED_LINE = re.compile(
    r"^\s*(preference|decision|project|goal|constraint|workflow|fact|task)\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)
_SECRET_HINT = re.compile(
    r"(?:api[_ -]?key|password|passphrase|private[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|secret)\s*[:=]",
    flags=re.IGNORECASE,
)
_SUPPORTED_TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".markdown", ".txt"}
_SUPPORTED_ATTACHMENT_SUFFIXES = frozenset(_SUPPORTED_TEXT_SUFFIXES)


@dataclass(frozen=True, slots=True)
class AttachmentLink:
    conversation_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class AttachmentInventory:
    """Content-free metadata for one ChatGPT export attachment member."""

    asset_id: str
    archive_name: str
    content_sha256: str
    byte_size: int
    original_filename: str | None
    mime_type: str | None
    mime_type_source: str | None
    conversation_ids: tuple[str, ...]
    message_ids: tuple[str, ...]
    extraction_status: str
    extracted_format: str | None
    provenance: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "archive_name": self.archive_name,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "mime_type_source": self.mime_type_source,
            "conversation_ids": list(self.conversation_ids),
            "message_ids": list(self.message_ids),
            "extraction_status": self.extraction_status,
            "extracted_format": self.extracted_format,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class ParsedArchive:
    candidates: list[CandidateInput]
    warnings: list[str]
    provider: str = ArchiveProvider.GENERIC.value
    export_format: str = "generic_document"
    stats: dict[str, Any] = field(default_factory=dict)
    available: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    complete: bool = True
    recognized_provider: bool = False
    closed_coverage: dict[str, int] = field(default_factory=dict)
    parser_identity: str = PARSER_VERSION
    attachments: list[AttachmentInventory] = field(default_factory=list)


@dataclass(slots=True)
class _GenericCoverage:
    """Closed accounting for generic JSON values outside provider schemas."""

    skipped: int = 0
    unparsed: int = 0


def _candidate(kind: str, content: str, *, evidence: str | None = None) -> CandidateInput | None:
    normalized = " ".join(content.split()).strip()
    if not normalized or len(normalized) > 64_000:
        return None
    if _SECRET_HINT.search(normalized):
        return None
    return CandidateInput(
        kind=kind,
        content=normalized,
        evidence=(evidence or content)[:16_000],
        confidence=1.0,
        sensitivity=classify_sensitivity(normalized),
        source_type="archive",
        availability=Availability.CORE,
        explicit_user_statement=True,
    )


def _extract_json(value: Any, candidates: list[CandidateInput]) -> None:
    if isinstance(value, list):
        for item in value:
            _extract_json(item, candidates)
        return
    if not isinstance(value, dict):
        return
    if isinstance(value.get("kind"), str) and isinstance(value.get("content"), str):
        item = _candidate(
            str(value["kind"])[:128], str(value["content"]), evidence=_safe_json(value)
        )
        if item is not None:
            candidates.append(item)
        return
    for key, raw in value.items():
        normalized_key = str(key).casefold().strip()
        kind = _KIND_MAP.get(normalized_key)
        if kind is not None:
            if isinstance(raw, str):
                item = _candidate(kind, raw, evidence=f"{key}: {raw}")
                if item is not None:
                    candidates.append(item)
            elif isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, str):
                        item = _candidate(kind, entry, evidence=f"{key}: {entry}")
                    elif isinstance(entry, dict):
                        content = entry.get("content") or entry.get("text") or entry.get("name")
                        item = (
                            _candidate(kind, str(content), evidence=_safe_json(entry))
                            if content is not None
                            else None
                        )
                    else:
                        item = None
                    if item is not None:
                        candidates.append(item)
            elif isinstance(raw, dict):
                for subkey, entry in raw.items():
                    if isinstance(entry, (str, int, float, bool)):
                        item = _candidate(kind, f"{subkey}: {entry}", evidence=_safe_json(raw))
                        if item is not None:
                            candidates.append(item)
        elif isinstance(raw, (dict, list)):
            _extract_json(raw, candidates)


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:16_000]


def parse_json(
    text: str,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    source_name: str = "import.json",
) -> ParsedArchive:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidStateError(
            f"invalid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    builder = _builder(provider)
    generic: list[CandidateInput] = []
    coverage = _GenericCoverage()
    _consume_json_value(builder, source_name, value, generic, coverage)
    return _combine(builder.finish(), generic, generic_coverage=coverage)


def parse_jsonl(
    text: str,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    source_name: str = "import.jsonl",
) -> ParsedArchive:
    builder = _builder(provider)
    candidates: list[CandidateInput] = []
    warnings: list[str] = []
    coverage = _GenericCoverage()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _append_warning(warnings, f"line {line_number}: invalid JSON skipped")
            coverage.unparsed += 1
            continue
        _consume_json_value(builder, source_name, value, candidates, coverage)
    return _combine(builder.finish(), candidates, warnings, coverage)


def parse_text(
    text: str,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    source_name: str = "import.txt",
) -> ParsedArchive:
    builder = _builder(provider)
    candidates: list[CandidateInput] = []
    recognized = builder.consume_text(source_name, text)
    if not recognized:
        candidates.extend(_labeled_text_candidates(text))
    return _combine(builder.finish(), candidates)


def _labeled_text_candidates(text: str) -> list[CandidateInput]:
    candidates: list[CandidateInput] = []
    for line in text.splitlines():
        cleaned = line.lstrip("#*- ").strip()
        match = _LABELED_LINE.match(cleaned)
        if match:
            item = _candidate(_KIND_MAP[match.group(1).casefold()], match.group(2), evidence=line)
            if item is not None:
                candidates.append(item)
    return _deduplicate(candidates)


def _builder(provider: str | ArchiveProvider) -> ProviderArchiveBuilder:
    try:
        return ProviderArchiveBuilder(normalize_provider(provider))
    except ValueError as error:
        raise InvalidStateError(str(error)) from error


def _consume_json_value(
    builder: ProviderArchiveBuilder,
    source_name: str,
    value: Any,
    generic: list[CandidateInput],
    coverage: _GenericCoverage,
) -> None:
    if isinstance(value, list):
        if builder.consume_json_list(source_name, value):
            return
        if not value:
            builder.note_file(source_name)
            coverage.skipped += 1
            return
        for item in value:
            _consume_json_value(builder, source_name, item, generic, coverage)
        return
    recognized = builder.consume_json(source_name, value)
    candidate_count = len(generic)
    if not recognized:
        _extract_json(value, generic)
    if (
        not recognized
        and len(generic) == candidate_count
        and not builder.note_unrecognized_json_value(source_name)
    ):
        coverage.skipped += 1


def _combine(
    provider_result: ProviderExtraction,
    generic: Iterable[CandidateInput],
    warnings: Sequence[str] = (),
    generic_coverage: _GenericCoverage | None = None,
    attachments: Sequence[AttachmentInventory] = (),
) -> ParsedArchive:
    combined_warnings = _deduplicate_strings([*warnings, *provider_result.warnings])[:512]
    generic_list = list(generic)
    candidates = _deduplicate([*generic_list, *provider_result.candidates])
    stats = dict(provider_result.stats)
    stats["candidates"] = len(candidates)
    available = provider_result.available or ["generic structured/labeled document"]
    limitations = provider_result.limitations
    if not provider_result.recognized:
        limitations = [
            "Generic documents produce candidates only from explicit kind/content objects, "
            "known structured keys, or labeled lines.",
            *limitations,
        ]
    incomplete_markers = (
        "invalid json",
        "could not parse",
        "exceeds",
        "truncated",
        "duplicate entry",
    )
    complete = provider_result.complete and not any(
        marker in warning.casefold()
        for warning in combined_warnings
        for marker in incomplete_markers
    )
    closed = dict(provider_result.closed_coverage)
    if not closed:
        closed = {
            "recognized": 0,
            "excluded": 0,
            "skipped": 0,
            "unavailable": 0,
            "failed": 0,
            "unparsed": 0,
        }
    # Generic kind/content and labeled extractors contribute recognized coverage.
    closed["recognized"] = max(int(closed.get("recognized", 0)), len(candidates))
    if generic_list and not provider_result.recognized:
        closed["recognized"] = max(closed["recognized"], len(candidates))
    if generic_coverage is not None:
        closed["skipped"] = int(closed.get("skipped", 0)) + generic_coverage.skipped
        closed["unparsed"] = int(closed.get("unparsed", 0)) + generic_coverage.unparsed
        stats["generic_skipped"] = generic_coverage.skipped
        stats["generic_unparsed"] = generic_coverage.unparsed
        if generic_coverage.unparsed:
            complete = False
    stats["closed_coverage"] = dict(closed)
    return ParsedArchive(
        candidates=candidates,
        warnings=combined_warnings,
        provider=provider_result.provider.value,
        export_format=provider_result.export_format,
        stats=stats,
        available=available,
        unavailable=provider_result.unavailable,
        limitations=limitations,
        complete=complete,
        recognized_provider=provider_result.recognized,
        closed_coverage=closed,
        parser_identity=provider_result.parser_identity
        or parser_identity_for(provider_result.provider),
        attachments=list(attachments),
    )


def _deduplicate(items: Iterable[CandidateInput]) -> list[CandidateInput]:
    result: list[CandidateInput] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.kind.casefold(), " ".join(item.content.casefold().split()))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _deduplicate_strings(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.casefold()
        if normalized not in seen:
            seen.add(normalized)
            result.append(item)
    return result


def parse_archive(
    filename: str,
    content: bytes,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
) -> ParsedArchive:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.casefold()
    if suffix == ".zip":
        return parse_zip_bundle(content, provider=provider)
    try:
        text = content.decode("utf-8-sig")
        decode_warnings: list[str] = []
    except UnicodeDecodeError:
        text = content.decode("utf-8-sig", errors="replace")
        decode_warnings = ["invalid UTF-8 sequences were replaced"]
    if suffix == ".json":
        result = parse_json(text, provider=provider, source_name=safe_name)
    elif suffix == ".jsonl":
        result = parse_jsonl(text, provider=provider, source_name=safe_name)
    elif suffix in {".md", ".markdown", ".txt", ""}:
        result = parse_text(text, provider=provider, source_name=safe_name)
    else:
        raise InvalidStateError("supported import types are ZIP, JSON, JSONL, Markdown, and text")
    return ParsedArchive(
        candidates=result.candidates,
        warnings=[*decode_warnings, *result.warnings],
        provider=result.provider,
        export_format=result.export_format,
        stats=result.stats,
        available=result.available,
        unavailable=result.unavailable,
        limitations=result.limitations,
        complete=result.complete,
        recognized_provider=result.recognized_provider,
        closed_coverage=dict(result.closed_coverage),
        parser_identity=result.parser_identity,
    )


def parse_archive_path(
    path: Path,
    *,
    display_name: str | None = None,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    max_uncompressed_bytes: int = DEFAULT_MAX_EXPANDED_TEXT_BYTES,
    progress: ImportProgressTracker | None = None,
) -> ParsedArchive:
    safe_name = Path(display_name or path.name).name
    suffix = Path(safe_name).suffix.casefold()
    if suffix == ".zip":
        return parse_zip_bundle(
            path,
            provider=provider,
            max_uncompressed_bytes=max_uncompressed_bytes,
            progress=progress,
        )
    if suffix == ".json":
        builder = _builder(provider)
        generic: list[CandidateInput] = []
        coverage = _GenericCoverage()
        try:
            with path.open("rb") as stream:
                for document in _iter_json_documents(stream):
                    if progress is not None:
                        progress.check_cancelled()
                    _consume_json_value(builder, safe_name, document, generic, coverage)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _invalid_json_error(error) from error
        return _combine(builder.finish(), generic, generic_coverage=coverage)
    if suffix == ".jsonl":
        return _parse_jsonl_stream(path, safe_name, provider, progress=progress)
    if suffix in {".md", ".markdown", ".txt", ""}:
        if progress is not None:
            progress.check_cancelled()
        try:
            text = path.read_text(encoding="utf-8-sig")
            warnings: list[str] = []
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            warnings = ["invalid UTF-8 sequences were replaced"]
        result = parse_text(text, provider=provider, source_name=safe_name)
        if progress is not None:
            progress.check_cancelled()
        return ParsedArchive(
            candidates=result.candidates,
            warnings=[*warnings, *result.warnings],
            provider=result.provider,
            export_format=result.export_format,
            stats=result.stats,
            available=result.available,
            unavailable=result.unavailable,
            limitations=result.limitations,
            complete=result.complete,
            recognized_provider=result.recognized_provider,
            closed_coverage=dict(result.closed_coverage),
            parser_identity=result.parser_identity,
        )
    raise InvalidStateError("supported import types are ZIP, JSON, JSONL, Markdown, and text")


def _parse_jsonl_stream(
    path: Path,
    source_name: str,
    provider: str | ArchiveProvider,
    *,
    progress: ImportProgressTracker | None = None,
) -> ParsedArchive:
    builder = _builder(provider)
    generic: list[CandidateInput] = []
    warnings: list[str] = []
    coverage = _GenericCoverage()
    processed = 0
    next_progress = 0
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            processed += len(raw_line)
            if progress is not None and processed >= next_progress:
                progress.advance_bytes(processed, message=f"parsed line {line_number}")
                if progress.liveness_sink is not None:
                    # Parsing millions of small JSON objects can keep this Core
                    # process continuously runnable. Operation-owned imports
                    # yield at the existing 1 MiB checkpoint so their dedicated
                    # observer and ASGI loop get a scheduling turn without
                    # changing durable progress semantics.
                    time.sleep(_OPERATION_COOPERATIVE_YIELD_SECONDS)
                next_progress = processed + 1024 * 1024
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _append_warning(warnings, f"line {line_number}: invalid JSON skipped")
                coverage.unparsed += 1
                continue
            _consume_json_value(builder, source_name, value, generic, coverage)
    if progress is not None:
        progress.advance_bytes(processed, message="raw source parsing complete")
    return _combine(builder.finish(), generic, warnings, coverage)


@dataclass(slots=True)
class _ChatGPTAttachmentContext:
    original_filenames: dict[str, str] = field(default_factory=dict)
    manifest_members: set[str] = field(default_factory=set)
    mime_types: dict[str, tuple[str, str]] = field(default_factory=dict)
    mime_types_by_filename: dict[str, str] = field(default_factory=dict)
    links: dict[str, set[AttachmentLink]] = field(default_factory=dict)
    control_members: set[str] = field(default_factory=set)


def _normalize_attachment_member_name(value: str) -> str:
    return _safe_zip_name(value)


def _attachment_id_from_member_name(safe_name: str) -> str:
    return PurePosixPath(safe_name).stem


def _safe_attachment_filename(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    name = PurePosixPath(normalized).name
    cleaned = "".join(char for char in name if ord(char) >= 32 and char != "\x7f")
    return cleaned[:512] or None


def _first_mapping_string(value: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:512]
    return None


def _collect_chatgpt_attachment_links(
    value: Any,
    context: _ChatGPTAttachmentContext,
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_chatgpt_attachment_links(item, context)
        return
    if not isinstance(value, dict):
        return
    mapping = value.get("mapping")
    if isinstance(mapping, dict):
        conversation_id = _first_mapping_string(value, ("conversation_id", "id", "uuid", "chat_id"))
        if conversation_id is None:
            return
        for node_id, node in mapping.items():
            if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                continue
            message = node["message"]
            message_id = _first_mapping_string(message, ("id", "uuid", "message_id"))
            if message_id is None:
                message_id = str(node_id)[:512]
            metadata = message.get("metadata")
            if not isinstance(metadata, dict):
                continue
            attachments = metadata.get("attachments")
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                asset_id = _first_mapping_string(attachment, ("id", "asset_id"))
                if asset_id is None:
                    continue
                context.links.setdefault(asset_id, set()).add(
                    AttachmentLink(conversation_id, message_id)
                )
                mime_type = attachment.get("mime_type")
                if isinstance(mime_type, str) and mime_type.strip():
                    context.mime_types.setdefault(
                        asset_id, (mime_type.strip()[:256], "conversation_attachment")
                    )
        return
    for key in ("conversations", "conversation_history", "items", "data", "export"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            _collect_chatgpt_attachment_links(nested, context)


def _collect_library_mime_types(value: Any, context: _ChatGPTAttachmentContext) -> None:
    if not isinstance(value, list):
        return
    by_filename: dict[str, set[str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        filename = item.get("file_name")
        mime_type = item.get("mime_type")
        if not isinstance(filename, str) or not isinstance(mime_type, str):
            continue
        safe_filename = _safe_attachment_filename(filename)
        if safe_filename is None or not mime_type.strip():
            continue
        by_filename.setdefault(safe_filename, set()).add(mime_type.strip()[:256])
    # A repeated filename with conflicting declarations is not safe to resolve.
    context.mime_types_by_filename = {
        filename: next(iter(values)) for filename, values in by_filename.items() if len(values) == 1
    }


def _read_zip_member_bytes(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    max_bytes: int,
) -> bytes:
    if member.file_size > max_bytes:
        raise InvalidStateError("ZIP member exceeds the read limit")
    chunks: list[bytes] = []
    total = 0
    with archive.open(member) as stream:
        while True:
            remaining = member.file_size - total
            chunk = stream.read(min(1024 * 1024, max(remaining, 1)))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes or total > member.file_size:
                raise InvalidStateError("ZIP member exceeded its bounded read")
            chunks.append(chunk)
    if total != member.file_size:
        raise InvalidStateError("ZIP member ended before its declared size")
    return b"".join(chunks)


def _hash_zip_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    max_text_bytes: int,
    progress: ImportProgressTracker | None = None,
) -> tuple[str, bytes | None]:
    digest = hashlib.sha256()
    text_chunks: list[bytes] = []
    total = 0
    retain_text = member.file_size <= max_text_bytes
    with archive.open(member) as stream:
        while True:
            if progress is not None:
                progress.check_cancelled()
            remaining = member.file_size - total
            chunk = stream.read(min(1024 * 1024, max(remaining, 1)))
            if not chunk:
                break
            total += len(chunk)
            if total > member.file_size:
                raise InvalidStateError("ZIP member exceeded its declared size")
            digest.update(chunk)
            if retain_text:
                text_chunks.append(chunk)
    if total != member.file_size:
        raise InvalidStateError("ZIP member ended before its declared size")
    return digest.hexdigest(), b"".join(text_chunks) if retain_text else None


def _load_zip_json_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    max_item_chars: int,
) -> Any:
    raw = _read_zip_member_bytes(archive, member, max_bytes=max_item_chars)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidStateError("attachment metadata JSON is invalid") from error


def _attachment_context(
    archive: zipfile.ZipFile,
    members: Sequence[zipfile.ZipInfo],
    *,
    max_item_chars: int,
) -> _ChatGPTAttachmentContext:
    context = _ChatGPTAttachmentContext()
    by_basename: dict[str, zipfile.ZipInfo] = {}
    for member in members:
        safe_name = _safe_zip_name(member.filename)
        by_basename.setdefault(PurePosixPath(safe_name).name.casefold(), member)

    asset_member = by_basename.get("conversation_asset_file_names.json")
    if asset_member is not None:
        context.control_members.add(_safe_zip_name(asset_member.filename))
        value = _load_zip_json_member(archive, asset_member, max_item_chars=max_item_chars)
        if isinstance(value, dict):
            for member_name, original_filename in value.items():
                if not isinstance(member_name, str) or not isinstance(original_filename, str):
                    continue
                safe_member = _normalize_attachment_member_name(member_name)
                if PurePosixPath(safe_member).suffix.casefold() == ".dat":
                    filename = _safe_attachment_filename(original_filename)
                    if filename is not None:
                        context.original_filenames[safe_member] = filename

    manifest_member = by_basename.get("export_manifest.json")
    if manifest_member is not None:
        context.control_members.add(_safe_zip_name(manifest_member.filename))
        value = _load_zip_json_member(archive, manifest_member, max_item_chars=max_item_chars)
        if isinstance(value, dict):
            logical_files = value.get("logical_files")
            if isinstance(logical_files, dict):
                for logical_name, logical_info in logical_files.items():
                    if not isinstance(logical_name, str):
                        continue
                    safe_logical = _normalize_attachment_member_name(logical_name)
                    if PurePosixPath(safe_logical).suffix.casefold() != ".dat":
                        continue
                    context.manifest_members.add(safe_logical)
                    if isinstance(logical_info, dict):
                        files = logical_info.get("files")
                        if isinstance(files, list):
                            context.manifest_members.update(
                                _normalize_attachment_member_name(item)
                                for item in files
                                if isinstance(item, str)
                                and PurePosixPath(item).suffix.casefold() == ".dat"
                            )
            export_files = value.get("export_files")
            if isinstance(export_files, list):
                context.manifest_members.update(
                    _normalize_attachment_member_name(item)
                    for item in export_files
                    if isinstance(item, str) and PurePosixPath(item).suffix.casefold() == ".dat"
                )

    library_member = by_basename.get("library_files.json")
    if library_member is not None:
        context.control_members.add(_safe_zip_name(library_member.filename))
        _collect_library_mime_types(
            _load_zip_json_member(archive, library_member, max_item_chars=max_item_chars),
            context,
        )
        for safe_name, original_filename in context.original_filenames.items():
            mime_type = context.mime_types_by_filename.get(original_filename)
            if mime_type is not None:
                asset_id = _attachment_id_from_member_name(safe_name)
                context.mime_types.setdefault(asset_id, (mime_type, "library_files"))

    return context


def _attachment_text_format(original_filename: str | None) -> str | None:
    if original_filename is None:
        return None
    suffix = PurePosixPath(original_filename).suffix.casefold()
    return suffix.lstrip(".") if suffix in _SUPPORTED_ATTACHMENT_SUFFIXES else None


def _csv_text(raw_text: str, *, max_chars: int) -> str:
    rows: list[str] = []
    total = 0
    try:
        reader = csv.reader(io.StringIO(raw_text, newline=""), strict=True)
        for row in reader:
            line = "\t".join(row).strip()
            if not line:
                continue
            total += len(line) + 1
            if total > max_chars:
                raise InvalidStateError("CSV attachment exceeds the text extraction limit")
            rows.append(line)
    except csv.Error as error:
        raise InvalidStateError("CSV attachment is not well formed") from error
    return "\n".join(rows)


def parse_zip_bundle(
    content: bytes | Path,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    max_entries: int = 10_000,
    max_uncompressed_bytes: int = DEFAULT_MAX_EXPANDED_TEXT_BYTES,
    max_member_uncompressed_bytes: int = DEFAULT_MAX_ZIP_MEMBER_BYTES,
    max_compression_ratio: int = 500,
    max_json_item_chars: int = DEFAULT_MAX_JSON_ITEM_CHARS,
    max_attachment_text_bytes: int = DEFAULT_MAX_ATTACHMENT_TEXT_BYTES,
    progress: ImportProgressTracker | None = None,
) -> ParsedArchive:
    """Parse bounded ZIP members in place; archive paths are never extracted."""
    if max_entries < 1 or max_uncompressed_bytes < 1 or max_member_uncompressed_bytes < 1:
        raise ValueError("ZIP limits must be positive")
    if max_compression_ratio < 1 or max_json_item_chars < 1 or max_attachment_text_bytes < 1:
        raise ValueError("ZIP read limits must be positive")
    builder = _builder(provider)
    generic: list[CandidateInput] = []
    warnings: list[str] = []
    coverage = _GenericCoverage()
    unsupported_entries = 0
    unsupported_attachments = 0
    attachments: list[AttachmentInventory] = []
    source: io.BytesIO | Path = io.BytesIO(content) if isinstance(content, bytes) else content
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise InvalidStateError("ZIP bundle contains too many entries")
            seen_names: set[str] = set()
            unique_members: list[zipfile.ZipInfo] = []
            total_uncompressed = sum(item.file_size for item in members if not item.is_dir())
            if total_uncompressed > max_uncompressed_bytes:
                raise InvalidStateError("ZIP bundle exceeds the total uncompressed-size limit")
            supported_members: list[zipfile.ZipInfo] = []
            for member in members:
                if member.is_dir():
                    continue
                safe_name = _validate_zip_member_name(member.filename)
                builder.note_file(safe_name)
                folded = safe_name.casefold()
                duplicate = folded in seen_names
                if member.file_size > max_member_uncompressed_bytes:
                    raise InvalidStateError("ZIP member exceeds the per-member size limit")
                if member.flag_bits & 0x1:
                    raise InvalidStateError("encrypted ZIP entries are not supported")
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > max_compression_ratio
                ):
                    raise InvalidStateError("ZIP bundle exceeds the compression-ratio limit")
                if duplicate:
                    _append_warning(
                        warnings,
                        f"{safe_name}: case-insensitive duplicate entry skipped",
                    )
                    continue
                seen_names.add(folded)
                unique_members.append(member)

            context = _attachment_context(
                archive,
                unique_members,
                max_item_chars=max_json_item_chars,
            )
            for member in unique_members:
                safe_name = _safe_zip_name(member.filename)
                if (
                    PurePosixPath(safe_name).suffix.casefold() != ".json"
                    or "conversation" not in PurePosixPath(safe_name).stem.casefold()
                ):
                    continue
                try:
                    with archive.open(member) as stream:
                        for document in _iter_json_documents(
                            stream, max_item_chars=max_json_item_chars
                        ):
                            _collect_chatgpt_attachment_links(document, context)
                except (UnicodeDecodeError, json.JSONDecodeError, InvalidStateError):
                    continue

            for member in unique_members:
                safe_name = _safe_zip_name(member.filename)
                suffix = PurePosixPath(safe_name).suffix.casefold()
                if suffix == ".dat":
                    if progress is not None:
                        progress.check_cancelled()
                    asset_id = _attachment_id_from_member_name(safe_name)
                    content_sha256, raw_text = _hash_zip_member(
                        archive,
                        member,
                        max_text_bytes=max_attachment_text_bytes,
                        progress=progress,
                    )
                    original_filename = context.original_filenames.get(safe_name)
                    mime_type, mime_source = context.mime_types.get(asset_id, (None, None))
                    text_format = (
                        _attachment_text_format(original_filename)
                        if safe_name in context.manifest_members
                        else None
                    )
                    extraction_status = "unsupported_binary"
                    extracted_format: str | None = None
                    if text_format is not None:
                        if raw_text is None:
                            extraction_status = "text_read_limit"
                        else:
                            try:
                                text = raw_text.decode("utf-8-sig")
                                source_name = f"attachment/{safe_name}"
                                if text_format == "json":
                                    for document in _iter_json_documents(
                                        io.BytesIO(raw_text), max_item_chars=max_json_item_chars
                                    ):
                                        _collect_chatgpt_attachment_links(document, context)
                                        _consume_json_value(
                                            builder,
                                            source_name,
                                            document,
                                            generic,
                                            coverage,
                                        )
                                elif text_format == "jsonl":
                                    for line_number, line in enumerate(text.splitlines(), 1):
                                        if not line.strip():
                                            continue
                                        try:
                                            document = json.loads(line)
                                        except json.JSONDecodeError:
                                            coverage.unparsed += 1
                                            _append_warning(
                                                warnings,
                                                f"{safe_name}: line {line_number}: "
                                                "invalid JSON skipped",
                                            )
                                            continue
                                        _consume_json_value(
                                            builder,
                                            source_name,
                                            document,
                                            generic,
                                            coverage,
                                        )
                                elif text_format == "csv":
                                    builder.consume_text(
                                        source_name,
                                        _csv_text(text, max_chars=max_attachment_text_bytes),
                                    )
                                else:
                                    builder.consume_text(source_name, text)
                                extraction_status = "text_extracted"
                                extracted_format = text_format
                            except (UnicodeDecodeError, json.JSONDecodeError, InvalidStateError):
                                extraction_status = "text_parse_failed"
                                _append_warning(warnings, f"{safe_name}: attachment text skipped")
                                coverage.unparsed += 1
                    if extraction_status != "text_extracted":
                        unsupported_attachments += 1
                        unsupported_entries += 1
                    links = sorted(
                        context.links.get(asset_id, set()),
                        key=lambda item: (item.conversation_id, item.message_id),
                    )
                    provenance = (
                        ["export_manifest.json"] if safe_name in context.manifest_members else []
                    )
                    if original_filename is not None:
                        provenance.append("conversation_asset_file_names.json")
                    if mime_source is not None:
                        provenance.append(mime_source)
                    if links:
                        provenance.append("conversations.json:message.metadata.attachments")
                    attachments.append(
                        AttachmentInventory(
                            asset_id=asset_id,
                            archive_name=safe_name,
                            content_sha256=content_sha256,
                            byte_size=member.file_size,
                            original_filename=original_filename,
                            mime_type=mime_type,
                            mime_type_source=mime_source,
                            conversation_ids=tuple(
                                dict.fromkeys(item.conversation_id for item in links)
                            ),
                            message_ids=tuple(dict.fromkeys(item.message_id for item in links)),
                            extraction_status=extraction_status,
                            extracted_format=extracted_format,
                            provenance=tuple(provenance),
                        )
                    )
                    continue
                if suffix not in _SUPPORTED_TEXT_SUFFIXES:
                    unsupported_entries += 1
                    continue
                if safe_name in context.control_members:
                    continue
                if suffix in {".md", ".markdown", ".txt"} and (
                    member.file_size > max_json_item_chars
                ):
                    _append_warning(
                        warnings,
                        f"{safe_name}: text entry exceeds the per-entry parse limit; retained raw",
                    )
                    continue
                supported_members.append(member)

            for member in supported_members:
                if progress is not None:
                    progress.check_cancelled()
                safe_name = _safe_zip_name(member.filename)
                suffix = PurePosixPath(safe_name).suffix.casefold()
                try:
                    if suffix == ".json":
                        with archive.open(member) as stream:
                            for document in _iter_json_documents(
                                stream, max_item_chars=max_json_item_chars
                            ):
                                _collect_chatgpt_attachment_links(document, context)
                                _consume_json_value(
                                    builder,
                                    safe_name,
                                    document,
                                    generic,
                                    coverage,
                                )
                    elif suffix == ".jsonl":
                        _consume_zip_jsonl(
                            archive,
                            member,
                            safe_name,
                            builder,
                            generic,
                            warnings,
                            coverage,
                            progress=progress,
                        )
                    else:
                        raw_text = _read_zip_member_bytes(
                            archive, member, max_bytes=max_json_item_chars
                        )
                        try:
                            text = raw_text.decode("utf-8-sig")
                        except UnicodeDecodeError:
                            text = raw_text.decode("utf-8-sig", errors="replace")
                            _append_warning(
                                warnings,
                                f"{safe_name}: invalid UTF-8 sequences were replaced",
                            )
                        if suffix == ".csv":
                            text = _csv_text(text, max_chars=max_json_item_chars)
                        recognized = builder.consume_text(safe_name, text)
                        if not recognized:
                            generic.extend(_labeled_text_candidates(text))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    coverage.unparsed += 1
                    _append_warning(warnings, f"{safe_name}: {_invalid_json_error(error)}")
                except InvalidStateError as error:
                    _append_warning(warnings, f"{safe_name}: {error}")
    except zipfile.BadZipFile as error:
        raise InvalidStateError("invalid ZIP bundle") from error
    builder.note_unsupported_entries(unsupported_entries)
    if unsupported_entries:
        _append_warning(
            warnings,
            f"{unsupported_entries} non-text or unsupported archive entries were retained raw "
            "and skipped during memory extraction",
        )
    if unsupported_attachments:
        _append_warning(
            warnings,
            f"{unsupported_attachments} binary or over-limit attachments were retained raw "
            "but were not text-extracted",
        )
    result = _combine(builder.finish(), generic, warnings, coverage, attachments)
    result.stats.update(
        {
            "attachment_entries": len(attachments),
            "attachment_hashed": len(attachments),
            "attachment_linked": sum(
                bool(item.conversation_ids or item.message_ids) for item in attachments
            ),
            "attachment_text_supported": sum(
                item.extraction_status in {"text_extracted", "text_read_limit", "text_parse_failed"}
                for item in attachments
            ),
            "attachment_text_extracted": sum(
                item.extraction_status == "text_extracted" for item in attachments
            ),
            "attachment_text_over_limit": sum(
                item.extraction_status == "text_read_limit" for item in attachments
            ),
            "attachment_text_parse_failed": sum(
                item.extraction_status == "text_parse_failed" for item in attachments
            ),
            "unsupported_attachments": unsupported_attachments,
            "zip_total_uncompressed_bytes": total_uncompressed,
        }
    )
    return result


def _consume_zip_jsonl(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    source_name: str,
    builder: ProviderArchiveBuilder,
    generic: list[CandidateInput],
    warnings: list[str],
    coverage: _GenericCoverage,
    *,
    progress: ImportProgressTracker | None = None,
) -> None:
    processed = 0
    next_progress = 0
    with archive.open(member) as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            processed += len(raw_line)
            if progress is not None and processed >= next_progress:
                progress.advance_bytes(processed, message=f"parsed ZIP line {line_number}")
                next_progress = processed + 1024 * 1024
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _append_warning(
                    warnings,
                    f"{source_name}: line {line_number}: invalid JSON skipped",
                )
                coverage.unparsed += 1
                continue
            _consume_json_value(builder, source_name, value, generic, coverage)


def _iter_json_documents(
    stream: IO[bytes],
    *,
    max_item_chars: int = DEFAULT_MAX_JSON_ITEM_CHARS,
    chunk_chars: int = 1024 * 1024,
) -> Iterator[Any]:
    """Yield a root JSON value, or each item of a root array, without loading the array."""
    wrapper = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="strict")
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False

    def fill() -> bool:
        nonlocal buffer, position, eof
        if eof:
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        chunk = wrapper.read(chunk_chars)
        if chunk:
            buffer += chunk
            return True
        eof = True
        return False

    while True:
        if not buffer and not fill():
            raise json.JSONDecodeError("empty JSON document", "", 0)
        while position < len(buffer) and buffer[position].isspace():
            position += 1
        if position < len(buffer):
            break
        if not fill():
            raise json.JSONDecodeError("empty JSON document", buffer, position)

    if buffer[position] != "[":
        document_parts = [buffer[position:]]
        document_length = len(document_parts[0])
        while chunk := wrapper.read(chunk_chars):
            document_parts.append(chunk)
            document_length += len(chunk)
            if document_length > max_item_chars:
                raise InvalidStateError("JSON document exceeds the parse limit")
        yield json.loads("".join(document_parts))
        return

    position += 1
    first_item = True
    while True:
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer):
                break
            if not fill():
                raise json.JSONDecodeError("unterminated JSON array", buffer, position)
        if buffer[position] == "]":
            if first_item:
                return
            raise json.JSONDecodeError("trailing comma in JSON array", buffer, position)
        while True:
            try:
                item, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if len(buffer) - position > max_item_chars:
                    raise InvalidStateError(
                        "one JSON conversation exceeds the per-conversation parse limit"
                    ) from None
                if not fill():
                    raise
                continue
            position = end
            yield item
            first_item = False
            break
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer):
                break
            if not fill():
                raise json.JSONDecodeError("unterminated JSON array", buffer, position)
        if buffer[position] == "]":
            return
        if buffer[position] != ",":
            raise json.JSONDecodeError("expected ',' or ']'", buffer, position)
        position += 1


def _invalid_json_error(error: UnicodeDecodeError | json.JSONDecodeError) -> InvalidStateError:
    if isinstance(error, json.JSONDecodeError):
        return InvalidStateError(f"invalid JSON at line {error.lineno}, column {error.colno}")
    return InvalidStateError("JSON is not valid UTF-8")


def _append_warning(warnings: list[str], warning: str) -> None:
    if len(warnings) < 512:
        warnings.append(warning[:2_000])


def _validate_zip_member_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    first = path.parts[0] if path.parts else ""
    if (
        path.is_absolute()
        or ".." in path.parts
        or first.endswith(":")
        or normalized.startswith("//")
    ):
        raise InvalidStateError("ZIP bundle contains an unsafe member path")
    return _safe_zip_name(filename)


def _safe_zip_name(filename: str) -> str:
    return filename.replace("\\", "/").lstrip("./")[-1_000:] or "archive-entry"


class ArchiveImportService:
    def __init__(
        self,
        store: CoreStore,
        *,
        max_bytes: int = DEFAULT_MAX_IMPORT_BYTES,
        max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_TEXT_BYTES,
        cancel_registry: ImportCancelRegistry | None = None,
        skip_disk_preflight: bool = False,
    ) -> None:
        if not 1 <= max_bytes <= MAX_IMPORT_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_IMPORT_BYTES}")
        self.store = store
        self.ingestion = IngestionService(store)
        self.max_bytes = max_bytes
        self.max_expanded_bytes = max(max_expanded_bytes, max_bytes)
        self.cancel_registry = cancel_registry or DEFAULT_CANCEL_REGISTRY
        self.skip_disk_preflight = skip_disk_preflight

    def import_path(
        self,
        path: Path,
        *,
        filename: str | None = None,
        source_service: str = ArchiveProvider.AUTO.value,
        provider: str | None = None,
    ) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise InvalidStateError("import file does not exist")
        size = resolved.stat().st_size
        refuse_if_over_boundary(size, limit=self.max_bytes)
        safe_name = Path(filename or resolved.name).name
        provider_hint = _provider_hint(provider, source_service)
        provisional_service = _provisional_source_service(source_service, provider)
        preflight = None
        if not self.skip_disk_preflight:
            preflight = preflight_disk_space(
                self.store.database_path.parent,
                size,
                database_path=self.store.database_path,
            )
        tracker = ImportProgressTracker(
            bytes_total=max(size, 1),
            registry=self.cancel_registry,
        )
        tracker.set_phase("preflight", message="disk preflight complete")
        tracker.set_phase("storing", message="preserving raw source")
        tracker.check_cancelled()
        metadata = _processing_source_metadata(
            provider_hint,
            preflight=preflight,
            progress=tracker.snapshot(),
        )
        source = self.store.add_source_file(
            resolved,
            source_service=provisional_service,
            source_type=_source_type(safe_name),
            filename=safe_name,
            media_type=_media_type(safe_name),
            metadata=metadata,
            parser_warnings=(),
            import_status="processing",
        )
        tracker.bind_source(source.id)
        tracker.durable_sink = self._durable_progress_sink(source.id)
        try:
            tracker.start_durable_heartbeats()
            tracker.advance_bytes(size, message="raw source preserved")
            with tempfile.TemporaryDirectory(
                prefix="atc-import-parse-", dir=self.store.database_path.parent
            ) as temporary_directory:
                preserved_path = Path(temporary_directory) / "preserved-source"
                self.store.copy_source_content_to_path(source.id, preserved_path)
                tracker.set_phase("parsing", message="parsing preserved raw source")
                parsed = parse_archive_path(
                    preserved_path,
                    display_name=safe_name,
                    provider=provider_hint,
                    max_uncompressed_bytes=self.max_expanded_bytes,
                    progress=tracker,
                )
            actual_service = _actual_source_service(parsed, source_service, provider)
            source = self.store.reclassify_source(
                source.id,
                source_service=actual_service,
                source_type=_source_type(safe_name),
            )
            tracker.bind_source(source.id)
            tracker.durable_sink = self._durable_progress_sink(source.id)
        except ImportCancelledError:
            self._mark_cancelled(source.id, tracker)
            raise
        except Exception as error:
            self._mark_failed(source.id, tracker, error)
            raise
        return self._ingest(source, parsed, actual_service, tracker=tracker)

    def import_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        source_service: str = ArchiveProvider.AUTO.value,
        provider: str | None = None,
    ) -> dict[str, Any]:
        refuse_if_over_boundary(len(content), limit=self.max_bytes)
        safe_name = Path(filename).name
        provider_hint = _provider_hint(provider, source_service)
        provisional_service = _provisional_source_service(source_service, provider)
        preflight = None
        if not self.skip_disk_preflight:
            preflight = preflight_disk_space(
                self.store.database_path.parent,
                len(content),
                database_path=self.store.database_path,
            )
        tracker = ImportProgressTracker(
            bytes_total=max(len(content), 1),
            registry=self.cancel_registry,
        )
        tracker.set_phase("preflight", message="disk preflight complete")
        tracker.set_phase("storing", message="preserving raw source")
        tracker.check_cancelled()
        metadata = _processing_source_metadata(
            provider_hint,
            preflight=preflight,
            progress=tracker.snapshot(),
        )
        source = self.store.add_source(
            content,
            source_service=provisional_service,
            source_type=_source_type(safe_name),
            filename=safe_name,
            media_type=_media_type(safe_name),
            metadata=metadata,
            parser_warnings=(),
            import_status="processing",
        )
        tracker.bind_source(source.id)
        tracker.durable_sink = self._durable_progress_sink(source.id)
        try:
            tracker.start_durable_heartbeats()
            tracker.advance_bytes(len(content), message="raw source preserved")
            tracker.set_phase("parsing", message="parsing preserved raw source")
            tracker.check_cancelled()
            parsed = (
                parse_zip_bundle(
                    content,
                    provider=provider_hint,
                    max_uncompressed_bytes=self.max_expanded_bytes,
                    progress=tracker,
                )
                if Path(safe_name).suffix.casefold() == ".zip"
                else parse_archive(safe_name, content, provider=provider_hint)
            )
            tracker.check_cancelled()
            actual_service = _actual_source_service(parsed, source_service, provider)
            source = self.store.reclassify_source(
                source.id,
                source_service=actual_service,
                source_type=_source_type(safe_name),
            )
            tracker.bind_source(source.id)
            tracker.durable_sink = self._durable_progress_sink(source.id)
        except ImportCancelledError:
            self._mark_cancelled(source.id, tracker)
            raise
        except Exception as error:
            self._mark_failed(source.id, tracker, error)
            raise
        return self._ingest(source, parsed, actual_service, tracker=tracker)

    def reprocess_source(
        self,
        source_id: str,
        *,
        progress_tracker: ImportProgressTracker | None = None,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        """Resume or rebuild extraction from the preserved raw blob.

        Failed and cancelled sources retry the existing parser session.
        ``rebuild=True`` on a complete source stages a new parser-versioned
        observation set, then atomically replaces eligible automatic records
        only after parsing and staging succeed. The raw blob and
        user-corrected records are not destroyed.
        """
        source = self.store.get_source(source_id, duplicate=True)
        resume_rebuild = bool(source.metadata.get("rebuild_in_progress"))
        resume_published_rebuild = resume_rebuild and (
            source.metadata.get("rebuild_published_generation") is not None
            and str(source.metadata.get("rebuild_published_generation"))
            == str(source.metadata.get("rebuild_generation"))
        )
        rebuild_generation: int | None = None
        withdrawn_record_ids: list[str] = []
        if rebuild or resume_rebuild:
            resumable_rebuild = resume_rebuild and source.import_status == "processing"
            if (
                source.import_status not in {"complete", "failed", "cancelled"}
                and not resumable_rebuild
            ):
                raise InvalidStateError("rebuild requires a terminal source")
            if source.import_status == "complete" and not resume_published_rebuild:
                rebuild_generation = int(source.metadata.get("rebuild_generation") or 0) + 1
                metadata = dict(source.metadata)
                metadata["rebuild_generation"] = rebuild_generation
                metadata["rebuild_in_progress"] = True
                self.store.update_source_import(
                    source.id,
                    import_status="processing",
                    metadata=metadata,
                    parser_warnings=source.parser_warnings,
                )
                source = self.store.get_source(source.id, duplicate=True)
            elif resume_rebuild:
                rebuild_generation = int(source.metadata.get("rebuild_generation") or 1)
                if resume_published_rebuild and source.import_status == "complete":
                    metadata = dict(source.metadata)
                    metadata["rebuild_in_progress"] = True
                    self.store.update_source_import(
                        source.id,
                        import_status="processing",
                        metadata=metadata,
                        parser_warnings=source.parser_warnings,
                    )
                    source = self.store.get_source(source.id, duplicate=True)
        if source.import_status == "complete":
            candidate_ids = self.store.candidate_ids_for_source(source.id)
            observations = [self.store.get_candidate(item) for item in candidate_ids]
            coverage = {
                "available": [source.filename or source.id],
                "unavailable": [],
                "limitations": [],
                "warnings": source.parser_warnings,
                "complete": bool(source.metadata.get("coverage_complete", True)),
                "closed_coverage": source.metadata.get("closed_coverage", {}),
            }
            return {
                "source": source.model_dump(mode="json"),
                "session": {
                    "status": "duplicate",
                    "candidate_count": len(candidate_ids),
                    "coverage": coverage,
                },
                "candidate_ids": candidate_ids,
                "outcomes": dict(Counter(item.disposition.value for item in observations)),
                "record_ids": list(
                    dict.fromkeys(
                        item.record_id for item in observations if item.record_id is not None
                    )
                ),
                "warnings": [
                    *source.parser_warnings,
                    "source extraction was already complete",
                ],
                "provider": str(source.metadata.get("provider", source.source_service)),
                "export_format": str(source.metadata.get("export_format", "generic_document")),
                "stats": (
                    source.metadata.get("stats", {})
                    if isinstance(source.metadata.get("stats", {}), dict)
                    else {}
                ),
                "coverage": coverage,
                "parser_identity": str(
                    source.metadata.get(
                        "parser_identity",
                        parser_identity_for(str(source.metadata.get("provider", "generic"))),
                    )
                ),
            }

        if not self.skip_disk_preflight:
            preflight_disk_space(
                self.store.database_path.parent,
                source.byte_size,
                database_path=self.store.database_path,
            )
        # Preserve the caller's operation-level sink across source-id merges.
        external_operation_sink = (
            progress_tracker.durable_sink if progress_tracker is not None else None
        )
        if progress_tracker is None:
            tracker = ImportProgressTracker(
                bytes_total=max(source.byte_size, 1),
                source_id=source.id,
                registry=self.cancel_registry,
            )
        else:
            tracker = progress_tracker
            tracker.bind_source(source.id)

        def attach_progress_sinks(bound_source_id: str) -> None:
            """Bind source telemetry; rebind after reclassify merge may change ids."""
            if external_operation_sink is not None:
                # The operation row is the queryable progress authority. Do not
                # delay its heartbeat behind a second source-metadata transaction;
                # explicit processing/terminal writes below still close the source.
                tracker.durable_sink = external_operation_sink
                return
            tracker.durable_sink = self._durable_progress_sink(bound_source_id)

        attach_progress_sinks(source.id)

        def checkpoint_preserved_source_copy() -> None:
            """Keep cancellation and operation liveness schedulable per chunk."""
            tracker.check_cancelled()
            if tracker.liveness_sink is not None:
                # Reconstructing a multi-gigabyte preserved blob can otherwise
                # keep the worker continuously runnable and starve its dedicated
                # heartbeat thread. Source-only reprocess keeps its prior path.
                time.sleep(_OPERATION_COOPERATIVE_YIELD_SECONDS)

        provider = str(source.metadata.get("provider", source.source_service))
        try:
            tracker.start_durable_heartbeats()
            if progress_tracker is None:
                tracker.set_phase("storing", message="using preserved raw source")
                tracker.advance_bytes(source.byte_size, message="preserved raw source ready")
            with tempfile.TemporaryDirectory(
                prefix="atc-reprocess-", dir=self.store.database_path.parent
            ) as temporary_directory:
                raw_path = Path(temporary_directory) / "preserved-source"
                self.store.copy_source_content_to_path(
                    source.id,
                    raw_path,
                    checkpoint=checkpoint_preserved_source_copy,
                )
                tracker.set_phase("parsing", message="parsing preserved raw source")
                tracker.check_cancelled()
                parsed = parse_archive_path(
                    raw_path,
                    display_name=source.filename or "import.txt",
                    provider=_provider_hint(None, provider),
                    max_uncompressed_bytes=self.max_expanded_bytes,
                    progress=tracker,
                )
            actual_service = _actual_source_service(
                parsed,
                source.source_service,
                None,
            )
            source = self.store.reclassify_source(
                source.id,
                source_service=actual_service,
                source_type=source.source_type,
            )
            tracker.bind_source(source.id)
            # Duplicate merge deletes the provisional source id; rebind sinks.
            attach_progress_sinks(source.id)
            if source.duplicate and source.import_status == "complete":
                # A reclassification merge can land on an already-complete
                # canonical source. Do not downgrade or re-ingest that source.
                processing = source
            else:
                metadata = _source_metadata(parsed)
                metadata = merge_progress_metadata(metadata, tracker.snapshot())
                if rebuild_generation is not None:
                    metadata["rebuild_generation"] = rebuild_generation
                    metadata["rebuild_in_progress"] = True
                    for marker in (
                        "rebuild_published_generation",
                        "rebuild_published_session_id",
                    ):
                        if marker in source.metadata:
                            metadata[marker] = source.metadata[marker]
                self.store.update_source_import(
                    source.id,
                    import_status="processing",
                    metadata=metadata,
                    parser_warnings=parsed.warnings,
                )
                processing = self.store.get_source(source.id, duplicate=True)
        except ImportCancelledError:
            self._mark_cancelled(source.id, tracker)
            raise
        except Exception as error:
            self._mark_failed(source.id, tracker, error)
            raise
        return self._ingest(
            processing,
            parsed,
            actual_service,
            tracker=tracker,
            rebuild_generation=rebuild_generation,
            withdrawn_record_ids=withdrawn_record_ids,
        )

    def cancel_import(self, source_id: str) -> dict[str, Any]:
        """Request cancellation of an in-flight import; acknowledged by the worker."""
        result = self.store.request_import_cancel(source_id)
        in_flight = self.cancel_registry.request_cancel(source_id)
        result["worker_registered"] = in_flight
        return result

    def import_progress(self, source_id: str) -> dict[str, Any]:
        source = self.store.get_source(source_id, duplicate=True)
        progress = source.metadata.get("import_progress")
        return {
            "source_id": source.id,
            "import_status": source.import_status,
            "progress": progress if isinstance(progress, dict) else None,
            "byte_size": source.byte_size,
            "cancel_requested": bool(source.metadata.get("cancel_requested")),
        }

    def _durable_progress_sink(self, source_id: str) -> Any:
        def _sink(progress: ImportProgress) -> None:
            status: Any = None
            if progress.phase == "complete":
                status = "complete"
            elif progress.phase == "cancelled":
                status = "cancelled"
            elif progress.phase == "failed":
                status = "failed"
            elif progress.phase in {
                "preflight",
                "awaiting_upload",
                "uploading",
                "hashing",
                "staging",
                "storing",
                "parsing",
                "ingesting",
                "verifying",
                "publishing",
            }:
                status = "processing"
            # Durable progress must commit; silent success would claim false progress.
            self.store.update_source_progress(
                source_id,
                progress=progress.as_dict(),
                import_status=status,
            )

        return _sink

    def _mark_cancelled(self, source_id: str, tracker: ImportProgressTracker) -> None:
        tracker.set_phase("cancelled", message="import cancelled")
        try:
            source = self.store.get_source(source_id, duplicate=True)
            metadata = merge_progress_metadata(source.metadata, tracker.snapshot())
            metadata["cancel_requested"] = True
            self.store.update_source_import(
                source_id,
                import_status="cancelled",
                metadata=metadata,
                parser_warnings=source.parser_warnings,
            )
        finally:
            tracker.close()

    def _mark_failed(
        self,
        source_id: str,
        tracker: ImportProgressTracker,
        error: Exception,
    ) -> None:
        # Closed content-free code only; never persist raw exception text.
        tracker.fail(message=durable_import_error_code(error))
        try:
            source = self.store.get_source(source_id, duplicate=True)
            metadata = merge_progress_metadata(source.metadata, tracker.snapshot())
            self.store.update_source_import(
                source_id,
                import_status="failed",
                metadata=metadata,
                parser_warnings=source.parser_warnings,
            )
        except Exception:
            pass
        finally:
            tracker.close()

    def _ingest(
        self,
        source: SourceOut,
        parsed: ParsedArchive,
        source_service: str,
        *,
        tracker: ImportProgressTracker | None = None,
        rebuild_generation: int | None = None,
        withdrawn_record_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        progress = tracker or ImportProgressTracker(
            bytes_total=max(source.byte_size, 1),
            source_id=source.id,
            registry=self.cancel_registry,
            durable_sink=self._durable_progress_sink(source.id),
        )
        if source.duplicate and source.import_status == "complete":
            existing_ids = self.store.candidate_ids_for_source(source.id)
            progress.complete(message="duplicate complete source")
            progress.close()
            return self._import_result(
                source,
                {
                    "status": "duplicate",
                    "candidate_count": len(existing_ids),
                    "coverage": {
                        "available": parsed.available,
                        "unavailable": parsed.unavailable,
                        "limitations": parsed.limitations,
                        "warnings": parsed.warnings,
                        "complete": parsed.complete,
                        "closed_coverage": parsed.closed_coverage,
                    },
                },
                existing_ids,
                parsed,
                duplicate=True,
            )

        candidates = [
            candidate.model_copy(
                update={
                    "source_id": source.id,
                    "source_service": source_service,
                    "source_type": candidate.source_type or source.source_type,
                }
            )
            for candidate in parsed.candidates
        ]
        try:
            progress.set_phase("ingesting", message="submitting observation batches")
            progress.check_cancelled()
            begin = self.ingestion.begin(
                archive_session_request(
                    source.id,
                    parser_version=PARSER_VERSION,
                    rebuild_generation=rebuild_generation,
                )
            )
            candidate_ids: list[str] = []
            batches = list(_chunks(candidates, 200)) or []
            total_batches = max(len(batches), 1)
            if not batches and not candidates:
                # Empty candidate set still needs a finish for atomic coverage.
                pass
            for index, batch in enumerate(batches):
                progress.check_cancelled()
                batch_key = f"{source.content_hash}:{PARSER_VERSION}:{index}"
                if rebuild_generation is not None:
                    batch_key = f"{batch_key}:rebuild:{rebuild_generation}"
                submitted = self.ingestion.submit(
                    SubmitBatchRequest(
                        session_id=str(begin["session_id"]),
                        idempotency_key=batch_key,
                        candidates=batch,
                    )
                )
                candidate_ids.extend(str(item) for item in submitted["candidate_ids"])
                # Bound progress to one storage chunk of accuracy for byte mapping.
                if source.byte_size > 0:
                    fraction = (index + 1) / total_batches
                    mapped = int(source.byte_size * fraction)
                    progress.advance_bytes(mapped, message=f"ingested batch {index + 1}")
            progress.set_phase("verifying", message="verifying coverage and integrity")
            progress.check_cancelled()
            coverage = CoverageReport(
                available=parsed.available,
                unavailable=parsed.unavailable,
                warnings=parsed.warnings,
                limitations=parsed.limitations,
                complete=parsed.complete,
            )
            progress.set_phase("publishing", message="atomic policy publication")
            progress.check_cancelled()
            finished = self.ingestion.finish(
                FinishIngestionRequest(
                    session_id=str(begin["session_id"]),
                    coverage_report=coverage,
                ),
                publish=rebuild_generation is None,
            )
            if rebuild_generation is not None:
                withdrawn_record_ids = self.store.publish_source_rebuild(
                    source.id,
                    str(begin["session_id"]),
                    rebuild_generation=rebuild_generation,
                )
            metadata = _source_metadata(parsed)
            # Preserve preflight and any earlier durable progress fields.
            if isinstance(source.metadata.get("preflight"), dict):
                metadata["preflight"] = source.metadata["preflight"]
            if rebuild_generation is not None:
                metadata["rebuild_generation"] = rebuild_generation
                metadata["rebuild_in_progress"] = False
                metadata["withdrawn_automatic_record_count"] = len(withdrawn_record_ids or [])
                metadata["rebuild_published_generation"] = rebuild_generation
                metadata["rebuild_published_session_id"] = str(begin["session_id"])
            progress.complete(message="import complete")
            metadata = merge_progress_metadata(metadata, progress.snapshot())
            self.store.update_source_import(
                source.id,
                import_status="complete",
                metadata=metadata,
                parser_warnings=parsed.warnings,
            )
            refreshed = self.store.get_source(source.id, duplicate=source.duplicate)
            result = self._import_result(
                refreshed,
                finished,
                candidate_ids or self.store.candidate_ids_for_source(source.id),
                parsed,
                duplicate=source.duplicate,
            )
            if rebuild_generation is not None:
                result["rebuild"] = True
                result["rebuild_generation"] = rebuild_generation
                result["withdrawn_record_ids"] = list(withdrawn_record_ids or [])
            progress.close()
            return result
        except ImportCancelledError:
            self._mark_cancelled(source.id, progress)
            raise
        except Exception as error:
            self._mark_failed(source.id, progress, error)
            raise

    def _import_result(
        self,
        source: SourceOut,
        session: dict[str, Any],
        candidate_ids: list[str],
        parsed: ParsedArchive,
        *,
        duplicate: bool,
    ) -> dict[str, Any]:
        result = _import_result(
            source,
            session,
            candidate_ids,
            parsed,
            duplicate=duplicate,
        )
        observations = [self.store.get_candidate(item) for item in candidate_ids]
        result["outcomes"] = dict(
            Counter(observation.disposition.value for observation in observations)
        )
        result["record_ids"] = list(
            dict.fromkeys(
                observation.record_id
                for observation in observations
                if observation.record_id is not None
            )
        )
        return result


def _source_metadata(
    parsed: ParsedArchive,
    *,
    preflight: Any | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "provider": parsed.provider,
        "export_format": parsed.export_format,
        "parser_version": PARSER_VERSION,
        "parser_identity": parsed.parser_identity or parser_identity_for(parsed.provider),
        "stats": parsed.stats,
        "attachments": [item.as_dict() for item in parsed.attachments],
        "coverage_complete": parsed.complete,
        "closed_coverage": dict(parsed.closed_coverage),
    }
    if preflight is not None and hasattr(preflight, "as_dict"):
        metadata["preflight"] = preflight.as_dict()
    return metadata


def _processing_source_metadata(
    provider: ArchiveProvider,
    *,
    preflight: Any | None,
    progress: ImportProgress,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "provider": provider.value,
        "parser_version": PARSER_VERSION,
        "parser_identity": parser_identity_for(provider),
        "coverage_complete": False,
        "closed_coverage": {
            "recognized": 0,
            "excluded": 0,
            "skipped": 0,
            "unavailable": 0,
            "failed": 0,
            "unparsed": 0,
        },
    }
    if preflight is not None and hasattr(preflight, "as_dict"):
        metadata["preflight"] = preflight.as_dict()
    return merge_progress_metadata(metadata, progress)


def _provisional_source_service(requested: str, explicit_provider: str | None) -> str:
    if explicit_provider is not None:
        try:
            return normalize_provider(explicit_provider).value
        except ValueError as error:
            raise InvalidStateError(str(error)) from error
    normalized = requested.strip()
    if not normalized or len(normalized) > 128:
        raise InvalidStateError("source service must contain 1 to 128 characters")
    return normalized


def _provider_hint(explicit_provider: str | None, source_service: str) -> ArchiveProvider:
    if explicit_provider is not None:
        try:
            return normalize_provider(explicit_provider)
        except ValueError as error:
            raise InvalidStateError(str(error)) from error
    try:
        return normalize_provider(source_service)
    except ValueError:
        return ArchiveProvider.AUTO


def _actual_source_service(
    parsed: ParsedArchive,
    requested: str,
    explicit_provider: str | None,
) -> str:
    if explicit_provider is None:
        try:
            hint = normalize_provider(requested)
        except ValueError:
            normalized = requested.strip()
            if not normalized or len(normalized) > 128:
                raise InvalidStateError("source service must contain 1 to 128 characters") from None
            return normalized
    else:
        try:
            hint = normalize_provider(explicit_provider)
        except ValueError as error:
            raise InvalidStateError(str(error)) from error
    if parsed.recognized_provider:
        return parsed.provider
    if hint not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
        return hint.value
    return ArchiveProvider.GENERIC.value


def _source_type(filename: str) -> str:
    return Path(filename).suffix.casefold().lstrip(".") or "text"


def _media_type(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".zip":
        return "application/zip"
    if suffix == ".json":
        return "application/json"
    if suffix == ".jsonl":
        return "application/x-ndjson"
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    return "text/plain"


def _import_result(
    source: SourceOut,
    session: dict[str, Any],
    candidate_ids: list[str],
    parsed: ParsedArchive,
    *,
    duplicate: bool,
) -> dict[str, Any]:
    warnings = list(parsed.warnings)
    if duplicate:
        warnings.append("duplicate source; existing extraction retained or resumed")
    return {
        "source": source.model_dump(mode="json"),
        "session": session,
        "candidate_ids": candidate_ids,
        "warnings": warnings,
        "provider": parsed.provider,
        "export_format": parsed.export_format,
        "stats": parsed.stats,
        "parser_identity": parsed.parser_identity or parser_identity_for(parsed.provider),
        "parser_version": PARSER_VERSION,
        "coverage": {
            "available": parsed.available,
            "unavailable": parsed.unavailable,
            "limitations": parsed.limitations,
            "warnings": parsed.warnings,
            "complete": parsed.complete,
            "closed_coverage": dict(parsed.closed_coverage),
        },
    }


def _chunks(items: Sequence[CandidateInput], size: int) -> Iterable[list[CandidateInput]]:
    for offset in range(0, len(items), size):
        yield list(items[offset : offset + size])

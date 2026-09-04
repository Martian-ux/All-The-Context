"""Bounded local archive parsing and resumable candidate ingestion."""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
import json
import re
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import IO, Any

from .activity import CoreActivityGate
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
    CLOSED_COVERAGE_KEYS,
    MAX_CLOSED_COVERAGE_COUNT,
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
    is_empty_provider_container,
    normalize_provider,
    parser_identity_for,
)
from .storage import CoreStore, InvalidStateError, source_rebuild_marker

DEFAULT_MAX_EXPANDED_TEXT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_JSON_ITEM_CHARS = 128 * 1024 * 1024
DEFAULT_MAX_JSON_NESTING_DEPTH = 128
DEFAULT_MAX_JSON_BYTES = DEFAULT_MAX_JSON_ITEM_CHARS * 4
DEFAULT_MAX_ZIP_MEMBER_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_ATTACHMENT_TEXT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_ATTACHMENT_LINK_PAIRS = 10_000
DEFAULT_MAX_ZIP_PATH_DEPTH = 64
DEFAULT_MAX_ZIP_MEMBER_NAME_CHARS = 1_000
MAX_CHATGPT_ATTACHMENT_SCAN_DEPTH = 64
MAX_CHATGPT_ATTACHMENT_SCAN_NODES = 10_000
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
_CHATGPT_CONTROL_BASENAMES = frozenset(
    {"conversation_asset_file_names.json", "export_manifest.json", "library_files.json"}
)
_PROVIDER_CONTAINER_BASENAMES = frozenset(
    {"conversations.json", "chats.json", "history.json", "messages.json"}
)
_PROVIDER_SIGNATURE_PATH_PARTS = frozenset(
    {"chatgpt", "openai", "claude", "anthropic", "grok", "xai", "x.ai"}
)
_DATED_CONVERSATIONS_BASENAME = re.compile(r"conversations-\d{4}(?:-\d{2}(?:-\d{2})?)?\.json$")


class _JsonNestingLimitError(InvalidStateError):
    """The bounded JSON scanner rejected a document before recursive decode."""


class JsonValueContext(StrEnum):
    """Context assigned by the bounded JSON reader to each yielded value."""

    ROOT = "root"
    ROOT_ARRAY_ITEM = "root_array_item"


@dataclass(frozen=True, slots=True)
class _JsonDocument:
    value: Any
    context: JsonValueContext


@dataclass(frozen=True, slots=True)
class AttachmentLink:
    conversation_id: str
    message_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
        }


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
    mime_type_status: str
    links: tuple[AttachmentLink, ...]
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
            "mime_type_status": self.mime_type_status,
            "links": [item.as_dict() for item in self.links],
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

    excluded: int = 0
    skipped: int = 0
    unavailable: int = 0
    failed: int = 0
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
        raw = text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidStateError("JSON is not valid UTF-8") from error
    return _parse_json_stream_atomic(
        lambda: io.BytesIO(raw),
        provider=provider,
        source_name=source_name,
    )


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
    coverage = _GenericCoverage()
    recognized = builder.consume_text(source_name, text)
    if not recognized:
        candidates.extend(_labeled_text_candidates(text))
        if not candidates:
            # A standalone generic text member is itself one logical source
            # item. It must not disappear merely because it yielded no
            # durable candidate.
            coverage.excluded += 1
    return _combine(builder.finish(), candidates, generic_coverage=coverage)


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
    *,
    provider_container: bool = False,
    context: JsonValueContext = JsonValueContext.ROOT,
) -> None:
    if isinstance(value, list):
        if builder.consume_json_list(source_name, value):
            return
        if not value:
            builder.note_file(source_name)
            if provider_container:
                builder.note_provider_terminal(
                    source_name,
                    "unparsed"
                    if context is JsonValueContext.ROOT_ARRAY_ITEM
                    else "skipped"
                    if builder.provider_context_established()
                    else "unparsed",
                )
            else:
                coverage.skipped += 1
            return
        for item in value:
            _consume_json_value(
                builder,
                source_name,
                item,
                generic,
                coverage,
                provider_container=provider_container,
                context=context,
            )
        return
    if provider_container and is_empty_provider_container(value):
        builder.note_provider_terminal(
            source_name,
            "unparsed"
            if context is JsonValueContext.ROOT_ARRAY_ITEM
            else "skipped"
            if builder.provider_context_established()
            else "unparsed",
        )
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
        if provider_container:
            builder.note_provider_terminal(source_name, "unparsed")
        else:
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
            "duplicate": 0,
            "failed": 0,
            "unparsed": 0,
        }
    # Generic kind/content and labeled extractors contribute recognized coverage.
    closed["recognized"] = max(int(closed.get("recognized", 0)), len(candidates))
    if generic_list and not provider_result.recognized:
        closed["recognized"] = max(closed["recognized"], len(candidates))
    if generic_coverage is not None:
        closed["excluded"] = int(closed.get("excluded", 0)) + generic_coverage.excluded
        closed["skipped"] = int(closed.get("skipped", 0)) + generic_coverage.skipped
        closed["unavailable"] = int(closed.get("unavailable", 0)) + generic_coverage.unavailable
        closed["failed"] = int(closed.get("failed", 0)) + generic_coverage.failed
        closed["unparsed"] = int(closed.get("unparsed", 0)) + generic_coverage.unparsed
        stats["generic_excluded"] = generic_coverage.excluded
        stats["generic_skipped"] = generic_coverage.skipped
        stats["generic_unavailable"] = generic_coverage.unavailable
        stats["generic_failed"] = generic_coverage.failed
        stats["generic_unparsed"] = generic_coverage.unparsed
        if generic_coverage.failed or generic_coverage.unparsed:
            complete = False
    if any(closed.get(key, 0) > 0 for key in ("unavailable", "duplicate", "failed", "unparsed")):
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


def _generic_failure_result(
    provider: str | ArchiveProvider,
    warning: str,
    *,
    reason: str,
) -> ParsedArchive:
    """Return one content-free terminal result for a standalone member failure."""
    if reason not in {"unavailable", "failed", "unparsed"}:
        raise ValueError("unsupported generic failure reason")
    coverage = _GenericCoverage()
    match reason:
        case "unavailable":
            coverage.unavailable = 1
        case "failed":
            coverage.failed = 1
        case "unparsed":
            coverage.unparsed = 1
    return _combine(
        _builder(provider).finish(),
        (),
        [warning],
        coverage,
    )


def _archive_preflight_result(
    provider: ArchiveProvider,
    warning: str,
    *,
    archive_level_failure: str,
    file_members: int,
    directories_excluded: int,
    terminal_reason: str | None = None,
    total_uncompressed: int = 0,
    member_coverage_available: bool = True,
) -> ParsedArchive:
    """Return content-free ZIP safety accounting without opening rejected payloads."""
    buckets = {
        "recognized": 0,
        "excluded": 0,
        "skipped": 0,
        "unavailable": 0,
        "duplicate": 0,
        "failed": 0,
        "unparsed": 0,
    }
    if terminal_reason is not None:
        buckets[terminal_reason] = file_members
    result = _combine(_builder(provider).finish(), (), [warning])
    stats = dict(result.stats)
    stats["archive_member_coverage"] = {
        "file_members": file_members,
        "directories_excluded": directories_excluded,
        "structural_members": 0,
        "standalone_members": sum(buckets.values()),
        "unaccounted_members": file_members - sum(buckets.values()),
        "terminal_member_buckets": buckets,
        "denominator": (
            "enumerated non-directory ZIP members; payloads were not read after a ZIP "
            "pre-read safety rejection"
            if member_coverage_available
            else "no member denominator exists because the ZIP could not be enumerated"
        ),
        "archive_level_failure": archive_level_failure,
        "member_coverage_available": member_coverage_available,
        "closed_coverage_total": sum(result.closed_coverage.values()),
    }
    stats["zip_total_uncompressed_bytes"] = total_uncompressed
    return replace(
        result,
        complete=False,
        unavailable=[warning],
        stats=stats,
    )


def _parse_csv_document(
    text: str,
    *,
    provider: str | ArchiveProvider,
    source_name: str,
    max_chars: int = DEFAULT_MAX_JSON_ITEM_CHARS,
) -> ParsedArchive:
    try:
        normalized = _csv_text(text, max_chars=max_chars)
    except InvalidStateError as error:
        message = str(error).casefold()
        reason = "unparsed" if "not well formed" in message else "unavailable"
        return _generic_failure_result(
            provider,
            "CSV input was rejected by the bounded parser",
            reason=reason,
        )
    return parse_text(normalized, provider=provider, source_name=source_name)


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


def _parse_json_stream_atomic(
    open_stream: Callable[[], IO[bytes]],
    *,
    provider: str | ArchiveProvider,
    source_name: str,
    max_item_chars: int = DEFAULT_MAX_JSON_ITEM_CHARS,
    max_json_bytes: int = DEFAULT_MAX_JSON_BYTES,
    progress: ImportProgressTracker | None = None,
) -> ParsedArchive:
    """Validate a bounded JSON stream before consuming any logical items.

    The stream is opened twice so direct bytes, filesystem paths, and ZIP members
    share the same trailing-data, UTF-8, byte, item, and nesting contracts. Each
    raw-decoded value is bounded and discarded during validation; the second pass
    is the only pass allowed to mutate the builder or generic candidate list.
    """
    builder = _builder(provider)
    observed_providers: set[ArchiveProvider] = set()

    def validate() -> None:
        # Keep provider evidence in a disposable builder until the complete
        # bounded iterator succeeds. A valid prefix must not promote the live
        # builder before trailing data or a later limit failure is observed.
        validation_builder = _builder(provider)
        with open_stream() as stream:
            for document in _iter_json_documents(
                stream,
                max_item_chars=max_item_chars,
                max_bytes=max_json_bytes,
            ):
                if progress is not None:
                    progress.check_cancelled()
                detected = validation_builder.observe_json_provider(source_name, document.value)
                if detected not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
                    observed_providers.add(detected)

    try:
        validate()
    except UnicodeDecodeError:
        if _is_neutral_auto_provider_container(source_name, provider):
            return _generic_failure_result(
                ArchiveProvider.GENERIC,
                "neutral provider container failed bounded JSON validation",
                reason="unparsed",
            )
        return _generic_failure_result(
            provider,
            "standalone input is not valid UTF-8",
            reason="unparsed",
        )
    except json.JSONDecodeError as error:
        if _is_neutral_auto_provider_container(source_name, provider):
            return _generic_failure_result(
                ArchiveProvider.GENERIC,
                "neutral provider container failed bounded JSON validation",
                reason="unparsed",
            )
        raise _invalid_json_error(error) from error
    except _JsonNestingLimitError:
        if _is_neutral_auto_provider_container(source_name, provider):
            return _generic_failure_result(
                ArchiveProvider.GENERIC,
                "neutral provider container failed bounded JSON validation",
                reason="unparsed",
            )
        return _generic_failure_result(
            provider,
            "standalone JSON exceeded the nesting-depth limit",
            reason="unparsed",
        )
    except RecursionError:
        # Keep the public outcome deterministic if a future decoder path
        # recurses before the scanner can reject the document.
        if _is_neutral_auto_provider_container(source_name, provider):
            return _generic_failure_result(
                ArchiveProvider.GENERIC,
                "neutral provider container failed bounded JSON validation",
                reason="unparsed",
            )
        return _generic_failure_result(
            provider,
            "standalone JSON exceeded the nesting-depth limit",
            reason="unparsed",
        )
    except InvalidStateError:
        if _is_neutral_auto_provider_container(source_name, provider):
            return _generic_failure_result(
                ArchiveProvider.GENERIC,
                "neutral provider container failed bounded JSON validation",
                reason="unparsed",
            )
        raise

    for detected in sorted(observed_providers, key=lambda item: item.value):
        builder.note_provider_context(detected)
    generic: list[CandidateInput] = []
    coverage = _GenericCoverage()
    provider_container = _is_conversation_json_member(source_name, normalize_provider(provider))
    try:
        with open_stream() as stream:
            for document in _iter_json_documents(
                stream,
                max_item_chars=max_item_chars,
                max_bytes=max_json_bytes,
            ):
                if progress is not None:
                    progress.check_cancelled()
                _consume_json_value(
                    builder,
                    source_name,
                    document.value,
                    generic,
                    coverage,
                    provider_container=provider_container,
                    context=document.context,
                )
    except UnicodeDecodeError:
        return _generic_failure_result(
            provider,
            "standalone input is not valid UTF-8",
            reason="unparsed",
        )
    except json.JSONDecodeError as error:
        raise _invalid_json_error(error) from error
    except _JsonNestingLimitError:
        return _generic_failure_result(
            provider,
            "standalone JSON exceeded the nesting-depth limit",
            reason="unparsed",
        )
    except RecursionError:
        return _generic_failure_result(
            provider,
            "standalone JSON exceeded the nesting-depth limit",
            reason="unparsed",
        )
    return _combine(builder.finish(), generic, generic_coverage=coverage)


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
    if suffix == ".json":
        return _parse_json_stream_atomic(
            lambda: io.BytesIO(content),
            provider=provider,
            source_name=safe_name,
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _generic_failure_result(
            provider,
            "standalone input is not valid UTF-8",
            reason="unparsed",
        )
    if suffix == ".jsonl":
        result = parse_jsonl(text, provider=provider, source_name=safe_name)
    elif suffix == ".csv":
        result = _parse_csv_document(text, provider=provider, source_name=safe_name)
    elif suffix in {".md", ".markdown", ".txt", ""}:
        result = parse_text(text, provider=provider, source_name=safe_name)
    else:
        raise InvalidStateError(
            "supported import types are ZIP, JSON, JSONL, CSV, Markdown, and text"
        )
    return ParsedArchive(
        candidates=result.candidates,
        warnings=result.warnings,
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
        attachments=list(result.attachments),
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
        return _parse_json_stream_atomic(
            lambda: path.open("rb"),
            provider=provider,
            source_name=safe_name,
            progress=progress,
        )
    if suffix == ".jsonl":
        return _parse_jsonl_stream(path, safe_name, provider, progress=progress)
    if suffix in {".csv", ".md", ".markdown", ".txt", ""}:
        if progress is not None:
            progress.check_cancelled()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return _generic_failure_result(
                provider,
                "standalone input is not valid UTF-8",
                reason="unparsed",
            )
        result = (
            _parse_csv_document(text, provider=provider, source_name=safe_name)
            if suffix == ".csv"
            else parse_text(text, provider=provider, source_name=safe_name)
        )
        if progress is not None:
            progress.check_cancelled()
        return ParsedArchive(
            candidates=result.candidates,
            warnings=result.warnings,
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
            attachments=list(result.attachments),
        )
    raise InvalidStateError("supported import types are ZIP, JSON, JSONL, CSV, Markdown, and text")


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
    mime_types: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    mime_types_by_filename: dict[str, str | None] = field(default_factory=dict)
    ambiguous_mime_filenames: set[str] = field(default_factory=set)
    mime_types_by_member: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    links: dict[str, set[AttachmentLink]] = field(default_factory=dict)
    link_sources: dict[str, set[str]] = field(default_factory=dict)
    member_names_by_reference: dict[str, set[str]] = field(default_factory=dict)
    control_members: set[str] = field(default_factory=set)
    invalid_control_members: set[str] = field(default_factory=set)
    max_link_pairs: int = DEFAULT_MAX_ATTACHMENT_LINK_PAIRS
    link_pairs: int = 0
    links_truncated: bool = False
    scan_nodes: int = 0
    scan_truncated: bool = False

    def add_link(self, reference: str, link: AttachmentLink) -> bool:
        existing = self.links.get(reference)
        if existing is None:
            if self.link_pairs >= self.max_link_pairs:
                self.links_truncated = True
                return False
            existing = set()
            self.links[reference] = existing
        if link in existing:
            return True
        if self.link_pairs >= self.max_link_pairs:
            self.links_truncated = True
            return False
        existing.add(link)
        self.link_pairs += 1
        return True

    def note_scan_node(self, depth: int) -> bool:
        if depth > MAX_CHATGPT_ATTACHMENT_SCAN_DEPTH:
            self.scan_truncated = True
            return False
        if self.scan_nodes >= MAX_CHATGPT_ATTACHMENT_SCAN_NODES:
            self.scan_truncated = True
            return False
        self.scan_nodes += 1
        return True

    def begin_scan(self) -> None:
        self.scan_nodes = 0


def _normalize_attachment_member_name(value: str) -> str:
    return _safe_zip_name(value)


def _attachment_id_from_member_name(safe_name: str) -> str:
    """Return the archive member identity, not a collision-prone filename stem."""
    return safe_name


def _attachment_reference_from_member_name(safe_name: str) -> str:
    """Return the provider attachment reference used to resolve a member link."""
    return PurePosixPath(safe_name).stem.casefold()


def _normalize_attachment_reference(value: str) -> str:
    return value.strip().casefold()[:512]


def _safe_attachment_filename(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    name = PurePosixPath(normalized).name
    cleaned = _safe_diagnostic_text(name)
    return cleaned[:512] or None


def _first_mapping_string(value: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:512]
    return None


def _merge_mime_declaration(
    current: tuple[str | None, str] | None,
    incoming: tuple[str | None, str],
) -> tuple[str | None, str]:
    if current is None:
        return incoming
    if current[0] is None or incoming[0] is None:
        return (None, "ambiguous")
    if current[0].casefold() != incoming[0].casefold():
        return (None, "ambiguous")
    return current


def _declare_mime(
    context: _ChatGPTAttachmentContext,
    reference: str,
    mime_type: str,
    source: str,
) -> None:
    normalized = mime_type.strip()[:256]
    if not normalized:
        return
    key = _normalize_attachment_reference(reference)
    if not key:
        return
    context.mime_types[key] = _merge_mime_declaration(
        context.mime_types.get(key), (normalized, source)
    )


def _declare_member_mime(
    context: _ChatGPTAttachmentContext,
    member_name: str,
    declaration: tuple[str | None, str],
) -> None:
    context.mime_types_by_member[member_name] = _merge_mime_declaration(
        context.mime_types_by_member.get(member_name), declaration
    )


def _collect_chatgpt_attachment_links(
    value: Any,
    context: _ChatGPTAttachmentContext,
    *,
    source_name: str | None = None,
) -> None:
    context.begin_scan()
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if not context.note_scan_node(depth):
            continue
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in reversed(current))
            continue
        if not isinstance(current, dict):
            continue
        mapping = current.get("mapping")
        if isinstance(mapping, dict):
            conversation_id = _first_mapping_string(
                current, ("conversation_id", "id", "uuid", "chat_id")
            )
            if conversation_id is None:
                continue
            for node_id, node in mapping.items():
                if not context.note_scan_node(depth + 1):
                    break
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
                    if not context.note_scan_node(depth + 2):
                        break
                    if not isinstance(attachment, dict):
                        continue
                    asset_id = _first_mapping_string(attachment, ("id", "asset_id"))
                    if asset_id is None:
                        continue
                    reference = _normalize_attachment_reference(asset_id)
                    link = AttachmentLink(conversation_id, message_id)
                    if context.add_link(reference, link) and source_name is not None:
                        context.link_sources.setdefault(reference, set()).add(source_name)
                    mime_type = attachment.get("mime_type")
                    if isinstance(mime_type, str) and mime_type.strip():
                        _declare_mime(
                            context,
                            reference,
                            mime_type,
                            "conversation_attachment",
                        )
            continue
        for key in ("conversations", "conversation_history", "items", "data", "export"):
            nested = current.get(key)
            if isinstance(nested, (dict, list)):
                pending.append((nested, depth + 1))


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
        filename: next(iter(values)) if len(values) == 1 else None
        for filename, values in by_filename.items()
    }
    context.ambiguous_mime_filenames = {
        filename for filename, values in by_filename.items() if len(values) > 1
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
        root_is_array = raw.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"[")
        documents = list(
            _iter_json_documents(
                io.BytesIO(raw),
                max_item_chars=max_item_chars,
                max_bytes=max_item_chars,
            )
        )
        values = [document.value for document in documents]
        if root_is_array:
            if len(values) == 1 and values[0] == []:
                return []
            return values
        if len(documents) == 1:
            return values[0]
        return values
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidStateError) as error:
        raise InvalidStateError("attachment metadata JSON is invalid") from error


def _attachment_context(
    archive: zipfile.ZipFile,
    members: Sequence[zipfile.ZipInfo],
    *,
    max_item_chars: int,
    max_link_pairs: int,
) -> _ChatGPTAttachmentContext:
    context = _ChatGPTAttachmentContext(max_link_pairs=max_link_pairs)
    by_basename: dict[str, zipfile.ZipInfo] = {}
    for member in members:
        safe_name = _safe_zip_name(member.filename)
        by_basename.setdefault(PurePosixPath(safe_name).name.casefold(), member)
        if PurePosixPath(safe_name).suffix.casefold() == ".dat":
            reference = _attachment_reference_from_member_name(safe_name)
            if reference:
                context.member_names_by_reference.setdefault(reference, set()).add(safe_name)

    asset_member = by_basename.get("conversation_asset_file_names.json")
    if asset_member is not None:
        safe_asset_name = _safe_zip_name(asset_member.filename)
        context.control_members.add(safe_asset_name)
        try:
            value = _load_zip_json_member(archive, asset_member, max_item_chars=max_item_chars)
        except InvalidStateError:
            context.invalid_control_members.add(safe_asset_name)
            value = None
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
        safe_manifest_name = _safe_zip_name(manifest_member.filename)
        context.control_members.add(safe_manifest_name)
        try:
            value = _load_zip_json_member(archive, manifest_member, max_item_chars=max_item_chars)
        except InvalidStateError:
            context.invalid_control_members.add(safe_manifest_name)
            value = None
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
        safe_library_name = _safe_zip_name(library_member.filename)
        context.control_members.add(safe_library_name)
        try:
            library_value = _load_zip_json_member(
                archive,
                library_member,
                max_item_chars=max_item_chars,
            )
        except InvalidStateError:
            context.invalid_control_members.add(safe_library_name)
            library_value = None
        _collect_library_mime_types(library_value, context)
        for safe_name, original_filename in context.original_filenames.items():
            if original_filename in context.ambiguous_mime_filenames:
                _declare_member_mime(context, safe_name, (None, "ambiguous"))
                continue
            mime_type = context.mime_types_by_filename.get(original_filename)
            if mime_type is not None:
                _declare_member_mime(context, safe_name, (mime_type, "library_files"))

    return context


def _mime_for_member(
    context: _ChatGPTAttachmentContext,
    safe_name: str,
) -> tuple[str | None, str | None, str]:
    declarations: list[tuple[str | None, str]] = []
    member_declaration = context.mime_types_by_member.get(safe_name)
    if member_declaration is not None:
        declarations.append(member_declaration)
    reference = _attachment_reference_from_member_name(safe_name)
    reference_declaration = context.mime_types.get(reference)
    if reference_declaration is not None:
        matching_members = context.member_names_by_reference.get(reference, set())
        if len(matching_members) == 1:
            declarations.append(reference_declaration)
        else:
            declarations.append((None, "ambiguous"))
    if not declarations:
        return None, None, "unknown"
    merged = declarations[0]
    for declaration in declarations[1:]:
        merged = _merge_mime_declaration(merged, declaration)
    if merged[0] is None:
        return None, None, "ambiguous"
    return merged[0], merged[1], "known"


def _links_for_member(
    context: _ChatGPTAttachmentContext,
    safe_name: str,
) -> tuple[list[AttachmentLink], list[str]]:
    reference = _attachment_reference_from_member_name(safe_name)
    if len(context.member_names_by_reference.get(reference, set())) != 1:
        return [], []
    links = sorted(
        context.links.get(reference, set()),
        key=lambda item: (item.conversation_id, item.message_id),
    )
    sources = sorted(context.link_sources.get(reference, set())) if links else []
    return links, sources


def _looks_like_chatgpt_structure(value: Any) -> bool:
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        if depth > MAX_CHATGPT_ATTACHMENT_SCAN_DEPTH or nodes >= MAX_CHATGPT_ATTACHMENT_SCAN_NODES:
            return False
        nodes += 1
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in reversed(current))
            continue
        if not isinstance(current, dict):
            continue
        if isinstance(current.get("mapping"), dict):
            return True
        pending.extend(
            (current[key], depth + 1)
            for key in ("conversations", "conversation_history", "items", "data", "export")
            if isinstance(current.get(key), (dict, list))
        )
    return False


def _is_conversation_json_member(
    safe_name: str,
    provider_hint: ArchiveProvider = ArchiveProvider.AUTO,
) -> bool:
    """Recognize provider containers without treating generic JSON as one.

    The exact filename allowlist is the canonical ``conversations.json`` plus
    the frozen alternate basenames ``chats.json``, ``history.json``, and
    ``messages.json``. The canonical name is always structural. An alternate
    name is structural only with an explicit provider hint or an exact provider
    signature path component (for example ``chatgpt/chats.json``); otherwise a
    valid provider-shaped value can still become structural through parser
    statistics, while malformed neutral JSON remains an ordinary item.
    """
    path = PurePosixPath(safe_name)
    basename = path.name.casefold()
    canonical = basename == "conversations.json" or bool(
        _DATED_CONVERSATIONS_BASENAME.fullmatch(basename)
    )
    if path.suffix.casefold() != ".json" or (
        not canonical and basename not in _PROVIDER_CONTAINER_BASENAMES
    ):
        return False
    if canonical:
        return True
    if provider_hint not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
        return True
    return any(part.casefold() in _PROVIDER_SIGNATURE_PATH_PARTS for part in path.parts[:-1])


def _is_neutral_auto_provider_container(
    safe_name: str,
    provider: str | ArchiveProvider,
) -> bool:
    """Identify alternate JSON names whose auto-mode signature is provisional."""
    if normalize_provider(provider) != ArchiveProvider.AUTO:
        return False
    path = PurePosixPath(safe_name)
    basename = path.name.casefold()
    if path.suffix.casefold() != ".json" or basename not in _PROVIDER_CONTAINER_BASENAMES:
        return False
    if basename == "conversations.json" or _DATED_CONVERSATIONS_BASENAME.fullmatch(basename):
        return False
    return not any(part.casefold() in _PROVIDER_SIGNATURE_PATH_PARTS for part in path.parts[:-1])


def _is_chatgpt_control_member(safe_name: str) -> bool:
    return PurePosixPath(safe_name).name.casefold() in _CHATGPT_CONTROL_BASENAMES


def _archive_chatgpt_structure_members(
    archive: zipfile.ZipFile,
    members: Sequence[zipfile.ZipInfo],
    *,
    max_item_chars: int,
    progress: ImportProgressTracker | None = None,
) -> set[str]:
    detected_members: set[str] = set()
    for member in members:
        safe_name = _safe_zip_name(member.filename)
        basename = PurePosixPath(safe_name).name.casefold()
        if PurePosixPath(safe_name).suffix.casefold() != ".json" or (
            basename not in _PROVIDER_CONTAINER_BASENAMES
            and not _DATED_CONVERSATIONS_BASENAME.fullmatch(basename)
        ):
            continue
        signature_observed = False
        try:
            with archive.open(member) as stream:
                for document in _iter_json_documents(stream, max_item_chars=max_item_chars):
                    if _looks_like_chatgpt_structure(document.value):
                        # Do not publish the signature until the complete
                        # bounded iterator has validated successfully.
                        signature_observed = True
        except (UnicodeDecodeError, json.JSONDecodeError, InvalidStateError):
            continue
        finally:
            if progress is not None:
                progress.check_cancelled()
        if signature_observed:
            detected_members.add(safe_name)
    return detected_members


def _archive_has_chatgpt_structure(
    archive: zipfile.ZipFile,
    members: Sequence[zipfile.ZipInfo],
    *,
    max_item_chars: int,
    progress: ImportProgressTracker | None = None,
) -> bool:
    """Compatibility predicate backed by the content-signature member scan."""
    return bool(
        _archive_chatgpt_structure_members(
            archive,
            members,
            max_item_chars=max_item_chars,
            progress=progress,
        )
    )


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
    max_path_depth: int = DEFAULT_MAX_ZIP_PATH_DEPTH,
    max_json_item_chars: int = DEFAULT_MAX_JSON_ITEM_CHARS,
    max_attachment_text_bytes: int = DEFAULT_MAX_ATTACHMENT_TEXT_BYTES,
    max_attachment_link_pairs: int = DEFAULT_MAX_ATTACHMENT_LINK_PAIRS,
    progress: ImportProgressTracker | None = None,
) -> ParsedArchive:
    """Parse bounded ZIP members in place; archive paths are never extracted."""
    if (
        max_entries < 1
        or max_uncompressed_bytes < 1
        or max_member_uncompressed_bytes < 1
        or max_path_depth < 1
    ):
        raise ValueError("ZIP limits must be positive")
    if (
        max_compression_ratio < 1
        or max_json_item_chars < 1
        or max_attachment_text_bytes < 1
        or max_attachment_link_pairs < 1
    ):
        raise ValueError("ZIP read limits must be positive")
    provider_hint = normalize_provider(provider)
    builder = _builder(provider_hint)
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
            file_members = sum(not item.is_dir() for item in members)
            directories_excluded = sum(item.is_dir() for item in members)
            if len(members) > max_entries:
                return _archive_preflight_result(
                    provider_hint,
                    "ZIP archive rejected by the entry-count safety limit",
                    archive_level_failure="zip_entry_count_limit",
                    file_members=file_members,
                    directories_excluded=directories_excluded,
                    terminal_reason="unavailable",
                )
            seen_names: set[str] = set()
            unique_entries: list[tuple[int, zipfile.ZipInfo]] = []
            archive_member_states: dict[int, str] = {}
            archive_structural_indices: set[int] = set()
            archive_member_buckets = {
                "recognized": 0,
                "excluded": 0,
                "skipped": 0,
                "unavailable": 0,
                "duplicate": 0,
                "failed": 0,
                "unparsed": 0,
            }
            archive_file_members = 0
            archive_directories_excluded = 0
            archive_structural_members = 0

            def close_archive_member(member_index: int, reason: str) -> None:
                if member_index in archive_member_states:
                    raise InvalidStateError("ZIP member coverage was assigned twice")
                if reason not in archive_member_buckets:
                    raise InvalidStateError("ZIP member coverage used an unknown reason")
                archive_member_states[member_index] = reason
                archive_member_buckets[reason] += 1

            def mark_structural_member(member_index: int) -> None:
                nonlocal archive_structural_members
                if member_index in archive_member_states or (
                    member_index in archive_structural_indices
                ):
                    raise InvalidStateError("ZIP structural member was assigned twice")
                archive_structural_indices.add(member_index)
                archive_structural_members += 1

            total_uncompressed = sum(item.file_size for item in members if not item.is_dir())
            if total_uncompressed > max_uncompressed_bytes:
                return _archive_preflight_result(
                    provider_hint,
                    "ZIP archive rejected by the total uncompressed-size safety limit",
                    archive_level_failure="zip_total_size_limit",
                    file_members=file_members,
                    directories_excluded=directories_excluded,
                    terminal_reason="unavailable",
                    total_uncompressed=total_uncompressed,
                )
            supported_members: list[zipfile.ZipInfo] = []
            for member_index, member in enumerate(members):
                if member.is_dir():
                    archive_directories_excluded += 1
                    continue
                archive_file_members += 1
                try:
                    safe_name = _validate_zip_member_name(
                        member.filename,
                        max_depth=max_path_depth,
                    )
                except InvalidStateError:
                    unsupported_entries += 1
                    close_archive_member(member_index, "unavailable")
                    _append_warning(
                        warnings,
                        f"{_safe_zip_name(member.filename)}: "
                        "ZIP member rejected by the path safety limit",
                    )
                    continue
                builder.note_file(safe_name)
                # ZIP member names are logical identifiers even though members
                # are never extracted. Collapse compatibility-equivalent Unicode
                # spellings as well as case so one cross-platform logical path
                # cannot be interpreted twice with conflicting payloads.
                folded = unicodedata.normalize("NFKC", safe_name).casefold()
                duplicate = folded in seen_names
                if member.file_size > max_member_uncompressed_bytes:
                    unsupported_entries += 1
                    close_archive_member(member_index, "unavailable")
                    _append_warning(
                        warnings,
                        "one ZIP member was rejected by the per-member size limit",
                    )
                    continue
                if member.flag_bits & 0x1:
                    unsupported_entries += 1
                    close_archive_member(member_index, "unavailable")
                    _append_warning(
                        warnings,
                        "one encrypted ZIP member was rejected before payload reads",
                    )
                    continue
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > max_compression_ratio
                ):
                    unsupported_entries += 1
                    close_archive_member(member_index, "unavailable")
                    _append_warning(
                        warnings,
                        "one ZIP member was rejected by the compression-ratio safety limit",
                    )
                    continue
                if duplicate:
                    builder.note_duplicate_entries()
                    close_archive_member(member_index, "duplicate")
                    _append_warning(
                        warnings,
                        f"{safe_name}: Unicode/case-insensitive duplicate entry skipped",
                    )
                    continue
                seen_names.add(folded)
                unique_entries.append((member_index, member))

            unique_members = [member for _index, member in unique_entries]
            unique_index_by_identity = {id(member): index for index, member in unique_entries}

            chatgpt_structure_names: set[str] = set()
            attachment_enabled = provider_hint == ArchiveProvider.CHATGPT
            if provider_hint in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
                chatgpt_structure_names = _archive_chatgpt_structure_members(
                    archive,
                    unique_members,
                    max_item_chars=max_json_item_chars,
                    progress=progress,
                )
                attachment_enabled = bool(chatgpt_structure_names)
            elif provider_hint == ArchiveProvider.CHATGPT:
                chatgpt_structure_names = {
                    _safe_zip_name(member.filename)
                    for member in unique_members
                    if _is_conversation_json_member(
                        _safe_zip_name(member.filename),
                        provider_hint,
                    )
                }
            context = (
                _attachment_context(
                    archive,
                    unique_members,
                    max_item_chars=max_json_item_chars,
                    max_link_pairs=max_attachment_link_pairs,
                )
                if attachment_enabled
                else _ChatGPTAttachmentContext(max_link_pairs=max_attachment_link_pairs)
            )
            structural_names = {
                _safe_zip_name(member.filename)
                for member in unique_members
                if _is_chatgpt_control_member(_safe_zip_name(member.filename))
                or _safe_zip_name(member.filename) in context.control_members
            }
            provider_container_names = {
                _safe_zip_name(member.filename)
                for member in unique_members
                if _is_conversation_json_member(
                    _safe_zip_name(member.filename),
                    provider_hint,
                )
            }
            provider_container_names.update(chatgpt_structure_names)
            if context.invalid_control_members:
                coverage.unparsed += len(context.invalid_control_members)
                for invalid_name in sorted(context.invalid_control_members):
                    _append_warning(warnings, f"{invalid_name}: invalid control JSON")

            def close_parsed_member(
                member_index: int,
                before_stats: Mapping[str, int],
                before_generic: int,
                before_coverage: Mapping[str, int],
            ) -> None:
                """Audit one non-structural member without changing item coverage.

                Provider containers are structural: their contained messages and
                provider-memory items are the logical denominator. A generic
                standalone member is audited as one archive content member;
                its logical items remain in ``closed_coverage``.
                """
                after_stats = builder.stats_snapshot()
                provider_fields = (
                    "conversations",
                    "messages",
                    "message_records",
                    "memory_items",
                    "recognized_files",
                )
                if any(
                    after_stats.get(key, 0) > before_stats.get(key, 0) for key in provider_fields
                ):
                    mark_structural_member(member_index)
                    return
                for reason in ("unparsed", "failed"):
                    if getattr(coverage, reason) > before_coverage.get(reason, 0):
                        close_archive_member(member_index, reason)
                        return
                recognized_delta = after_stats.get("recognized_items", 0) > before_stats.get(
                    "recognized_items", 0
                )
                if len(generic) > before_generic or recognized_delta:
                    close_archive_member(member_index, "recognized")
                    return
                for reason in ("skipped", "excluded"):
                    if getattr(coverage, reason) > before_coverage.get(reason, 0):
                        close_archive_member(member_index, reason)
                        return
                close_archive_member(member_index, "excluded")

            for member in unique_members:
                safe_name = _safe_zip_name(member.filename)
                if not attachment_enabled or safe_name not in chatgpt_structure_names:
                    continue
                try:
                    with archive.open(member) as stream:
                        for document in _iter_json_documents(
                            stream, max_item_chars=max_json_item_chars
                        ):
                            if attachment_enabled:
                                _collect_chatgpt_attachment_links(
                                    document.value,
                                    context,
                                    source_name=safe_name,
                                )
                except (UnicodeDecodeError, json.JSONDecodeError, InvalidStateError):
                    continue

            for member_index, member in unique_entries:
                safe_name = _safe_zip_name(member.filename)
                suffix = PurePosixPath(safe_name).suffix.casefold()
                if safe_name in structural_names:
                    mark_structural_member(member_index)
                    if safe_name in context.invalid_control_members:
                        # The malformed control JSON was already accounted as
                        # one unparsed logical item above.
                        continue
                    continue
                before_stats = builder.stats_snapshot()
                before_generic = len(generic)
                before_coverage = {
                    "excluded": coverage.excluded,
                    "skipped": coverage.skipped,
                    "failed": coverage.failed,
                    "unparsed": coverage.unparsed,
                }
                if suffix == ".dat" and attachment_enabled:
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
                    mime_type, mime_source, mime_status = _mime_for_member(context, safe_name)
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
                                    # Validate the whole bounded member before publishing any
                                    # candidates. The streaming reader may yield valid array
                                    # items before discovering malformed trailing bytes.
                                    for _ in _iter_json_documents(
                                        io.BytesIO(raw_text),
                                        max_item_chars=max_json_item_chars,
                                    ):
                                        pass
                                    for document in _iter_json_documents(
                                        io.BytesIO(raw_text),
                                        max_item_chars=max_json_item_chars,
                                    ):
                                        if attachment_enabled and _is_conversation_json_member(
                                            safe_name
                                        ):
                                            _collect_chatgpt_attachment_links(
                                                document.value,
                                                context,
                                                source_name=safe_name,
                                            )
                                        _consume_json_value(
                                            builder,
                                            source_name,
                                            document.value,
                                            generic,
                                            coverage,
                                            context=document.context,
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
                                    csv_text = _csv_text(
                                        text,
                                        max_chars=max_attachment_text_bytes,
                                    )
                                    before_generic = len(generic)
                                    recognized = builder.consume_text(source_name, csv_text)
                                    if not recognized:
                                        generic.extend(_labeled_text_candidates(csv_text))
                                        if len(generic) == before_generic:
                                            coverage.excluded += 1
                                else:
                                    before_generic = len(generic)
                                    recognized = builder.consume_text(source_name, text)
                                    if not recognized:
                                        generic.extend(_labeled_text_candidates(text))
                                        if len(generic) == before_generic:
                                            coverage.excluded += 1
                                extraction_status = "text_extracted"
                                extracted_format = text_format
                            except (UnicodeDecodeError, json.JSONDecodeError, InvalidStateError):
                                extraction_status = "text_parse_failed"
                                _append_warning(warnings, f"{safe_name}: attachment text skipped")
                                coverage.unparsed += 1
                    # A malformed declared-text attachment was already
                    # accounted as unparsed above. Only binary and bounded
                    # read-limit attachments are unavailable.
                    if extraction_status in {"unsupported_binary", "text_read_limit"}:
                        unsupported_attachments += 1
                        unsupported_entries += 1
                    links, link_sources = _links_for_member(context, safe_name)
                    provenance = (
                        ["export_manifest.json"] if safe_name in context.manifest_members else []
                    )
                    if original_filename is not None:
                        provenance.append("conversation_asset_file_names.json")
                    if mime_source is not None and mime_status == "known":
                        provenance.append(mime_source)
                    if links:
                        provenance.extend(
                            f"{source}:message.metadata.attachments" for source in link_sources
                        )
                    attachments.append(
                        AttachmentInventory(
                            asset_id=asset_id,
                            archive_name=safe_name,
                            content_sha256=content_sha256,
                            byte_size=member.file_size,
                            original_filename=original_filename,
                            mime_type=mime_type,
                            mime_type_source=mime_source,
                            mime_type_status=mime_status,
                            links=tuple(links),
                            extraction_status=extraction_status,
                            extracted_format=extracted_format,
                            provenance=tuple(provenance),
                        )
                    )
                    if extraction_status in {"unsupported_binary", "text_read_limit"}:
                        close_archive_member(member_index, "unavailable")
                    elif extraction_status == "text_parse_failed":
                        close_archive_member(member_index, "unparsed")
                    else:
                        close_parsed_member(
                            member_index,
                            before_stats,
                            before_generic,
                            before_coverage,
                        )
                    continue
                if suffix == ".dat":
                    unsupported_entries += 1
                    close_archive_member(member_index, "unavailable")
                    continue
                if suffix == ".json" and _is_chatgpt_control_member(safe_name):
                    mark_structural_member(member_index)
                    continue
                if suffix not in _SUPPORTED_TEXT_SUFFIXES:
                    unsupported_entries += 1
                    close_archive_member(member_index, "unavailable")
                    continue
                if safe_name in context.control_members:
                    mark_structural_member(member_index)
                    continue
                if suffix in {".md", ".markdown", ".txt"} and (
                    member.file_size > max_json_item_chars
                ):
                    # Retained raw does not mean extracted: close this member
                    # as unavailable in item-level coverage.
                    unsupported_entries += 1
                    _append_warning(
                        warnings,
                        f"{safe_name}: text entry exceeds the per-entry parse limit; retained raw",
                    )
                    close_archive_member(member_index, "unavailable")
                    continue
                supported_members.append(member)

            for member in supported_members:
                if progress is not None:
                    progress.check_cancelled()
                member_index = unique_index_by_identity[id(member)]
                safe_name = _safe_zip_name(member.filename)
                suffix = PurePosixPath(safe_name).suffix.casefold()
                is_provider_container = safe_name in provider_container_names
                before_stats = builder.stats_snapshot()
                before_generic = len(generic)
                before_coverage = {
                    "excluded": coverage.excluded,
                    "skipped": coverage.skipped,
                    "failed": coverage.failed,
                    "unparsed": coverage.unparsed,
                }
                try:
                    if suffix == ".json":
                        observed_providers: set[ArchiveProvider] = set()
                        validation_builder = _builder(provider_hint)
                        with archive.open(member) as stream:
                            # Validate the complete bounded member before
                            # publishing any candidates. Root-array items can
                            # be yielded before trailing garbage is found.
                            for document in _iter_json_documents(
                                stream,
                                max_item_chars=max_json_item_chars,
                            ):
                                detected = validation_builder.observe_json_provider(
                                    safe_name, document.value
                                )
                                if detected not in {
                                    ArchiveProvider.AUTO,
                                    ArchiveProvider.GENERIC,
                                }:
                                    observed_providers.add(detected)
                        # Provider promotion is atomic with complete member
                        # validation. The observations are only bounded
                        # provider signatures, never a buffered JSON root.
                        for detected in sorted(observed_providers, key=lambda item: item.value):
                            builder.note_provider_context(detected)
                        with archive.open(member) as stream:
                            for document in _iter_json_documents(
                                stream,
                                max_item_chars=max_json_item_chars,
                            ):
                                _consume_json_value(
                                    builder,
                                    safe_name,
                                    document.value,
                                    generic,
                                    coverage,
                                    provider_container=is_provider_container,
                                    context=document.context,
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
                        text = raw_text.decode("utf-8-sig")
                        if suffix == ".csv":
                            text = _csv_text(text, max_chars=max_json_item_chars)
                        before_generic = len(generic)
                        recognized = builder.consume_text(safe_name, text)
                        if not recognized:
                            generic.extend(_labeled_text_candidates(text))
                            if len(generic) == before_generic:
                                coverage.excluded += 1
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    coverage.unparsed += 1
                    _append_warning(warnings, f"{safe_name}: {_invalid_json_error(error)}")
                    if is_provider_container:
                        mark_structural_member(member_index)
                    else:
                        close_archive_member(member_index, "unparsed")
                except (_JsonNestingLimitError, RecursionError):
                    coverage.unparsed += 1
                    _append_warning(warnings, f"{safe_name}: bounded JSON nesting limit")
                    if is_provider_container:
                        mark_structural_member(member_index)
                    else:
                        close_archive_member(member_index, "unparsed")
                except InvalidStateError as error:
                    malformed_csv = suffix == ".csv" and "not well formed" in str(error).casefold()
                    malformed_neutral_json = (
                        suffix == ".json"
                        and _is_neutral_auto_provider_container(safe_name, provider_hint)
                    )
                    if malformed_csv or malformed_neutral_json:
                        # Malformed text is one unparsed logical item. Keep
                        # `failed` for bounded parser/runtime failures that
                        # are not malformed source data.
                        coverage.unparsed += 1
                    else:
                        # A bounded member parser failure is a durable failed
                        # item, not an invisible warning. The raw ZIP remains
                        # preserved for retry with a later parser.
                        builder.note_failed_items()
                    if is_provider_container:
                        mark_structural_member(member_index)
                    elif malformed_csv or malformed_neutral_json:
                        close_archive_member(member_index, "unparsed")
                    else:
                        close_archive_member(member_index, "failed")
                    _append_warning(warnings, f"{safe_name}: {error}")
                else:
                    if is_provider_container:
                        mark_structural_member(member_index)
                    else:
                        close_parsed_member(
                            member_index,
                            before_stats,
                            before_generic,
                            before_coverage,
                        )
    except zipfile.BadZipFile:
        return _archive_preflight_result(
            provider_hint,
            "ZIP archive could not be enumerated; member coverage is unavailable",
            archive_level_failure="zip_enumeration_failed",
            file_members=0,
            directories_excluded=0,
            member_coverage_available=False,
        )
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
    if context.links_truncated:
        _append_warning(
            warnings,
            "attachment conversation/message link accumulation was truncated at the configured "
            f"limit of {context.max_link_pairs} pairs",
        )
    if context.scan_truncated:
        _append_warning(
            warnings,
            "attachment link scanning was truncated at the configured depth/node bound",
        )
    accounted_members = len(archive_member_states) + len(archive_structural_indices)
    if accounted_members != archive_file_members:
        raise InvalidStateError("ZIP member coverage partition did not close every file member")
    archive_member_coverage = {
        "file_members": archive_file_members,
        "directories_excluded": archive_directories_excluded,
        "structural_members": len(archive_structural_indices),
        "standalone_members": sum(archive_member_buckets.values()),
        "unaccounted_members": archive_file_members - accounted_members,
        "terminal_member_buckets": dict(archive_member_buckets),
        "denominator": (
            "non-directory ZIP members after safety validation; control/manifest and provider "
            "container members are structural, while their logical messages/entries remain in "
            "closed_coverage; standalone generic members are one archive content member"
        ),
    }
    result = _combine(builder.finish(), generic, warnings, coverage, attachments)
    result.stats.update(
        {
            "archive_member_coverage": {
                **archive_member_coverage,
                "closed_coverage_total": sum(result.closed_coverage.values()),
            },
            "attachment_entries": len(attachments),
            "attachment_hashed": len(attachments),
            "attachment_linked": sum(bool(item.links) for item in attachments),
            "attachment_link_pairs": sum(len(item.links) for item in attachments),
            "attachment_link_limit": max_attachment_link_pairs,
            "attachment_links_truncated": context.links_truncated,
            "attachment_link_scan_truncated": context.scan_truncated,
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
    max_bytes: int | None = None,
    max_depth: int = DEFAULT_MAX_JSON_NESTING_DEPTH,
) -> Iterator[_JsonDocument]:
    """Yield bounded JSON values with root context, without root materialization."""
    if max_item_chars < 1 or chunk_chars < 1 or max_depth < 1:
        raise ValueError("JSON limits must be positive")
    byte_limit = max_bytes if max_bytes is not None else max_item_chars * 4
    if byte_limit < 1:
        raise ValueError("JSON byte limit must be positive")

    decoder_factory = codecs.getincrementaldecoder("utf-8-sig")
    utf8_decoder = decoder_factory(errors="strict")
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False
    total_bytes = 0
    depth = 0
    in_string = False
    escaped = False

    def scan_depth(chunk: str) -> None:
        nonlocal depth, in_string, escaped
        for character in chunk:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character in "[{":
                depth += 1
                if depth > max_depth:
                    raise _JsonNestingLimitError("JSON document exceeds the nesting-depth limit")
            elif character in "]}" and depth:
                depth -= 1

    def decode_chunk(raw: bytes, *, final: bool = False) -> str:
        nonlocal total_bytes
        total_bytes += len(raw)
        if total_bytes > byte_limit:
            raise InvalidStateError("JSON document exceeds the byte parse limit")
        chunk = utf8_decoder.decode(raw, final=final)
        if chunk:
            scan_depth(chunk)
        return chunk

    def fill() -> bool:
        nonlocal buffer, position, eof
        if eof:
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        raw = stream.read(chunk_chars)
        if raw:
            buffer += decode_chunk(raw)
            return True
        tail = decode_chunk(b"", final=True)
        if tail:
            buffer += tail
            eof = True
            return True
        eof = True
        return False

    def reject_trailing_data() -> None:
        nonlocal position
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer):
                raise json.JSONDecodeError("extra data after JSON document", buffer, position)
            if not fill():
                return

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
        while True:
            if len(buffer) - position > max_item_chars:
                raise InvalidStateError("JSON document exceeds the item parse limit")
            try:
                document, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if eof:
                    raise
                if not fill():
                    raise
                continue
            except RecursionError as error:
                raise _JsonNestingLimitError(
                    "JSON document exceeds the nesting-depth limit"
                ) from error
            position = end
            yield _JsonDocument(document, JsonValueContext.ROOT)
            reject_trailing_data()
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
                position += 1
                # Preserve an empty ordinary root as one logical value. The
                # caller may classify a provider container as structural and
                # suppress this generic item accounting explicitly.
                yield _JsonDocument([], JsonValueContext.ROOT)
                reject_trailing_data()
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
            except RecursionError as error:
                raise _JsonNestingLimitError(
                    "JSON document exceeds the nesting-depth limit"
                ) from error
            if end - position > max_item_chars:
                raise InvalidStateError(
                    "one JSON conversation exceeds the per-conversation parse limit"
                )
            position = end
            yield _JsonDocument(item, JsonValueContext.ROOT_ARRAY_ITEM)
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
            position += 1
            reject_trailing_data()
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
        warnings.append(_safe_diagnostic_text(warning)[:2_000])


def _validate_zip_member_name(
    filename: str,
    *,
    max_depth: int = DEFAULT_MAX_ZIP_PATH_DEPTH,
) -> str:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    first = path.parts[0] if path.parts else ""
    if (
        max_depth < 1
        or len(normalized) > DEFAULT_MAX_ZIP_MEMBER_NAME_CHARS
        or len(path.parts) > max_depth
        or path.is_absolute()
        or ".." in path.parts
        or first.endswith(":")
        or re.match(r"^[A-Za-z]:", first) is not None
        or normalized.startswith("//")
        or any(not char.isprintable() for char in normalized)
    ):
        raise InvalidStateError("ZIP bundle contains an unsafe member path")
    return _safe_zip_name(filename)


def _safe_zip_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return _safe_diagnostic_text(normalized)[-1_000:] or "archive-entry"


def _safe_diagnostic_text(value: str) -> str:
    """Keep untrusted names and warning text bounded and single-line safe."""
    escaped: list[str] = []
    for char in value:
        if char.isprintable() and unicodedata.category(char) != "Cc":
            escaped.append(char)
            continue
        codepoint = ord(char)
        escaped.append(f"\\x{codepoint:02x}" if codepoint <= 0xFF else f"\\u{codepoint:04x}")
    return "".join(escaped)


class ArchiveImportService:
    def __init__(
        self,
        store: CoreStore,
        *,
        max_bytes: int = DEFAULT_MAX_IMPORT_BYTES,
        max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_TEXT_BYTES,
        cancel_registry: ImportCancelRegistry | None = None,
        skip_disk_preflight: bool = False,
        activity_gate: CoreActivityGate | None = None,
    ) -> None:
        if not 1 <= max_bytes <= MAX_IMPORT_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_IMPORT_BYTES}")
        self.store = store
        self.ingestion = IngestionService(store)
        self.max_bytes = max_bytes
        self.max_expanded_bytes = max(max_expanded_bytes, max_bytes)
        self.cancel_registry = cancel_registry or DEFAULT_CANCEL_REGISTRY
        self.skip_disk_preflight = skip_disk_preflight
        self.activity_gate = activity_gate or CoreActivityGate()
        self._activity_lock = threading.Lock()
        self._active_activity_count = 0

    @contextmanager
    def _activity_scope(self) -> Iterator[None]:
        with self.activity_gate.activity():
            with self._activity_lock:
                self._active_activity_count += 1
            try:
                yield
            finally:
                with self._activity_lock:
                    self._active_activity_count -= 1

    def activity_snapshot(self) -> dict[str, Any]:
        """Return bounded, content-free direct importer activity."""

        with self._activity_lock:
            count = self._active_activity_count
        bounded_count = min(count, 1)
        return {
            "active": count > 0,
            "count": bounded_count,
            "truncated": count > bounded_count,
        }

    def import_path(
        self,
        path: Path,
        *,
        filename: str | None = None,
        source_service: str = ArchiveProvider.AUTO.value,
        provider: str | None = None,
    ) -> dict[str, Any]:
        with self._activity_scope():
            return self._import_path(
                path,
                filename=filename,
                source_service=source_service,
                provider=provider,
            )

    def _import_path(
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
        with self._activity_scope():
            return self._import_bytes(
                filename,
                content,
                source_service=source_service,
                provider=provider,
            )

    def _import_bytes(
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
        with self._activity_scope():
            return self._reprocess_source(
                source_id,
                progress_tracker=progress_tracker,
                rebuild=rebuild,
            )

    def _reprocess_source(
        self,
        source_id: str,
        *,
        progress_tracker: ImportProgressTracker | None = None,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        """Resume or rebuild extraction from the preserved raw blob.

        Failed and cancelled sources retry the existing parser session.
        Complete sources with incomplete coverage use the same bounded repair
        path as an explicit rebuild, so the preserved blob is reparsed and
        canonical automatic records are replaced only after complete coverage
        is proven.
        ``rebuild=True`` on a complete source stages a new parser-versioned
        observation set, then atomically replaces eligible automatic records
        only after parsing and staging succeed. The raw blob and
        user-corrected records are not destroyed.
        """
        source = self.store.get_source(source_id, duplicate=True)
        repair_incomplete_coverage = (
            not rebuild
            and source.import_status == "complete"
            and source.metadata.get("coverage_complete") is False
        )
        if repair_incomplete_coverage:
            # A complete source with closed coverage is not a retryable
            # no-op. Reuse the existing rebuild authority so parsing remains
            # non-destructive and publication remains fail-closed.
            rebuild = True
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
                metadata["rebuild_source_marker"] = source_rebuild_marker(
                    source.id, source.content_hash, rebuild_generation
                )
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
                        "rebuild_source_marker",
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
            metadata = _mark_terminal_status(source.metadata, "cancelled")
            metadata = merge_progress_metadata(metadata, tracker.snapshot())
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
            metadata = _mark_terminal_status(source.metadata, "failed")
            metadata = merge_progress_metadata(metadata, tracker.snapshot())
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
                closed_coverage=parsed.closed_coverage,
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
                metadata["rebuild_source_marker"] = source_rebuild_marker(
                    source.id, source.content_hash, rebuild_generation
                )
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


def _mark_terminal_status(metadata: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Preserve terminal status separately from item-level coverage counts."""

    updated = dict(metadata)
    closed = {key: 0 for key in CLOSED_COVERAGE_KEYS}
    existing = metadata.get("closed_coverage")
    if isinstance(existing, Mapping):
        for key in closed:
            value = existing.get(key)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= MAX_CLOSED_COVERAGE_COUNT
            ):
                closed[key] = value
    if reason not in {"failed", "cancelled"}:
        raise ValueError("invalid terminal import reason")
    updated["closed_coverage"] = closed
    updated["coverage_complete"] = False
    updated["source_terminal_reason"] = reason
    return updated


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
            "duplicate": 0,
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

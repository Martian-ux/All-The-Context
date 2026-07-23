"""Bounded local archive parsing and resumable candidate ingestion."""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

from .ingestion import IngestionService, archive_session_request
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
)
from .storage import CoreStore, InvalidStateError

DEFAULT_MAX_IMPORT_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_EXPANDED_TEXT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_JSON_ITEM_CHARS = 128 * 1024 * 1024

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
_SUPPORTED_TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".markdown", ".txt"}


@dataclass(frozen=True, slots=True)
class ParsedArchive:
    candidates: list[CandidateInput]
    warnings: list[str]
    provider: str = ArchiveProvider.GENERIC.value
    export_format: str = "generic_document"
    stats: dict[str, int | str] = field(default_factory=dict)
    available: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    complete: bool = True
    recognized_provider: bool = False


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
    _consume_json_value(builder, source_name, value, generic)
    return _combine(builder.finish(), generic)


def parse_jsonl(
    text: str,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    source_name: str = "import.jsonl",
) -> ParsedArchive:
    builder = _builder(provider)
    candidates: list[CandidateInput] = []
    warnings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _append_warning(warnings, f"line {line_number}: invalid JSON skipped")
            continue
        _consume_json_value(builder, source_name, value, candidates)
    return _combine(builder.finish(), candidates, warnings)


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
) -> None:
    if isinstance(value, list):
        if not value:
            builder.note_file(source_name)
            return
        for item in value:
            recognized = builder.consume_json(source_name, item)
            if not recognized:
                _extract_json(item, generic)
        return
    recognized = builder.consume_json(source_name, value)
    if not recognized:
        _extract_json(value, generic)


def _combine(
    provider_result: ProviderExtraction,
    generic: Iterable[CandidateInput],
    warnings: Sequence[str] = (),
) -> ParsedArchive:
    combined_warnings = _deduplicate_strings([*warnings, *provider_result.warnings])[:512]
    candidates = _deduplicate([*generic, *provider_result.candidates])
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
    )


def parse_archive_path(
    path: Path,
    *,
    display_name: str | None = None,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    max_uncompressed_bytes: int = DEFAULT_MAX_EXPANDED_TEXT_BYTES,
) -> ParsedArchive:
    safe_name = Path(display_name or path.name).name
    suffix = Path(safe_name).suffix.casefold()
    if suffix == ".zip":
        return parse_zip_bundle(
            path,
            provider=provider,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
    if suffix == ".json":
        builder = _builder(provider)
        generic: list[CandidateInput] = []
        try:
            with path.open("rb") as stream:
                for document in _iter_json_documents(stream):
                    _consume_json_value(builder, safe_name, document, generic)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _invalid_json_error(error) from error
        return _combine(builder.finish(), generic)
    if suffix == ".jsonl":
        return _parse_jsonl_stream(path, safe_name, provider)
    if suffix in {".md", ".markdown", ".txt", ""}:
        try:
            text = path.read_text(encoding="utf-8-sig")
            warnings: list[str] = []
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            warnings = ["invalid UTF-8 sequences were replaced"]
        result = parse_text(text, provider=provider, source_name=safe_name)
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
        )
    raise InvalidStateError("supported import types are ZIP, JSON, JSONL, Markdown, and text")


def _parse_jsonl_stream(
    path: Path,
    source_name: str,
    provider: str | ArchiveProvider,
) -> ParsedArchive:
    builder = _builder(provider)
    generic: list[CandidateInput] = []
    warnings: list[str] = []
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _append_warning(warnings, f"line {line_number}: invalid JSON skipped")
                continue
            _consume_json_value(builder, source_name, value, generic)
    return _combine(builder.finish(), generic, warnings)


def parse_zip_bundle(
    content: bytes | Path,
    *,
    provider: str | ArchiveProvider = ArchiveProvider.AUTO,
    max_entries: int = 10_000,
    max_uncompressed_bytes: int = DEFAULT_MAX_EXPANDED_TEXT_BYTES,
    max_compression_ratio: int = 500,
    max_json_item_chars: int = DEFAULT_MAX_JSON_ITEM_CHARS,
) -> ParsedArchive:
    """Parse supported ZIP members in place; archive paths are never extracted."""
    builder = _builder(provider)
    generic: list[CandidateInput] = []
    warnings: list[str] = []
    unsupported_entries = 0
    source: io.BytesIO | Path = io.BytesIO(content) if isinstance(content, bytes) else content
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise InvalidStateError("ZIP bundle contains too many entries")
            seen_names: set[str] = set()
            supported_size = 0
            supported_members: list[zipfile.ZipInfo] = []
            for member in members:
                if member.is_dir():
                    continue
                safe_name = _validate_zip_member_name(member.filename)
                builder.note_file(safe_name)
                folded = safe_name.casefold()
                if folded in seen_names:
                    _append_warning(
                        warnings,
                        f"{safe_name}: case-insensitive duplicate entry skipped",
                    )
                    continue
                seen_names.add(folded)
                suffix = PurePosixPath(safe_name).suffix.casefold()
                if suffix not in _SUPPORTED_TEXT_SUFFIXES:
                    unsupported_entries += 1
                    continue
                if member.flag_bits & 0x1:
                    raise InvalidStateError("encrypted ZIP text entries are not supported")
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > max_compression_ratio
                ):
                    raise InvalidStateError("ZIP bundle exceeds the compression-ratio limit")
                supported_size += member.file_size
                if supported_size > max_uncompressed_bytes:
                    raise InvalidStateError(
                        "ZIP bundle exceeds the uncompressed-size limit for text entries"
                    )
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
                safe_name = _safe_zip_name(member.filename)
                suffix = PurePosixPath(safe_name).suffix.casefold()
                try:
                    if suffix == ".json":
                        with archive.open(member) as stream:
                            for document in _iter_json_documents(
                                stream, max_item_chars=max_json_item_chars
                            ):
                                _consume_json_value(builder, safe_name, document, generic)
                    elif suffix == ".jsonl":
                        _consume_zip_jsonl(archive, member, safe_name, builder, generic, warnings)
                    else:
                        with archive.open(member) as stream:
                            raw_text = stream.read()
                        try:
                            text = raw_text.decode("utf-8-sig")
                        except UnicodeDecodeError:
                            text = raw_text.decode("utf-8-sig", errors="replace")
                            _append_warning(
                                warnings,
                                f"{safe_name}: invalid UTF-8 sequences were replaced",
                            )
                        recognized = builder.consume_text(safe_name, text)
                        if not recognized:
                            generic.extend(_labeled_text_candidates(text))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    _append_warning(warnings, f"{safe_name}: {_invalid_json_error(error)}")
                except InvalidStateError as error:
                    _append_warning(warnings, f"{safe_name}: {error}")
    except zipfile.BadZipFile as error:
        raise InvalidStateError("invalid ZIP bundle") from error
    builder.note_unsupported_entries(unsupported_entries)
    if unsupported_entries:
        _append_warning(
            warnings,
            f"{unsupported_entries} non-text archive entries were retained raw and skipped "
            "during memory extraction",
        )
    return _combine(builder.finish(), generic, warnings)


def _consume_zip_jsonl(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    source_name: str,
    builder: ProviderArchiveBuilder,
    generic: list[CandidateInput],
    warnings: list[str],
) -> None:
    with archive.open(member) as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _append_warning(
                    warnings,
                    f"{source_name}: line {line_number}: invalid JSON skipped",
                )
                continue
            _consume_json_value(builder, source_name, value, generic)


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
    ) -> None:
        self.store = store
        self.ingestion = IngestionService(store)
        self.max_bytes = max_bytes
        self.max_expanded_bytes = max(max_expanded_bytes, max_bytes)

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
        if size > self.max_bytes:
            raise InvalidStateError(f"import exceeds the {self.max_bytes}-byte size limit")
        safe_name = Path(filename or resolved.name).name
        parsed = parse_archive_path(
            resolved,
            display_name=safe_name,
            provider=_provider_hint(provider, source_service),
            max_uncompressed_bytes=self.max_expanded_bytes,
        )
        actual_service = _actual_source_service(parsed, source_service, provider)
        source = self.store.add_source_file(
            resolved,
            source_service=actual_service,
            source_type=_source_type(safe_name),
            filename=safe_name,
            media_type=_media_type(safe_name),
            metadata=_source_metadata(parsed),
            parser_warnings=parsed.warnings,
            import_status="processing",
        )
        return self._ingest(source, parsed, actual_service)

    def import_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        source_service: str = ArchiveProvider.AUTO.value,
        provider: str | None = None,
    ) -> dict[str, Any]:
        if len(content) > self.max_bytes:
            raise InvalidStateError(f"import exceeds the {self.max_bytes}-byte size limit")
        safe_name = Path(filename).name
        provider_hint = _provider_hint(provider, source_service)
        parsed = (
            parse_zip_bundle(
                content,
                provider=provider_hint,
                max_uncompressed_bytes=self.max_expanded_bytes,
            )
            if Path(safe_name).suffix.casefold() == ".zip"
            else parse_archive(safe_name, content, provider=provider_hint)
        )
        actual_service = _actual_source_service(parsed, source_service, provider)
        source = self.store.add_source(
            content,
            source_service=actual_service,
            source_type=_source_type(safe_name),
            filename=safe_name,
            media_type=_media_type(safe_name),
            metadata=_source_metadata(parsed),
            parser_warnings=parsed.warnings,
            import_status="processing",
        )
        return self._ingest(source, parsed, actual_service)

    def reprocess_source(self, source_id: str) -> dict[str, Any]:
        """Resume extraction from the preserved raw blob after interruption or failure."""
        source = self.store.get_source(source_id, duplicate=True)
        if source.import_status == "complete":
            candidate_ids = self.store.candidate_ids_for_source(source.id)
            observations = [self.store.get_candidate(item) for item in candidate_ids]
            coverage = {
                "available": [source.filename or source.id],
                "unavailable": [],
                "limitations": [],
                "warnings": source.parser_warnings,
                "complete": bool(source.metadata.get("coverage_complete", True)),
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
            }

        provider = str(source.metadata.get("provider", source.source_service))
        with tempfile.TemporaryDirectory(
            prefix="atc-reprocess-", dir=self.store.database_path.parent
        ) as temporary_directory:
            raw_path = Path(temporary_directory) / "preserved-source"
            self.store.copy_source_content_to_path(source.id, raw_path)
            parsed = parse_archive_path(
                raw_path,
                display_name=source.filename or "import.txt",
                provider=_provider_hint(None, provider),
                max_uncompressed_bytes=self.max_expanded_bytes,
            )
        self.store.update_source_import(
            source.id,
            import_status="processing",
            metadata=_source_metadata(parsed),
            parser_warnings=parsed.warnings,
        )
        processing = self.store.get_source(source.id, duplicate=True)
        return self._ingest(processing, parsed, source.source_service)

    def _ingest(
        self,
        source: SourceOut,
        parsed: ParsedArchive,
        source_service: str,
    ) -> dict[str, Any]:
        if source.duplicate and source.import_status == "complete":
            existing_ids = self.store.candidate_ids_for_source(source.id)
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
            begin = self.ingestion.begin(
                archive_session_request(source.id, parser_version=PARSER_VERSION)
            )
            candidate_ids: list[str] = []
            for index, batch in enumerate(_chunks(candidates, 200)):
                submitted = self.ingestion.submit(
                    SubmitBatchRequest(
                        session_id=str(begin["session_id"]),
                        idempotency_key=(f"{source.content_hash}:{PARSER_VERSION}:{index}"),
                        candidates=batch,
                    )
                )
                candidate_ids.extend(str(item) for item in submitted["candidate_ids"])
            coverage = CoverageReport(
                available=parsed.available,
                unavailable=parsed.unavailable,
                warnings=parsed.warnings,
                limitations=parsed.limitations,
                complete=parsed.complete,
            )
            finished = self.ingestion.finish(
                FinishIngestionRequest(
                    session_id=str(begin["session_id"]),
                    coverage_report=coverage,
                )
            )
            self.store.update_source_import(
                source.id,
                import_status="complete",
                metadata=_source_metadata(parsed),
                parser_warnings=parsed.warnings,
            )
            refreshed = self.store.get_source(source.id, duplicate=source.duplicate)
            return self._import_result(
                refreshed,
                finished,
                candidate_ids or self.store.candidate_ids_for_source(source.id),
                parsed,
                duplicate=source.duplicate,
            )
        except Exception:
            self.store.update_source_import(
                source.id,
                import_status="failed",
                metadata=_source_metadata(parsed),
                parser_warnings=parsed.warnings,
            )
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


def _source_metadata(parsed: ParsedArchive) -> dict[str, Any]:
    return {
        "provider": parsed.provider,
        "export_format": parsed.export_format,
        "parser_version": PARSER_VERSION,
        "stats": parsed.stats,
        "coverage_complete": parsed.complete,
    }


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
        "coverage": {
            "available": parsed.available,
            "unavailable": parsed.unavailable,
            "limitations": parsed.limitations,
            "warnings": parsed.warnings,
            "complete": parsed.complete,
        },
    }


def _chunks(items: Sequence[CandidateInput], size: int) -> Iterable[list[CandidateInput]]:
    for offset in range(0, len(items), size):
        yield list(items[offset : offset + size])

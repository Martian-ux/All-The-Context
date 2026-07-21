"""Bounded, inert archive parsers and deterministic obvious-fact extraction."""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ingestion import IngestionService, archive_session_request
from .models import (
    Availability,
    CandidateInput,
    CoverageReport,
    FinishIngestionRequest,
    SubmitBatchRequest,
)
from .storage import CoreStore, InvalidStateError

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
    r"(?:api[_ -]?key|password|private[_ -]?key|access[_ -]?token|secret)\s*[:=]",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedArchive:
    candidates: list[CandidateInput]
    warnings: list[str]


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


def _extract_json(value: Any, candidates: list[CandidateInput], warnings: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _extract_json(item, candidates, warnings)
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
            _extract_json(raw, candidates, warnings)


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:16_000]


def parse_json(text: str) -> ParsedArchive:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise InvalidStateError(
            f"invalid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    candidates: list[CandidateInput] = []
    warnings: list[str] = []
    _extract_json(value, candidates, warnings)
    candidates.extend(_extract_chatgpt_user_statements(value))
    return ParsedArchive(_deduplicate(candidates), warnings)


def parse_jsonl(text: str) -> ParsedArchive:
    candidates: list[CandidateInput] = []
    warnings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"line {line_number}: invalid JSON skipped")
            continue
        _extract_json(value, candidates, warnings)
    return ParsedArchive(_deduplicate(candidates), warnings)


def parse_text(text: str) -> ParsedArchive:
    candidates: list[CandidateInput] = []
    for line in text.splitlines():
        cleaned = line.lstrip("#*- ").strip()
        match = _LABELED_LINE.match(cleaned)
        if match:
            item = _candidate(_KIND_MAP[match.group(1).casefold()], match.group(2), evidence=line)
            if item is not None:
                candidates.append(item)
    return ParsedArchive(_deduplicate(candidates), [])


def _deduplicate(items: Iterable[CandidateInput]) -> list[CandidateInput]:
    result: list[CandidateInput] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.kind.casefold(), item.content.casefold())
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def parse_archive(filename: str, content: bytes) -> ParsedArchive:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.casefold()
    try:
        text = content.decode("utf-8")
        decode_warnings: list[str] = []
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
        decode_warnings = ["invalid UTF-8 sequences were replaced"]
    if suffix == ".json":
        result = parse_json(text)
    elif suffix == ".jsonl":
        result = parse_jsonl(text)
    elif suffix in {".md", ".markdown", ".txt", ""}:
        result = parse_text(text)
    else:
        raise InvalidStateError("supported import types are JSON, JSONL, Markdown, and text")
    return ParsedArchive(result.candidates, [*decode_warnings, *result.warnings])


def _extract_chatgpt_user_statements(value: Any) -> list[CandidateInput]:
    """Read only explicitly labeled durable facts from user-authored export messages."""
    if not isinstance(value, list):
        return []
    extracted: list[CandidateInput] = []
    for conversation in value:
        if not isinstance(conversation, dict) or not isinstance(conversation.get("mapping"), dict):
            continue
        for node in conversation["mapping"].values():
            if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                continue
            message = node["message"]
            author = message.get("author")
            content = message.get("content")
            if not isinstance(author, dict) or author.get("role") != "user":
                continue
            if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
                continue
            for part in content["parts"]:
                if isinstance(part, str):
                    extracted.extend(parse_text(part).candidates)
    return _deduplicate(extracted)


def parse_zip_bundle(
    content: bytes,
    *,
    max_entries: int = 1_000,
    max_uncompressed_bytes: int = 50 * 1024 * 1024,
    max_compression_ratio: int = 100,
) -> ParsedArchive:
    """Parse supported ZIP members in memory without extracting paths to disk."""
    candidates: list[CandidateInput] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise InvalidStateError("ZIP bundle contains too many entries")
            total_size = sum(member.file_size for member in members)
            if total_size > max_uncompressed_bytes:
                raise InvalidStateError("ZIP bundle exceeds the uncompressed-size limit")
            for member in members:
                member_path = Path(member.filename.replace("\\", "/"))
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise InvalidStateError("ZIP bundle contains an unsafe member path")
                if member.flag_bits & 0x1:
                    raise InvalidStateError("encrypted ZIP entries are not supported")
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > max_compression_ratio
                ):
                    raise InvalidStateError("ZIP bundle exceeds the compression-ratio limit")
                if member.is_dir():
                    continue
                suffix = member_path.suffix.casefold()
                if suffix not in {".json", ".jsonl", ".md", ".markdown", ".txt"}:
                    warnings.append(f"{member.filename}: unsupported entry skipped")
                    continue
                try:
                    parsed = parse_archive(member_path.name, archive.read(member))
                except InvalidStateError as error:
                    warnings.append(f"{member.filename}: {error}")
                    continue
                candidates.extend(parsed.candidates)
                warnings.extend(f"{member.filename}: {warning}" for warning in parsed.warnings)
    except zipfile.BadZipFile as error:
        raise InvalidStateError("invalid ZIP bundle") from error
    return ParsedArchive(_deduplicate(candidates), warnings)


class ArchiveImportService:
    def __init__(self, store: CoreStore, *, max_bytes: int = 50 * 1024 * 1024) -> None:
        self.store = store
        self.ingestion = IngestionService(store)
        self.max_bytes = max_bytes

    def import_bytes(
        self,
        filename: str,
        content: bytes,
        *,
        source_service: str = "generic",
    ) -> dict[str, Any]:
        if len(content) > self.max_bytes:
            raise InvalidStateError(f"import exceeds the {self.max_bytes}-byte size limit")
        safe_name = Path(filename).name
        is_zip = Path(safe_name).suffix.casefold() == ".zip"
        parsed = (
            parse_zip_bundle(content, max_uncompressed_bytes=self.max_bytes)
            if is_zip
            else parse_archive(safe_name, content)
        )
        source = self.store.add_source(
            content,
            source_service=source_service,
            source_type=Path(safe_name).suffix.casefold().lstrip(".") or "text",
            filename=safe_name,
            media_type=(
                "application/zip"
                if is_zip
                else (
                    "application/json" if safe_name.casefold().endswith(".json") else "text/plain"
                )
            ),
            parser_warnings=parsed.warnings,
        )
        if source.duplicate:
            existing_ids = self.store.candidate_ids_for_source(source.id)
            return {
                "source": source.model_dump(mode="json"),
                "session": {
                    "status": "duplicate",
                    "candidate_count": len(existing_ids),
                    "coverage": {"available": [safe_name], "unavailable": []},
                },
                "candidate_ids": existing_ids,
                "warnings": [*parsed.warnings, "duplicate source; existing extraction retained"],
            }
        candidates = [
            candidate.model_copy(
                update={
                    "source_id": source.id,
                    "source_service": source_service,
                    "source_type": source.source_type,
                }
            )
            for candidate in parsed.candidates
        ]
        begin = self.ingestion.begin(archive_session_request(source.id))
        candidate_ids: list[str] = []
        if candidates:
            for index, batch in enumerate(_chunks(candidates, 200)):
                submitted = self.ingestion.submit(
                    SubmitBatchRequest(
                        session_id=str(begin["session_id"]),
                        idempotency_key=f"{source.content_hash}:deterministic-v1:{index}",
                        candidates=batch,
                    )
                )
                candidate_ids.extend(str(item) for item in submitted["candidate_ids"])
        finished = self.ingestion.finish(
            FinishIngestionRequest(
                session_id=str(begin["session_id"]),
                coverage_report=CoverageReport(
                    available=[safe_name],
                    unavailable=[],
                    warnings=parsed.warnings,
                    limitations=["Only explicitly labeled or structured facts were extracted."],
                ),
            )
        )
        return {
            "source": source.model_dump(mode="json"),
            "session": finished,
            "candidate_ids": candidate_ids,
            "warnings": parsed.warnings,
        }


def _chunks(items: Sequence[CandidateInput], size: int) -> Iterable[list[CandidateInput]]:
    for offset in range(0, len(items), size):
        yield list(items[offset : offset + size])

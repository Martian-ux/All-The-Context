"""Provider-neutral normalization and safe deterministic memory extraction.

Provider exports are untrusted input.  This module never executes archive content and
never treats assistant messages as user facts.  It normalizes the parts of official
account exports that are useful for provenance, then emits reviewable candidates from
user-authored statements and dedicated memory/profile fields.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from .models import Availability, CandidateInput, Sensitivity

PARSER_VERSION = "provider-archives-v1"


class ArchiveProvider(StrEnum):
    AUTO = "auto"
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GROK = "grok"
    GENERIC = "generic"


SUPPORTED_PROVIDER_VALUES = tuple(provider.value for provider in ArchiveProvider)

_SECRET_HINT = re.compile(
    r"(?:api[_ -]?key|password|passphrase|private[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|secret)\s*[:=]",
    flags=re.IGNORECASE,
)
_SENSITIVE_HINT = re.compile(
    r"(?:\b(?:social security|ssn|passport|driver'?s license|date of birth|dob)\b|"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:phone|mobile) number\b)",
    flags=re.IGNORECASE,
)
_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", flags=re.DOTALL)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n+")
_MARKDOWN_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)")
_ROLE_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?"
    r"(user|human|you|assistant|chatgpt|claude|grok)"
    r"(?:\*\*)?\s*(?::|-)?\s*(.*)$",
    flags=re.IGNORECASE,
)
_LABEL = re.compile(
    r"^(preference|preferences|decision|decisions|project|projects|goal|goals|"
    r"constraint|constraints|workflow|workflows|fact|facts|task|tasks)\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)
_LABEL_KINDS = {
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
_MEMORY_KEY_PARTS = (
    "memory",
    "memories",
    "custom_instruction",
    "custominstruction",
    "about_user",
    "about_model",
    "user_profile",
    "personalization",
    "project_instruction",
)
_TRANSIENT_HINT = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|right now|this chat|this conversation)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    provider: ArchiveProvider
    conversation_id: str
    conversation_title: str | None
    message_id: str
    role: str
    text: str
    source_name: str
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderExtraction:
    provider: ArchiveProvider
    export_format: str
    candidates: list[CandidateInput]
    warnings: list[str]
    stats: dict[str, int | str]
    available: list[str]
    unavailable: list[str]
    limitations: list[str]
    complete: bool
    recognized: bool


def normalize_provider(value: str | ArchiveProvider | None) -> ArchiveProvider:
    if isinstance(value, ArchiveProvider):
        return value
    normalized = (value or ArchiveProvider.AUTO.value).strip().casefold()
    aliases = {
        "openai": ArchiveProvider.CHATGPT,
        "chat-gpt": ArchiveProvider.CHATGPT,
        "anthropic": ArchiveProvider.CLAUDE,
        "x": ArchiveProvider.GROK,
        "xai": ArchiveProvider.GROK,
        "x.ai": ArchiveProvider.GROK,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return ArchiveProvider(normalized)
    except ValueError as error:
        supported = ", ".join(SUPPORTED_PROVIDER_VALUES)
        raise ValueError(f"unsupported archive provider; choose one of: {supported}") from error


@dataclass(slots=True)
class ProviderArchiveBuilder:
    """Accumulate normalized provider data across one file or ZIP bundle."""

    provider_hint: ArchiveProvider = ArchiveProvider.AUTO
    _candidates: list[CandidateInput] = field(default_factory=list, init=False)
    _warnings: list[str] = field(default_factory=list, init=False)
    _providers: set[ArchiveProvider] = field(default_factory=set, init=False)
    _formats: set[str] = field(default_factory=set, init=False)
    _files_seen: set[str] = field(default_factory=set, init=False)
    _recognized_files: set[str] = field(default_factory=set, init=False)
    _stats: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.provider_hint = normalize_provider(self.provider_hint)
        self._stats = {
            "documents": 0,
            "conversations": 0,
            "messages": 0,
            "message_records": 0,
            "user_messages": 0,
            "assistant_messages": 0,
            "other_messages": 0,
            "memory_items": 0,
            "skipped_messages": 0,
            "unparsed_messages": 0,
            "unsupported_entries": 0,
        }

    def note_file(self, source_name: str) -> None:
        self._files_seen.add(_safe_source_name(source_name))

    def note_unsupported_entries(self, count: int) -> None:
        self._stats["unsupported_entries"] += max(count, 0)

    def add_warning(self, warning: str) -> None:
        if warning and warning not in self._warnings and len(self._warnings) < 512:
            self._warnings.append(warning[:2_000])

    def consume_json(self, source_name: str, value: Any) -> bool:
        """Consume a JSON document, returning whether a provider schema was recognized."""
        safe_name = _safe_source_name(source_name)
        self.note_file(safe_name)
        self._stats["documents"] += 1
        provider = _detect_json_provider(value, safe_name, self.provider_hint)
        recognized = False

        conversations = _conversation_values(value)
        if _looks_like_conversation(value):
            conversations = [value]
        if conversations:
            for conversation in conversations:
                conversation_provider = _detect_json_provider(conversation, safe_name, provider)
                messages = _normalize_conversation(
                    conversation,
                    conversation_provider,
                    safe_name,
                    self._stats["conversations"],
                )
                if not _looks_like_conversation(conversation):
                    continue
                recognized = True
                self._providers.add(conversation_provider)
                self._formats.add(_format_for_conversation(conversation, conversation_provider))
                self._stats["conversations"] += 1
                raw_message_count = _conversation_message_count(conversation)
                self._stats["message_records"] += raw_message_count
                self._stats["unparsed_messages"] += max(raw_message_count - len(messages), 0)
                self._consume_messages(messages)

        memory_items = list(_deduplicate_strings(_memory_strings(value)))
        if not memory_items and _looks_like_memory_filename(safe_name):
            memory_items = list(_deduplicate_strings(_dedicated_memory_strings(value)))
        if memory_items and (_looks_like_memory_document(value, safe_name) or recognized):
            memory_provider = provider
            if memory_provider in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
                memory_provider = _provider_from_filename(safe_name)
            if memory_provider == ArchiveProvider.AUTO:
                memory_provider = ArchiveProvider.GENERIC
            self._providers.add(memory_provider)
            self._formats.add("provider_memory_json")
            recognized = True
            for index, memory in enumerate(memory_items):
                candidate = _memory_candidate(
                    memory,
                    provider=memory_provider,
                    reference=f"{safe_name}#memory-{index + 1}",
                )
                if candidate is not None:
                    self._candidates.append(candidate)
                    self._stats["memory_items"] += 1

        if recognized:
            self._recognized_files.add(safe_name)
        return recognized

    def consume_text(self, source_name: str, text: str) -> bool:
        """Consume a provider memory text file or Markdown conversation transcript."""
        safe_name = _safe_source_name(source_name)
        self.note_file(safe_name)
        provider = self.provider_hint
        if provider in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
            detected = _provider_from_text_or_filename(text, safe_name)
            if detected != ArchiveProvider.AUTO:
                provider = detected
        messages = _markdown_messages(text, provider, safe_name)
        if messages:
            if provider in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
                provider = _assistant_provider(messages) or ArchiveProvider.GENERIC
                messages = [
                    NormalizedMessage(
                        provider=provider,
                        conversation_id=item.conversation_id,
                        conversation_title=item.conversation_title,
                        message_id=item.message_id,
                        role=item.role,
                        text=item.text,
                        source_name=item.source_name,
                        created_at=item.created_at,
                    )
                    for item in messages
                ]
            self._providers.add(provider)
            self._formats.add("markdown_transcript")
            self._recognized_files.add(safe_name)
            self._stats["conversations"] += 1
            self._consume_messages(messages)
            return True

        if _looks_like_memory_filename(safe_name) or self.provider_hint not in {
            ArchiveProvider.AUTO,
            ArchiveProvider.GENERIC,
        }:
            if provider == ArchiveProvider.AUTO:
                provider = ArchiveProvider.GENERIC
            items = list(_memory_text_items(text))
            if not items:
                return False
            self._providers.add(provider)
            self._formats.add("provider_memory_text")
            self._recognized_files.add(safe_name)
            for index, item in enumerate(items):
                candidate = _memory_candidate(
                    item,
                    provider=provider,
                    reference=f"{safe_name}#memory-{index + 1}",
                )
                if candidate is not None:
                    self._candidates.append(candidate)
                    self._stats["memory_items"] += 1
            return True
        return False

    def finish(self) -> ProviderExtraction:
        candidates = _deduplicate_candidates(self._candidates)
        provider = self._result_provider()
        formats = sorted(self._formats)
        export_format = "+".join(formats) if formats else "generic_document"
        stats: dict[str, int | str] = {
            **self._stats,
            "files": len(self._files_seen),
            "recognized_files": len(self._recognized_files),
            "candidates": len(candidates),
            "provider": provider.value,
            "parser_version": PARSER_VERSION,
        }
        recognized = bool(self._recognized_files)
        available = [f"raw import ({len(self._files_seen)} file entries inspected)"]
        if self._stats["conversations"]:
            available.append(
                f"{self._stats['conversations']} conversations / "
                f"{self._stats['user_messages']} user messages"
            )
        if self._stats["memory_items"]:
            available.append(f"{self._stats['memory_items']} provider memory/profile items")

        unavailable: list[str] = []
        if self._stats["unsupported_entries"]:
            unavailable.append(
                f"{self._stats['unsupported_entries']} non-text attachments were retained raw "
                "but not converted into memory candidates"
            )
        if not recognized and provider != ArchiveProvider.GENERIC:
            unavailable.append(f"no recognized {provider.value} conversation schema")

        limitations = [
            "Only user-authored messages and dedicated provider memory/profile fields can "
            "produce candidates.",
            "Assistant responses, system/tool messages, and attachments remain in the raw "
            "source and are never trusted as user memory.",
            "Deterministic extraction can miss implicit context; the preserved source can be "
            "reprocessed by a later extractor.",
        ]
        complete = not any(
            marker in warning.casefold()
            for warning in self._warnings
            for marker in ("invalid json", "could not parse", "exceeds", "truncated")
        )
        return ProviderExtraction(
            provider=provider,
            export_format=export_format,
            candidates=candidates,
            warnings=list(self._warnings),
            stats=stats,
            available=available,
            unavailable=unavailable,
            limitations=limitations,
            complete=complete,
            recognized=recognized,
        )

    def _consume_messages(self, messages: Sequence[NormalizedMessage]) -> None:
        for message in messages:
            self._stats["messages"] += 1
            if message.role == "user":
                self._stats["user_messages"] += 1
                extracted = _durable_candidates(message)
                self._candidates.extend(extracted)
                if not extracted:
                    self._stats["skipped_messages"] += 1
            elif message.role == "assistant":
                self._stats["assistant_messages"] += 1
            else:
                self._stats["other_messages"] += 1

    def _result_provider(self) -> ArchiveProvider:
        meaningful = {
            item
            for item in self._providers
            if item not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}
        }
        if len(meaningful) == 1:
            return next(iter(meaningful))
        if len(meaningful) > 1:
            self.add_warning(
                "multiple provider schemas were found; the import is reported as generic"
            )
            return ArchiveProvider.GENERIC
        if self.provider_hint != ArchiveProvider.AUTO:
            return self.provider_hint
        return ArchiveProvider.GENERIC


def _detect_json_provider(
    value: Any,
    source_name: str,
    hint: ArchiveProvider,
) -> ArchiveProvider:
    if isinstance(value, dict):
        if isinstance(value.get("mapping"), dict):
            return ArchiveProvider.CHATGPT
        if isinstance(value.get("chat_messages"), list):
            return ArchiveProvider.CLAUDE
        if isinstance(value.get("grok_conversations"), list):
            return ArchiveProvider.GROK
        service_material = " ".join(
            str(value.get(key, "")) for key in ("provider", "service", "model", "source")
        ).casefold()
        if "grok" in service_material or "x.ai" in service_material or "xai" in service_material:
            return ArchiveProvider.GROK
        if "claude" in service_material or "anthropic" in service_material:
            return ArchiveProvider.CLAUDE
        if "chatgpt" in service_material or "openai" in service_material:
            return ArchiveProvider.CHATGPT
        nested = _conversation_values(value)
        if nested:
            first = next((item for item in nested if isinstance(item, dict)), None)
            if first is not None and first is not value:
                detected = _detect_json_provider(first, source_name, ArchiveProvider.AUTO)
                if detected != ArchiveProvider.AUTO:
                    return detected
    if hint not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
        return hint
    by_name = _provider_from_filename(source_name)
    return by_name


def _provider_from_filename(source_name: str) -> ArchiveProvider:
    lowered = source_name.casefold()
    if "grok" in lowered or "xai" in lowered or "x.ai" in lowered:
        return ArchiveProvider.GROK
    if "claude" in lowered or "anthropic" in lowered:
        return ArchiveProvider.CLAUDE
    if "chatgpt" in lowered or "openai" in lowered:
        return ArchiveProvider.CHATGPT
    return ArchiveProvider.AUTO


def _provider_from_text_or_filename(text: str, source_name: str) -> ArchiveProvider:
    by_name = _provider_from_filename(source_name)
    if by_name != ArchiveProvider.AUTO:
        return by_name
    sample = text[:8_000].casefold()
    if re.search(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?grok(?:\*\*)?\s*:", sample):
        return ArchiveProvider.GROK
    if re.search(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?claude(?:\*\*)?\s*:", sample):
        return ArchiveProvider.CLAUDE
    if re.search(r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?chatgpt(?:\*\*)?\s*:", sample):
        return ArchiveProvider.CHATGPT
    return ArchiveProvider.AUTO


def _conversation_values(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, dict):
        return []
    for key in (
        "conversations",
        "grok_conversations",
        "conversation_history",
        "chats",
        "threads",
        "items",
    ):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    for key in ("data", "export", "account_data"):
        nested = value.get(key)
        if isinstance(nested, dict):
            conversations = _conversation_values(nested)
            if conversations:
                return conversations
    return []


def _looks_like_conversation(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("mapping"), dict)
        or any(
            isinstance(value.get(key), list)
            for key in ("chat_messages", "messages", "turns", "responses")
        )
        or (
            any(isinstance(value.get(key), str) for key in ("user", "query", "prompt", "human"))
            and any(
                isinstance(value.get(key), str)
                for key in ("assistant", "response", "answer", "grok")
            )
        )
    )


def _format_for_conversation(value: Mapping[str, Any], provider: ArchiveProvider) -> str:
    if isinstance(value.get("mapping"), dict):
        return "chatgpt_conversation_graph"
    if isinstance(value.get("chat_messages"), list):
        return "claude_conversations"
    if provider == ArchiveProvider.GROK:
        return "grok_conversations"
    return "provider_conversations"


def _normalize_conversation(
    value: Mapping[str, Any],
    provider: ArchiveProvider,
    source_name: str,
    ordinal: int,
) -> list[NormalizedMessage]:
    title = _first_string(value, ("title", "name", "subject"))
    raw_id = _first_string(value, ("id", "uuid", "conversation_id", "chat_id"))
    conversation_id = raw_id or _stable_id(f"{source_name}:{title or ''}:{ordinal}")
    if isinstance(value.get("mapping"), dict):
        raw_messages: list[tuple[int, Mapping[str, Any]]] = []
        for index, (node_id, node) in enumerate(value["mapping"].items()):
            if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                continue
            message = dict(node["message"])
            message.setdefault("id", str(node_id))
            raw_messages.append((index, message))
        raw_messages.sort(key=lambda pair: _message_sort_key(pair[1], pair[0]))
        return [
            normalized
            for index, (_, message) in enumerate(raw_messages)
            if (
                normalized := _normalize_message(
                    message, provider, source_name, conversation_id, title, index
                )
            )
            is not None
        ]

    raw_values: list[Any] = []
    for key in ("chat_messages", "messages", "turns", "responses", "history"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            raw_values = candidate
            break
    if not raw_values and _looks_like_turn_pair(value):
        raw_values = [value]
    result: list[NormalizedMessage] = []
    for index, message in enumerate(raw_values):
        if not isinstance(message, dict):
            continue
        normalized = _normalize_message(
            message, provider, source_name, conversation_id, title, index
        )
        if normalized is not None:
            result.append(normalized)
            continue
        result.extend(
            _normalize_turn_pair(
                message,
                provider=provider,
                source_name=source_name,
                conversation_id=conversation_id,
                title=title,
                ordinal=index,
            )
        )
    return result


def _conversation_message_count(value: Mapping[str, Any]) -> int:
    mapping = value.get("mapping")
    if isinstance(mapping, dict):
        return sum(
            1
            for node in mapping.values()
            if isinstance(node, dict) and isinstance(node.get("message"), dict)
        )
    for key in ("chat_messages", "messages", "turns", "responses", "history"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return len(candidate)
    if _looks_like_turn_pair(value):
        return 2
    return 0


def _looks_like_turn_pair(value: Mapping[str, Any]) -> bool:
    return any(
        isinstance(value.get(key), str) for key in ("user", "query", "prompt", "human")
    ) and any(
        isinstance(value.get(key), str) for key in ("assistant", "response", "answer", "grok")
    )


def _normalize_turn_pair(
    value: Mapping[str, Any],
    *,
    provider: ArchiveProvider,
    source_name: str,
    conversation_id: str,
    title: str | None,
    ordinal: int,
) -> list[NormalizedMessage]:
    user_text = next(
        (
            str(value[key])
            for key in ("user", "query", "prompt", "human")
            if isinstance(value.get(key), str) and str(value[key]).strip()
        ),
        None,
    )
    assistant_text = next(
        (
            str(value[key])
            for key in ("assistant", "response", "answer", "grok")
            if isinstance(value.get(key), str) and str(value[key]).strip()
        ),
        None,
    )
    result: list[NormalizedMessage] = []
    for role, text, suffix in (
        ("user", user_text, "user"),
        ("assistant", assistant_text, "assistant"),
    ):
        if text is None:
            continue
        result.append(
            NormalizedMessage(
                provider=provider,
                conversation_id=conversation_id,
                conversation_title=title,
                message_id=f"{ordinal + 1}-{suffix}",
                role=role,
                text=text,
                source_name=source_name,
            )
        )
    return result


def _message_sort_key(message: Mapping[str, Any], fallback: int) -> tuple[int, float, int]:
    value = message.get("create_time") or message.get("created_at") or message.get("timestamp")
    if isinstance(value, (int, float)):
        return (0, float(value), fallback)
    if isinstance(value, str):
        digest = int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:12], 16)
        return (1, float(digest), fallback)
    return (2, float(fallback), fallback)


def _normalize_message(
    value: Mapping[str, Any],
    provider: ArchiveProvider,
    source_name: str,
    conversation_id: str,
    title: str | None,
    ordinal: int,
) -> NormalizedMessage | None:
    role_value: Any = (
        value.get("role")
        or value.get("sender")
        or value.get("author")
        or value.get("sender_type")
        or value.get("message_type")
    )
    if isinstance(role_value, dict):
        role_value = role_value.get("role") or role_value.get("name")
    role = _normalize_role(str(role_value or ""))
    text = _message_text(value)
    if not role or not text.strip():
        return None
    message_id = _first_string(value, ("id", "uuid", "message_id")) or str(ordinal + 1)
    created = value.get("created_at") or value.get("create_time") or value.get("timestamp")
    created_at = str(created) if isinstance(created, (str, int, float)) else None
    return NormalizedMessage(
        provider=provider,
        conversation_id=conversation_id[:200],
        conversation_title=title[:500] if title else None,
        message_id=message_id[:200],
        role=role,
        text=text,
        source_name=source_name,
        created_at=created_at,
    )


def _normalize_role(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"user", "human", "you", "customer", "client"}:
        return "user"
    if normalized in {"assistant", "ai", "bot", "chatgpt", "claude", "grok"}:
        return "assistant"
    if normalized in {"system", "tool", "developer", "function"}:
        return normalized
    return ""


def _message_text(value: Mapping[str, Any]) -> str:
    for key in ("text", "message", "body"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
        fragments = list(_text_fragments(candidate))
        if fragments:
            return "\n".join(fragments).strip()
    content = value.get("content")
    return "\n".join(_text_fragments(content)).strip()


def _text_fragments(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _text_fragments(item)
        return
    if not isinstance(value, dict):
        return
    for key in ("text", "parts"):
        candidate = value.get(key)
        if candidate is not None:
            yield from _text_fragments(candidate)


def _markdown_messages(
    text: str,
    provider: ArchiveProvider,
    source_name: str,
) -> list[NormalizedMessage]:
    segments: list[tuple[str, list[str]]] = []
    current_role: str | None = None
    current_lines: list[str] = []
    assistant_name: str | None = None
    for line in text.splitlines():
        match = _ROLE_HEADING.match(line)
        if match:
            label = match.group(1).casefold()
            if label in {"chatgpt", "claude", "grok"}:
                assistant_name = label
            role = _normalize_role(label)
            if current_role is not None:
                segments.append((current_role, current_lines))
            current_role = role
            current_lines = [match.group(2)] if match.group(2).strip() else []
        elif current_role is not None:
            current_lines.append(line)
    if current_role is not None:
        segments.append((current_role, current_lines))
    if not segments or not any(role == "user" for role, _ in segments):
        return []
    detected = provider
    if detected in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC} and assistant_name:
        detected = ArchiveProvider(assistant_name)
    if detected == ArchiveProvider.AUTO:
        detected = ArchiveProvider.GENERIC
    conversation_id = _stable_id(f"{source_name}:{text[:500]}")
    result: list[NormalizedMessage] = []
    for index, (role, lines) in enumerate(segments):
        body = "\n".join(lines).strip()
        if not body:
            continue
        result.append(
            NormalizedMessage(
                provider=detected,
                conversation_id=conversation_id,
                conversation_title=None,
                message_id=str(index + 1),
                role=role,
                text=body,
                source_name=source_name,
            )
        )
    return result


def _assistant_provider(messages: Sequence[NormalizedMessage]) -> ArchiveProvider | None:
    return next(
        (
            item.provider
            for item in messages
            if item.provider not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}
        ),
        None,
    )


def _durable_candidates(message: NormalizedMessage) -> list[CandidateInput]:
    text = _FENCED_CODE.sub(" ", message.text)
    reference = (
        f"{message.source_name}#conversation={message.conversation_id}&message={message.message_id}"
    )
    result: list[CandidateInput] = []
    for raw_segment in _SENTENCE_BREAK.split(text):
        segment = _clean_statement(raw_segment)
        if not segment or len(segment) > 4_000 or _SECRET_HINT.search(segment):
            continue
        classified = _classify_statement(segment)
        if classified is None:
            continue
        kind, confidence, entity_key, attribute_key = classified
        label = _LABEL.match(segment)
        candidate_content = label.group(2).strip() if label else segment
        sensitivity = (
            Sensitivity.SENSITIVE
            if _SENSITIVE_HINT.search(candidate_content)
            else Sensitivity.NORMAL
        )
        result.append(
            CandidateInput(
                kind=kind,
                content=candidate_content,
                entity_key=entity_key,
                attribute_key=attribute_key,
                scopes=["personal"],
                tags=[f"provider:{message.provider.value}", "archive_import"],
                source_reference=reference,
                source_service=message.provider.value,
                source_type="provider_archive",
                evidence=message.text[:16_000],
                confidence=confidence,
                sensitivity=sensitivity,
                availability=Availability.CORE,
                explicit_user_statement=True,
            )
        )
    return _deduplicate_candidates(result)


def _clean_statement(value: str) -> str:
    cleaned = _MARKDOWN_PREFIX.sub("", value).strip().strip("\u2022")
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def _classify_statement(
    statement: str,
) -> tuple[str, float, str | None, str | None] | None:
    lowered = statement.casefold()
    label = _LABEL.match(statement)
    if label:
        return (_LABEL_KINDS[label.group(1).casefold()], 1.0, None, None)
    if statement.endswith("?") or len(statement) < 5:
        return None
    if re.search(r"\b(?:remember|please remember|keep in mind)\s+(?:that\s+)?", lowered):
        return ("personal_context", 0.96, None, None)
    if re.search(
        r"\bmy name is\b|\bi am called\b|\bi'm called\b|\bcall me\b|\bi go by\b",
        lowered,
    ):
        return ("personal_detail", 0.96, "user", "name")
    if re.search(r"\b(?:i live in|i am based in|i'm based in|my home is in)\b", lowered):
        return ("personal_detail", 0.94, "user", "location")
    if re.search(r"\b(?:i work as|my occupation is|my job is)\b", lowered):
        return ("personal_detail", 0.92, "user", "occupation")
    if re.search(r"\b(?:i work at|my employer is)\b", lowered):
        return ("personal_detail", 0.92, "user", "employer")
    if re.search(r"\bmy pronouns are\b", lowered):
        return ("personal_detail", 0.94, "user", "pronouns")
    if re.search(r"\bmy time ?zone is\b", lowered):
        return ("personal_detail", 0.92, "user", "timezone")
    if re.search(
        r"\b(?:i prefer|i like|i don't like|i do not like|i dislike|i hate|i love|"
        r"my preference is|"
        r"please always|please never|when you (?:answer|respond)|"
        r"i want (?:you|answers|responses) to)\b",
        lowered,
    ):
        return ("interaction_preference", 0.92, None, None)
    if re.search(
        r"^(?:please\s+)?(?:never|do not|don't|avoid)\s+"
        r"(?:using|use|including|include|mentioning|mention)\b",
        lowered,
    ):
        return ("interaction_preference", 0.86, None, None)
    if re.search(
        r"\b(?:my goal is|my goals are|i aim to|i plan to|we aim to|"
        r"i want to (?:build|create|develop|ship|launch|learn|become|achieve))\b",
        lowered,
    ):
        return ("goal", 0.9, None, None)
    if re.search(
        r"\b(?:i am working on|i'm working on|we are working on|we're working on|"
        r"i am building|i'm building|we are building|we're building|my project is)\b",
        lowered,
    ):
        return ("project", 0.88, None, None)
    if re.search(
        r"\b(?:i decided|we decided|i chose|we chose|we are going with|"
        r"we're going with|i am going with|i'm going with|we are using|"
        r"we're using|i am using|i'm using|i am naming|i'm naming|"
        r"we (?:are not|aren'?t|won't) using|i am not using|i'm not using|"
        r"i won't use)\b",
        lowered,
    ):
        return ("project_decision", 0.91, None, None)
    if re.search(
        r"\b(?:i use|we use|my workflow|our workflow|my stack|our stack|"
        r"i usually|we usually)\b",
        lowered,
    ):
        return ("workflow", 0.84, None, None)
    if re.search(
        r"\b(?:i must|we must|i cannot|i can't|we cannot|we can't|must not|"
        r"must be|needs? to)\b",
        lowered,
    ):
        return ("constraint", 0.84, None, None)
    if _TRANSIENT_HINT.search(lowered):
        return None
    if re.search(
        r"\b(?:i am|i'm|i have|i've|i own|i speak|my [a-z][a-z -]{1,30} (?:is|are)|"
        r"we are|we're|we have|we've|our [a-z][a-z -]{1,30} (?:is|are))\b",
        lowered,
    ):
        return ("personal_context", 0.7, None, None)
    return None


def _memory_candidate(
    content: str,
    *,
    provider: ArchiveProvider,
    reference: str,
) -> CandidateInput | None:
    cleaned = _clean_statement(content)
    if not cleaned or len(cleaned) > 4_000 or _SECRET_HINT.search(cleaned):
        return None
    classified = _classify_statement(cleaned)
    kind = classified[0] if classified is not None else "provider_memory"
    label = _LABEL.match(cleaned)
    candidate_content = label.group(2).strip() if label else cleaned
    sensitivity = (
        Sensitivity.SENSITIVE if _SENSITIVE_HINT.search(candidate_content) else Sensitivity.NORMAL
    )
    return CandidateInput(
        kind=kind,
        content=candidate_content,
        scopes=["personal"],
        tags=[f"provider:{provider.value}", "provider_memory", "archive_import"],
        source_reference=reference,
        source_service=provider.value,
        source_type="provider_memory",
        evidence=cleaned[:16_000],
        confidence=0.76,
        sensitivity=sensitivity,
        availability=Availability.CORE,
        explicit_user_statement=False,
    )


def _memory_strings(value: Any) -> Iterable[str]:
    if not isinstance(value, dict):
        return
    for raw_key, nested in value.items():
        key = str(raw_key).casefold().replace("-", "_").replace(" ", "_")
        if any(part in key for part in _MEMORY_KEY_PARTS):
            yield from _leaf_strings(nested)
        elif isinstance(nested, (dict, list)) and key not in {
            "mapping",
            "messages",
            "chat_messages",
            "turns",
            "history",
        }:
            yield from _memory_strings(nested)


def _dedicated_memory_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield from _memory_text_items(value)
        return
    if isinstance(value, list):
        for item in value:
            yield from _dedicated_memory_strings(item)
        return
    if not isinstance(value, dict):
        return
    selected = False
    for key, nested in value.items():
        normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
        if normalized in {
            "content",
            "text",
            "summary",
            "description",
            "instruction",
            "instructions",
            "memory",
            "memories",
            "profile",
            "value",
        }:
            selected = True
            yield from _leaf_strings(nested)
    if not selected:
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _dedicated_memory_strings(nested)


def _leaf_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield from _memory_text_items(value)
    elif isinstance(value, list):
        for item in value:
            yield from _leaf_strings(item)
    elif isinstance(value, dict):
        preferred = [
            value.get(key)
            for key in ("content", "text", "memory", "value", "name")
            if value.get(key) is not None
        ]
        if preferred:
            for item in preferred:
                yield from _leaf_strings(item)
        else:
            for item in value.values():
                yield from _leaf_strings(item)


def _memory_text_items(text: str) -> Iterable[str]:
    without_code = _FENCED_CODE.sub(" ", text)
    paragraphs: list[str] = []
    current: list[str] = []
    for line in without_code.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if stripped.startswith("#") and not _LABEL.match(stripped.lstrip("# ")):
            continue
        if _MARKDOWN_PREFIX.match(stripped):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(_clean_statement(stripped))
        else:
            current.append(stripped)
    if current:
        paragraphs.append(" ".join(current))
    for paragraph in paragraphs:
        cleaned = _clean_statement(paragraph)
        if cleaned:
            yield cleaned


def _looks_like_memory_filename(source_name: str) -> bool:
    stem = PurePosixPath(source_name).stem.casefold()
    return any(part in stem for part in ("memory", "memories", "profile", "instruction"))


def _looks_like_memory_document(value: Any, source_name: str) -> bool:
    if _looks_like_memory_filename(source_name):
        return True
    if not isinstance(value, dict):
        return False
    return any(
        any(part in str(key).casefold().replace("-", "_") for part in _MEMORY_KEY_PARTS)
        for key in value
    )


def _first_string(value: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _stable_id(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _safe_source_name(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {".", "..", "/"}]
    return "/".join(parts)[-1_000:] or "import"


def _deduplicate_strings(items: Iterable[str]) -> Iterable[str]:
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(item.casefold().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            yield item


def _deduplicate_candidates(items: Iterable[CandidateInput]) -> list[CandidateInput]:
    result: list[CandidateInput] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.kind.casefold(), " ".join(item.content.casefold().split()))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

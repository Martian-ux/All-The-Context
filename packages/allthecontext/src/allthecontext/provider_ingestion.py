"""Provider-neutral normalization and safe deterministic memory extraction.

Provider exports are untrusted input. This module never executes archive content and
never treats assistant messages as user facts. It normalizes the parts of official
account exports that are useful for provenance, then emits observations from
user-authored statements and dedicated memory/profile fields for Core policy.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, cast

from .memory_policy import archive_lineage_key, classify_sensitivity
from .models import MAX_SLOT_KEY_CHARS, Availability, CandidateInput, Sensitivity

PARSER_VERSION = "provider-archives-v2"

# Per-provider claim identities. Session idempotency still uses PARSER_VERSION;
# these values version each mandatory provider surface independently.
PARSER_IDENTITIES: dict[str, str] = {
    "chatgpt": "chatgpt-archives-v2",
    "claude": "claude-archives-v2",
    "grok": "grok-archives-v2",
    "generic": "generic-documents-v2",
}

_CONVERSATION_LIST_KEYS = (
    "conversations",
    "grok_conversations",
    "conversation_history",
    "chats",
    "threads",
    "items",
)
_NESTED_CONVERSATION_WRAPPER_KEYS = ("data", "export", "account_data")

CLOSED_COVERAGE_REASONS = (
    "recognized",
    "excluded",
    "skipped",
    "unavailable",
    "duplicate",
    "failed",
    "unparsed",
)


class ArchiveProvider(StrEnum):
    AUTO = "auto"
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GROK = "grok"
    GENERIC = "generic"


SUPPORTED_PROVIDER_VALUES = tuple(provider.value for provider in ArchiveProvider)


def parser_identity_for(provider: str | ArchiveProvider) -> str:
    key = provider.value if isinstance(provider, ArchiveProvider) else provider.strip().casefold()
    if key in {ArchiveProvider.AUTO.value}:
        return PARSER_VERSION
    return PARSER_IDENTITIES.get(key, PARSER_IDENTITIES["generic"])


def _closed_coverage_counts(
    stats: Mapping[str, int],
    candidates: Sequence[CandidateInput],
) -> dict[str, int]:
    """Map extractor stats into closed coverage reasons.

    Unknown/unparsed material stays visible and is never folded into success.
    """
    recognized = max(int(stats.get("recognized_items", 0)), len(candidates))
    excluded = int(stats.get("assistant_messages", 0)) + int(stats.get("other_messages", 0))
    skipped = int(stats.get("skipped_messages", 0))
    unavailable = int(stats.get("unsupported_entries", 0))
    duplicate = int(stats.get("duplicate_entries", 0))
    failed = int(stats.get("failed_items", 0))
    unparsed = int(stats.get("unparsed_messages", 0))
    skipped += int(stats.get("skipped_memory_items", 0))
    return {
        "recognized": recognized,
        "excluded": excluded,
        "skipped": skipped,
        "unavailable": unavailable,
        "duplicate": duplicate,
        "failed": failed,
        "unparsed": unparsed,
    }


_SECRET_HINT = re.compile(
    r"(?:api[_ -]?key|password|passphrase|private[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|secret)\s*[:=]",
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
_TASK_LOCAL_HINT = re.compile(
    r"\b(?:can you|could you|would you|will you|"
    r"please (?:write|explain|help|make|create|generate|summarize|fix|debug|refactor)|"
    r"in this (?:chat|conversation|message|prompt)|"
    r"as an? (?:ai|assistant|language model)|"
    r"(?:i|we) (?:prefer|want|need|would like|would love|expect) you to|"
    r"(?:i'd|i would|we'd|we would) (?:like|love|prefer) you to)\b",
    flags=re.IGNORECASE,
)
_ADVERSARIAL_INSTRUCTION_HINT = re.compile(
    r"(?:\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?"
    r"(?:of\s+)?(?:the\s+)?(?:previous|earlier|above|prior|system|developer)?\s*"
    r"instructions?\b|"
    r"\b(?:follow|obey|execute)\s+(?:these|the following|my|all)\s+"
    r"(?:instructions?|commands?)\b|"
    r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b|"
    r"\b(?:do not|don't|never)\s+follow\s+"
    r"(?:previous|earlier|above|these)\s+instructions?\b)",
    flags=re.IGNORECASE,
)
_DURABLE_PREFERENCE_HINT = re.compile(
    r"(?:^\s*(?:i|we)\s+(?:always|never|usually|generally|normally|typically)\b|"
    r"\b(?:in general|as a general rule|by default|from now on)\b|"
    r"\bmy preferences?\b|"
    r"\bwhen you (?:answer|respond)\b|"
    r"\bi want (?:answers?|responses?)\s+to\b|"
    r"^\s*(?:i|we)\s+(?:prefer|like|love|hate|dislike)(?!\s+you\s+to\b)\b|"
    r"^\s*(?:please\s+)?(?:never|always)\b|"
    r"^\s*(?:please\s+)?(?:do not|don't|avoid)\s+"
    r"(?:using|use|including|include|mentioning|mention)\b)",
    flags=re.IGNORECASE,
)
_CITATION_ARTIFACT_HINT = re.compile(
    r"(?:\[\^?[0-9]{1,4}(?:\s*[-,]\s*\^?[0-9]{1,4})*\]|"
    r"\[[A-Z][A-Za-z-]+(?:\s+et al\.)?,?\s*20\d{2}[a-z]?\]|"
    r"\(\s*[A-Z][A-Za-z-]+(?:\s+et al\.)?,\s*20\d{2}[a-z]?\s*\)|"
    r"\b(?:doi|arxiv):|\bet al\.|https?://)",
    flags=re.IGNORECASE,
)
_REFERENCE_PROSE_HINT = re.compile(
    r"^\s*(?:>\s*|(?:abstract|references?|bibliography)\s*:|"
    r"(?:according to|as (?:shown|described|defined|reported) (?:in|by)|"
    r"the (?:paper|study|authors?|research)|this (?:paper|study)|"
    r"we (?:show|prove|find|derive|observe)\b))",
    flags=re.IGNORECASE,
)
_MARKDOWN_TABLE_ROW = re.compile(r"^\s*\|[^|\r\n]*(?:\|[^|\r\n]*)+\|\s*$")
_PERSONAL_CONSTRAINT_HINT = re.compile(
    r"(?:\b(?:i|we)\s+(?:must|need(?:s)?(?:\s+to)?|cannot|can't|can not)\b|"
    r"\b(?:my|our)\b[^.!?\r\n]{0,160}\b"
    r"(?:must|need(?:s)?(?:\s+to)?|cannot|can't|can not)\b)",
    flags=re.IGNORECASE,
)
_DIRECT_PRODUCT_CONSTRAINT_HINT = re.compile(
    r"^\s*(?!(?i:the|a|an|there|this|that|these|those|it|one|any|each|every|some)\b)"
    r"[A-Z][A-Za-z0-9_.+#/-]*(?:\s+[A-Za-z][A-Za-z0-9_.+#/-]*){0,4}\s+"
    r"(?:must|needs?\s+to|cannot|can't|can\s+not)\b"
)
_GENERIC_TECHNICAL_SUBJECT = re.compile(
    r"^\s*(?:system|function|method|algorithm|equation|theorem|proof|result|answer|"
    r"model|application|service|endpoint|program|code|solution|experiment|study|"
    r"research|paper|assistant|tool|message|input|output|data|table|row|field|"
    r"value|variable|object|class|module|library|api)\b",
    flags=re.IGNORECASE,
)
_EPHEMERAL_STANCE = re.compile(
    r"\b(?:i think|i guess|i feel(?: like)?|i'm trying|i am trying|"
    r"i'm looking|i was wondering|just curious|for this (?:task|one))\b",
    flags=re.IGNORECASE,
)
_FALLBACK_MIN_CHARS = 48
_SPECIFIC_MIN_CHARS = 12


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
    stats: dict[str, Any]
    available: list[str]
    unavailable: list[str]
    limitations: list[str]
    complete: bool
    recognized: bool
    closed_coverage: dict[str, int] = field(default_factory=dict)
    parser_identity: str = PARSER_VERSION


@dataclass(frozen=True, slots=True)
class _ConversationCollection:
    values: list[Mapping[str, Any]]
    malformed_count: int = 0
    key: str | None = None


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
            "skipped_memory_items": 0,
            "skipped_messages": 0,
            "unparsed_messages": 0,
            "unsupported_entries": 0,
            "duplicate_entries": 0,
            "failed_items": 0,
            "recognized_items": 0,
        }

    def note_file(self, source_name: str) -> None:
        self._files_seen.add(_safe_source_name(source_name))

    def note_provider_context(self, provider: str | ArchiveProvider) -> None:
        """Remember provider evidence without publishing logical items."""
        normalized = normalize_provider(provider)
        if normalized not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}:
            self._providers.add(normalized)

    def observe_json_provider(self, source_name: str, value: Any) -> ArchiveProvider:
        """Observe one validated JSON value before its siblings are consumed."""
        detected = _detect_json_provider(value, _safe_source_name(source_name), self.provider_hint)
        self.note_provider_context(detected)
        return detected

    def provider_context_established(self) -> bool:
        return self.provider_hint not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC} or any(
            provider not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}
            for provider in self._providers
        )

    def note_provider_container(self, source_name: str) -> None:
        """Record a provider container as structural without counting it twice."""
        safe_name = _safe_source_name(source_name)
        self.note_file(safe_name)
        self._formats.add("provider_conversations")
        if self.provider_context_established():
            self._recognized_files.add(safe_name)

    def note_provider_terminal(self, source_name: str, reason: str) -> None:
        """Close one provider-container logical item without exposing its content."""
        if reason not in {"skipped", "unparsed"}:
            raise ValueError("unsupported provider terminal reason")
        self.note_provider_container(source_name)
        if reason == "skipped":
            self._stats["skipped_messages"] += 1
            return
        self._note_unparsed_conversation_entries(_safe_source_name(source_name), 1)

    def stats_snapshot(self) -> dict[str, int]:
        """Return bounded parser counters for one-member transaction probes."""
        return {
            **self._stats,
            "files": len(self._files_seen),
            "recognized_files": len(self._recognized_files),
        }

    def note_unsupported_entries(self, count: int) -> None:
        self._stats["unsupported_entries"] += max(count, 0)

    def note_duplicate_entries(self, count: int = 1) -> None:
        self._stats["duplicate_entries"] += max(count, 0)

    def note_failed_items(self, count: int = 1) -> None:
        self._stats["failed_items"] += max(count, 0)

    def add_warning(self, warning: str) -> None:
        if warning and warning not in self._warnings and len(self._warnings) < 512:
            self._warnings.append(_safe_diagnostic_text(warning)[:2_000])

    def consume_json(self, source_name: str, value: Any) -> bool:
        """Consume a JSON document, returning whether a provider schema was recognized."""
        safe_name = _safe_source_name(source_name)
        self.note_file(safe_name)
        self._stats["documents"] += 1
        provider = _detect_json_provider(value, safe_name, self.provider_hint)
        recognized = False

        collection = _conversation_collection(value)
        conversations = collection.values
        if _looks_like_conversation(value):
            conversations = [value]
            malformed_count = 0
        else:
            malformed_count = collection.malformed_count
        provider_list = (
            not _looks_like_conversation(value)
            and _is_provider_conversation_collection(collection, provider, safe_name)
            and bool(collection.values or collection.malformed_count)
        )
        if provider_list and malformed_count:
            self._note_unparsed_conversation_entries(safe_name, malformed_count)
        if conversations or provider_list:
            for conversation in conversations:
                conversation_provider = _detect_json_provider(conversation, safe_name, provider)
                messages, residual = _normalize_conversation(
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
                # Known classifiable residuals close into excluded/skipped/unavailable.
                # Only genuinely unknown/malformed material stays unparsed.
                self._stats["assistant_messages"] += residual["assistant_excluded"]
                self._stats["other_messages"] += residual["other_excluded"]
                self._stats["skipped_messages"] += residual["skipped"]
                self._stats["unsupported_entries"] += residual["unavailable"]
                self._stats["unparsed_messages"] += residual["unparsed"]
                accounted = (
                    len(messages)
                    + residual["assistant_excluded"]
                    + residual["other_excluded"]
                    + residual["skipped"]
                    + residual["unavailable"]
                    + residual["unparsed"]
                )
                if accounted < raw_message_count:
                    self._stats["unparsed_messages"] += raw_message_count - accounted
                if raw_message_count == 0 and not any(residual.values()):
                    # A recognized empty conversation is still one logical
                    # provider item; it is not a fabricated memory candidate.
                    self._stats["skipped_messages"] += 1
                self._consume_messages(messages)
            if provider_list and not conversations:
                # A provider-shaped list with no valid siblings is still a
                # recognized provider surface, but its coverage is incomplete.
                self._providers.add(provider)
                self._formats.add("provider_conversations")
                recognized = True

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
                self._stats["memory_items"] += 1
                candidate = _memory_candidate(
                    memory,
                    provider=memory_provider,
                    reference=f"{safe_name}#memory-{index + 1}",
                )
                if candidate is not None:
                    self._candidates.append(candidate)
                    self._stats["recognized_items"] += 1
                else:
                    self._stats["skipped_memory_items"] += 1

        if recognized:
            self._recognized_files.add(safe_name)
        return recognized

    def consume_json_list(self, source_name: str, value: list[Any]) -> bool:
        """Consume a root provider conversation list without dropping residuals."""
        safe_name = _safe_source_name(source_name)
        if not _is_provider_conversation_sequence(value, self.provider_hint, safe_name):
            return False
        self.note_file(safe_name)
        provider = _detect_json_provider(value, safe_name, self.provider_hint)
        valid = [
            item for item in value if isinstance(item, dict) and _looks_like_conversation(item)
        ]
        malformed_count = len(value) - len(valid)
        if malformed_count:
            self._note_unparsed_conversation_entries(safe_name, malformed_count)
        for conversation in valid:
            self.consume_json(safe_name, conversation)
        if not valid:
            self._stats["documents"] += 1
            self._providers.add(provider)
            self._formats.add("provider_conversations")
            self._recognized_files.add(safe_name)
        return True

    def _note_unparsed_conversation_entries(self, source_name: str, count: int) -> None:
        self._stats["unparsed_messages"] += count
        self.add_warning(
            f"{source_name}: {count} malformed or unrecognized provider conversation "
            "list entries were left unparsed"
        )

    def note_unrecognized_json_value(self, source_name: str) -> bool:
        """Account for a streamed residual once a provider shape is established."""
        safe_name = _safe_source_name(source_name)
        meaningful = any(
            item not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC} for item in self._providers
        )
        provider_context = (
            self.provider_hint not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC}
            or _provider_from_filename(safe_name) != ArchiveProvider.AUTO
            or meaningful
        )
        if not provider_context:
            return False
        self._note_unparsed_conversation_entries(safe_name, 1)
        return True

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
                self._stats["memory_items"] += 1
                candidate = _memory_candidate(
                    item,
                    provider=provider,
                    reference=f"{safe_name}#memory-{index + 1}",
                )
                if candidate is not None:
                    self._candidates.append(candidate)
                    self._stats["recognized_items"] += 1
                else:
                    self._stats["skipped_memory_items"] += 1
            return True
        return False

    def finish(self) -> ProviderExtraction:
        candidates = _deduplicate_candidates(self._candidates)
        provider = self._result_provider()
        formats = sorted(self._formats)
        export_format = "+".join(formats) if formats else "generic_document"
        closed_coverage = _closed_coverage_counts(self._stats, candidates)
        identity = parser_identity_for(provider)
        stats: dict[str, Any] = {
            **self._stats,
            "files": len(self._files_seen),
            "recognized_files": len(self._recognized_files),
            "candidates": len(candidates),
            "provider": provider.value,
            "parser_version": PARSER_VERSION,
            "parser_identity": identity,
            "closed_coverage": closed_coverage,
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
            f"Parser identity for this claim surface is {identity}.",
        ]
        warnings = list(self._warnings)
        if closed_coverage["unparsed"] or closed_coverage["failed"]:
            warnings.append(
                "unknown or unparsed material remains visible in closed coverage and is not "
                "counted as parser success"
            )
        if closed_coverage["excluded"]:
            warnings.append(
                f"{closed_coverage['excluded']} assistant/system/tool/attachment items were "
                "excluded from context publication"
            )
        if self._stats["skipped_memory_items"]:
            warnings.append(
                f"{self._stats['skipped_memory_items']} provider memory/profile items were "
                "skipped by content policy"
            )
        if closed_coverage["duplicate"]:
            warnings.append(
                f"{closed_coverage['duplicate']} duplicate source entries were skipped with "
                "their original entry retained"
            )
        complete = not any(
            marker in warning.casefold()
            for warning in warnings
            for marker in ("invalid json", "could not parse", "exceeds", "truncated")
        ) and all(closed_coverage[key] == 0 for key in ("unavailable", "duplicate", "failed"))
        # Unparsed material keeps coverage incomplete so it cannot report pure success.
        if closed_coverage["unparsed"] > 0:
            complete = False
        return ProviderExtraction(
            provider=provider,
            export_format=export_format,
            candidates=candidates,
            warnings=warnings,
            stats=stats,
            available=available,
            unavailable=unavailable,
            limitations=limitations,
            complete=complete,
            recognized=recognized,
            closed_coverage=closed_coverage,
            parser_identity=identity,
        )

    def _consume_messages(self, messages: Sequence[NormalizedMessage]) -> None:
        conversation_candidates: dict[tuple[ArchiveProvider, str], list[CandidateInput]] = {}
        for message in messages:
            self._stats["messages"] += 1
            if message.role == "user":
                self._stats["user_messages"] += 1
                extracted = _durable_candidates(message)
                if extracted:
                    self._stats["recognized_items"] += len(extracted)
                    key = (message.provider, message.conversation_id)
                    conversation_candidates.setdefault(key, []).extend(extracted)
                else:
                    self._stats["skipped_messages"] += 1
            elif message.role == "assistant":
                self._stats["assistant_messages"] += 1
            else:
                self._stats["other_messages"] += 1
        for (provider, conversation_id), candidates in conversation_candidates.items():
            self._candidates.extend(
                _scope_conversation_candidates(
                    candidates,
                    provider=provider,
                    conversation_id=conversation_id,
                )
            )

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
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and _looks_like_conversation(item):
                detected = _detect_json_provider(item, source_name, hint)
                if detected != ArchiveProvider.AUTO:
                    return detected
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
        collection = _conversation_collection(value)
        if collection.key == "grok_conversations":
            return ArchiveProvider.GROK
        if collection.values:
            first = collection.values[0]
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


def _conversation_collection(value: Any) -> _ConversationCollection:
    if not isinstance(value, dict):
        return _ConversationCollection([])
    for key in _CONVERSATION_LIST_KEYS:
        nested = value.get(key)
        if isinstance(nested, list):
            values = [
                cast(Mapping[str, Any], item)
                for item in nested
                if isinstance(item, dict) and _looks_like_conversation(item)
            ]
            return _ConversationCollection(
                values=values,
                malformed_count=len(nested) - len(values),
                key=key,
            )
    for key in _NESTED_CONVERSATION_WRAPPER_KEYS:
        nested = value.get(key)
        if isinstance(nested, dict):
            collection = _conversation_collection(nested)
            if collection.key is not None:
                return collection
    return _ConversationCollection([])


def _conversation_values(value: Any) -> list[Mapping[str, Any]]:
    """Return valid conversation mappings without dropping residual accounting."""
    return _conversation_collection(value).values


def _is_provider_conversation_collection(
    collection: _ConversationCollection,
    provider: ArchiveProvider,
    source_name: str,
) -> bool:
    if collection.key is None:
        return False
    if collection.values:
        return True
    # `items` is also a generic-document convention. Treat it as a provider
    # list only when the provider hint or safe filename establishes that shape.
    if collection.key == "items":
        return provider not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC} or (
            _provider_from_filename(source_name) != ArchiveProvider.AUTO
        )
    return True


def _is_provider_conversation_sequence(
    value: Sequence[Any],
    provider_hint: ArchiveProvider,
    source_name: str,
) -> bool:
    if not value:
        return False
    if any(isinstance(item, dict) and _looks_like_conversation(item) for item in value):
        return True
    return provider_hint not in {ArchiveProvider.AUTO, ArchiveProvider.GENERIC} or (
        _provider_from_filename(source_name) != ArchiveProvider.AUTO
    )


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


def is_empty_provider_container(value: Any) -> bool:
    """Return whether a value is an empty provider conversation container/wrapper."""
    if isinstance(value, dict) and not value:
        return True
    if isinstance(value, dict):
        for key in _NESTED_CONVERSATION_WRAPPER_KEYS:
            nested = value.get(key)
            if nested == [] or (isinstance(nested, dict) and is_empty_provider_container(nested)):
                return True
    collection = _conversation_collection(value)
    return (
        collection.key is not None
        and not collection.values
        and collection.malformed_count == 0
        and not _looks_like_conversation(value)
    )


def _format_for_conversation(value: Mapping[str, Any], provider: ArchiveProvider) -> str:
    if isinstance(value.get("mapping"), dict):
        return "chatgpt_conversation_graph"
    if isinstance(value.get("chat_messages"), list):
        return "claude_conversations"
    if provider == ArchiveProvider.GROK:
        return "grok_conversations"
    return "provider_conversations"


def _empty_message_residual() -> dict[str, int]:
    return {
        "assistant_excluded": 0,
        "other_excluded": 0,
        "skipped": 0,
        "unavailable": 0,
        "unparsed": 0,
    }


def _normalize_conversation(
    value: Mapping[str, Any],
    provider: ArchiveProvider,
    source_name: str,
    ordinal: int,
) -> tuple[list[NormalizedMessage], dict[str, int]]:
    title = _first_string(value, ("title", "name", "subject"))
    raw_id = _first_string(value, ("id", "uuid", "conversation_id", "chat_id"))
    conversation_id = raw_id or _stable_id(f"{source_name}:{title or ''}:{ordinal}")
    residual = _empty_message_residual()
    if isinstance(value.get("mapping"), dict):
        raw_messages: list[tuple[int, Mapping[str, Any]]] = []
        for index, (node_id, node) in enumerate(value["mapping"].items()):
            if not isinstance(node, dict):
                residual["unparsed"] += 1
                continue
            message_value = node.get("message")
            if message_value is None:
                continue
            if not isinstance(message_value, dict):
                residual["unparsed"] += 1
                continue
            node_message = dict(message_value)
            node_message.setdefault("id", str(node_id))
            raw_messages.append((index, node_message))
        raw_messages.sort(key=lambda pair: _message_sort_key(pair[1], pair[0]))
        result: list[NormalizedMessage] = []
        for index, (_, mapped_message) in enumerate(raw_messages):
            normalized, disposition = _normalize_or_classify_message(
                mapped_message, provider, source_name, conversation_id, title, index
            )
            if normalized is not None:
                result.append(normalized)
            elif disposition is not None:
                residual[disposition] += 1
        return result, residual

    raw_values: list[Any] = []
    for key in ("chat_messages", "messages", "turns", "responses", "history"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            raw_values = candidate
            break
    if not raw_values and _looks_like_turn_pair(value):
        raw_values = [value]
    result = []
    for index, message in enumerate(raw_values):
        if not isinstance(message, dict):
            residual["unparsed"] += 1
            continue
        normalized, disposition = _normalize_or_classify_message(
            message, provider, source_name, conversation_id, title, index
        )
        if normalized is not None:
            result.append(normalized)
            continue
        pair = _normalize_turn_pair(
            message,
            provider=provider,
            source_name=source_name,
            conversation_id=conversation_id,
            title=title,
            ordinal=index,
        )
        if pair:
            result.extend(pair)
            continue
        if disposition is not None:
            residual[disposition] += 1
        else:
            residual["unparsed"] += 1
    return result, residual


def _conversation_message_count(value: Mapping[str, Any]) -> int:
    mapping = value.get("mapping")
    if isinstance(mapping, dict):
        return sum(
            1
            for node in mapping.values()
            if not isinstance(node, dict) or node.get("message") is not None
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


_KNOWN_NON_TEXT_CONTENT_TYPES = frozenset(
    {
        "image_asset_pointer",
        "audio_asset_pointer",
        "video_asset_pointer",
        "real_time_user_audio_video_asset_pointer",
        "code",
        "execution_output",
        "system_error",
        "tether_browsing_display",
        "tether_quote",
        "tether_image",
    }
)
_EXCLUDED_NON_USER_ROLES = frozenset({"system", "tool", "developer", "function"})


def _message_role_raw(value: Mapping[str, Any]) -> str:
    role_value: Any = (
        value.get("role")
        or value.get("sender")
        or value.get("author")
        or value.get("sender_type")
        or value.get("message_type")
    )
    if isinstance(role_value, dict):
        role_value = role_value.get("role") or role_value.get("name")
    return str(role_value or "")


def _normalize_or_classify_message(
    value: Mapping[str, Any],
    provider: ArchiveProvider,
    source_name: str,
    conversation_id: str,
    title: str | None,
    ordinal: int,
) -> tuple[NormalizedMessage | None, str | None]:
    """Normalize a message or classify residual closed-coverage disposition.

    Known provider roles/structures close into excluded, skipped, or unavailable.
    Genuinely unknown or malformed structures remain unparsed so coverage stays
    fail-closed.
    """
    role = _normalize_role(_message_role_raw(value))
    text = _message_text(value)
    if role and text.strip():
        message_id = _first_string(value, ("id", "uuid", "message_id")) or str(ordinal + 1)
        created = value.get("created_at") or value.get("create_time") or value.get("timestamp")
        created_at = str(created) if isinstance(created, (str, int, float)) else None
        return (
            NormalizedMessage(
                provider=provider,
                conversation_id=conversation_id[:200],
                conversation_title=title[:500] if title else None,
                message_id=message_id[:200],
                role=role,
                text=text,
                source_name=source_name,
                created_at=created_at,
            ),
            None,
        )
    if role == "assistant":
        return None, "assistant_excluded"
    if role in _EXCLUDED_NON_USER_ROLES:
        return None, "other_excluded"
    if role == "user":
        if _looks_like_attachment_or_nontext_only(value):
            return None, "unavailable"
        return None, "skipped"
    # Missing and unknown roles remain unparsed even for attachment-shaped
    # content. Without a trusted provider role the residual is not classifiable.
    return None, "unparsed"


def _normalize_role(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"user", "human", "you", "customer", "client"}:
        return "user"
    if normalized in {"assistant", "ai", "bot", "chatgpt", "claude", "grok"}:
        return "assistant"
    if normalized in {"system", "tool", "developer", "function"}:
        return normalized
    return ""


def _looks_like_attachment_or_nontext_only(value: Mapping[str, Any]) -> bool:
    """True when a message is a known non-text/attachment shell without usable text."""
    if _message_text(value).strip():
        return False
    content = value.get("content")
    if isinstance(content, dict):
        content_type = str(content.get("content_type") or "").casefold()
        if content_type in _KNOWN_NON_TEXT_CONTENT_TYPES:
            return True
        if content_type in {"multimodal_text", "text", ""}:
            parts = content.get("parts")
            if isinstance(parts, list) and parts and all(isinstance(part, dict) for part in parts):
                return True
    if any(isinstance(value.get(key), list) and value.get(key) for key in ("attachments", "files")):
        return True
    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        attachments = metadata.get("attachments")
        if isinstance(attachments, list) and attachments:
            return True
    return False


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
    content_type = str(value.get("content_type") or "").casefold()
    if content_type in _KNOWN_NON_TEXT_CONTENT_TYPES:
        # Asset pointers and execution shells are not user-authored text.
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
    result: list[CandidateInput] = []
    paragraphs = re.split(r"\n\s*\n", text) or [text]
    for paragraph in paragraphs:
        if _looks_like_reference_material(paragraph):
            continue
        sentences = [
            _clean_statement(part)
            for part in _SENTENCE_BREAK.split(paragraph)
            if _clean_statement(part)
        ]
        if not sentences:
            continue
        specific = [
            candidate
            for segment in sentences
            if (candidate := _candidate_from_statement(segment, message, require_specific=True))
            is not None
        ]
        if specific:
            result.extend(specific)
            continue
        fallback = _candidate_from_statement(" ".join(sentences), message, require_specific=False)
        if fallback is not None:
            result.append(fallback)
    return _deduplicate_candidates(result)


def _candidate_from_statement(
    segment: str,
    message: NormalizedMessage,
    *,
    require_specific: bool,
) -> CandidateInput | None:
    cleaned = _clean_statement(segment)
    if (
        not cleaned
        or len(cleaned) > 4_000
        or _SECRET_HINT.search(cleaned)
        or _looks_like_reference_material(cleaned)
    ):
        return None
    if cleaned.endswith("?") or _is_inert_instruction(cleaned) or _EPHEMERAL_STANCE.search(cleaned):
        return None
    classified = _classify_statement(cleaned)
    if classified is None:
        return None
    kind, confidence, entity_key, attribute_key = classified
    if require_specific and confidence < 0.5:
        return None
    if confidence < 0.5:
        if len(cleaned) < _FALLBACK_MIN_CHARS or _TRANSIENT_HINT.search(cleaned):
            return None
    elif len(cleaned) < _SPECIFIC_MIN_CHARS and _LABEL.match(cleaned) is None:
        return None
    label = _LABEL.match(cleaned)
    candidate_content = label.group(2).strip() if label else cleaned
    if not candidate_content:
        return None
    if entity_key is None and attribute_key is None:
        slot = archive_lineage_key(kind, candidate_content)
        if slot:
            entity_key = "archive_slot"
            attribute_key = slot[:MAX_SLOT_KEY_CHARS]
    reference = (
        f"{message.source_name}#conversation={message.conversation_id}&message={message.message_id}"
    )
    return CandidateInput(
        kind=kind,
        content=candidate_content,
        entity_key=entity_key,
        attribute_key=attribute_key,
        scopes=["personal"],
        tags=[f"provider:{message.provider.value}", "archive_import"],
        source_reference=reference,
        source_service=message.provider.value,
        source_type="provider_archive",
        evidence=cleaned[:16_000],
        confidence=confidence,
        sensitivity=classify_sensitivity(candidate_content),
        availability=Availability.CORE,
        observed_at=_provider_observed_at(message.created_at),
        explicit_user_statement=True,
    )


def _scope_conversation_candidates(
    candidates: Sequence[CandidateInput],
    *,
    provider: ArchiveProvider,
    conversation_id: str,
) -> list[CandidateInput]:
    """Attach an opaque project scope only for one safe project anchor."""
    anchors = [
        candidate
        for candidate in candidates
        if (
            candidate.explicit_user_statement
            and candidate.kind.casefold() in {"project", "project_identity"}
            and candidate.sensitivity != Sensitivity.HIGHLY_SENSITIVE
        )
    ]
    if len(anchors) != 1:
        return list(candidates)
    scope = f"project:archive-{_stable_id(f'{provider.value}:{conversation_id}')}"
    result: list[CandidateInput] = []
    for candidate in candidates:
        scopes = list(candidate.scopes)
        if scope not in scopes:
            scopes.append(scope)
        result.append(candidate.model_copy(update={"scopes": scopes}))
    return result


def _provider_observed_at(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        timestamp = float(value)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _clean_statement(value: str) -> str:
    cleaned = _MARKDOWN_PREFIX.sub("", value).strip().strip("\u2022")
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def _looks_like_reference_material(value: str) -> bool:
    lines = value.splitlines()
    return (
        any(_MARKDOWN_TABLE_ROW.fullmatch(line) for line in lines)
        or bool(_CITATION_ARTIFACT_HINT.search(value))
        or any(_REFERENCE_PROSE_HINT.search(line) for line in lines)
    )


def _classify_statement(
    statement: str,
) -> tuple[str, float, str | None, str | None] | None:
    lowered = statement.casefold()
    if _is_inert_instruction(statement):
        return None
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
        r"i (?:always|usually|generally|normally|typically)\s+"
        r"(?:want|prefer|like|love|hate|dislike)|"
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
    if _PERSONAL_CONSTRAINT_HINT.search(statement) or (
        _DIRECT_PRODUCT_CONSTRAINT_HINT.search(statement)
        and not _GENERIC_TECHNICAL_SUBJECT.search(statement)
    ):
        return ("constraint", 0.84, None, None)
    if _TRANSIENT_HINT.search(lowered):
        return None
    if re.search(
        r"\b(?:i am|i'm|i have|i've|i own|i speak|my [a-z][a-z -]{1,30} (?:is|are)|"
        r"we are|we're|we have|we've|our [a-z][a-z -]{1,30} (?:is|are))\b",
        lowered,
    ):
        # Broad first-person prose is retained as a noncurrent observation.
        # It is not durable current memory on its own.
        return ("personal_context", 0.4, None, None)
    return None


def _memory_candidate(
    content: str,
    *,
    provider: ArchiveProvider,
    reference: str,
) -> CandidateInput | None:
    cleaned = _clean_statement(content)
    if (
        not cleaned
        or len(cleaned) > 4_000
        or _SECRET_HINT.search(cleaned)
        or _is_inert_instruction(cleaned)
        or _looks_like_reference_material(cleaned)
        or classify_sensitivity(cleaned) == Sensitivity.HIGHLY_SENSITIVE
    ):
        return None
    classified = _classify_statement(cleaned)
    kind = classified[0] if classified is not None else "provider_memory"
    label = _LABEL.match(cleaned)
    candidate_content = label.group(2).strip() if label else cleaned
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
        sensitivity=classify_sensitivity(candidate_content),
        availability=Availability.CORE,
        explicit_user_statement=False,
    )


def _is_inert_instruction(statement: str) -> bool:
    """Reject task-local or adversarial imported prose before kind extraction.

    Imported text is data, not an instruction channel. Durable preference
    markers can authorize ordinary response-style preferences, but explicit
    prompt-injection language always wins and remains inert.
    """
    if _ADVERSARIAL_INSTRUCTION_HINT.search(statement):
        return True
    return bool(_TASK_LOCAL_HINT.search(statement)) and not bool(
        _DURABLE_PREFERENCE_HINT.search(statement)
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
    return _safe_diagnostic_text("/".join(parts))[-1_000:] or "import"


def _safe_diagnostic_text(value: str) -> str:
    """Keep untrusted source names and warnings bounded and single-line safe."""
    escaped: list[str] = []
    for char in value:
        if char.isprintable() and unicodedata.category(char) != "Cc":
            escaped.append(char)
            continue
        codepoint = ord(char)
        escaped.append(f"\\x{codepoint:02x}" if codepoint <= 0xFF else f"\\u{codepoint:04x}")
    return "".join(escaped)


def _deduplicate_strings(items: Iterable[str]) -> Iterable[str]:
    seen: set[str] = set()
    for item in items:
        normalized = " ".join(item.casefold().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            yield item


def _deduplicate_candidates(items: Iterable[CandidateInput]) -> list[CandidateInput]:
    result: list[CandidateInput] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in items:
        key = (
            item.kind.casefold(),
            " ".join(item.content.casefold().split()),
            tuple(item.scopes),
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

"""Frozen fictional provider shapes and closed coverage accounting for B-105.

These synthetic fixtures exercise ChatGPT, Claude, and Grok parser identities.
They contain only fictional content. Real personal exports are never used here;
current-real-provider receipts remain human-controlled acceptance work (B-204).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .provider_ingestion import PARSER_VERSION, ArchiveProvider

# Per-provider parser claim identities. Aggregate PARSER_VERSION remains the
# session/idempotency material; these identities version each claim surface.
PARSER_IDENTITIES: dict[str, str] = {
    ArchiveProvider.CHATGPT.value: "chatgpt-archives-v2",
    ArchiveProvider.CLAUDE.value: "claude-archives-v2",
    ArchiveProvider.GROK.value: "grok-archives-v2",
    ArchiveProvider.GENERIC.value: "generic-documents-v2",
}

CLOSED_COVERAGE_REASONS = (
    "recognized",
    "excluded",
    "skipped",
    "unavailable",
    "duplicate",
    "failed",
    "unparsed",
)


@dataclass(frozen=True, slots=True)
class FrozenProviderShape:
    provider: str
    parser_identity: str
    export_format: str
    shape_id: str
    description: str
    document: Any
    filename: str
    expected_counts: Mapping[str, int]
    expected_min_candidates: int
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "parser_identity": self.parser_identity,
            "export_format": self.export_format,
            "shape_id": self.shape_id,
            "description": self.description,
            "filename": self.filename,
            "expected_counts": dict(self.expected_counts),
            "expected_min_candidates": self.expected_min_candidates,
            "notes": list(self.notes),
            "aggregate_parser_version": PARSER_VERSION,
        }

    def payload_bytes(self) -> bytes:
        if isinstance(self.document, (bytes, bytearray)):
            return bytes(self.document)
        if isinstance(self.document, str):
            return self.document.encode("utf-8")
        if self.document is None:
            raise ValueError(f"shape {self.shape_id} has no document payload")
        return json.dumps(self.document, ensure_ascii=False, indent=2).encode("utf-8")


def parser_identity_for(provider: str) -> str:
    normalized = provider.strip().casefold()
    if normalized in PARSER_IDENTITIES:
        return PARSER_IDENTITIES[normalized]
    return PARSER_IDENTITIES[ArchiveProvider.GENERIC.value]


def empty_closed_coverage() -> dict[str, int]:
    return {reason: 0 for reason in CLOSED_COVERAGE_REASONS}


def reconcile_closed_coverage(counts: Mapping[str, int]) -> dict[str, Any]:
    """Return a closed coverage summary; unknown material is never 'success'."""
    closed = empty_closed_coverage()
    for key, value in counts.items():
        if key not in closed:
            raise ValueError(f"unknown coverage reason: {key}")
        if value < 0:
            raise ValueError(f"coverage count for {key} must be non-negative")
        closed[key] = int(value)
    total = sum(closed.values())
    unresolved = closed["unparsed"] + closed["failed"]
    return {
        "counts": closed,
        "total": total,
        "unresolved": unresolved,
        "truthful_success": unresolved == 0 and closed["recognized"] > 0,
        "unknown_material_visible": unresolved > 0 or closed["unavailable"] > 0,
    }


def frozen_provider_shapes() -> tuple[FrozenProviderShape, ...]:
    """Sanitized fictional canary shapes claimed by the current parser freeze."""
    return (
        FrozenProviderShape(
            provider=ArchiveProvider.CHATGPT.value,
            parser_identity=PARSER_IDENTITIES[ArchiveProvider.CHATGPT.value],
            export_format="chatgpt_conversation_graph",
            shape_id="chatgpt-conversation-graph-v1",
            description="ChatGPT mapping graph with one durable user statement and assistant noise",
            filename="conversations.json",
            document=[
                {
                    "id": "chatgpt-fictional-1",
                    "title": "Fictional portable notes",
                    "mapping": {
                        "user-node": {
                            "message": {
                                "id": "chatgpt-user-1",
                                "author": {"role": "user"},
                                "create_time": 1_700_000_000,
                                "content": {
                                    "parts": ["Preference: Keep fictional demo answers concise."]
                                },
                            }
                        },
                        "assistant-node": {
                            "message": {
                                "id": "chatgpt-assistant-1",
                                "author": {"role": "assistant"},
                                "create_time": 1_700_000_001,
                                "content": {
                                    "parts": [
                                        "Fact: fabricated assistant claim must stay excluded."
                                    ]
                                },
                            }
                        },
                        "tool-node": {
                            "message": {
                                "id": "chatgpt-tool-1",
                                "author": {"role": "tool"},
                                "create_time": 1_700_000_002,
                                "content": {"parts": ["tool payload stays excluded"]},
                            }
                        },
                    },
                }
            ],
            expected_counts={
                "recognized": 1,
                "excluded": 2,
                "skipped": 0,
                "unavailable": 0,
                "duplicate": 0,
                "failed": 0,
                "unparsed": 0,
            },
            expected_min_candidates=1,
            notes=(
                "Assistant and tool roles are excluded, never counted as success.",
                "Raw preservation retains the full graph for later parser versions.",
            ),
        ),
        FrozenProviderShape(
            provider=ArchiveProvider.CLAUDE.value,
            parser_identity=PARSER_IDENTITIES[ArchiveProvider.CLAUDE.value],
            export_format="claude_conversations",
            shape_id="claude-chat-messages-v1",
            description="Claude chat_messages export with human durable goal and assistant noise",
            filename="conversations.json",
            document=[
                {
                    "uuid": "claude-fictional-1",
                    "name": "Fictional planning",
                    "chat_messages": [
                        {
                            "uuid": "claude-human-1",
                            "sender": "human",
                            "text": "Goal: Finish the fictional local context demo.",
                        },
                        {
                            "uuid": "claude-assistant-1",
                            "sender": "assistant",
                            "text": "Fact: fabricated Claude memory must stay excluded.",
                        },
                        {
                            "uuid": "claude-human-skip",
                            "sender": "human",
                            "text": "ok thanks",
                        },
                    ],
                }
            ],
            expected_counts={
                "recognized": 1,
                "excluded": 1,
                "skipped": 1,
                "unavailable": 0,
                "duplicate": 0,
                "failed": 0,
                "unparsed": 0,
            },
            expected_min_candidates=1,
            notes=(
                "Short non-durable human turns are skipped with a closed reason.",
                "Assistant content remains inert and excluded.",
            ),
        ),
        FrozenProviderShape(
            provider=ArchiveProvider.GROK.value,
            parser_identity=PARSER_IDENTITIES[ArchiveProvider.GROK.value],
            export_format="grok_conversations",
            shape_id="grok-conversation-json-v1",
            description="Grok conversation envelope with user durable fact and assistant noise",
            filename="grok-export.json",
            document={
                "provider": "grok",
                "grok_conversations": [
                    {
                        "id": "grok-fictional-1",
                        "title": "Fictional automation",
                        "messages": [
                            {
                                "id": "grok-user-1",
                                "role": "user",
                                "text": "Fact: The fictional lab uses PowerShell on Windows.",
                            },
                            {
                                "id": "grok-assistant-1",
                                "role": "assistant",
                                "text": "Fact: fabricated Grok memory must stay excluded.",
                            },
                        ],
                    }
                ],
            },
            expected_counts={
                "recognized": 1,
                "excluded": 1,
                "skipped": 0,
                "unavailable": 0,
                "duplicate": 0,
                "failed": 0,
                "unparsed": 0,
            },
            expected_min_candidates=1,
            notes=(
                "Grok schema is adaptive; unrecognized material must stay visible.",
                "Assistant roles cannot publish current context.",
            ),
        ),
        FrozenProviderShape(
            provider=ArchiveProvider.GROK.value,
            parser_identity=PARSER_IDENTITIES[ArchiveProvider.GROK.value],
            export_format="markdown_transcript",
            shape_id="grok-markdown-transcript-v1",
            description="Grok Build-style Markdown transcript with one durable user statement",
            filename="session.md",
            document=(
                "## User\n"
                "Constraint: Keep the fictional demo offline-only.\n"
                "## Grok\n"
                "Fact: fabricated Markdown assistant claim must stay excluded.\n"
            ),
            expected_counts={
                "recognized": 1,
                "excluded": 1,
                "skipped": 0,
                "unavailable": 0,
                "duplicate": 0,
                "failed": 0,
                "unparsed": 0,
            },
            expected_min_candidates=1,
            notes=("Markdown role headings are normalized without executing content.",),
        ),
    )


def grok_markdown_canary_text() -> str:
    return (
        "## User\n"
        "Constraint: Keep the fictional demo offline-only.\n"
        "## Grok\n"
        "Fact: fabricated Markdown assistant claim must stay excluded.\n"
    )


def frozen_shape_by_id(shape_id: str) -> FrozenProviderShape:
    for shape in frozen_provider_shapes():
        if shape.shape_id == shape_id:
            return shape
    raise KeyError(shape_id)


def provider_claim_manifest() -> dict[str, Any]:
    shapes = frozen_provider_shapes()
    return {
        "aggregate_parser_version": PARSER_VERSION,
        "parser_identities": dict(PARSER_IDENTITIES),
        "closed_coverage_reasons": list(CLOSED_COVERAGE_REASONS),
        "shapes": [shape.as_dict() for shape in shapes],
        "notes": [
            "Imported text is untrusted data and is never executed as instructions.",
            "Unknown or unparsed material is a visible coverage warning, not success.",
            "Real nonempty provider-export receipts are human-controlled and separate.",
        ],
    }

"""Bounded, content-only evidence projection for retrieval decisions."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import MAX_CONTEXT_CHARS

_CONTENT_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)

# Deliberately small and inspectable. These equivalences are never learned
# from the vault and cannot create authority or a canonical record.
CURATED_CONTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "eviction": ("cache",),
    "latest": ("current", "recent"),
    "now": ("current", "recent"),
    "where": ("location", "city"),
    "procedure": ("workflow", "runbook"),
    "rollback": ("restore",),
}


@dataclass(frozen=True, slots=True)
class ContentEvidence:
    """Exact content matches for query anchors and their curated aliases."""

    direct_matches: frozenset[str]
    alias_matches: frozenset[str]

    @property
    def matched_anchors(self) -> frozenset[str]:
        """Return each matched anchor once, regardless of evidence path."""

        return self.direct_matches | self.alias_matches


def _normalized_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", value).casefold())
    return "".join(character for character in normalized if unicodedata.combining(character) == 0)


def content_tokens(value: str) -> frozenset[str]:
    """Tokenize bounded record content using the retrieval evidence boundary."""

    bounded = _normalized_token(value[:MAX_CONTEXT_CHARS])
    return frozenset(_CONTENT_TOKEN_RE.findall(bounded))


def project_content_evidence(
    content: str,
    anchors: Sequence[str] | set[str] | frozenset[str],
    aliases: Mapping[str, Sequence[str]],
    *,
    allow_prefix: bool = False,
) -> ContentEvidence:
    """Project exact content evidence without consulting structural fields.

    Alias evidence is attributed to the original anchor that owns the mapped
    alias. It therefore contributes at most one matched anchor and never turns
    an alias-only record into full query coverage.
    """

    record_tokens = content_tokens(content)
    normalized_anchors = tuple(dict.fromkeys(_normalized_token(anchor) for anchor in anchors))
    direct_matches = frozenset(
        anchor
        for anchor in normalized_anchors
        if anchor in record_tokens
        or (
            allow_prefix
            and len(anchor) >= 4
            and any(token.startswith(anchor) for token in record_tokens)
        )
    )
    alias_matches = frozenset(
        anchor
        for anchor in normalized_anchors
        if anchor not in direct_matches
        and bool(
            record_tokens.intersection(
                _normalized_token(alias) for alias in aliases.get(anchor, ())
            )
        )
    )
    return ContentEvidence(direct_matches, alias_matches)

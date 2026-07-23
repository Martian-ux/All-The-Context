"""Deterministic extension contracts for retrieval experiments.

This module is intentionally not wired into :mod:`allthecontext.retrieval`.
It gives later retrieval phases typed boundaries without changing the public
API or the authoritative Core's current ranking behavior.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, final

from .retrieval import RankingExplanation, V2LexicalRanker


class DiagnosticReasonCode(StrEnum):
    """Closed, content-free reasons safe for aggregate diagnostics."""

    TEMPORAL_CURRENT = "temporal_current"
    TEMPORAL_AS_OF = "temporal_as_of"
    TASK_ADMISSIBLE = "task_admissible"
    TASK_INADMISSIBLE = "task_inadmissible"
    SET_SELECTED = "set_selected"
    SET_DUPLICATE_SUPPRESSED = "set_duplicate_suppressed"
    SET_CONFLICT_RESOLVED = "set_conflict_resolved"
    SHADOW_COMPLETED = "shadow_completed"
    SHADOW_SKIPPED = "shadow_skipped"
    POLICY_FILTERED = "policy_filtered"
    NOT_EXERCISED = "not_exercised"


class DiagnosticMetricCode(StrEnum):
    """Closed names for sanitized numeric or boolean diagnostic values."""

    CANDIDATE_COUNT = "candidate_count"
    SELECTED_COUNT = "selected_count"
    FILTERED_COUNT = "filtered_count"
    DUPLICATE_COUNT = "duplicate_count"
    CONFLICT_COUNT = "conflict_count"
    ELAPSED_MILLISECONDS = "elapsed_milliseconds"
    SCORE = "score"
    DETERMINISTIC = "deterministic"


DiagnosticScalar = bool | int | float


@dataclass(frozen=True, slots=True)
class DiagnosticValue:
    """One sanitized aggregate/value pair with no free-form metadata channel."""

    metric: DiagnosticMetricCode
    value: DiagnosticScalar

    def __post_init__(self) -> None:
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("diagnostic numeric values must be finite")


@dataclass(frozen=True, slots=True)
class SafeRetrievalDiagnostic:
    """Stable reason code and aggregate values only.

    Deliberately absent are message, record ID, query, term, and arbitrary
    mapping fields. That makes it impossible to attach record content, denied
    identifiers, unauthorized terms, credentials, or raw personal context to
    this diagnostic vocabulary.
    """

    reason: DiagnosticReasonCode
    values: tuple[DiagnosticValue, ...] = ()

    def __post_init__(self) -> None:
        metrics = tuple(value.metric for value in self.values)
        if len(metrics) != len(set(metrics)):
            raise ValueError("diagnostic metrics must be unique")


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """Explicit deterministic clock inputs for current or as-of resolution."""

    evaluated_at: datetime
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        for value in (self.evaluated_at, self.as_of):
            if value is not None and value.utcoffset() is None:
                raise ValueError("temporal context requires timezone-aware timestamps")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(UTC))
        if self.as_of is not None:
            object.__setattr__(self, "as_of", self.as_of.astimezone(UTC))

    @property
    def effective_at(self) -> datetime:
        """Return the caller-supplied comparison instant without reading a clock."""

        return self.as_of if self.as_of is not None else self.evaluated_at


@dataclass(frozen=True, slots=True)
class TemporalResolution[CandidateT]:
    """Ordered candidates produced by a temporal resolver."""

    candidates: tuple[CandidateT, ...]
    diagnostics: tuple[SafeRetrievalDiagnostic, ...] = ()


class TemporalResolver[CandidateT](Protocol):
    """Resolve candidates against only the explicit temporal context supplied."""

    def resolve(
        self, candidates: Sequence[CandidateT], context: TemporalContext
    ) -> TemporalResolution[CandidateT]: ...


@dataclass(frozen=True, slots=True)
class AdmissibilityDecision:
    """Binary task admissibility outcome without score or free-form rationale."""

    admissible: bool
    diagnostics: tuple[SafeRetrievalDiagnostic, ...] = ()


class TaskAdmissibilityEvaluator[CandidateT, TaskT](Protocol):
    """Decide task admissibility; implementations must be deterministic."""

    def evaluate(self, candidate: CandidateT, task: TaskT) -> AdmissibilityDecision: ...


@dataclass(frozen=True, slots=True)
class SetSelectionConstraints:
    """Bounded inputs for a set-level selector."""

    limit: int
    budget: int | None = None

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("selection limit must be non-negative")
        if self.budget is not None and self.budget < 0:
            raise ValueError("selection budget must be non-negative")


@dataclass(frozen=True, slots=True)
class SetSelection[CandidateT]:
    """Immutable ordered result of set-level selection."""

    candidates: tuple[CandidateT, ...]
    diagnostics: tuple[SafeRetrievalDiagnostic, ...] = ()


class SetSelector[CandidateT](Protocol):
    """Choose a compatible ordered set without mutating candidate objects."""

    def select(
        self,
        candidates: Sequence[CandidateT],
        constraints: SetSelectionConstraints,
    ) -> SetSelection[CandidateT]: ...


@dataclass(frozen=True, slots=True)
class ShadowRetrieval[CandidateT]:
    """Call-local shadow output that is never canonical by contract."""

    candidates: tuple[CandidateT, ...]
    diagnostics: tuple[SafeRetrievalDiagnostic, ...] = ()


class ShadowRetriever[QueryT, CandidateT](Protocol):
    """Optional observer retriever; output must not mutate canonical Core state."""

    def retrieve(self, query: QueryT, limit: int) -> ShadowRetrieval[CandidateT]: ...


@dataclass(frozen=True, slots=True)
class ShadowRetrievalPlan[QueryT, CandidateT]:
    """Immutable optional shadow configuration; an empty plan is disabled."""

    retrievers: tuple[ShadowRetriever[QueryT, CandidateT], ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.retrievers)


@dataclass(frozen=True, slots=True)
class ComparatorIdentity:
    """Machine-stable identity of a frozen comparator contract."""

    name: str
    revision: int
    base_commit: str


FROZEN_V2_COMPARATOR: Final = ComparatorIdentity(
    name="retrieval_v2_lexical_rrf",
    revision=1,
    base_commit="70a4808cc5d9fc35f4a7b9a75bc3cbfbb0e9ce40",
)


@final
class FrozenV2Comparator:
    """Named comparator adapter for the production V2 ranker at the base commit.

    It intentionally delegates rather than restating lexical business logic.
    Fixture parity tests pin observable ordering and require an explicit
    comparator revision if the production implementation later changes.
    """

    identity: Final = FROZEN_V2_COMPARATOR
    frozen_pipeline: Final = True

    def __init__(self) -> None:
        self._delegate = V2LexicalRanker()

    @property
    def explanations(self) -> Sequence[RankingExplanation]:
        return self._delegate.explanations

    def rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> list[sqlite3.Row]:
        return self._delegate.rank(connection, candidates, query)

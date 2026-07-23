"""Optional local dense-shadow primitives for repository-only experiments.

This module is deliberately outside the ``allthecontext`` package. It has no
production integration, persistence, canonical write path, or ranking
authority. Every in-memory snapshot is rebuilt from candidates whose complete
authorization boundary is asserted by the caller. Candidate text is untrusted
data supplied to an embedding runtime, never instructions.

The optional Sentence Transformers adapter is loaded only by an explicit
benchmark request, accepts only a caller-supplied local model directory, pins
execution to CPU, and requires ``local_files_only=True``. Application startup
and the default test path never import or initialize that runtime.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import import_module
from importlib.util import find_spec as _find_spec
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol, cast

EMBEDDING_DIMENSION = 384
FLOAT32_BYTES = 4
VECTOR_BYTES_PER_CANDIDATE = EMBEDDING_DIMENSION * FLOAT32_BYTES
MAX_AUTHORIZED_CANDIDATES = 10_000
MAX_CANDIDATE_ID_CHARS = 256
MAX_CANDIDATE_TEXT_CHARS = 8_192
MAX_QUERY_CHARS = 2_048
MAX_RESULTS = 100


class ExperimentStatus(StrEnum):
    """Whether a measurement was genuinely exercised, never a pass/fail gate."""

    EXERCISED = "exercised"
    NOT_EXERCISED = "not_exercised"


class DenseShadowReason(StrEnum):
    """Closed reason vocabulary with no content, paths, IDs, or exception text."""

    COMPLETED = "completed"
    DISABLED = "disabled"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    MODEL_PATH_NOT_CONFIGURED = "model_path_not_configured"
    MODEL_PATH_UNAVAILABLE = "model_path_unavailable"
    MODEL_LOAD_UNAVAILABLE = "model_load_unavailable"
    MODEL_DIMENSION_INCOMPATIBLE = "model_dimension_incompatible"
    NO_AUTHORIZED_CANDIDATES = "no_authorized_candidates"
    INPUT_REJECTED = "input_rejected"
    RUNTIME_ERROR = "runtime_error"
    INVALID_EMBEDDING = "invalid_embedding"
    FIXTURE_RUNTIME = "fixture_runtime"


class DenseEmbeddingRuntime(Protocol):
    """Minimal optional runtime interface used only inside the experiment."""

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> Iterable[Iterable[float]]: ...


@dataclass(frozen=True, slots=True)
class DenseShadowCandidate:
    """Candidate data plus the complete upstream boundary decision.

    Rejected candidates are discarded before IDs or text are validated, sorted,
    counted, or supplied to the runtime. Keeping content out of ``repr`` reduces
    accidental disclosure during local experimentation.
    """

    record_id: str
    content: str = field(repr=False)
    candidate_authorized: bool = True
    candidate_temporally_eligible: bool = True
    content_authorized: bool = True
    content_temporally_eligible: bool = True

    @property
    def boundary_verified(self) -> bool:
        return (
            self.candidate_authorized
            and self.candidate_temporally_eligible
            and self.content_authorized
            and self.content_temporally_eligible
        )


@dataclass(frozen=True, slots=True)
class DenseShadowDiagnostic:
    """Sanitized numeric diagnostics derived only from authorized candidates."""

    status: ExperimentStatus
    reason: DenseShadowReason
    authorized_candidate_count: int = 0
    indexed_candidate_count: int = 0
    dimension: int = EMBEDDING_DIMENSION
    vector_storage_bytes: int = 0


@dataclass(frozen=True, slots=True)
class DenseShadowHit:
    """Shadow-only score for a candidate already authorized by the caller."""

    record_id: str
    score: float


@dataclass(frozen=True, slots=True)
class DenseQueryVector:
    """A normalized query vector prepared once for repeated exact scans."""

    values: tuple[float, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class DenseQueryPreparation:
    query_vector: DenseQueryVector | None = field(repr=False)
    diagnostics: DenseShadowDiagnostic


@dataclass(frozen=True, slots=True)
class DenseShadowSearch:
    hits: tuple[DenseShadowHit, ...]
    diagnostics: DenseShadowDiagnostic


@dataclass(frozen=True, slots=True)
class DenseShadowBuild:
    index: DenseShadowIndex | None = field(repr=False)
    diagnostics: DenseShadowDiagnostic


class _InvalidEmbedding(ValueError):
    """Internal marker whose details are never returned in diagnostics."""


def _normalized_float32(values: Iterable[float]) -> tuple[float, ...]:
    converted: list[float] = []
    try:
        for position, value in enumerate(values):
            if position >= EMBEDDING_DIMENSION:
                raise _InvalidEmbedding
            number = float(value)
            if not math.isfinite(number):
                raise _InvalidEmbedding
            converted.append(number)
    except (TypeError, ValueError, OverflowError) as error:
        raise _InvalidEmbedding from error
    if len(converted) != EMBEDDING_DIMENSION:
        raise _InvalidEmbedding
    magnitude = math.sqrt(math.fsum(value * value for value in converted))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise _InvalidEmbedding
    compact = array("f", (value / magnitude for value in converted))
    if compact.itemsize != FLOAT32_BYTES:
        raise _InvalidEmbedding
    return tuple(float(value) for value in compact)


def _bounded_embeddings(
    runtime: DenseEmbeddingRuntime,
    texts: Sequence[str],
) -> tuple[tuple[float, ...], ...]:
    try:
        generated = iter(runtime.embed(texts))
        vectors: list[tuple[float, ...]] = []
        for _position in range(len(texts) + 1):
            try:
                raw = next(generated)
            except StopIteration:
                break
            if len(vectors) == len(texts):
                raise _InvalidEmbedding
            vectors.append(_normalized_float32(raw))
    except _InvalidEmbedding:
        raise
    except Exception as error:
        raise RuntimeError from error
    if len(vectors) != len(texts):
        raise _InvalidEmbedding
    return tuple(vectors)


@dataclass(frozen=True, slots=True)
class DenseShadowIndex:
    """Immutable, noncanonical float32 matrix scanned exhaustively on CPU."""

    _record_ids: tuple[str, ...] = field(repr=False)
    _matrix: bytes = field(repr=False)
    _runtime: DenseEmbeddingRuntime = field(repr=False, compare=False)

    @property
    def candidate_count(self) -> int:
        return len(self._record_ids)

    @property
    def vector_storage_bytes(self) -> int:
        return len(self._matrix)

    def payload_equal(self, other: DenseShadowIndex) -> bool:
        """Compare deterministic payloads without exposing a content fingerprint."""

        return self._record_ids == other._record_ids and self._matrix == other._matrix

    def prepare_query(self, query: str) -> DenseQueryPreparation:
        """Embed one bounded query; failures return sanitized not-exercised state."""

        base = self._diagnostic(ExperimentStatus.NOT_EXERCISED, DenseShadowReason.INPUT_REJECTED)
        if not query or len(query) > MAX_QUERY_CHARS:
            return DenseQueryPreparation(None, base)
        try:
            vectors = _bounded_embeddings(self._runtime, (query,))
        except _InvalidEmbedding:
            return DenseQueryPreparation(
                None,
                self._diagnostic(
                    ExperimentStatus.NOT_EXERCISED,
                    DenseShadowReason.INVALID_EMBEDDING,
                ),
            )
        except RuntimeError:
            return DenseQueryPreparation(
                None,
                self._diagnostic(
                    ExperimentStatus.NOT_EXERCISED,
                    DenseShadowReason.RUNTIME_ERROR,
                ),
            )
        return DenseQueryPreparation(
            DenseQueryVector(vectors[0]),
            self._diagnostic(ExperimentStatus.EXERCISED, DenseShadowReason.COMPLETED),
        )

    def exact_scan(self, query: DenseQueryVector, *, limit: int = 5) -> DenseShadowSearch:
        """Exhaustively score every authorized vector; no ANN structure exists."""

        if not 1 <= limit <= MAX_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
        if len(query.values) != EMBEDDING_DIMENSION or not all(
            math.isfinite(value) for value in query.values
        ):
            return DenseShadowSearch(
                (),
                self._diagnostic(
                    ExperimentStatus.NOT_EXERCISED,
                    DenseShadowReason.INVALID_EMBEDDING,
                ),
            )
        scored: list[tuple[float, str]] = []
        matrix = memoryview(self._matrix).cast("f")
        for position, record_id in enumerate(self._record_ids):
            offset = position * EMBEDDING_DIMENSION
            score = math.fsum(
                query.values[dimension] * float(matrix[offset + dimension])
                for dimension in range(EMBEDDING_DIMENSION)
            )
            scored.append((score, record_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits = tuple(
            DenseShadowHit(record_id=record_id, score=round(score, 12))
            for score, record_id in scored[:limit]
        )
        return DenseShadowSearch(
            hits,
            self._diagnostic(ExperimentStatus.EXERCISED, DenseShadowReason.COMPLETED),
        )

    def search(self, query: str, *, limit: int = 5) -> DenseShadowSearch:
        """Convenience composition of query embedding and exhaustive scan."""

        prepared = self.prepare_query(query)
        if prepared.query_vector is None:
            return DenseShadowSearch((), prepared.diagnostics)
        return self.exact_scan(prepared.query_vector, limit=limit)

    def _diagnostic(
        self, status: ExperimentStatus, reason: DenseShadowReason
    ) -> DenseShadowDiagnostic:
        return DenseShadowDiagnostic(
            status=status,
            reason=reason,
            authorized_candidate_count=self.candidate_count,
            indexed_candidate_count=self.candidate_count,
            vector_storage_bytes=self.vector_storage_bytes,
        )


class DenseShadowExperiment:
    """Explicitly disabled-by-default builder for discardable local snapshots."""

    def __init__(
        self,
        runtime: DenseEmbeddingRuntime | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self.runtime = runtime
        self.enabled = enabled

    def rebuild(self, candidates: Sequence[DenseShadowCandidate]) -> DenseShadowBuild:
        """Rebuild an in-memory index after dropping every unsafe candidate."""

        if not self.enabled:
            return self._not_exercised(DenseShadowReason.DISABLED)
        if self.runtime is None:
            return self._not_exercised(DenseShadowReason.RUNTIME_UNAVAILABLE)
        try:
            dimension = int(self.runtime.dimension)
        except Exception:
            return self._not_exercised(DenseShadowReason.RUNTIME_UNAVAILABLE)
        if dimension != EMBEDDING_DIMENSION:
            return self._not_exercised(DenseShadowReason.MODEL_DIMENSION_INCOMPATIBLE)

        authorized = sorted(
            (candidate for candidate in candidates if candidate.boundary_verified),
            key=lambda candidate: candidate.record_id,
        )
        count = len(authorized)
        if not authorized:
            return self._not_exercised(DenseShadowReason.NO_AUTHORIZED_CANDIDATES)
        if count > MAX_AUTHORIZED_CANDIDATES:
            return self._not_exercised(
                DenseShadowReason.INPUT_REJECTED,
                authorized_candidate_count=count,
            )
        if any(
            not candidate.record_id
            or len(candidate.record_id) > MAX_CANDIDATE_ID_CHARS
            or not candidate.content
            or len(candidate.content) > MAX_CANDIDATE_TEXT_CHARS
            for candidate in authorized
        ):
            return self._not_exercised(
                DenseShadowReason.INPUT_REJECTED,
                authorized_candidate_count=count,
            )
        if any(
            previous.record_id == current.record_id for previous, current in pairwise(authorized)
        ):
            return self._not_exercised(
                DenseShadowReason.INPUT_REJECTED,
                authorized_candidate_count=count,
            )

        try:
            vectors = _bounded_embeddings(
                self.runtime,
                tuple(candidate.content for candidate in authorized),
            )
        except _InvalidEmbedding:
            return self._not_exercised(
                DenseShadowReason.INVALID_EMBEDDING,
                authorized_candidate_count=count,
            )
        except RuntimeError:
            return self._not_exercised(
                DenseShadowReason.RUNTIME_ERROR,
                authorized_candidate_count=count,
            )
        matrix = array("f")
        for vector in vectors:
            matrix.extend(vector)
        if matrix.itemsize != FLOAT32_BYTES:
            return self._not_exercised(
                DenseShadowReason.INVALID_EMBEDDING,
                authorized_candidate_count=count,
            )
        index = DenseShadowIndex(
            tuple(candidate.record_id for candidate in authorized),
            matrix.tobytes(),
            self.runtime,
        )
        diagnostics = DenseShadowDiagnostic(
            status=ExperimentStatus.EXERCISED,
            reason=DenseShadowReason.COMPLETED,
            authorized_candidate_count=count,
            indexed_candidate_count=count,
            vector_storage_bytes=index.vector_storage_bytes,
        )
        return DenseShadowBuild(index, diagnostics)

    @staticmethod
    def _not_exercised(
        reason: DenseShadowReason,
        *,
        authorized_candidate_count: int = 0,
    ) -> DenseShadowBuild:
        return DenseShadowBuild(
            None,
            DenseShadowDiagnostic(
                status=ExperimentStatus.NOT_EXERCISED,
                reason=reason,
                authorized_candidate_count=authorized_candidate_count,
            ),
        )


@dataclass(frozen=True, slots=True)
class LocalSentenceTransformerRuntime:
    """Small adapter around an explicitly loaded local CPU model."""

    _model: Any = field(repr=False, compare=False)
    _dimension: int

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> Iterable[Iterable[float]]:
        encoded: Any = self._model.encode(
            list(texts),
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=False,
            device="cpu",
        )
        return cast(Iterable[Iterable[float]], encoded)


@dataclass(frozen=True, slots=True)
class RuntimeAvailability:
    """Sanitized availability result; the local path and loader errors are absent."""

    status: ExperimentStatus
    reason: DenseShadowReason
    runtime: LocalSentenceTransformerRuntime | None = field(default=None, repr=False, compare=False)


def probe_local_sentence_transformer(
    *,
    enabled: bool = False,
    model_path: Path | None = None,
) -> RuntimeAvailability:
    """Feature-detect an explicitly local model without downloading anything."""

    if not enabled:
        return RuntimeAvailability(ExperimentStatus.NOT_EXERCISED, DenseShadowReason.DISABLED)
    if model_path is None:
        return RuntimeAvailability(
            ExperimentStatus.NOT_EXERCISED,
            DenseShadowReason.MODEL_PATH_NOT_CONFIGURED,
        )
    if not model_path.is_dir():
        return RuntimeAvailability(
            ExperimentStatus.NOT_EXERCISED,
            DenseShadowReason.MODEL_PATH_UNAVAILABLE,
        )
    try:
        specification = _find_spec("sentence_transformers")
    except (ImportError, ModuleNotFoundError, ValueError):
        specification = None
    if specification is None:
        return RuntimeAvailability(
            ExperimentStatus.NOT_EXERCISED,
            DenseShadowReason.RUNTIME_UNAVAILABLE,
        )
    try:
        module = import_module("sentence_transformers")
        model_type = module.SentenceTransformer
        model = model_type(
            str(model_path),
            device="cpu",
            local_files_only=True,
        )
        dimension = model.get_sentence_embedding_dimension()
        if dimension is None or int(dimension) != EMBEDDING_DIMENSION:
            return RuntimeAvailability(
                ExperimentStatus.NOT_EXERCISED,
                DenseShadowReason.MODEL_DIMENSION_INCOMPATIBLE,
            )
        evaluate = getattr(model, "eval", None)
        if callable(evaluate):
            evaluate()
    except Exception:
        return RuntimeAvailability(
            ExperimentStatus.NOT_EXERCISED,
            DenseShadowReason.MODEL_LOAD_UNAVAILABLE,
        )
    return RuntimeAvailability(
        ExperimentStatus.EXERCISED,
        DenseShadowReason.COMPLETED,
        LocalSentenceTransformerRuntime(model, EMBEDDING_DIMENSION),
    )

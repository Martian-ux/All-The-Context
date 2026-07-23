from __future__ import annotations

from collections.abc import Iterable, Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import SimpleNamespace

import pytest

import bench.dense_shadow as dense_shadow
from bench.dense_shadow import (
    EMBEDDING_DIMENSION,
    VECTOR_BYTES_PER_CANDIDATE,
    DenseShadowCandidate,
    DenseShadowExperiment,
    DenseShadowReason,
    ExperimentStatus,
    probe_local_sentence_transformer,
)


def _axis(position: int) -> tuple[float, ...]:
    values = [0.0] * EMBEDDING_DIMENSION
    values[position] = 1.0
    return tuple(values)


class RecordingRuntime:
    dimension = EMBEDDING_DIMENSION

    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> Iterable[Iterable[float]]:
        batch = tuple(texts)
        self.batches.append(batch)
        for text in batch:
            yield _axis(0 if "alpha" in text else 1)


def _candidate(record_id: str, content: str, **values: bool) -> DenseShadowCandidate:
    return DenseShadowCandidate(record_id=record_id, content=content, **values)


def test_disabled_shadow_does_not_inspect_runtime_or_candidate_content() -> None:
    class ExplodingRuntime:
        @property
        def dimension(self) -> int:
            raise AssertionError("disabled runtime was inspected")

        def embed(self, texts: Sequence[str]) -> Iterable[Iterable[float]]:
            raise AssertionError(texts)

    result = DenseShadowExperiment(ExplodingRuntime()).rebuild(
        [_candidate("disabled-secret-id", "disabled secret content")]
    )

    assert result.index is None
    assert result.diagnostics.status == ExperimentStatus.NOT_EXERCISED
    assert result.diagnostics.reason == DenseShadowReason.DISABLED
    assert result.diagnostics.authorized_candidate_count == 0
    assert "secret" not in repr(result)


def test_unsafe_candidates_cannot_affect_vectors_scores_statistics_or_diagnostics() -> None:
    safe = [
        _candidate("alpha", "alpha permitted fixture"),
        _candidate("beta", "beta permitted fixture"),
    ]
    unsafe = [
        _candidate(
            "denied-record-id",
            "alpha denied secret",
            candidate_authorized=False,
        ),
        _candidate(
            "expired-record-id",
            "alpha expired secret",
            candidate_temporally_eligible=False,
        ),
        _candidate(
            "unauthorized-content-id",
            "alpha unauthorized content secret",
            content_authorized=False,
        ),
        _candidate(
            "",
            "x" * 20_000,
            content_temporally_eligible=False,
        ),
    ]
    clean_runtime = RecordingRuntime()
    polluted_runtime = RecordingRuntime()
    clean = DenseShadowExperiment(clean_runtime, enabled=True).rebuild(safe)
    polluted = DenseShadowExperiment(polluted_runtime, enabled=True).rebuild(
        [*unsafe, *reversed(safe)]
    )

    assert clean.index is not None
    assert polluted.index is not None
    assert polluted.diagnostics == clean.diagnostics
    assert polluted.index.payload_equal(clean.index)
    assert (
        polluted_runtime.batches
        == clean_runtime.batches
        == [("alpha permitted fixture", "beta permitted fixture")]
    )

    clean_result = clean.index.search("alpha query", limit=2)
    polluted_result = polluted.index.search("alpha query", limit=2)

    assert polluted_result == clean_result
    assert polluted_result.hits[0].record_id == "alpha"
    assert polluted_result.hits[0].score == 1.0
    rendered = repr(polluted_result)
    assert "denied-record-id" not in rendered
    assert "expired-record-id" not in rendered
    assert "unauthorized-content-id" not in rendered


def test_float32_snapshot_is_compact_rebuildable_and_tie_deterministic() -> None:
    runtime = RecordingRuntime()
    candidates = [
        _candidate("zeta", "alpha same vector"),
        _candidate("alpha", "alpha same vector"),
    ]

    forward = DenseShadowExperiment(runtime, enabled=True).rebuild(candidates)
    reverse = DenseShadowExperiment(runtime, enabled=True).rebuild(list(reversed(candidates)))

    assert forward.index is not None
    assert reverse.index is not None
    assert forward.index.payload_equal(reverse.index)
    assert forward.index.vector_storage_bytes == 2 * VECTOR_BYTES_PER_CANDIDATE
    assert forward.diagnostics.vector_storage_bytes == 2 * 384 * 4
    assert [hit.record_id for hit in forward.index.search("alpha").hits] == [
        "alpha",
        "zeta",
    ]


@pytest.mark.parametrize(
    ("runtime", "reason"),
    [
        (SimpleNamespace(dimension=128), DenseShadowReason.MODEL_DIMENSION_INCOMPATIBLE),
        (
            SimpleNamespace(
                dimension=EMBEDDING_DIMENSION,
                embed=lambda texts: [[0.0] * EMBEDDING_DIMENSION for _text in texts],
            ),
            DenseShadowReason.INVALID_EMBEDDING,
        ),
    ],
)
def test_incompatible_runtime_is_not_exercised(runtime: object, reason: DenseShadowReason) -> None:
    result = DenseShadowExperiment(runtime, enabled=True).rebuild(  # type: ignore[arg-type]
        [_candidate("safe", "safe synthetic content")]
    )

    assert result.index is None
    assert result.diagnostics.status == ExperimentStatus.NOT_EXERCISED
    assert result.diagnostics.reason == reason


def test_runtime_errors_are_sanitized_for_build_and_query() -> None:
    class FailingRuntime:
        dimension = EMBEDDING_DIMENSION

        def embed(self, texts: Sequence[str]) -> Iterable[Iterable[float]]:
            raise RuntimeError("raw personal context and credential detail")

    build = DenseShadowExperiment(FailingRuntime(), enabled=True).rebuild(
        [_candidate("safe", "safe synthetic content")]
    )

    assert build.index is None
    assert build.diagnostics.reason == DenseShadowReason.RUNTIME_ERROR
    assert "personal" not in repr(build)
    assert "credential" not in repr(build)

    runtime = RecordingRuntime()
    successful = DenseShadowExperiment(runtime, enabled=True).rebuild(
        [_candidate("safe", "alpha synthetic content")]
    )
    assert successful.index is not None
    runtime.embed = FailingRuntime().embed  # type: ignore[method-assign]
    query = successful.index.search("raw query content")
    assert query.diagnostics.status == ExperimentStatus.NOT_EXERCISED
    assert query.diagnostics.reason == DenseShadowReason.RUNTIME_ERROR
    assert "raw query content" not in repr(query)


def test_disabled_and_missing_local_model_probes_never_import_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_find_spec(name: str) -> ModuleSpec | None:
        raise AssertionError(name)

    monkeypatch.setattr(dense_shadow, "_find_spec", fail_find_spec)

    disabled = probe_local_sentence_transformer(
        enabled=False,
        model_path=tmp_path,
    )
    missing = probe_local_sentence_transformer(
        enabled=True,
        model_path=tmp_path / "missing",
    )

    assert disabled.status == ExperimentStatus.NOT_EXERCISED
    assert disabled.reason == DenseShadowReason.DISABLED
    assert missing.status == ExperimentStatus.NOT_EXERCISED
    assert missing.reason == DenseShadowReason.MODEL_PATH_UNAVAILABLE


def test_local_model_probe_requires_runtime_without_installing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dense_shadow, "_find_spec", lambda name: None)

    availability = probe_local_sentence_transformer(enabled=True, model_path=tmp_path)

    assert availability.runtime is None
    assert availability.status == ExperimentStatus.NOT_EXERCISED
    assert availability.reason == DenseShadowReason.RUNTIME_UNAVAILABLE


def test_local_model_probe_pins_cpu_and_local_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    class FakeModel:
        def __init__(self, path: str, **kwargs: object) -> None:
            calls.append({"path": path, **kwargs})

        @staticmethod
        def get_sentence_embedding_dimension() -> int:
            return EMBEDDING_DIMENSION

        @staticmethod
        def eval() -> None:
            return None

    monkeypatch.setattr(
        dense_shadow,
        "_find_spec",
        lambda name: ModuleSpec(name, loader=None),
    )
    monkeypatch.setattr(
        dense_shadow,
        "import_module",
        lambda name: SimpleNamespace(SentenceTransformer=FakeModel),
    )

    availability = probe_local_sentence_transformer(enabled=True, model_path=tmp_path)

    assert availability.status == ExperimentStatus.EXERCISED
    assert availability.runtime is not None
    assert calls == [
        {
            "path": str(tmp_path),
            "device": "cpu",
            "local_files_only": True,
        }
    ]
    assert str(tmp_path) not in repr(availability)

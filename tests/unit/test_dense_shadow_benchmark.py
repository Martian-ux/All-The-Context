from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path

import pytest

import bench.dense_shadow_benchmark as benchmark
from bench.dense_shadow import (
    EMBEDDING_DIMENSION,
    DenseShadowReason,
    ExperimentStatus,
    LocalSentenceTransformerRuntime,
    RuntimeAvailability,
)
from bench.dense_shadow_benchmark import (
    FIXTURES,
    BenchmarkMode,
    run,
    select_profiles,
)


def test_fixture_is_sanitized_and_declares_authorization_controls() -> None:
    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == 1
    assert len(fixture["queries"]) == 4
    assert any(not record["authorized"] for record in fixture["records"])
    assert any(not record["eligible"] for record in fixture["records"])
    assert all("facets" in query for query in fixture["queries"])


def test_default_report_is_disabled_not_exercised_and_has_no_pass_claim() -> None:
    report = run()
    rendered = json.dumps(report, sort_keys=True)

    assert report["configuration"]["default_enabled"] is False
    assert report["configuration"]["canonical"] is False
    assert report["configuration"]["production_ranking_authority"] is False
    assert report["optional_model"]["status"] == ExperimentStatus.NOT_EXERCISED
    assert report["optional_model"]["reason"] == DenseShadowReason.DISABLED
    assert report["exact_scan"]["status"] == ExperimentStatus.NOT_EXERCISED
    assert report["exact_scan"]["profiles"] == {}
    assert report["semantic_comparison"]["status"] == ExperimentStatus.NOT_EXERCISED
    assert '"passed"' not in rendered


def test_exact_scan_only_measures_compact_storage_latency_and_determinism() -> None:
    report = run(BenchmarkMode.EXACT_SCAN_ONLY, profiles=[32], iterations=2)
    profile = report["exact_scan"]["profiles"]["32"]
    metrics = profile["metrics"]
    rendered = json.dumps(report, sort_keys=True)

    assert report["optional_model"]["status"] == ExperimentStatus.NOT_EXERCISED
    assert report["optional_model"]["reason"] == DenseShadowReason.FIXTURE_RUNTIME
    assert report["exact_scan"]["status"] == ExperimentStatus.EXERCISED
    assert profile["status"] == ExperimentStatus.EXERCISED
    assert metrics["dimension"] == 384
    assert metrics["vector_storage_bytes"] == 32 * 384 * 4
    assert metrics["vector_bytes_per_candidate"] == 1536.0
    assert metrics["repeated_exact_scan_deterministic"] is True
    assert metrics["embedding_sample_deterministic"] is True
    assert metrics["exact_scan_latency"]["p95_ms"] >= 0.0
    assert report["semantic_comparison"]["status"] == ExperimentStatus.NOT_EXERCISED
    assert report["ann"] == {
        "implemented": False,
        "status": ExperimentStatus.NOT_EXERCISED,
        "reason": "forbidden_in_this_experiment",
    }
    assert "denied-semantic-control" not in rendered
    assert "expired-semantic-control" not in rendered


def test_unavailable_requested_model_is_not_exercised_not_passed() -> None:
    report = run(BenchmarkMode.LOCAL_MODEL, profiles=[8], model_path=None)

    assert report["optional_model"] == {
        "status": ExperimentStatus.NOT_EXERCISED,
        "reason": DenseShadowReason.MODEL_PATH_NOT_CONFIGURED,
    }
    assert report["exact_scan"]["status"] == ExperimentStatus.NOT_EXERCISED
    assert report["semantic_comparison"] == {
        "status": ExperimentStatus.NOT_EXERCISED,
        "reason": DenseShadowReason.MODEL_PATH_NOT_CONFIGURED,
    }


class SemanticFixtureModel:
    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.casefold()
        groups = (
            ("automobile", "gasoline", "car", "fuel"),
            ("canine", "puppy"),
            ("physician", "doctor", "hydration", "water"),
            ("purchased", "notebook", "customer", "buyer", "store", "market"),
        )
        values = [0.0] * EMBEDDING_DIMENSION
        for position, terms in enumerate(groups):
            if any(term in lowered for term in terms):
                values[position] = 1.0
        if not any(values):
            values[-1] = 1.0
        return values

    def encode(self, texts: Sequence[str], **kwargs: object) -> list[list[float]]:
        assert kwargs["device"] == "cpu"
        assert kwargs["normalize_embeddings"] is False
        return [self._vector(text) for text in texts]


def test_semantic_coverage_is_compared_only_for_concrete_local_model_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = LocalSentenceTransformerRuntime(
        SemanticFixtureModel(),
        EMBEDDING_DIMENSION,
    )
    monkeypatch.setattr(
        benchmark,
        "probe_local_sentence_transformer",
        lambda **kwargs: RuntimeAvailability(
            ExperimentStatus.EXERCISED,
            DenseShadowReason.COMPLETED,
            runtime,
        ),
    )

    report = run(
        BenchmarkMode.LOCAL_MODEL,
        profiles=[8],
        iterations=2,
        model_path=tmp_path,
    )

    assert report["optional_model"]["status"] == ExperimentStatus.EXERCISED
    assert report["exact_scan"]["status"] == ExperimentStatus.EXERCISED
    semantic = report["semantic_comparison"]
    assert semantic["status"] == ExperimentStatus.EXERCISED
    assert semantic["query_count"] == 4
    assert semantic["facet_count"] == 4
    assert semantic["dense_coverage_at_5"] == 1.0
    assert 0.0 <= semantic["lexical_coverage_at_5"] <= 1.0
    assert "semantic_coverage_comparison" not in report["declared_not_exercised"]

    polluted_fixture = benchmark._load_fixture()
    clean_fixture = deepcopy(polluted_fixture)
    clean_fixture["records"] = [
        record for record in clean_fixture["records"] if record["authorized"] and record["eligible"]
    ]
    assert benchmark._semantic_comparison(
        polluted_fixture, runtime
    ) == benchmark._semantic_comparison(clean_fixture, runtime)


def test_profile_bounds_keep_10k_explicitly_opt_in() -> None:
    assert select_profiles([], False) == (128, 1_024)
    with pytest.raises(ValueError, match="above 1024"):
        select_profiles([10_000], False)
    assert select_profiles([10_000], True) == (10_000,)
    with pytest.raises(ValueError, match="above 10k"):
        select_profiles([10_001], True)


def test_cli_returns_unavailable_for_explicit_missing_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = benchmark.main(["--enable-local-model", "--profiles", "8"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["optional_model"]["status"] == "not_exercised"

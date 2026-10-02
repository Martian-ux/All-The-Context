from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from allthecontext.models import ApprovalRequest, BootstrapRequest, CandidateInput, SearchRequest
from allthecontext.project_binding import ProjectBindingError, ProjectBindings
from allthecontext.retrieval import EligibleRecordSelector, RetrievalEngine
from allthecontext.retrieval_contracts import FrozenV2Comparator
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

from bench.synthetic_principal import register_synthetic_principal

READER = ClientPrincipal("partial-reader", "Synthetic reader", frozenset({"context:read"}))


def request(query: str = "", budget_chars: int = 12_000, **extra: Any) -> BootstrapRequest:
    return BootstrapRequest.model_validate({"query": query, "budget_chars": budget_chars, **extra})


def approve(store: CoreStore, key: str, content: str, **extra: Any) -> str:
    values: dict[str, Any] = {
        "kind": "fact",
        "scopes": ["project:aurora"],
        "explicit_user_statement": True,
        **extra,
    }
    candidate = store.add_candidate(CandidateInput(content=content, idempotency_key=key, **values))
    return store.approve_candidate(candidate.id, ApprovalRequest(), actor="synthetic").id


@pytest.fixture
def bound_store(tmp_path: Path) -> Iterator[CoreStore]:
    store = CoreStore(tmp_path / "partial.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    register_synthetic_principal(store, READER)
    anchor = approve(store, "anchor", "Aurora project", kind="project")
    bindings = ProjectBindings(store)
    project = bindings.register_project(anchor)
    bindings.register_client(READER.id, str(project["canonical_project_id"]))
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize(
    "query, content",
    [
        ("Aurora region and rationale", "Aurora region is west."),
        ("Aurora owner and deadline", "Aurora owner is Morgan."),
        ("Aurora storage and latency", "Aurora storage uses SQLite."),
    ],
)
def test_bound_partial_keeps_supported_content_and_provenance(
    bound_store: CoreStore, query: str, content: str
) -> None:
    fact = approve(bound_store, "supported", content, source_reference="synthetic:notes")
    engine = RetrievalEngine(bound_store)
    result = engine.bootstrap(request(query=query, budget_chars=1600), READER)
    assert fact in {item.id for item in result.items}
    item = next(item for item in result.items if item.id == fact)
    assert item.content == content
    assert item.source_reference == "synthetic:notes"
    assert result.used_chars <= 1600
    # A partial bootstrap pack does not weaken direct answer precision.
    assert not engine.search(SearchRequest(query=query), READER).items


@pytest.mark.parametrize(
    "boundary",
    [
        "foreign",
        "global",
        "ambiguous",
        "denied",
        "allowlisted",
        "expired",
        "future",
        "deleted",
        "conflict",
    ],
)
def test_bound_partial_preserves_every_evidence_gate(bound_store: CoreStore, boundary: str) -> None:
    region = approve(bound_store, "region", "Aurora region is west.")
    extra: dict[str, Any] = {}
    if boundary == "foreign":
        extra["scopes"] = ["project:vega"]
    elif boundary == "global":
        extra["scopes"] = []
    elif boundary == "ambiguous":
        extra["scopes"] = ["project:aurora", "project:vega"]
    elif boundary == "denied":
        extra["denied_clients"] = [READER.id]
    elif boundary == "allowlisted":
        extra["allowed_clients"] = ["other-reader"]
    elif boundary == "expired":
        extra["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif boundary == "future":
        extra["valid_from"] = "2099-01-01T00:00:00+00:00"
    elif boundary == "conflict":
        extra.update(
            explicit_user_statement=False,
            entity_key="aurora",
            attribute_key="deployment_blocker",
            structured_value={"blocker": "approval"},
        )
    blocker = approve(bound_store, "blocker", "Aurora production blocker is approval.", **extra)
    forbidden = {blocker}
    if boundary == "deleted":
        bound_store.delete_record(blocker, reason="Synthetic deletion")
    elif boundary == "conflict":
        forbidden.add(
            approve(
                bound_store,
                "conflicting-blocker",
                "Aurora production blocker is testing.",
                explicit_user_statement=False,
                entity_key="aurora",
                attribute_key="deployment_blocker",
                structured_value={"blocker": "testing"},
            )
        )
    result = RetrievalEngine(bound_store).bootstrap(
        request(query="Aurora region, production blocker and rationale", budget_chars=1600),
        READER,
    )
    ids = {item.id for item in result.items}
    assert region in ids
    assert not ids & forbidden
    assert all(item.scopes == ["project:aurora"] for item in result.items)


def test_bound_partial_budget_retains_whole_supported_records(bound_store: CoreStore) -> None:
    first = approve(bound_store, "region", "Aurora region is west.")
    second = approve(
        bound_store, "blocker", "Aurora blocker is approval. " + "Pending review. " * 50
    )
    query = "Aurora region, blocker and rationale"
    engine = RetrievalEngine(bound_store)
    small = engine.bootstrap(request(query=query, budget_chars=256), READER)
    assert first in {item.id for item in small.items}
    assert second not in {item.id for item in small.items}
    assert small.used_chars <= 256
    assert small.pack_metadata is not None
    assert small.pack_metadata.truncated
    assert "budget" in small.pack_metadata.truncation_reasons
    large = engine.bootstrap(request(query=query, budget_chars=1600), READER)
    assert {first, second} <= {item.id for item in large.items}
    assert next(item for item in large.items if item.id == second).content.endswith(
        "Pending review. "
    )
    assert large.used_chars <= 1600


def test_bound_partial_keeps_mandatory_preference_tier(bound_store: CoreStore) -> None:
    preference = approve(
        bound_store, "preference", "I prefer concise answers.", kind="interaction_preference"
    )
    foreign = approve(
        bound_store,
        "foreign-preference",
        "I prefer detailed answers.",
        kind="interaction_preference",
        scopes=["project:vega"],
    )
    region = approve(bound_store, "region", "Aurora region is west.")
    result = RetrievalEngine(bound_store).bootstrap(
        request(query="Aurora region and rationale", budget_chars=1600), READER
    )
    assert {preference, region} <= {item.id for item in result.items}
    assert foreign not in {item.id for item in result.items}


@pytest.mark.parametrize(
    "hints", [{}, {"current_project": "vega"}, {"requested_scopes": ["project:aurora"]}]
)
@pytest.mark.parametrize("principal", [READER, None])
def test_unbound_partial_abstention_is_unchanged(
    tmp_path: Path, hints: dict[str, Any], principal: ClientPrincipal | None
) -> None:
    store = CoreStore(tmp_path / "unbound.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    register_synthetic_principal(store, READER)
    approve(store, "atlas", "Atlas region is west.", scopes=["project:atlas"])
    approve(store, "aurora", "Aurora region is east.")
    try:
        result = RetrievalEngine(store).bootstrap(
            request(query="Vega region and rationale", budget_chars=1600, **hints), principal
        )
        assert not result.items
        assert (
            not RetrievalEngine(store)
            .bootstrap(
                request(query="Aurora region and rationale", budget_chars=1600, **hints),
                principal,
            )
            .items
        )
    finally:
        store.close()


def test_bound_partial_hints_cannot_broaden_exact_scope(bound_store: CoreStore) -> None:
    region = approve(bound_store, "region", "Aurora region is west.")
    foreign = approve(
        bound_store, "foreign", "Vega region and rationale are published.", scopes=["project:vega"]
    )
    result = RetrievalEngine(bound_store).bootstrap(
        request(query="region and rationale", current_project="vega", host_project_hint="vega"),
        READER,
    )
    # A mismatched scoring hint may still fail admissibility; it cannot grant access.
    assert not result.items
    assert region not in {item.id for item in result.items}
    assert foreign not in {item.id for item in result.items}
    disjoint = RetrievalEngine(bound_store).bootstrap(
        request(query="region and rationale", requested_scopes=["project:vega"]), READER
    )
    assert not disjoint.items


@pytest.mark.parametrize("failure", ["revoked", "missing", "deleted_anchor", "expired_anchor"])
def test_bound_partial_invalid_binding_never_falls_back(
    bound_store: CoreStore, failure: str
) -> None:
    approve(bound_store, "region", "Aurora region is west.")
    bindings = ProjectBindings(bound_store)
    context = bindings.resolve(READER)
    assert context is not None
    if failure == "revoked":
        bindings.revoke(context.binding_id)
    else:
        with bound_store.transaction() as connection:
            if failure == "missing":
                connection.execute(
                    "DELETE FROM principal_project_bindings WHERE client_id=?", (READER.id,)
                )
            else:
                column = "deleted_at" if failure == "deleted_anchor" else "expires_at"
                connection.execute(
                    f"UPDATE context_records SET {column}=? WHERE id="
                    "(SELECT anchor_record_id FROM canonical_projects WHERE id=?)",
                    ("2000-01-01T00:00:00+00:00", context.canonical_project_id),
                )
    with pytest.raises(ProjectBindingError):
        RetrievalEngine(bound_store).bootstrap(request(query="Aurora region and rationale"), READER)


def test_alternate_selector_does_not_enable_partial_contract(bound_store: CoreStore) -> None:
    class AlternateSelector(EligibleRecordSelector):
        pass

    approve(bound_store, "region", "Aurora region is west.")
    assert (
        not RetrievalEngine(bound_store, selector=AlternateSelector(bound_store))
        .bootstrap(request(query="Aurora region and rationale"), READER)
        .items
    )


def test_frozen_comparator_retains_completeness_contract(bound_store: CoreStore) -> None:
    approve(bound_store, "region", "Aurora region is west.")
    # The existing comparator exposes explanations read-only; the facade's
    # historical protocol declares them writable. Preserve both runtime types.
    engine = RetrievalEngine(bound_store, ranker=FrozenV2Comparator())  # type: ignore[arg-type]
    assert not engine.bootstrap(request(query="Aurora region and rationale"), READER).items


def test_bound_partial_never_falls_back_to_foreign_only_evidence(bound_store: CoreStore) -> None:
    approve(
        bound_store,
        "foreign",
        "Vega region and rationale are published.",
        scopes=["project:vega"],
    )
    assert (
        not RetrievalEngine(bound_store)
        .bootstrap(request(query="region and rationale"), READER)
        .items
    )


def test_bound_partial_preserves_supersession(bound_store: CoreStore) -> None:
    # Manual candidate approval preserves a distinct supersession chain;
    # explicit local statements may instead correct the existing canonical ID.
    old = approve(bound_store, "region", "Aurora region is west.", explicit_user_statement=False)
    new = approve(
        bound_store,
        "updated-region",
        "Aurora region is east.",
        supersedes=old,
        explicit_user_statement=False,
    )
    assert new != old
    result = RetrievalEngine(bound_store).bootstrap(
        request(query="Aurora region and rationale"), READER
    )
    assert new in {item.id for item in result.items}
    assert old not in {item.id for item in result.items}
    assert next(item for item in result.items if item.id == new).content == "Aurora region is east."


def test_bound_partial_candidate_pool_and_record_limits_stay_bounded(
    bound_store: CoreStore,
) -> None:
    for index in range(120):
        approve(bound_store, f"fact-{index}", f"Aurora region shard {index} is west.")
    result = RetrievalEngine(bound_store).bootstrap(
        request(query="Aurora region and rationale", budget_chars=100_000), READER
    )
    assert result.items
    assert len(result.items) <= 40
    assert result.pack_metadata is not None
    assert result.pack_metadata.candidate_pool_truncated
    assert "candidate_pool" in result.pack_metadata.truncation_reasons
    assert result.used_chars <= 100_000

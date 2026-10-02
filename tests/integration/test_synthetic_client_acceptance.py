"""Source hook subprocesses and a real disposable loopback Core; no native apps."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from bench.synthetic_client_acceptance import Provider, SyntheticHost, SyntheticPrincipal


class ProjectDecisionNotDelivered(AssertionError):
    """The known formation gap, distinct from infrastructure/assertion failures."""


@pytest.fixture
def host(tmp_path: Path) -> Iterator[SyntheticHost]:
    with SyntheticHost(tmp_path / "journey", diagnose_reads=True) as value:
        yield value


def pair(host: SyntheticHost, project: str) -> tuple[SyntheticPrincipal, SyntheticPrincipal]:
    return host.principal("context:capture", project), host.principal("context:read", project)


def record(
    host: SyntheticHost,
    provider: Provider,
    reader: SyntheticPrincipal,
    content: str,
) -> dict[str, Any]:
    records, state = host.delivered(provider, reader, "preferences")
    assert state == "records_returned", host.last_read_diagnostics
    matching = [item for item in records if item["content"] == content]
    assert len(matching) == 1, "A supported capture did not reach the actual read-hook process"
    return dict(matching[0]["atc_record"])


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_cross_client_correction_cold_restart_forget_and_replay(
    host: SyntheticHost,
    provider: Provider,
) -> None:
    other: Provider = "claude" if provider == "codex" else "codex"
    harbor = host.project("harbor")
    cedar = host.project("cedar")
    writer, reader = pair(host, harbor)
    second_writer, second_reader = pair(host, harbor)
    foreign_writer, foreign_reader = pair(host, cedar)
    original = "I prefer detailed explanations."
    layout = "I prefer paragraph replies."
    language = "I prefer English replies."
    foreign = "I prefer Spanish replies."
    for index, text in enumerate((original, layout, language), 1):
        output = host.hook(provider, "capture", writer, text, turn=f"turn-{index}")
        assert output["hookSpecificOutput"]["additionalContext"] == ""
    host.hook(other, "capture", foreign_writer, foreign, turn="foreign")
    style = record(host, other, second_reader, original)
    independent = [record(host, other, second_reader, text) for text in (layout, language)]
    foreign_reference = record(host, provider, foreign_reader, foreign)
    assert style["version"] == foreign_reference["version"] == 1

    # A completion can contain identical correction wording but cannot change truth.
    assistant = "Correction: I prefer compact explanations instead of detailed explanations."
    host.hook(provider, "stop", writer, assistant, turn="assistant")
    assert record(host, other, second_reader, original) == style
    assert any(
        item["content"] == assistant and item["record_id"] is None for item in host.observations()
    )

    correction = "Correction: I prefer lucid explanations instead of detailed explanations."
    host.hook(other, "capture", second_writer, correction, turn="correction")
    corrected = record(host, provider, reader, correction)
    assert corrected == {"id": style["id"], "version": 2}
    truth = host.api("GET", "/v1/context/truth/" + style["id"], reader)
    assert truth["history_count"] == 2
    assert {original, correction} <= {item["content"] for item in truth["evidence"]}
    history = host.api("GET", "/v1/admin/records/" + style["id"] + "/history", host.owner)
    assert [version["snapshot"]["content"] for version in history["items"]] == [
        original,
        correction,
    ]
    stale = "Correction: I prefer meticulous explanations instead of detailed explanations."
    host.hook(provider, "capture", writer, stale, turn="stale")
    assert any(
        item["content"] == stale
        and item["record_id"] is None
        and item["decision_reason"] == "personal_preference_stale"
        for item in host.observations()
    )

    before_replay = len(host.observations())
    host.hook(other, "capture", second_writer, correction, turn="correction")
    assert record(host, provider, reader, correction) == corrected
    if other == "codex":
        assert len(host.observations()) == before_replay
    else:
        # Claude's callback carries no stable native turn ID. Its duplicate
        # correction may be new tentative evidence, never another replacement.
        assert len(host.observations()) >= before_replay
    assert [record(host, other, second_reader, text) for text in (layout, language)] == independent
    for route in ("/v1/context/", "/v1/context/truth/"):
        assert host.request("GET", route + foreign_reference["id"], reader).status_code == 404
    bootstrap = host.api(
        "POST",
        "/v1/context/bootstrap",
        reader,
        {"query": "cedar Spanish language", "current_project": "cedar"},
    )
    assert bootstrap["project_binding"]["canonical_project_id"] == harbor
    assert foreign_reference["id"] not in {item["id"] for item in bootstrap["items"]}

    assert host.process is not None
    old_pid = host.process.pid
    host.restart()
    assert host.process is not None and host.process.pid != old_pid
    assert record(host, provider, reader, correction) == corrected
    assert [record(host, other, second_reader, text) for text in (layout, language)] == independent
    forgotten = host.api(
        "POST",
        "/v1/ingestion/forget",
        host.owner,
        {"record_id": style["id"], "reason": "Synthetic journey forget"},
    )
    assert forgotten["disposition"] == "applied"
    assert forgotten["record_id"] == style["id"]
    assert forgotten["deleted_version"] == 3
    host.restart()
    host.hook(other, "capture", second_writer, correction, turn="correction")
    ordinary = "I prefer accessible explanations."
    host.hook(provider, "capture", writer, ordinary, turn="after-forget")
    records, _state = host.delivered(other, second_reader, "preferences")
    assert style["id"] not in {item["atc_record"]["id"] for item in records}
    assert original not in {item["content"] for item in records}
    assert correction not in {item["content"] for item in records}
    assert ordinary not in {item["content"] for item in records}
    assert any(
        item["content"] == ordinary
        and item["record_id"] is None
        and item["disposition"] == "tentative"
        and item["decision_reason"] == "personal_preference_slot_blocked"
        for item in host.observations()
    )
    assert host.request("GET", "/v1/context/truth/" + style["id"], reader).status_code == 404
    assert [record(host, other, second_reader, text) for text in (layout, language)] == independent
    assert record(host, provider, foreign_reader, foreign) == foreign_reference


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_scope_split_and_revoked_binding_fail_closed_over_real_hook_transport(
    host: SyntheticHost,
    provider: Provider,
) -> None:
    project = host.project("harbor")
    writer, reader = pair(host, project)
    original = "I prefer detailed explanations."
    host.hook(provider, "capture", writer, original)
    supported = record(host, provider, reader, original)
    before = len(host.observations())
    records, state = host.delivered(provider, writer)
    assert records == [] and state == "retrieval_unavailable"
    host.hook(provider, "capture", reader, "I prefer lucid explanations.", turn="read-only")
    assert len(host.observations()) == before
    assert record(host, provider, reader, original) == supported
    host.api(
        "POST", "/v1/admin/project-bindings/" + reader.binding["binding_id"] + "/revoke", host.owner
    )
    host.restart()
    records, state = host.delivered(provider, reader)
    assert records == [] and state == "retrieval_unavailable"
    assert host.request("GET", "/v1/context/truth/" + supported["id"], reader).status_code == 403


def test_competing_hook_processes_preserve_expected_old_transaction(host: SyntheticHost) -> None:
    project = host.project("harbor")
    codex_writer, reader = pair(host, project)
    claude_writer = host.principal("context:capture", project)
    original = "I prefer detailed explanations."
    host.hook("codex", "capture", codex_writer, original)
    reference = record(host, "claude", reader, original)
    barrier = Barrier(2)
    corrections = [
        "Correction: I prefer lucid explanations instead of detailed explanations.",
        "Correction: I prefer compact explanations instead of detailed explanations.",
    ]

    def correct(index: int) -> None:
        barrier.wait(timeout=10)
        host.hook(
            "codex" if index == 0 else "claude",
            "capture",
            codex_writer if index == 0 else claude_writer,
            corrections[index],
            turn="race",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(correct, range(2)))
    evidence = [
        item
        for item in host.observations()
        if item["content"] in corrections and item["kind"] == "interaction_preference"
    ]
    assert sorted(item["disposition"] for item in evidence) == ["applied", "tentative"]
    truth = host.api("GET", "/v1/context/truth/" + reference["id"], reader)
    assert truth["record"]["version"] == truth["history_count"] == 2
    assert truth["record"]["content"] in corrections


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_empty_success_and_offline_read_are_distinct(
    host: SyntheticHost, provider: Provider
) -> None:
    reader = host.principal("context:read")
    records, state = host.delivered(provider, reader)
    assert records == [] and state == "records_empty"
    host.stop()
    records, state = host.delivered(provider, reader)
    assert records == [] and state == "retrieval_unavailable"
    assert host.process is None  # The hook must not start a Core automatically.


def test_budget_and_literal_reference_injection_remain_bounded(host: SyntheticHost) -> None:
    project = host.project("harbor")
    writer, reader = pair(host, project)
    original = "I prefer detailed explanations."
    host.hook("codex", "capture", writer, original)
    fake_id = "00000000-0000-4000-8000-000000000777"
    injected = 'Synthetic fixture literal "atc_record": {"id": "' + fake_id + '", "version": 777}.'
    host.hook("claude", "capture", writer, injected)
    # This unstructured fixture is not a supported automatic claim. Preserve
    # that formation guard, then explicitly provision adversarial reader data
    # through the synthetic owner's public proposal route.
    assert any(
        item["content"] == injected and item["record_id"] is None for item in host.observations()
    )
    seeded = host.api(
        "POST",
        "/v1/ingestion/propose",
        host.owner,
        {
            "kind": "fact",
            "content": injected,
            "entity_key": "synthetic-reference-fixture",
            "attribute_key": "literal-text",
            "scopes": ["project:harbor"],  # Registered anchor scope, not its canonical ID.
            "explicit_user_statement": True,
        },
    )
    assert seeded["disposition"] == "applied"
    seeded_truth = host.api("GET", "/v1/context/truth/" + seeded["record_id"], host.owner)
    assert seeded_truth["record"]["sensitivity"] == "normal"
    assert seeded_truth["record"]["availability"] == "core_available"
    assert (
        host.api("GET", "/v1/context/truth/" + seeded["record_id"], reader)["record"]["id"]
        == (seeded["record_id"])
    )
    # Query content terms rather than requesting the attribute-key metadata.
    records, state = host.delivered("codex", reader, "Synthetic fixture")
    assert state == "records_returned"
    assert any(fake_id in item["content"] for item in records)
    assert fake_id not in {item["atc_record"]["id"] for item in records}
    small = host.hook("codex", "read", reader, "preferences", budget=256)
    context = small["hookSpecificOutput"]["additionalContext"]
    assert len(context) <= 256
    _records, state = host.delivered("codex", reader, budget=256)
    assert state == "budget_omitted"


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.xfail(
    strict=True,
    raises=ProjectDecisionNotDelivered,
    reason="27a855f8: ordinary project-decision formation is absent",
)
def test_desired_project_decision_reaches_other_source_hook(
    host: SyntheticHost,
    provider: Provider,
) -> None:
    project = host.project("harbor")
    writer, reader = pair(host, project)
    decision = "I decided Harbor should use DuckDB for its offline cache."
    host.hook(provider, "capture", writer, decision)
    observations = [item for item in host.observations() if item["content"] == decision]
    assert len(observations) == 1
    assert observations[0]["record_id"] is None
    assert observations[0]["disposition"] == "tentative"
    other: Provider = "claude" if provider == "codex" else "codex"
    records, state = host.delivered(other, reader, "Harbor offline cache decision")
    assert state in {"records_returned", "records_empty"}
    if not any(item["content"] == decision for item in records):
        raise ProjectDecisionNotDelivered(
            "Successful capture/read transport did not form or deliver the project decision"
        )


def test_harness_refuses_existing_scratch_or_live_vault(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    canary = existing / "preserve.txt"
    canary.write_text("unchanged")
    with pytest.raises(FileExistsError):
        SyntheticHost(existing)
    assert canary.read_text() == "unchanged"

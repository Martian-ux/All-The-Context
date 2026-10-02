"""Real source-hook journeys with explicit registered names and bound principals."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bench.synthetic_client_acceptance import Provider, SyntheticHost, SyntheticPrincipal


@pytest.fixture
def host(tmp_path: Path) -> Iterator[SyntheticHost]:
    with SyntheticHost(tmp_path / "named-journey", diagnose_reads=True) as value:
        yield value


def register_named_project(host: SyntheticHost, name: str, key: str) -> str:
    # Only the anchor's structured name conditions named language. The key,
    # anchor text and query cannot select or grant a canonical project identity.
    anchor = host.api(
        "POST",
        "/v1/ingestion/propose",
        host.owner,
        {
            "kind": "project",
            "content": "An explicit synthetic project identity.",
            "entity_key": key,
            "attribute_key": "identity",
            "scopes": ["project:" + key],
            "structured_value": {"project_name": name},
            "explicit_user_statement": True,
        },
    )
    assert anchor["disposition"] == "applied"
    registered = host.api(
        "POST",
        "/v1/admin/project-identities",
        host.owner,
        {"anchor_record_id": anchor["record_id"]},
    )
    truth = host.api("GET", "/v1/context/truth/" + anchor["record_id"], host.owner)
    assert truth["record"]["structured_value"]["project_name"] == name
    return str(registered["canonical_project_id"])


def bound_principal(host: SyntheticHost, scope: str, project: str) -> SyntheticPrincipal:
    principal = host.principal(scope, project)
    assert principal.binding["canonical_project_id"] == project
    assert principal.binding["state"] == "bound"
    return principal


def delivered_decision(
    host: SyntheticHost,
    provider: Provider,
    reader: SyntheticPrincipal,
    statement: str,
    subject: str,
    value: str,
    project: str,
    key: str,
) -> dict[str, Any]:
    records, state = host.delivered(provider, reader, subject.replace("_", " ") + " decision")
    assert state == "records_returned", host.last_read_diagnostics
    matching = [item for item in records if item["content"] == statement]
    assert len(matching) == 1, "The actual source reader did not receive the supported decision"
    reference = dict(matching[0]["atc_record"])
    truth = host.api("GET", "/v1/context/truth/" + reference["id"], reader)
    record = truth["record"]
    assert record["kind"] == "project_decision"
    assert record["entity_key"] == "project-decision:" + project
    assert record["attribute_key"] == subject
    assert record["scopes"] == ["project:" + key]
    assert record["structured_value"]["decision_subject"] == subject
    assert record["structured_value"]["value"] == value
    assert record["version"] == reference["version"]
    assert any(item["content"] == statement for item in truth["evidence"])
    return reference


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_registered_harbor_cedar_cross_source_decision_correction_restart_forget(
    host: SyntheticHost, provider: Provider
) -> None:
    other: Provider = "claude" if provider == "codex" else "codex"
    harbor_key, cedar_key = "named-context-a", "named-context-b"
    harbor = register_named_project(host, "Harbor", harbor_key)
    cedar = register_named_project(host, "Cedar", cedar_key)
    assert harbor != cedar
    writer = bound_principal(host, "context:capture", harbor)
    second_writer = bound_principal(host, "context:capture", harbor)
    reader = bound_principal(host, "context:read", harbor)
    foreign_writer = bound_principal(host, "context:capture", cedar)
    foreign_reader = bound_principal(host, "context:read", cedar)
    original = "I decided Harbor should use DuckDB for its offline cache."
    foreign = "I decided Cedar should use SQLite for its offline cache."
    database = "I decided Harbor should use PostgreSQL for its database."
    for principal, statement, turn in (
        (writer, original, "named-original"),
        (foreign_writer, foreign, "cedar-original"),
        (writer, database, "independent-database"),
    ):
        output = host.hook(provider, "capture", principal, statement, turn=turn)
        assert output["hookSpecificOutput"]["additionalContext"] == ""
    observations = [item for item in host.observations() if item["content"] == original]
    assert any(item["kind"] == "captured_user_turn" for item in observations)
    formed = [item for item in observations if item["kind"] == "project_decision"]
    assert len(formed) == 1 and formed[0]["disposition"] == "applied"
    first = delivered_decision(
        host, other, reader, original, "offline_cache", "DuckDB", harbor, harbor_key
    )
    independent = delivered_decision(
        host, other, reader, database, "database", "PostgreSQL", harbor, harbor_key
    )
    foreign_reference = delivered_decision(
        host, other, foreign_reader, foreign, "offline_cache", "SQLite", cedar, cedar_key
    )
    assert first["version"] == independent["version"] == foreign_reference["version"] == 1
    assert first["id"] != independent["id"] != foreign_reference["id"]
    assert first["id"] == formed[0]["record_id"]
    for route in ("/v1/context/", "/v1/context/truth/"):
        assert host.request("GET", route + foreign_reference["id"], reader).status_code == 404
        assert host.request("GET", route + first["id"], foreign_reader).status_code == 404
    foreign_attempt = "I decided Cedar should use DuckDB for its offline cache."
    host.hook(other, "capture", second_writer, foreign_attempt, turn="foreign-name")
    rejected = [item for item in host.observations() if item["content"] == foreign_attempt]
    assert rejected and all(item["record_id"] is None for item in rejected)
    pack = host.api(
        "POST",
        "/v1/context/bootstrap",
        reader,
        {"query": "Cedar offline cache", "current_project": "Cedar"},
    )
    assert pack["project_binding"]["canonical_project_id"] == harbor
    assert foreign_reference["id"] not in {item["id"] for item in pack["items"]}

    correction = (
        "Correction: I decided Harbor should use SQLite for its offline cache instead of DuckDB."
    )
    host.hook(other, "stop", second_writer, correction, turn="assistant-correction")
    assert (
        delivered_decision(
            host, other, reader, original, "offline_cache", "DuckDB", harbor, harbor_key
        )
        == first
    )
    host.hook(other, "capture", second_writer, correction, turn="named-correction")
    corrected = delivered_decision(
        host, provider, reader, correction, "offline_cache", "SQLite", harbor, harbor_key
    )
    assert corrected == {"id": first["id"], "version": 2}
    truth = host.api("GET", "/v1/context/truth/" + first["id"], reader)
    assert truth["history_count"] == 2
    assert {original, correction} <= {item["content"] for item in truth["evidence"]}
    history = host.api("GET", "/v1/admin/records/" + first["id"] + "/history", host.owner)
    assert [item["snapshot"]["content"] for item in history["items"]] == [original, correction]

    assert host.process is not None
    old_pid = host.process.pid
    host.restart()
    assert host.process is not None and host.process.pid != old_pid
    # New principals and subprocess readers have no previous client/session cache.
    cold_reader = bound_principal(host, "context:read", harbor)
    cold_foreign = bound_principal(host, "context:read", cedar)
    assert (
        delivered_decision(
            host, provider, cold_reader, correction, "offline_cache", "SQLite", harbor, harbor_key
        )
        == corrected
    )
    # Only Codex exposes the original native event ID. A new Claude assertion
    # of the conflicting old value would be new evidence, not original replay.
    if provider == "codex":
        observation_count = len(host.observations())
        host.hook(provider, "capture", writer, original, turn="named-original")
        assert len(host.observations()) == observation_count
    host.hook(other, "capture", second_writer, correction, turn="named-correction")
    assert (
        delivered_decision(
            host, other, cold_reader, correction, "offline_cache", "SQLite", harbor, harbor_key
        )
        == corrected
    )
    assert host.api("GET", "/v1/context/truth/" + first["id"], cold_reader)["history_count"] == 2
    assert (
        delivered_decision(
            host, other, cold_reader, database, "database", "PostgreSQL", harbor, harbor_key
        )
        == independent
    )
    assert (
        delivered_decision(
            host, provider, cold_foreign, foreign, "offline_cache", "SQLite", cedar, cedar_key
        )
        == foreign_reference
    )

    forgotten = host.api(
        "POST",
        "/v1/ingestion/forget",
        host.owner,
        {"record_id": first["id"], "reason": "Synthetic named journey forget"},
    )
    assert forgotten["disposition"] == "applied" and forgotten["deleted_version"] == 3
    host.restart()
    final_reader = bound_principal(host, "context:read", harbor)
    host.hook(provider, "capture", writer, original, turn="named-original")
    host.hook(other, "capture", second_writer, correction, turn="named-correction")
    ordinary = "For Harbor, I choose DuckDB for the offline cache."
    host.hook(provider, "capture", writer, ordinary, turn="fresh-after-forget")
    records, state = host.delivered(other, final_reader, "Harbor offline cache database decisions")
    assert state == "records_returned", host.last_read_diagnostics
    assert first["id"] not in {item["atc_record"]["id"] for item in records}
    assert not {original, correction, ordinary} & {item["content"] for item in records}
    assert foreign_reference["id"] not in {item["atc_record"]["id"] for item in records}
    assert host.request("GET", "/v1/context/truth/" + first["id"], final_reader).status_code == 404
    assert host.request("GET", "/v1/context/truth/" + first["id"], host.owner).status_code == 404
    rejected = [item for item in host.observations() if item["content"] == ordinary]
    assert rejected and all(
        item["record_id"] is None and item["disposition"] == "tentative" for item in rejected
    )
    assert (
        delivered_decision(
            host, provider, final_reader, database, "database", "PostgreSQL", harbor, harbor_key
        )
        == independent
    )
    assert (
        delivered_decision(
            host, other, cold_foreign, foreign, "offline_cache", "SQLite", cedar, cedar_key
        )
        == foreign_reference
    )
    # Only synthetic public references and achieved assertions enter the receipt.
    (host.root / "named-journey-result.json").write_text(
        json.dumps(
            {
                "provider": provider,
                "reader": other,
                "original": first,
                "corrected": corrected,
                "deleted_version": 3,
                "independent_database": independent,
                "foreign_cache": foreign_reference,
                "explicit_names": ["Harbor", "Cedar"],
                "source_hooks": "fresh subprocesses; real verified loopback Core",
                "cold_restarts": 2,
                "fresh_bound_readers": True,
                "foreign_name_and_query_cannot_override_binding": True,
                "citations_resolve_to_current_truth_and_evidence": True,
                "replay_monotonic_and_tombstone_preserved": True,
                "original_native_event_replay": "verified"
                if provider == "codex"
                else "UNAVAILABLE",
                "installed_clients": "UNRUN",
                "reader_model": "UNRUN",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from allthecontext.importers import parse_json
from allthecontext.models import (
    Availability,
    BootstrapRequest,
    CandidateInput,
    CandidateOut,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
)
from allthecontext.retrieval import RetrievalEngine
from allthecontext.storage import CoreStore


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.db")
    store.initialize_vault()
    return store


def _archive_observation(
    store: CoreStore,
    *,
    content: str,
    batch_key: str,
) -> CandidateOut:
    payload = b"synthetic archive fixture"
    content_hash = sha256(payload).hexdigest()
    state = store.begin_incomplete_source_blob(
        content_hash=content_hash,
        byte_size=len(payload),
        media_type="text/plain",
    )
    if state != "complete":
        store.write_source_blob_chunk(
            content_hash=content_hash,
            chunk_index=0,
            content=payload,
        )
        store.finalize_source_blob(
            content_hash=content_hash,
            expected_byte_size=len(payload),
            media_type="text/plain",
        )
    source = store.create_source_record_for_blob(
        content_hash=content_hash,
        source_service="synthetic",
        source_type="archive",
        filename="synthetic.txt",
    )
    source_id = source.id
    source_reference = "synthetic-export#conversation=synthetic&message=preference"
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=f"session-{batch_key}",
    )
    submitted = store.submit_batch(
        str(session["session_id"]),
        batch_key,
        [
            CandidateInput(
                kind="interaction_preference",
                content=content,
                scopes=["personal"],
                source_id=source_id,
                source_reference=source_reference,
                source_service="synthetic",
                source_type="provider_archive",
                availability=Availability.CORE,
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=[source_id]),
    )
    return store.get_candidate(str(submitted["candidate_ids"][0]))


def test_archive_admission_requires_a_durable_self_contained_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)

    tentative = _archive_observation(
        store,
        content="I prefer this.",
        batch_key="ambiguous-fragment",
    )

    assert tentative.disposition == ObservationDisposition.TENTATIVE
    assert tentative.record_id is None
    assert store.list_observations(disposition=ObservationDisposition.APPLIED)[1] == 0


def test_archive_parser_normalizes_wrappers_and_merges_semantic_duplicates() -> None:
    export = [
        {
            "id": "synthetic-conversation",
            "mapping": {
                "first": {
                    "message": {
                        "id": "synthetic-message-1",
                        "author": {"role": "user"},
                        "content": {"parts": ["  “I prefer concise answers.”  "]},
                    }
                },
                "second": {
                    "message": {
                        "id": "synthetic-message-2",
                        "author": {"role": "user"},
                        "content": {"parts": ["I prefer concise answers!"]},
                    }
                },
            },
        }
    ]

    parsed = parse_json(
        json.dumps(export),
        provider="chatgpt",
        source_name="synthetic-export.json",
    )

    assert len(parsed.candidates) == 1
    candidate = parsed.candidates[0]
    assert candidate.content == "I prefer concise answers."
    assert candidate.source_reference is not None
    assert candidate.source_reference.startswith("archive-provenance-v1:")
    assert "message=synthetic-message-1" in candidate.source_reference
    assert "message=synthetic-message-2" in candidate.source_reference

    generic = parse_json(
        json.dumps(
            [
                {"kind": "preference", "content": "I prefer concise answers."},
                {"kind": "preference", "content": "i prefer concise answers!"},
            ]
        ),
        provider="generic",
    )
    assert len(generic.candidates) == 1


def test_explicit_current_correction_blocks_stale_archive_on_next_bootstrap(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _archive_observation(
        store,
        content="I prefer concise answers.",
        batch_key="original-archive",
    )
    assert original.record_id is not None

    corrected = store.correct_record(
        original.record_id,
        content="I prefer detailed answers.",
        reason="Synthetic explicit correction",
    )
    stale = _archive_observation(
        store,
        content="I prefer concise answers.",
        batch_key="stale-archive",
    )

    assert corrected.content == "I prefer detailed answers."
    assert stale.disposition == ObservationDisposition.IGNORED
    assert stale.record_id == original.record_id
    assert store.get_record(original.record_id).content == "I prefer detailed answers."

    bootstrapped = RetrievalEngine(store).bootstrap(
        BootstrapRequest(query="answer style", budget_chars=4_000)
    )
    contents = [item.content for item in bootstrapped.items]
    assert "I prefer detailed answers." in contents
    assert "I prefer concise answers." not in contents

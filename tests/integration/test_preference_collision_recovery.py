"""Synthetic subject collisions and persisted old-runtime compatibility."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.memory_policy import extract_live_user_claim
from allthecontext.models import CandidateInput, ClientCreate
from fastapi.testclient import TestClient

from tests.integration.test_personal_preference_contract import _capture, _records, core

__all__ = ["core"]
OLD_RUNTIME = "cc49f62d44818dead799259a4c4fb62edf0af9a0"
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("text", "facet"),
    [
        ("I prefer paragraph answers.", "response_layout"),
        ("My preference is replies in paragraphs.", "response_layout"),
        ("I like English replies.", "response_language"),
        ("I now prefer responses in Spanish.", "response_language"),
        ("I prefer cryptic replies.", None),
        ("I prefer medical replies.", None),
        ("I prefer local replies.", None),
        ("I prefer friendly replies.", None),
        ("I prefer not-detailed explanations.", None),
        ("I do not prefer paragraph answers.", None),
        ("I prefer English replies for Project Harbor.", None),
        ("Correction: I prefer paragraph answers instead of detailed explanations.", None),
        ("Correction: I prefer English replies instead of detailed explanations.", None),
    ],
)
def test_subject_classification_preserves_independent_style(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
    text: str,
    facet: str | None,
) -> None:
    service, client, writer, reader = core
    original = "I prefer detailed explanations."
    first = _capture(client, writer, 1, original)
    record_id = service.store.get_observation(first["observation_id"]).record_id
    assert record_id is not None
    result = _capture(client, writer, 2, text)
    observation = service.store.get_observation(result["observation_id"])
    claim = extract_live_user_claim(text)
    if facet is None:
        assert claim is None and observation.record_id is None
        assert len(_records(client, reader)) == 1
    else:
        assert claim is not None and claim.attribute_key == facet
        assert observation.disposition.value == "applied"
        assert observation.record_id not in {None, record_id}
        assert len(_records(client, reader)) == 2
    assert service.store.get_record(record_id).content == original
    assert service.store.get_record(record_id).version == 1
    assert len(service.store.record_history(record_id)) == 1
    assert _capture(client, writer, 2, text)["observation_id"] == result["observation_id"]
    assert service.store.get_record(record_id).version == 1


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("I prefer relaxing evenings.", "relaxing evenings"),
        ("I prefer herbal tea and numbered replies.", "herbal tea and numbered replies"),
        ("I prefer herbal tea.", "numbered replies"),
        ("I prefer that tea.", "that tea"),
    ],
)
def test_ambiguous_legacy_records_keep_conservative_guard(
    core: tuple[CoreService, TestClient, dict[str, str], dict[str, str]],
    deleted: bool,
    text: str,
    value: str,
) -> None:
    service, client, writer, reader = core
    candidate = service.store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            entity_key="user",
            attribute_key="preference",
            content=text,
            structured_value={"value": value},
        )
    )
    legacy = service.store.approve_candidate(candidate.id)
    if deleted:
        service.store.delete_record(legacy.id, reason="Synthetic ambiguity deletion")
    before = service.store.get_record(legacy.id, include_deleted=True)
    result = _capture(client, writer, 1, "I prefer numbered replies.")
    observation = service.store.get_observation(result["observation_id"])
    assert observation.record_id is None
    assert observation.decision_reason == "personal_preference_legacy"
    assert service.store.get_record(legacy.id, include_deleted=True) == before
    assert len(_records(client, reader)) == (0 if deleted else 1)


OLD_CAPTURE = """
import json, sys, uuid
from pathlib import Path
import keyring
from keyring.backends.null import Keyring
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.core.app import create_app
from allthecontext.models import ClientCreate
from fastapi.testclient import TestClient
keyring.set_keyring(Keyring())
root = Path(sys.argv[1])
config = CoreConfig.in_directory(root, require_auth=True)
with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
    principal, token = service.store.create_client(
        ClientCreate(name="Synthetic old writer", scopes=["*"]))
    headers = {"Authorization": "Bearer " + token, "X-ATC-Client-ID": principal.id}
    payload = {"event_id": "old-tea", "idempotency_key": str(uuid.uuid4()),
        "session_id": "synthetic-upgrade", "conversation_id": "synthetic-upgrade",
        "sequence": 1, "role": "user", "content": "I prefer herbal tea.",
        "observed_at": "2026-10-02T10:00:00Z"}
    response = client.post("/v1/lifecycle/events", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    record_id = service.store.get_observation(body["observation_id"]).record_id
    assert record_id is not None
    assert service.store.get_record(record_id).attribute_key == "preference"
    if sys.argv[2] == "deleted":
        forgotten = client.post("/v1/ingestion/forget", headers=headers,
            json={"record_id": record_id, "reason": "Synthetic old-runtime deletion"})
        assert forgotten.status_code == 200, forgotten.text
    (root / "synthetic-upgrade.json").write_text(json.dumps({
        "headers": headers, "payload": payload, "record_id": record_id,
        "observation_id": body["observation_id"]}), encoding="utf-8")
"""


@pytest.mark.parametrize("deleted", [False, True])
def test_actual_old_runtime_capture_reopens_without_global_facet_block(
    tmp_path: Path,
    deleted: bool,
) -> None:
    # Only runtime source is exported; no frozen fixtures or holdouts are opened.
    archive = subprocess.run(
        ["git", "archive", "--format=tar", OLD_RUNTIME, "packages/allthecontext/src"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    old_code = tmp_path / "old-code"
    old_code.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(old_code, filter="data")
    vault = tmp_path / "synthetic-vault"
    env = {
        **os.environ,
        "PYTHONPATH": str(old_code / "packages/allthecontext/src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    seeded = subprocess.run(
        [sys.executable, "-B", "-c", OLD_CAPTURE, str(vault), "deleted" if deleted else "active"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert seeded.returncode == 0, seeded.stderr
    state = json.loads((vault / "synthetic-upgrade.json").read_text(encoding="utf-8"))
    config = CoreConfig.in_directory(vault, require_auth=True)
    with CoreService(config) as service, TestClient(create_app(config, service=service)) as client:
        headers = state["headers"]
        legacy = service.store.get_record(state["record_id"], include_deleted=True)
        history = service.store.record_history(legacy.id)
        replay = client.post("/v1/lifecycle/events", headers=headers, json=state["payload"])
        assert replay.status_code == 200
        assert replay.json()["status"] == "replayed"
        assert replay.json()["observation_id"] == state["observation_id"]
        for sequence, text in enumerate(
            (
                "I prefer cold coffee.",
                "Correction: I prefer cold coffee instead of herbal tea.",
                "I prefer herbal tea.",
            ),
            1,
        ):
            result = _capture(client, headers, sequence, text)
            observation = service.store.get_observation(result["observation_id"])
            assert observation.record_id is None
            assert observation.disposition.value == "tentative"
        texts = [
            "I prefer numbered replies.",
            "I prefer lucid replies.",
            "I prefer English replies.",
        ]
        receipts = [_capture(client, headers, i, text) for i, text in enumerate(texts, 4)]
        assert all(service.store.get_observation(r["observation_id"]).record_id for r in receipts)
        assert service.store.get_record(legacy.id, include_deleted=True) == legacy
        assert service.store.record_history(legacy.id) == history
        correction = "Correction: I prefer paragraph replies instead of numbered replies."
        accepted = _capture(client, headers, 7, correction)
        layout_id = service.store.get_observation(accepted["observation_id"]).record_id
        assert layout_id is not None and service.store.get_record(layout_id).version == 2
        stale = _capture(
            client, headers, 8, "Correction: I prefer bulleted replies instead of numbered replies."
        )
        assert (
            service.store.get_observation(stale["observation_id"]).decision_reason
            == "personal_preference_stale"
        )
        assert (
            _capture(client, headers, 7, correction)["observation_id"] == accepted["observation_id"]
        )
        assert service.store.get_record(layout_id).version == 2
    with CoreService(config) as cold, TestClient(create_app(config, service=cold)) as client:
        current = _records(client, headers)
        assert {item["content"] for item in current} == (
            {correction, *texts[1:]} if deleted else {legacy.content, correction, *texts[1:]}
        )
        assert cold.store.get_record(legacy.id, include_deleted=True) == legacy
        assert client.get(f"/v1/context/truth/{legacy.id}", headers=headers).status_code == (
            404 if deleted else 200
        )


def test_separate_store_stale_corrections_keep_unrelated_language_and_layout(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as first, TestClient(create_app(config, service=first)) as one:
        principal, token = first.store.create_client(
            ClientCreate(name="Synthetic writer", scopes=["*"])
        )
        writer = {"Authorization": f"Bearer {token}", "X-ATC-Client-ID": principal.id}
        receipts = [
            _capture(one, writer, i, text)
            for i, text in enumerate(
                (
                    "I prefer detailed explanations.",
                    "I prefer paragraph replies.",
                    "I prefer English replies.",
                ),
                1,
            )
        ]
        ids = [first.store.get_observation(r["observation_id"]).record_id for r in receipts]
        assert all(ids)
        with CoreService(config) as second, TestClient(create_app(config, service=second)) as two:
            other, other_token = second.store.create_client(
                ClientCreate(name="Second writer", scopes=["*"])
            )
            other_headers = {"Authorization": f"Bearer {other_token}", "X-ATC-Client-ID": other.id}
            barrier = Barrier(2)
            texts = [
                "Correction: I prefer lucid explanations instead of detailed explanations.",
                "Correction: I prefer compact explanations instead of detailed explanations.",
            ]

            def run(index: int) -> dict[str, Any]:
                barrier.wait(timeout=15)
                return _capture(
                    one if index == 0 else two,
                    writer if index == 0 else other_headers,
                    index + 4,
                    texts[index],
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(run, range(2)))
            observations = [first.store.get_observation(r["observation_id"]) for r in results]
            assert sorted(o.disposition.value for o in observations) == ["applied", "tentative"]
            assert [o.decision_reason for o in observations if o.record_id is None] == [
                "personal_preference_stale"
            ]
            for index, result in enumerate(results):
                replay = _capture(
                    one if index == 0 else two,
                    writer if index == 0 else other_headers,
                    index + 4,
                    texts[index],
                )
                assert replay["observation_id"] == result["observation_id"]
            assert ids[0] is not None
            assert first.store.get_record(ids[0]).version == 2
            assert len(first.store.record_history(ids[0])) == 2
            for record_id in ids[1:]:
                assert record_id is not None
                assert first.store.get_record(record_id).version == 1
                assert len(first.store.record_history(record_id)) == 1

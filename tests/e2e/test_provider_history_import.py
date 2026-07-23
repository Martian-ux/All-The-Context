from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from fastapi.testclient import TestClient


def _bundle(name: str, content: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return output.getvalue()


def test_chatgpt_claude_and_grok_history_survive_review_and_core_restart(
    tmp_path: Path,
) -> None:
    chatgpt = _bundle(
        "conversations.json",
        json.dumps(
            [
                {
                    "id": "chatgpt-conversation",
                    "mapping": {
                        "u": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["I prefer concise technical answers."]},
                            }
                        },
                        "a": {
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["Fact: fabricated ChatGPT memory."]},
                            }
                        },
                    },
                }
            ]
        ),
    )
    claude = _bundle(
        "claude/conversations.json",
        json.dumps(
            [
                {
                    "uuid": "claude-conversation",
                    "chat_messages": [
                        {
                            "sender": "human",
                            "text": "My goal is a portable local context system.",
                        },
                        {
                            "sender": "assistant",
                            "text": "Fact: fabricated Claude memory.",
                        },
                    ],
                }
            ]
        ),
    )
    grok = _bundle(
        "grok/session.md",
        "## User\nI use PowerShell for local automation.\n## Grok\nFact: fabricated Grok memory.\n",
    )
    config = CoreConfig.in_directory(tmp_path, require_auth=False)

    with TestClient(create_app(config)) as client:
        results = [
            client.post(
                "/v1/admin/import",
                files={"file": (filename, payload, "application/zip")},
                data={"provider": provider},
            )
            for filename, payload, provider in (
                ("chatgpt.zip", chatgpt, "auto"),
                ("claude.zip", claude, "auto"),
                ("grok.zip", grok, "auto"),
            )
        ]
        assert all(result.status_code == 200 for result in results)
        assert [result.json()["provider"] for result in results] == [
            "chatgpt",
            "claude",
            "grok",
        ]
        assert [result.json()["stats"]["conversations"] for result in results] == [
            1,
            1,
            1,
        ]

        pending = client.get("/v1/admin/candidates").json()["items"]
        assert len(pending) == 3
        assert all(item["approval_status"] == "pending" for item in pending)
        assert all("fabricated" not in item["content"] for item in pending)
        for item in pending:
            approval = client.post(f"/v1/admin/candidates/{item['id']}/approve", json={})
            assert approval.status_code == 200

    with TestClient(create_app(config)) as restarted:
        search = restarted.post(
            "/v1/context/search",
            json={"query": "portable local context", "limit": 20},
        )
        sources = restarted.get("/v1/admin/sources").json()["items"]

    assert search.status_code == 200
    assert any("portable local context" in item["content"] for item in search.json()["items"])
    assert {source["source_service"] for source in sources} == {
        "chatgpt",
        "claude",
        "grok",
    }
    assert all(source["import_status"] == "complete" for source in sources)

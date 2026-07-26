from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from allthecontext.desktop import main as desktop_main
from allthecontext.packaged_provider_acceptance import run_packaged_provider_acceptance
from allthecontext.provider_shapes import frozen_provider_shapes


def _chatgpt_export(path: Path) -> str:
    shape = next(
        item
        for item in frozen_provider_shapes()
        if item.provider == "chatgpt" and item.filename == "conversations.json"
    )
    path.write_bytes(shape.payload_bytes())
    return json.dumps(shape.document, ensure_ascii=False)


def _chatgpt_realistic_graph() -> list[dict[str, Any]]:
    return [
        {
            "id": "chatgpt-acceptance-classifiable",
            "title": "Fictional acceptance graph",
            "mapping": {
                "sys": {
                    "message": {
                        "id": "system-empty",
                        "author": {"role": "system"},
                        "content": {"content_type": "text", "parts": [""]},
                    }
                },
                "user": {
                    "message": {
                        "id": "user-durable",
                        "author": {"role": "user"},
                        "create_time": 1_700_000_000,
                        "content": {
                            "content_type": "text",
                            "parts": ["Preference: Keep fictional packaged answers concise."],
                        },
                    }
                },
                "assistant": {
                    "message": {
                        "id": "assistant-text",
                        "author": {"role": "assistant"},
                        "create_time": 1_700_000_001,
                        "content": {
                            "content_type": "text",
                            "parts": ["Fact: fabricated assistant claim must stay excluded."],
                        },
                    }
                },
                "tool-empty": {
                    "message": {
                        "id": "tool-empty",
                        "author": {"role": "tool"},
                        "content": {"content_type": "code", "parts": []},
                    }
                },
                "user-attachment": {
                    "message": {
                        "id": "user-attachment",
                        "author": {"role": "user"},
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [
                                {
                                    "content_type": "image_asset_pointer",
                                    "asset_pointer": "file-service://file-fictional",
                                }
                            ],
                        },
                    }
                },
                "user-empty": {
                    "message": {
                        "id": "user-empty",
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": [""]},
                    }
                },
            },
        }
    ]


def _write_zip(path: Path, entries: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            payload = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, payload)


def test_packaged_surface_imports_through_core_without_content_in_report(
    tmp_path: Path,
) -> None:
    export = tmp_path / "conversations.json"
    source_text = _chatgpt_export(export)
    report = tmp_path / "report.json"
    data_dir = tmp_path / "vault"

    assert (
        run_packaged_provider_acceptance(
            report_path=report,
            export_path=export,
            provider="chatgpt",
            data_dir=data_dir,
        )
        == 0
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["provider"] == "chatgpt"
    assert payload["candidate_count"] >= 1
    assert payload["coverage_complete"] is True
    assert payload["loopback_bound"] is True
    rendered = json.dumps(payload)
    assert str(export) not in rendered
    for value in json.loads(source_text):
        title = value.get("title")
        if isinstance(title, str):
            assert title not in rendered


def test_desktop_routes_packaged_provider_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "conversations.json"
    _chatgpt_export(export)
    report = tmp_path / "report.json"
    monkeypatch.setenv("ATC_PACKAGED_SMOKE", "1")
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(tmp_path / "vault"))

    assert (
        desktop_main(
            [
                "--packaged-provider-acceptance",
                str(report),
                "--provider-accept-provider",
                "chatgpt",
                "--provider-accept-export",
                str(export),
            ]
        )
        == 0
    )
    assert json.loads(report.read_text(encoding="utf-8"))["operation_status"] == "complete"


def test_packaged_surface_fails_closed_without_overwriting_report(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"status":"stale"}\n', encoding="utf-8")

    assert (
        run_packaged_provider_acceptance(
            report_path=report,
            export_path=tmp_path / "missing.json",
            provider="chatgpt",
        )
        == 1
    )
    assert json.loads(report.read_text(encoding="utf-8")) == {"status": "stale"}


def test_packaged_surface_removes_its_disposable_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "conversations.json"
    _chatgpt_export(export)
    report = tmp_path / "report.json"
    disposable = tmp_path / "owned-vault"

    def fake_data_dir() -> Path:
        disposable.mkdir()
        return disposable

    monkeypatch.setattr(
        "allthecontext.packaged_provider_acceptance._make_temp_data_dir",
        fake_data_dir,
    )
    assert (
        run_packaged_provider_acceptance(
            report_path=report,
            export_path=export,
            provider="chatgpt",
        )
        == 0
    )
    assert not disposable.exists()


def test_packaged_surface_accepts_classifiable_chatgpt_zip_without_content(
    tmp_path: Path,
) -> None:
    """ZIP + realistic classifiable ChatGPT graph yields complete content-free report."""
    export = tmp_path / "chatgpt-export.zip"
    # Inflated member larger than compressed raw archive so progress domains differ.
    conversations = json.dumps(_chatgpt_realistic_graph(), ensure_ascii=False)
    # Modest non-pathological padding: compressible enough to shrink the ZIP,
    # but within the archive compression-ratio limit.
    padding = "".join(f"line-{index:04d} fictional padding\n" for index in range(200))
    _write_zip(
        export,
        {
            "conversations.json": conversations,
            "readme.txt": padding,
        },
    )
    expanded = len(conversations.encode("utf-8")) + len(padding.encode("utf-8"))
    assert export.stat().st_size < expanded
    report = tmp_path / "report.json"
    data_dir = tmp_path / "vault"

    assert (
        run_packaged_provider_acceptance(
            report_path=report,
            export_path=export,
            provider="chatgpt",
            data_dir=data_dir,
        )
        == 0
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert payload["operation_status"] == "complete"
    assert payload["provider"] == "chatgpt"
    assert payload["coverage_complete"] is True
    assert payload["candidate_count"] >= 1
    assert payload["closed_coverage"]["unparsed"] == 0
    assert payload["closed_coverage"]["excluded"] >= 1
    assert payload["closed_coverage"]["skipped"] >= 1
    assert payload["closed_coverage"]["unavailable"] >= 1
    rendered = json.dumps(payload)
    assert "Preference:" not in rendered
    assert "fabricated assistant" not in rendered
    assert str(export) not in rendered


def test_packaged_surface_splits_operation_and_reconcile_failure_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "conversations.json"
    _chatgpt_export(export)
    data_dir = tmp_path / "vault-ops"
    report_ops = tmp_path / "report-ops.json"

    class _BoomOps:
        def import_path_via_operation(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise ValueError("synthetic operation validation failure")

    class _BoomService:
        def __init__(self, _config: object) -> None:
            self.import_operations = _BoomOps()

    monkeypatch.setattr(
        "allthecontext.packaged_provider_acceptance.CoreService",
        _BoomService,
    )
    assert (
        run_packaged_provider_acceptance(
            report_path=report_ops,
            export_path=export,
            provider="chatgpt",
            data_dir=data_dir,
        )
        == 1
    )
    ops_payload = json.loads(report_ops.read_text(encoding="utf-8"))
    assert ops_payload["error_code"] == "import_operation_failed"
    assert ops_payload["content_free"] is True
    assert "synthetic" not in json.dumps(ops_payload)

    report_incomplete = tmp_path / "report-incomplete.json"
    data_incomplete = tmp_path / "vault-incomplete"

    class _IncompleteOps:
        def import_path_via_operation(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"status": "failed", "result": None}

    class _IncompleteService:
        def __init__(self, _config: object) -> None:
            self.import_operations = _IncompleteOps()

    monkeypatch.setattr(
        "allthecontext.packaged_provider_acceptance.CoreService",
        _IncompleteService,
    )
    assert (
        run_packaged_provider_acceptance(
            report_path=report_incomplete,
            export_path=export,
            provider="chatgpt",
            data_dir=data_incomplete,
        )
        == 1
    )
    incomplete_payload = json.loads(report_incomplete.read_text(encoding="utf-8"))
    assert incomplete_payload["error_code"] == "import_operation_incomplete"

    report_reconcile = tmp_path / "report-reconcile.json"
    data_reconcile = tmp_path / "vault-reconcile"

    class _ReconcileOps:
        def import_path_via_operation(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "status": "complete",
                "result": {
                    "provider": "chatgpt",
                    "parser_identity": "chatgpt-archives-v1",
                    "export_format": "chatgpt_conversation_graph",
                    "coverage": {
                        "complete": False,
                        "closed_coverage": {
                            "recognized": 1,
                            "excluded": 0,
                            "skipped": 0,
                            "unavailable": 0,
                            "failed": 0,
                            "unparsed": 1,
                        },
                    },
                    "candidate_ids": ["candidate-1"],
                    "outcomes": {"applied": 1},
                },
            }

    class _ReconcileService:
        def __init__(self, _config: object) -> None:
            self.import_operations = _ReconcileOps()

    monkeypatch.setattr(
        "allthecontext.packaged_provider_acceptance.CoreService",
        _ReconcileService,
    )
    assert (
        run_packaged_provider_acceptance(
            report_path=report_reconcile,
            export_path=export,
            provider="chatgpt",
            data_dir=data_reconcile,
        )
        == 1
    )
    reconcile_payload = json.loads(report_reconcile.read_text(encoding="utf-8"))
    assert reconcile_payload["error_code"] == "import_acceptance_reconcile_failed"
    assert reconcile_payload["content_free"] is True
    # Stage codes must be distinct; the old ambiguous code is gone.
    assert reconcile_payload["error_code"] != ops_payload["error_code"]
    assert incomplete_payload["error_code"] not in {
        ops_payload["error_code"],
        reconcile_payload["error_code"],
    }


def test_packaged_surface_reports_reconcile_stage_for_unknown_graph_nodes(
    tmp_path: Path,
) -> None:
    """Unknown residual material fails closed at reconcile, not as a vague validation code."""
    export = tmp_path / "unknown-graph.json"
    export.write_text(
        json.dumps(
            [
                {
                    "id": "chatgpt-unknown-only",
                    "title": "Fictional unknown residual",
                    "mapping": {
                        "user": {
                            "message": {
                                "id": "user-durable",
                                "author": {"role": "user"},
                                "content": {
                                    "parts": ["Preference: Keep fictional answers concise."]
                                },
                            }
                        },
                        "unknown": {
                            "message": {
                                "id": "plugin-unknown",
                                "author": {"role": "plugin"},
                                "content": {"parts": ["unknown shell"]},
                            }
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report-unknown.json"
    assert (
        run_packaged_provider_acceptance(
            report_path=report,
            export_path=export,
            provider="chatgpt",
            data_dir=tmp_path / "vault-unknown",
        )
        == 1
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["error_code"] == "import_acceptance_reconcile_failed"
    assert payload["content_free"] is True
    assert "plugin" not in json.dumps(payload)

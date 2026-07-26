from __future__ import annotations

import json
from pathlib import Path

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

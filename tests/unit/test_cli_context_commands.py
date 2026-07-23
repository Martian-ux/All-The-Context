from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from allthecontext import cli
from allthecontext.config import CoreConfig
from allthecontext.models import ObservationDisposition


class _Observation:
    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {"id": "observation-1", "disposition": "tentative"}


class _Store:
    def __init__(self) -> None:
        self.list_call: dict[str, Any] | None = None
        self.restore_call: dict[str, Any] | None = None

    def list_observations(
        self,
        *,
        disposition: ObservationDisposition | None,
        limit: int,
        offset: int,
    ) -> tuple[list[_Observation], int]:
        self.list_call = {
            "disposition": disposition,
            "limit": limit,
            "offset": offset,
        }
        return [_Observation()], 1

    def restore_record(
        self,
        record_id: str,
        *,
        version: int | None,
        reason: str,
    ) -> dict[str, Any]:
        self.restore_call = {
            "record_id": record_id,
            "version": version,
            "reason": reason,
        }
        return {"id": record_id, "version": 7}


def test_observations_command_filters_by_disposition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _Store()
    monkeypatch.setattr(cli, "_store", lambda _args: store)

    cli._cmd_observations(
        argparse.Namespace(
            data_dir=None,
            disposition="tentative",
            limit=25,
            offset=4,
        )
    )

    assert store.list_call == {
        "disposition": ObservationDisposition.TENTATIVE,
        "limit": 25,
        "offset": 4,
    }
    assert json.loads(capsys.readouterr().out) == {
        "items": [{"id": "observation-1", "disposition": "tentative"}],
        "total": 1,
    }


def test_observations_command_omits_optional_disposition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _Store()
    monkeypatch.setattr(cli, "_store", lambda _args: store)

    cli._cmd_observations(argparse.Namespace(data_dir=None, disposition=None, limit=100, offset=0))

    assert store.list_call is not None
    assert store.list_call["disposition"] is None
    assert json.loads(capsys.readouterr().out)["total"] == 1


def test_restore_record_command_passes_version_and_reason(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _Store()
    monkeypatch.setattr(cli, "_store", lambda _args: store)

    cli._cmd_restore_record(
        argparse.Namespace(
            data_dir=None,
            record_id="record-1",
            version=3,
            reason="undo accidental correction",
        )
    )

    assert store.restore_call == {
        "record_id": "record-1",
        "version": 3,
        "reason": "undo accidental correction",
    }
    assert json.loads(capsys.readouterr().out) == {"id": "record-1", "version": 7}


def test_restore_dry_run_validates_without_initializing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = CoreConfig.in_directory(tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "_config", lambda _args: config)
    monkeypatch.setattr(cli, "_passphrase", lambda _args: "private passphrase")
    monkeypatch.setattr(
        cli,
        "restore_export",
        lambda source, database, passphrase, *, dry_run: (
            calls.append(
                {
                    "source": source,
                    "database": database,
                    "passphrase": passphrase,
                    "dry_run": dry_run,
                }
            )
            or {"valid": True, "dry_run": True}
        ),
    )
    monkeypatch.setattr(
        cli,
        "CoreStore",
        lambda _path: pytest.fail("dry-run must not initialize or migrate Core"),
    )

    cli._cmd_restore(
        argparse.Namespace(
            data_dir=str(tmp_path),
            source="backup.atcexp",
            dry_run=True,
            passphrase_env="ATC_TEST_PASSPHRASE",
        )
    )

    assert calls == [
        {
            "source": Path("backup.atcexp"),
            "database": config.database_path,
            "passphrase": "private passphrase",
            "dry_run": True,
        }
    ]
    assert not config.database_path.exists()
    assert json.loads(capsys.readouterr().out) == {"dry_run": True, "valid": True}


def test_cli_help_prioritizes_automatic_policy_and_marks_legacy_review_commands() -> None:
    help_text = " ".join(cli.build_parser().format_help().split())

    assert "observations" in help_text
    assert "restore-record" in help_text
    assert "automatic context-policy observations" in help_text
    assert "[deprecated compatibility] List legacy approval candidates" in help_text
    assert "[deprecated compatibility] Approve one legacy candidate" in help_text
    assert "[deprecated compatibility] Reject one legacy candidate" in help_text


def test_observation_disposition_parser_accepts_only_policy_outcomes() -> None:
    parser = cli.build_parser()

    parsed = parser.parse_args(["observations", "--disposition", "ignored"])
    assert parsed.handler is cli._cmd_observations
    assert parsed.disposition == "ignored"

    with pytest.raises(SystemExit):
        parser.parse_args(["observations", "--disposition", "approved"])


@pytest.mark.parametrize("command", ["candidates", "approve", "reject"])
def test_legacy_command_specific_help_is_deprecated(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args([command, "--help"])

    assert exit_info.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "Deprecated compatibility command" in help_text
    assert "automatic" in help_text

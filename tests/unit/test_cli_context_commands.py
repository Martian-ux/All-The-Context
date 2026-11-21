from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from allthecontext import cli
from allthecontext.config import DEFAULT_MAX_IMPORT_BYTES, CoreConfig
from allthecontext.export import MAGIC
from allthecontext.models import (
    CandidateInput,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
)
from allthecontext.storage import CoreStore

_CLI_PASSPHRASE = "fictional-cli-export-passphrase"
_CLI_CONTENT = "The fictional observatory uses amber notebooks."
_CLI_SOURCE_BYTES = b"Fictional observatory archive\x00\xff\nAmber notebooks only.\n"


def _run_cli(
    tmp_path: Path,
    *arguments: str,
    passphrase: str = _CLI_PASSPHRASE,
) -> subprocess.CompletedProcess[str]:
    checkout = Path(__file__).resolve().parents[2]
    host = tmp_path / "cli-host"
    temporary = host / "temp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ATC_") and key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    environment.update(
        {
            "PYTHONPATH": str(checkout / "packages" / "allthecontext" / "src"),
            "PYTHONNOUSERSITE": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE": "1",
            "ATC_TEST_CLI_PASSPHRASE": passphrase,
            "ATC_CORE_DATA_DIR": str(host / "unused-core"),
            "ATC_CLAUDE_CONFIG": str(host / "claude" / "config.json"),
            "CODEX_HOME": str(host / "codex"),
            "HOME": str(host),
            "USERPROFILE": str(host),
            "APPDATA": str(host / "appdata"),
            "LOCALAPPDATA": str(host / "localappdata"),
            "XDG_CONFIG_HOME": str(host / "config"),
            "XDG_DATA_HOME": str(host / "data"),
            "XDG_CACHE_HOME": str(host / "cache"),
            "TMPDIR": str(temporary),
            "TMP": str(temporary),
            "TEMP": str(temporary),
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "allthecontext",
            *arguments,
            "--passphrase-env",
            "ATC_TEST_CLI_PASSPHRASE",
        ],
        cwd=checkout,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def cli_encrypted_archive(tmp_path: Path) -> tuple[Path, str, str, str]:
    config = CoreConfig.in_directory(tmp_path / "source")
    store = CoreStore(config.database_path)
    vault_id = store.initialize_vault("Fictional observatory")
    source = store.add_source(
        _CLI_SOURCE_BYTES,
        source_service="fictional-observatory",
        source_type="provider_archive",
    )
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key="fictional-cli-session",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "fictional-cli-batch",
        [
            CandidateInput(
                kind="fact",
                content=_CLI_CONTENT,
                source_id=source.id,
                source_reference="message:fictional-observatory",
                source_service="fictional-observatory",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=[source.id], complete=True),
    )
    record_id = store.get_observation(str(batch["candidate_ids"][0])).record_id
    assert record_id is not None
    assert store.get_record(record_id).content == _CLI_CONTENT
    archive = tmp_path / "fictional.atcexp"
    exported = _run_cli(
        tmp_path, "export", str(archive), "--data-dir", str(config.data_dir), "--include-sources"
    )
    assert exported.returncode == 0, (exported.stdout, exported.stderr)
    assert json.loads(exported.stdout)["include_sources"] is True
    return archive, vault_id, source.id, record_id


def test_cli_encrypted_export_restore_round_trip(
    tmp_path: Path, cli_encrypted_archive: tuple[Path, str, str, str]
) -> None:
    archive, vault_id, source_id, record_id = cli_encrypted_archive
    encrypted = archive.read_bytes()
    assert encrypted.startswith(MAGIC)
    assert _CLI_CONTENT.encode() not in encrypted
    assert _CLI_SOURCE_BYTES not in encrypted
    destination = CoreConfig.in_directory(tmp_path / "restored")

    restored = _run_cli(tmp_path, "restore", str(archive), "--data-dir", str(destination.data_dir))

    assert restored.returncode == 0, (restored.stdout, restored.stderr)
    result = json.loads(restored.stdout)
    assert result["valid"] is True
    assert result["dry_run"] is False
    assert result["manifest"]["include_sources"] is True
    store = CoreStore(destination.database_path)
    assert store.vault_id() == vault_id
    record = store.get_record(record_id)
    assert record.content == _CLI_CONTENT
    assert record.source_id == source_id
    assert store.get_source_content(source_id) == _CLI_SOURCE_BYTES


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "restore"])
@pytest.mark.parametrize("existing", [False, True], ids=["absent", "existing"])
def test_cli_restore_auth_failure_preserves_destination(
    tmp_path: Path,
    cli_encrypted_archive: tuple[Path, str, str, str],
    dry_run: bool,
    existing: bool,
) -> None:
    archive, _vault_id, _source_id, _record_id = cli_encrypted_archive
    destination = CoreConfig.in_directory(tmp_path / "destination")
    if existing:
        store = CoreStore(destination.database_path)
        vault_id = store.initialize_vault("Existing fictional vault")
        sentinel = store.add_source(
            b"existing fictional source must survive",
            source_service="fictional-destination",
            source_type="provider_archive",
        )
        record = store.add_candidate(
            CandidateInput(
                kind="fact", content="Existing fictional fact", explicit_user_statement=True
            )
        )
        before = destination.database_path.read_bytes()
    arguments = ["restore", str(archive), "--data-dir", str(destination.data_dir)]
    if dry_run:
        arguments.append("--dry-run")

    failed = _run_cli(tmp_path, *arguments, passphrase="wrong-fictional-passphrase")

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    error = json.loads(failed.stdout)
    assert error["ok"] is False
    assert error["error"]["code"] == "InvalidTag"
    if existing:
        assert destination.database_path.read_bytes() == before
        assert store.vault_id() == vault_id
        assert store.get_record(record.record_id).content == "Existing fictional fact"
        assert store.get_source_content(sentinel.id) == b"existing fictional source must survive"
    else:
        assert not destination.database_path.exists()
        assert not destination.data_dir.exists()


def test_cli_restore_dry_run_does_not_initialize_destination(
    tmp_path: Path, cli_encrypted_archive: tuple[Path, str, str, str]
) -> None:
    archive, _vault_id, _source_id, _record_id = cli_encrypted_archive
    destination = CoreConfig.in_directory(tmp_path / "dry-run-destination")

    validated = _run_cli(
        tmp_path, "restore", str(archive), "--data-dir", str(destination.data_dir), "--dry-run"
    )

    assert validated.returncode == 0, (validated.stdout, validated.stderr)
    result = json.loads(validated.stdout)
    assert result["valid"] is True
    assert result["dry_run"] is True
    assert result["manifest"]["include_sources"] is True
    assert not destination.database_path.exists()
    assert not destination.data_dir.exists()


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


def test_import_command_defaults_to_the_supported_two_gigabyte_limit() -> None:
    parser = cli.build_parser()
    parsed = parser.parse_args(["import", "provider-export.zip"])

    assert parsed.max_bytes == DEFAULT_MAX_IMPORT_BYTES == 2_000_000_000
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "import",
                "provider-export.zip",
                "--max-bytes",
                str(DEFAULT_MAX_IMPORT_BYTES + 1),
            ]
        )


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

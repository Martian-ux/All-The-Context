"""Focused sanitized proof for productized foreground capture composition."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from allthecontext import cli
from allthecontext.capture import CaptureError
from allthecontext.capture_runtime import (
    AUTHORIZATION_FILENAME,
    LOCAL_WORKSPACE_ACCOUNT_LABEL,
    authorization_path,
    authorize_local_workspace,
    canonical_workspace_root,
    compose_capture_coordinator,
)
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.export import create_export
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_CODE_OWNED_SCOPES,
    REGISTERED_SOURCE_FACT_SENTENCES,
)
from allthecontext.models import ClientCreate, MemoryTruthStatus, SearchRequest
from allthecontext.registered_source_admission import RegisteredSourceCaptureApplicationSink
from allthecontext.storage import CoreStore, NotFoundError
from fastapi.testclient import TestClient

from tests.fixtures.local_git_workspace import create_sanitized_workspace

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_NO_FACT_RELATIVE_PATH = "notes/data.json"


def _workspace(tmp_path: Path) -> Path:
    root = create_sanitized_workspace(tmp_path / "workspace")
    target = root / _NO_FACT_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"fixture":true}\n', encoding="utf-8", newline="\n")
    return root.resolve()


def _config(tmp_path: Path) -> CoreConfig:
    return CoreConfig.in_directory(tmp_path / "core")


def _init_vault(config: CoreConfig) -> None:
    with CoreService(config):
        pass


def _authorize(config: CoreConfig, workspace: Path) -> dict[str, Any]:
    with CoreService(config) as service:
        return authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )


def _record_id_for_relative_path(store: CoreStore, source_id: str, relative_path: str) -> str:
    with store.connect() as connection:
        row = connection.execute(
            "SELECT r.id FROM context_records r "
            "JOIN context_candidates c ON c.id=r.candidate_id "
            "JOIN capture_events e ON e.id=c.capture_event_id "
            "WHERE c.capture_source_id=? "
            "AND json_extract(e.normalized_payload_json,'$.relative_path')=?",
            (source_id, relative_path),
        ).fetchone()
    assert row is not None
    return str(row["id"])


def _path_leak_forms(path: Path) -> frozenset[str]:
    resolved = path.resolve()
    forms = {
        str(path),
        str(resolved),
        path.as_posix(),
        resolved.as_posix(),
        os.fspath(path),
        os.fspath(resolved),
    }
    escaped = {json.dumps(form)[1:-1] for form in forms}
    return frozenset(form for form in forms | escaped if form)


def _assert_no_root_leak(material: Any, *roots: Path) -> None:
    rendered = material if isinstance(material, str) else json.dumps(material, default=str)
    for root in roots:
        for form in _path_leak_forms(root):
            assert form not in rendered


def _cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    original = sys.argv
    try:
        sys.argv = argv
        cli.main()
    finally:
        sys.argv = original
    return cast(dict[str, Any], json.loads(capsys.readouterr().out))


def test_core_starts_without_authorization_and_adapter_is_unavailable(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with CoreService(config) as service:
        assert service.store.status()["vault_id"]
        assert service.capture.adapters == {}
        assert isinstance(service.capture.sink, RegisteredSourceCaptureApplicationSink)
        source = service.capture.create_source(
            provider=LOCAL_GIT_WORKSPACE_PROVIDER,
            account_label="unauth-workspace",
            account_fingerprint="workspace-source-" + ("ab" * 32),
            requested_scopes=REGISTERED_SOURCE_CODE_OWNED_SCOPES,
            local_only_acknowledged=True,
        )
        enabled = service.capture.enable(source.id)
        result = service.capture.run(source.id)
    assert enabled.lifecycle_state == "enabled"
    assert result.status == "skipped"
    assert result.error_code == "capture_adapter_unavailable"


def test_invalid_sidecar_does_not_fail_core_and_keeps_vault_available(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _init_vault(config)
    sidecar = authorization_path(config.data_dir)
    sidecar.write_text("{not-json", encoding="utf-8")
    with CoreService(config) as service:
        assert service.store.status()["vault_id"]
        assert service.capture.adapters == {}
        source = service.capture.create_source(
            provider="fake",
            account_label="sidecar-invalid",
            local_only_acknowledged=True,
        )
        service.capture.enable(source.id)
        result = service.capture.run(source.id)
    assert result.error_code == "capture_adapter_unavailable"


def test_authorize_creates_exactly_one_disabled_workspace_source(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    _init_vault(config)
    authorized = _authorize(config, workspace)
    adapter = LocalGitWorkspaceCaptureProviderAdapter((workspace,))
    assert authorized["lifecycle_state"] == "disabled"
    assert authorized["provider"] == LOCAL_GIT_WORKSPACE_PROVIDER
    assert authorized["requested_scopes"] == list(REGISTERED_SOURCE_CODE_OWNED_SCOPES)
    assert authorized["account_label"] == LOCAL_WORKSPACE_ACCOUNT_LABEL
    assert authorized["account_fingerprint"] == adapter.source_identity
    assert authorized["reconciled"] is False
    assert authorized["local_only"] is True
    assert authorized["local_only_acknowledged"] is True
    _assert_no_root_leak(authorized, workspace, config.data_dir)
    sidecar = json.loads(authorization_path(config.data_dir).read_text(encoding="utf-8"))
    assert sidecar["source_identity"] == adapter.source_identity
    assert Path(sidecar["canonical_root"]).resolve() == workspace
    with CoreService(config) as service:
        sources, total = service.capture.list_sources()
    assert total == 1
    assert sources[0].id == authorized["id"]
    assert sources[0].lifecycle_state == "disabled"


def test_authorize_reconciles_same_identity_and_refuses_a_second_root(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    other = create_sanitized_workspace(tmp_path / "other")
    _init_vault(config)
    first = _authorize(config, workspace)
    second = _authorize(config, workspace)
    assert second["id"] == first["id"]
    assert second["reconciled"] is True
    assert second["lifecycle_state"] == "disabled"
    with CoreService(config) as service:
        with pytest.raises(CaptureError, match="capture_capability_invalid"):
            authorize_local_workspace(
                service.store,
                config,
                other,
                local_only_acknowledged=True,
            )
        sources, total = service.capture.list_sources()
    assert total == 1
    assert sources[0].id == first["id"]
    assert sources[0].account_fingerprint == first["account_fingerprint"]


def test_enable_and_foreground_run_admit_structural_facts_and_no_fact(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    authorized = _authorize(config, workspace)
    source_id = str(authorized["id"])
    with CoreService(config) as service:
        assert LOCAL_GIT_WORKSPACE_PROVIDER in service.capture.adapters
        enabled = service.capture.enable(source_id)
        result = service.capture.run(source_id)
        truth = service.store.list_memory_truth()
        search = service.retrieval.search(
            SearchRequest(query="workspace item", scopes=["workspace.structure"], limit=10)
        )
        with service.store.connect() as connection:
            candidate_count = connection.execute(
                "SELECT COUNT(*) FROM context_candidates WHERE capture_source_id=?",
                (source_id,),
            ).fetchone()[0]
            record_count = connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
            no_fact = connection.execute(
                "SELECT application_receipt FROM capture_events "
                "WHERE source_id=? AND json_extract(normalized_payload_json,'$.relative_path')=?",
                (source_id, _NO_FACT_RELATIVE_PATH),
            ).fetchone()
    assert enabled.lifecycle_state == "enabled"
    assert result.status == "completed"
    assert result.applied_events == 5
    assert candidate_count == 4
    assert record_count == 4
    assert no_fact is not None
    assert no_fact["application_receipt"] == "registered-source-no-fact"
    current = [item for item in truth.items if item.status == MemoryTruthStatus.CURRENT]
    assert len(current) == 4
    expected = set(REGISTERED_SOURCE_FACT_SENTENCES.values())
    assert {item.record.content for item in current} <= expected
    assert search.total == 4
    assert {item.content for item in search.items} <= expected
    _assert_no_root_leak(
        {
            "run": result.model_dump(),
            "truth": truth.model_dump(mode="json"),
            "search": search.model_dump(mode="json"),
        },
        workspace,
        config.data_dir,
    )


def test_restart_rebuilds_adapter_identity_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    authorized = _authorize(config, workspace)
    source_id = str(authorized["id"])
    with CoreService(config) as first:
        adapter = first.capture.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
        assert isinstance(adapter, LocalGitWorkspaceCaptureProviderAdapter)
        identity = adapter.source_identity
        first.capture.enable(source_id)
        first_run = first.capture.run(source_id)
        with first.store.connect() as connection:
            first_ids = [
                str(row["id"])
                for row in connection.execute("SELECT id FROM context_records ORDER BY id")
            ]
    assert first_run.status == "completed"
    assert identity == authorized["account_fingerprint"]
    with CoreService(config) as restarted:
        adapter = restarted.capture.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
        assert isinstance(adapter, LocalGitWorkspaceCaptureProviderAdapter)
        assert adapter.source_identity == identity
        replay = restarted.capture.run(source_id)
        with restarted.store.connect() as connection:
            replay_ids = [
                str(row["id"])
                for row in connection.execute("SELECT id FROM context_records ORDER BY id")
            ]
            event_count = connection.execute("SELECT COUNT(*) FROM capture_events").fetchone()[0]
    assert replay.status == "completed"
    assert replay.applied_events == 0
    assert replay.duplicate_events == 0
    assert replay_ids == first_ids
    assert event_count == 5


def test_cli_and_core_composition_parity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    authorized = _authorize(config, workspace)
    with CoreService(config) as core:
        cli_store = CoreStore(config.database_path)
        cli_store.migrate()
        try:
            cli_coordinator = compose_capture_coordinator(cli_store, config)
            core_adapter = core.capture.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
            cli_adapter = cli_coordinator.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
            assert isinstance(core_adapter, LocalGitWorkspaceCaptureProviderAdapter)
            assert isinstance(cli_adapter, LocalGitWorkspaceCaptureProviderAdapter)
            assert core_adapter.source_identity == cli_adapter.source_identity
            assert core_adapter.source_identity == authorized["account_fingerprint"]
            assert type(core.capture.sink) is RegisteredSourceCaptureApplicationSink
            assert type(cli_coordinator.sink) is RegisteredSourceCaptureApplicationSink
        finally:
            cli_store.close()


def test_admin_run_uses_the_same_composed_adapter_and_sink(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core", require_auth=True)
    workspace = _workspace(tmp_path)
    authorized = _authorize(config, workspace)
    source_id = str(authorized["id"])
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="capture-admin", scopes=["admin"], auto_approve=False)
        )
        assert principal.id
        app = create_app(config, service=service)
        with TestClient(app) as client:
            enabled = client.post(
                f"/v1/admin/capture/sources/{source_id}/enable",
                headers={"Authorization": f"Bearer {token}"},
            )
            ran = client.post(
                f"/v1/admin/capture/sources/{source_id}/run",
                headers={"Authorization": f"Bearer {token}"},
            )
            status = client.get(
                f"/v1/admin/capture/sources/{source_id}/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        with service.store.connect() as connection:
            record_count = connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
    assert enabled.status_code == 200
    assert enabled.json()["lifecycle_state"] == "enabled"
    assert ran.status_code == 200
    body = ran.json()
    assert body["status"] == "completed"
    assert body["applied_events"] == 5
    assert record_count == 4
    _assert_no_root_leak(body, workspace, config.data_dir)
    _assert_no_root_leak(status.json(), workspace, config.data_dir)
    assert AUTHORIZATION_FILENAME not in json.dumps(body)
    assert AUTHORIZATION_FILENAME not in json.dumps(status.json())


def test_file_deletion_withdraws_exact_record_without_tombstone(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    authorized = _authorize(config, workspace)
    source_id = str(authorized["id"])
    with CoreService(config) as service:
        service.capture.enable(source_id)
        assert service.capture.run(source_id).status == "completed"
        target_id = _record_id_for_relative_path(service.store, source_id, "README.md")
        other_id = _record_id_for_relative_path(service.store, source_id, "src/app.py")
        (workspace / "README.md").unlink()
        deleted = service.capture.run(source_id)
        with pytest.raises(NotFoundError):
            service.store.get_record(target_id)
        remaining = service.store.get_record(other_id)
        with service.store.connect() as connection:
            tombstone = connection.execute(
                "SELECT 1 FROM deletion_tombstones WHERE record_id=?", (target_id,)
            ).fetchone()
            deleted_row = connection.execute(
                "SELECT deleted_at FROM context_records WHERE id=?", (target_id,)
            ).fetchone()
    assert deleted.status == "completed"
    assert remaining.id == other_id
    assert tombstone is None
    assert deleted_row is not None and deleted_row["deleted_at"] is not None


def test_correction_delete_and_purge_barriers_remain_core_authoritative(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    authorized = _authorize(config, workspace)
    source_id = str(authorized["id"])
    with CoreService(config) as service:
        service.capture.enable(source_id)
        assert service.capture.run(source_id).status == "completed"
        corrected_id = _record_id_for_relative_path(service.store, source_id, "README.md")
        deleted_id = _record_id_for_relative_path(service.store, source_id, "docs/decision.md")
        purged_id = _record_id_for_relative_path(service.store, source_id, "src/app.py")
        corrected = service.store.correct_record(
            corrected_id,
            content="User correction remains authoritative.",
            reason="runtime barrier fixture",
        )
        service.store.delete_record(deleted_id, reason="runtime ordinary delete")
        service.store.purge(
            "record",
            purged_id,
            confirmation=service.store.purge_confirmation_phrase("record", purged_id),
            compact=False,
        )
        (workspace / "README.md").write_text("# changed after correction\n", encoding="utf-8")
        (workspace / "docs" / "decision.md").write_text(
            "# changed after delete\n", encoding="utf-8"
        )
        (workspace / "src" / "app.py").write_text("def answer() -> str:\n    return 'later'\n")
        assert service.capture.run(source_id).status == "completed"
        assert service.store.get_record(corrected_id).content == corrected.content
        with pytest.raises(NotFoundError):
            service.store.get_record(deleted_id)
        with pytest.raises(NotFoundError):
            service.store.get_record(purged_id)
        with service.store.connect() as connection:
            assert (
                connection.execute(
                    "SELECT 1 FROM deletion_tombstones WHERE record_id=?", (deleted_id,)
                ).fetchone()
                is not None
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM context_records WHERE id=?", (purged_id,)
                ).fetchone()[0]
                == 0
            )


def test_sidecar_and_path_never_cross_public_projections_export_or_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    caplog.set_level(logging.DEBUG)
    authorized = _authorize(config, workspace)
    source_id = str(authorized["id"])
    with CoreService(config) as service:
        service.capture.enable(source_id)
        ran = service.capture.run(source_id)
        status = service.capture.status(source_id)
        truth = service.store.list_memory_truth().model_dump(mode="json")
        search = service.retrieval.search(
            SearchRequest(query="Python source", scopes=["workspace.structure"], limit=10)
        ).model_dump(mode="json")
        package = tmp_path / "portable.atcexp"
        manifest = create_export(
            service.store.database_path,
            package,
            "capture-runtime-export-passphrase",
            include_sources=True,
        )
    sidecar = authorization_path(config.data_dir)
    assert sidecar.is_file()
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert Path(sidecar_payload["canonical_root"]).resolve() == workspace
    decrypted = tmp_path / "portable.zip"
    from allthecontext import export as portable_export

    portable_export._decrypt_file(package, decrypted, "capture-runtime-export-passphrase")
    exported_text = ""
    with zipfile.ZipFile(decrypted) as archive:
        exported_text = "\n".join(
            archive.read(name).decode("utf-8", errors="replace") for name in archive.namelist()
        )
        assert AUTHORIZATION_FILENAME not in archive.namelist()
        assert all("capture_" not in name for name in archive.namelist())
    public = {
        "authorized": authorized,
        "run": ran.model_dump(),
        "status": status,
        "truth": truth,
        "search": search,
        "manifest": manifest,
        "export": exported_text,
        "logs": caplog.text,
        "stdout": capsys.readouterr().out,
    }
    _assert_no_root_leak(public, workspace)
    assert AUTHORIZATION_FILENAME not in json.dumps(status)
    assert AUTHORIZATION_FILENAME not in exported_text
    assert authorized["account_label"] == LOCAL_WORKSPACE_ACCOUNT_LABEL
    assert str(workspace) not in authorized["account_label"]


def test_cli_authorize_enable_and_run_match_core_composition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    _init_vault(config)
    authorized = _cli(
        [
            "atc",
            "capture",
            "authorize-workspace",
            "--data-dir",
            str(config.data_dir),
            "--root",
            str(workspace),
            "--local-only-acknowledged",
        ],
        capsys,
    )
    assert authorized["lifecycle_state"] == "disabled"
    _assert_no_root_leak(authorized, workspace, config.data_dir)
    enabled = _cli(
        [
            "atc",
            "capture",
            "enable",
            "--data-dir",
            str(config.data_dir),
            str(authorized["id"]),
        ],
        capsys,
    )
    assert enabled["lifecycle_state"] == "enabled"
    ran = _cli(
        [
            "atc",
            "capture",
            "run",
            "--data-dir",
            str(config.data_dir),
            str(authorized["id"]),
        ],
        capsys,
    )
    assert ran["status"] == "completed"
    assert ran["applied_events"] == 5
    _assert_no_root_leak(ran, workspace, config.data_dir)
    with CoreService(config) as service:
        adapter = service.capture.adapters[LOCAL_GIT_WORKSPACE_PROVIDER]
        assert isinstance(adapter, LocalGitWorkspaceCaptureProviderAdapter)
        assert adapter.source_identity == authorized["account_fingerprint"]
        with service.store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 4


def test_cli_authorize_requires_local_only_acknowledgement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    _init_vault(config)
    original = sys.argv
    try:
        sys.argv = [
            "atc",
            "capture",
            "authorize-workspace",
            "--data-dir",
            str(config.data_dir),
            "--root",
            str(workspace),
        ]
        with pytest.raises(SystemExit):
            cli.main()
    finally:
        sys.argv = original
    output = capsys.readouterr().out
    body = json.loads(output)
    assert body["ok"] is False
    assert body["error"]["message"] == "capture_local_only_required"
    _assert_no_root_leak(output, workspace, config.data_dir)
    assert not authorization_path(config.data_dir).exists()


def test_missing_non_directory_home_and_cwd_roots_fail_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _init_vault(config)
    missing = tmp_path / "missing-root"
    file_root = tmp_path / "not-a-directory.txt"
    file_root.write_text("nope\n", encoding="utf-8")
    with CoreService(config) as service:
        for root in (missing, file_root, Path("~"), Path("."), Path("workspace")):
            with pytest.raises(CaptureError, match="capture_authorization_unavailable"):
                authorize_local_workspace(
                    service.store,
                    config,
                    root,
                    local_only_acknowledged=True,
                )
        with pytest.raises(CaptureError, match="capture_authorization_unavailable"):
            canonical_workspace_root(Path("~"))
        with pytest.raises(CaptureError, match="capture_authorization_unavailable"):
            canonical_workspace_root(Path("."))
        with pytest.raises(CaptureError, match="capture_local_only_required"):
            authorize_local_workspace(
                service.store,
                config,
                _workspace(tmp_path),
                local_only_acknowledged=False,
            )
    assert not authorization_path(config.data_dir).exists()


def test_cli_rejects_relative_and_home_roots_without_leaking_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = cli.build_parser()
    for root in (".", "~", "workspace"):
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "capture",
                    "authorize-workspace",
                    "--data-dir",
                    str(tmp_path / "core"),
                    "--root",
                    root,
                    "--local-only-acknowledged",
                ]
            )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "workspace root must be an explicit absolute path" in combined
        for form in _path_leak_forms(tmp_path):
            assert form not in combined


def test_redirecting_and_reparse_roots_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    _init_vault(config)
    other = tmp_path / "other"
    other.mkdir()

    class RedirectingPath(type(Path())):
        def resolve(self, strict: bool = False) -> Path:  # type: ignore[override]
            del strict
            return other

    with (
        CoreService(config) as service,
        pytest.raises(CaptureError, match="capture_authorization_unavailable"),
    ):
        authorize_local_workspace(
            service.store,
            config,
            RedirectingPath(str(workspace)),
            local_only_acknowledged=True,
        )

    original_lstat = Path.lstat

    def fake_lstat(self: Path) -> Any:
        result = original_lstat(self)
        try:
            if self.resolve() != workspace.resolve():
                return result
        except OSError:
            return result
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o777,
            st_file_attributes=_REPARSE_POINT,
        )

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with CoreService(config) as service:
        with pytest.raises(CaptureError, match="capture_authorization_unavailable"):
            authorize_local_workspace(
                service.store,
                config,
                workspace,
                local_only_acknowledged=True,
            )
        assert service.store.status()["vault_id"]
        assert service.capture.adapters == {}
    assert not authorization_path(config.data_dir).exists()


def test_retargeted_sidecar_does_not_register_adapter(tmp_path: Path) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    other = create_sanitized_workspace(tmp_path / "other-root")
    authorized = _authorize(config, workspace)
    sidecar = json.loads(authorization_path(config.data_dir).read_text(encoding="utf-8"))
    sidecar["canonical_root"] = os.fspath(other.resolve())
    authorization_path(config.data_dir).write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with CoreService(config) as service:
        assert service.capture.adapters == {}
        service.capture.enable(str(authorized["id"]))
        result = service.capture.run(str(authorized["id"]))
        assert service.store.status()["vault_id"]
    assert result.status == "skipped"
    assert result.error_code == "capture_adapter_unavailable"

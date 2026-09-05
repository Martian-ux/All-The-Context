"""Content-free failure diagnostics for packaged first-run smoke."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from allthecontext.build_identity import make_build_identity
from allthecontext.credentials import DEVELOPMENT_FALLBACK_ENV, FALLBACK_CREDENTIAL_STORAGE
from allthecontext.windows_update_helper import HelperError
from filelock import FileLock

ROOT = Path(__file__).resolve().parents[2]


def _load_smoke_module():
    path = ROOT / "scripts" / "smoke_packaged_first_run.py"
    spec = importlib.util.spec_from_file_location("smoke_packaged_first_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # scripts/ imports sibling smoke_desktop_artifact
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    package_src = str(ROOT / "packages" / "allthecontext" / "src")
    if package_src not in sys.path:
        sys.path.insert(0, package_src)
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()

TOKEN_CANARY = "atc-canary-token-NEVER-LOG-9f3c2b1a"
TICKET_CANARY = "live-browser-ticket-canary-deadbeef"
CLIENT_CANARY = "11111111-2222-4333-a444-555555555555"
PATH_CANARY = "C:" + r"\Users\canary\AppData\Local\ATC\secret"
DASHBOARD_CANARY = (
    f"http://127.0.0.1:18765/v1/browser/connect?ticket={TICKET_CANARY}&atc_token={TOKEN_CANARY}"
)
RAW_STATEMENT = "User said their password is hunter2-never-store"


def test_stop_core_waits_for_process_lock_after_health_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lock_path = data_dir / "core.lock"
    lock_ready = threading.Event()
    release = threading.Event()
    released = threading.Event()

    def hold_core_lock() -> None:
        with FileLock(str(lock_path), timeout=1):
            lock_ready.set()
            release.wait(timeout=2)
        released.set()

    holder = threading.Thread(target=hold_core_lock)
    holder.start()
    assert lock_ready.wait(timeout=1)
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(data_dir))

    monkeypatch.setattr(
        smoke,
        "api_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("Core is shutting down")),
    )
    monkeypatch.setattr(
        smoke,
        "_read_http_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("health is unavailable")),
    )
    timer = threading.Timer(0.08, release.set)
    timer.start()
    smoke.stop_core("http://127.0.0.1:7337", "token")
    assert released.is_set()

    timer.join(timeout=1)
    holder.join(timeout=1)
    assert not holder.is_alive()


def test_packaged_transaction_scopes_disposable_helper_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "packaged"
    data_dir = work / "data"
    install_dir = work / "installed"
    updates = data_dir / "updates"
    data_dir.mkdir(parents=True)
    install_dir.mkdir()
    updates.mkdir()
    installed_app = install_dir / "AllTheContext.exe"
    for name, content in (
        ("AllTheContext.exe", b"installed app"),
        ("AllTheContextMCP.exe", b"installed mcp"),
        ("AllTheContextRecovery.exe", b"installed recovery"),
        ("AllTheContextUpdater.exe", b"installed updater"),
    ):
        (install_dir / name).write_bytes(content)
    release_app = work / "AllTheContextSetup.exe"
    release_app.write_bytes(b"replacement app")
    database = data_dir / "core.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE facts(value TEXT NOT NULL)")
        connection.commit()
    finally:
        connection.close()
    (updates / "state.json").write_text(
        json.dumps(
            {
                "phase": "idle",
                "current_version": smoke.__version__,
                "offered_version": None,
                "mandatory": False,
                "release_notes_url": None,
                "downloaded_path": None,
                "backup_path": None,
                "last_checked_at": None,
                "last_error": None,
                "operation_id": None,
                "transaction_path": None,
                "recovery_attempts": 0,
                "manifest_identity": None,
                "handoff_identity": None,
                "pending_handoff_identity": None,
                "completed_handoff_identity": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("ATC_INSTALL_DIR", raising=False)
    monkeypatch.delenv("ATC_CORE_DATA_DIR", raising=False)
    helper_authority = {
        "ATC_CORE_DATA_DIR": str(data_dir),
        "ATC_INSTALL_DIR": str(install_dir),
        DEVELOPMENT_FALLBACK_ENV: "1",
    }

    assert "ATC_INSTALL_DIR" not in os.environ
    with smoke._temporary_environment(helper_authority):
        assert os.environ["ATC_INSTALL_DIR"] == str(install_dir)
        helper, journal_path = smoke.prepare_packaged_update_transaction(
            data_dir=data_dir,
            installed_app=installed_app,
            release_app=release_app,
            operation_id="f" * 24,
            core_port=7337,
            target_version=smoke.__version__,
            packaged_identity=make_build_identity(
                version=smoke.__version__,
                platform_name="windows",
                architecture="x86_64",
                source_commit="c" * 40,
            ),
        )
        journal = smoke.UpdateJournal.load(journal_path)
        assert Path(journal.helper_path) == helper
        assert Path(journal.application_path) == installed_app
        assert journal.current_source_commit == "c" * 40
        assert journal.target_source_commit == "c" * 40
        persisted = json.loads((updates / "state.json").read_text(encoding="utf-8"))
        assert persisted["current_source_commit"] == "c" * 40
        assert persisted["offered_source_commit"] == "c" * 40
        component = json.loads(
            (journal_path.parent / smoke.MANIFEST_FILE_NAME).read_text(encoding="utf-8")
        )
        assert component["source_commit"] == "c" * 40
        journal.application_path = str(work / "outside" / "AllTheContext.exe")
        with pytest.raises(HelperError, match="application_state_untrusted"):
            journal.validate(journal_path, boundary_code="application_state_untrusted")
    assert "ATC_INSTALL_DIR" not in os.environ
    assert "ATC_CORE_DATA_DIR" not in os.environ

    with pytest.raises(RuntimeError), smoke._temporary_environment(helper_authority):
        raise RuntimeError("scope test")
    assert "ATC_INSTALL_DIR" not in os.environ
    assert "ATC_CORE_DATA_DIR" not in os.environ


def test_read_http_response_rejects_oversized_content_without_logging_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = b"x" * (smoke._MAX_SMOKE_RESPONSE_BYTES + 1)
    real_client = smoke.httpx.Client

    def fake_client(*args: object, **kwargs: object) -> object:
        def handler(_request: object) -> object:
            return smoke.httpx.Response(200, content=oversized)

        kwargs["transport"] = smoke.httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(smoke.httpx, "Client", fake_client)

    with pytest.raises(RuntimeError) as error:
        smoke._read_http_response("http://127.0.0.1:1/oversized")

    assert str(error.value) == smoke._SMOKE_RESPONSE_LIMIT_ERROR
    assert str(oversized[:32], "utf-8") not in str(error.value)


def test_packaged_smoke_parent_override_must_be_absolute_and_external(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    assert smoke.packaged_smoke_parent(root=source, environ={}) == source / "tmp"
    with pytest.raises(SystemExit, match="absolute path"):
        smoke.packaged_smoke_parent(
            root=source,
            environ={"ATC_PACKAGED_SMOKE_PARENT": "relative"},
        )
    with pytest.raises(SystemExit, match="outside the source"):
        smoke.packaged_smoke_parent(
            root=source,
            environ={"ATC_PACKAGED_SMOKE_PARENT": str(source / "owned")},
        )
    external = tmp_path / "external"
    assert (
        smoke.packaged_smoke_parent(
            root=source,
            environ={"ATC_PACKAGED_SMOKE_PARENT": str(external)},
        )
        == external.resolve()
    )


def test_browser_session_reads_escaped_handoff_data_attribute() -> None:
    browser_session = 'opaque-session-"&<canary>'
    handoff_html = (
        '<body data-browser-token="opaque-session-&quot;&amp;&lt;canary&gt;">'
        '<script nonce="fixed">sessionStorage.setItem('
        "handoff.dataset.storageKey,handoff.dataset.browserToken);"
        "</script></body>"
    )

    assert smoke.browser_session_from_handoff_html(handoff_html) == browser_session
    assert smoke.browser_session_from_handoff_html("<body></body>") is None


def test_project_setup_report_strips_secrets_and_sensitive_fields() -> None:
    projected = smoke.project_setup_report_for_diagnostics(
        {
            "setup": "passed",
            "credential_storage": FALLBACK_CREDENTIAL_STORAGE,
            "dashboard_url": DASHBOARD_CANARY,
            "client_id": CLIENT_CANARY,
            "vault_id": CLIENT_CANARY,
            "log_path": PATH_CANARY,
            "core_url": "http://127.0.0.1:18765",
            "warnings": [f"token={TOKEN_CANARY}", RAW_STATEMENT],
            "token": TOKEN_CANARY,
        }
    )

    serialized = json.dumps(projected)
    assert projected["parseable"] is True
    assert projected["setup"] == "passed"
    assert projected["credential_storage"] == FALLBACK_CREDENTIAL_STORAGE
    assert projected["sensitive_fields_present"]["dashboard_url"] is True
    assert projected["sensitive_fields_present"]["client_id"] is True
    assert "dashboard_url" not in projected
    assert "client_id" not in projected
    assert "warnings" not in projected
    for canary in (TOKEN_CANARY, TICKET_CANARY, CLIENT_CANARY, PATH_CANARY, RAW_STATEMENT):
        assert canary not in serialized


def test_project_failed_setup_report_redacts_error_canaries() -> None:
    projected = smoke.project_setup_report_for_diagnostics(
        {
            "setup": "failed",
            "error_type": "RuntimeError",
            "error_code": "credential_store_unavailable",
            "setup_stage": "perform_setup",
            "error": (
                f"token={TOKEN_CANARY}; {DASHBOARD_CANARY}; "
                f"client={CLIENT_CANARY}; path={PATH_CANARY}; {RAW_STATEMENT}"
            ),
            "diagnostics_path": PATH_CANARY,
        }
    )
    serialized = json.dumps(projected)
    assert projected["setup"] == "failed"
    assert projected["error_type"] == "RuntimeError"
    assert projected["error_code"] == "credential_store_unavailable"
    assert projected["setup_stage"] == "perform_setup"
    assert "error" not in projected
    for canary in (TOKEN_CANARY, TICKET_CANARY, CLIENT_CANARY, PATH_CANARY):
        assert canary not in serialized


def test_failure_summary_never_embeds_raw_streams_or_absolute_work_paths(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "setup-report.json").write_text(
        json.dumps(
            {
                "setup": "passed",
                "dashboard_url": DASHBOARD_CANARY,
                "client_id": CLIENT_CANARY,
                "credential_storage": FALLBACK_CREDENTIAL_STORAGE,
            }
        ),
        encoding="utf-8",
    )
    (work / "data").mkdir()
    (work / "data" / "credentials.development.json").write_text(
        json.dumps({f"client:{CLIENT_CANARY}": TOKEN_CANARY}),
        encoding="utf-8",
    )
    (work / "codex").mkdir()
    (work / "codex" / "config.toml").write_text(
        f'ATC_CLIENT_TOKEN = "{TOKEN_CANARY}"\n',
        encoding="utf-8",
    )

    summary = smoke.build_failure_diagnostic_summary(
        phase="headless first-run setup",
        return_code=1,
        work=work,
        report_path=work / "setup-report.json",
        stdout_present=True,
        stderr_present=True,
        detail=f"failed with token={TOKEN_CANARY} at {PATH_CANARY}",
    )
    diagnostics_root = tmp_path / "diagnostics"
    written = smoke.write_failure_diagnostic_summary(summary, diagnostics_root=diagnostics_root)
    body = written.read_text(encoding="utf-8")
    printed = json.dumps(summary)

    assert summary["artifacts_present"]["setup-report.json"] is True
    assert summary["artifacts_present"]["data/credentials.development.json"] is True
    assert summary["stdout_present"] is True
    assert summary["stderr_present"] is True
    assert summary["detail_code"] == "diagnostic_failure"
    assert "stdout" not in summary
    assert "stderr" not in summary
    assert summary["setup_report"]["setup"] == "passed"
    assert "dashboard_url" not in summary["setup_report"]
    for canary in (
        TOKEN_CANARY,
        TICKET_CANARY,
        CLIENT_CANARY,
        PATH_CANARY,
        DASHBOARD_CANARY,
        str(work),
        RAW_STATEMENT,
    ):
        assert canary not in body
        assert canary not in printed


def test_emit_failure_diagnostics_prints_closed_schema_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "setup-report.json").write_text(
        json.dumps(
            {
                "setup": "failed",
                "error_type": "RuntimeError",
                "error_code": "setup_io_error",
                "setup_stage": "prepare_installed_runtime",
                "error": f"token={TOKEN_CANARY}",
                "dashboard_url": DASHBOARD_CANARY,
            }
        ),
        encoding="utf-8",
    )
    diagnostics_root = tmp_path / "diagnostics"
    path = smoke.emit_failure_diagnostics(
        phase="headless first-run setup",
        return_code=1,
        work=work,
        diagnostics_root=diagnostics_root,
        report_path=work / "setup-report.json",
        stdout_present=True,
        stderr_present=False,
        detail=f"boom token={TOKEN_CANARY}",
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["packaged_first_run_failure"] is True
    assert payload["diagnostics_file"] == path.name
    assert payload["setup_error_code"] == "setup_io_error"
    assert payload["setup_stage"] == "prepare_installed_runtime"
    assert payload["stdout_present"] is True
    assert TOKEN_CANARY not in out
    assert DASHBOARD_CANARY not in out
    assert TOKEN_CANARY not in path.read_text(encoding="utf-8")


def test_project_setup_report_discards_unknown_setup_codes_and_stages() -> None:
    projected = smoke.project_setup_report_for_diagnostics(
        {
            "setup": "failed",
            "error_code": "user_context_leak",
            "setup_stage": "unexpected-stage-with-secret-token",
        }
    )
    assert projected["setup"] == "failed"
    assert "error_code" not in projected
    assert "setup_stage" not in projected
    assert "user_context_leak" not in json.dumps(projected)
    assert "unexpected-stage-with-secret-token" not in json.dumps(projected)


def test_remove_work_tree_deletes_credential_bearing_tree(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    secret = work / "data" / "credentials.development.json"
    secret.parent.mkdir(parents=True)
    secret.write_text(json.dumps({"client:x": TOKEN_CANARY}), encoding="utf-8")
    smoke.remove_work_tree(work)
    assert not work.exists()


def test_packaged_rollback_smoke_reenters_exact_retry_state_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "AllTheContextUpdater.exe"
    journal = tmp_path / "journal.json"
    helper.write_bytes(b"helper")
    journal.write_text("{}", encoding="utf-8")
    calls = 0

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            journal.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "phase": "rolling_back",
                        "last_error_code": "rollback_retry_required",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 3)
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "phase": "rolled_back",
                    "last_error_code": "health_check_failed",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    smoke.run_packaged_rollback_smoke(
        helper=helper,
        journal=journal,
        environment={"ATC_CORE_DATA_DIR": str(tmp_path)},
    )

    assert calls == 2
    assert json.loads(journal.read_text(encoding="utf-8"))["phase"] == "rolled_back"


def test_packaged_rollback_smoke_rejects_unexpected_code_three_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "AllTheContextUpdater.exe"
    journal = tmp_path / "journal.json"
    helper.write_bytes(b"helper")
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "rolling_back",
                "last_error_code": "unexpected",
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 3)

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="first=3; retry=None"):
        smoke.run_packaged_rollback_smoke(
            helper=helper,
            journal=journal,
            environment={"ATC_CORE_DATA_DIR": str(tmp_path)},
        )

    assert calls == 1


def test_packaged_rollback_smoke_fails_after_one_unsuccessful_reentry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "AllTheContextUpdater.exe"
    journal = tmp_path / "journal.json"
    helper.write_bytes(b"helper")
    journal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "rolling_back",
                "last_error_code": "rollback_retry_required",
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 3)

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="first=3; retry=3"):
        smoke.run_packaged_rollback_smoke(
            helper=helper,
            journal=journal,
            environment={"ATC_CORE_DATA_DIR": str(tmp_path)},
        )

    assert calls == 2


def test_cleanup_recovers_token_then_scrubs_sensitive_state_first(tmp_path: Path) -> None:
    work = tmp_path / "work"
    data = work / "data"
    codex = work / "codex"
    config = work / "config"
    data.mkdir(parents=True)
    codex.mkdir()
    config.mkdir()
    client_id = "11111111-2222-4333-a444-555555555555"
    (work / "setup-report.json").write_text(
        json.dumps({"client_id": client_id, "dashboard_url": DASHBOARD_CANARY}),
        encoding="utf-8",
    )
    (data / "credentials.development.json").write_text(
        json.dumps({f"client:{client_id}": TOKEN_CANARY}),
        encoding="utf-8",
    )
    (data / "core.sqlite3").write_bytes(b"fictional vault")
    (codex / "config.toml").write_text(f'token = "{TOKEN_CANARY}"\n', encoding="utf-8")
    (config / "autostart.desktop").write_text("fictional\n", encoding="utf-8")
    (work / "mcp-stderr.log").write_text(RAW_STATEMENT, encoding="utf-8")
    sentinel = work / "installed" / "AllTheContextSetup.exe"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"MZ")

    assert smoke.recover_disposable_admin_token(work) == TOKEN_CANARY
    smoke.scrub_sensitive_work_tree(work)

    assert not data.exists()
    assert not codex.exists()
    assert not config.exists()
    assert not (work / "setup-report.json").exists()
    assert not (work / "mcp-stderr.log").exists()
    assert sentinel.is_file()


def test_source_contract_never_retains_full_work_tree() -> None:
    text = (ROOT / "scripts" / "smoke_packaged_first_run.py").read_text(encoding="utf-8")
    assert "retain_work_on_failure" not in text
    assert "kept work directory for diagnosis" not in text
    assert "build_failure_diagnostic_summary" in text
    assert "recover_disposable_admin_token" in text
    assert "scrub_sensitive_work_tree" in text
    assert "remove_work_tree" in text
    assert "searched.is_error" in text
    assert "searched.structured_content" in text
    assert "status.isError" not in text
    assert "status.structuredContent" not in text
    assert "raise SystemExit(str(exc))" not in text
    assert "packaged-first-run-diagnostics" in text
    assert 'context/status", token' not in text
    assert "wait_for_core(base_url, token)" not in text
    # Must not print raw subprocess streams.
    assert "completed.stdout" not in text or "stdout_present" in text
    assert 'print(f"{label} stdout' not in text
    assert 'print(f"{label} stderr' not in text


def test_packaged_uninstall_observation_exceeds_product_cleanup_budget() -> None:
    assert smoke.WINDOWS_INSTALL_REMOVAL_TIMEOUT_SECONDS == 30.0
    assert smoke.WINDOWS_INSTALL_REMOVAL_OBSERVATION_SECONDS == 35.0


def test_packaged_mcp_surface_is_exactly_read_only() -> None:
    expected = {
        "bootstrap_context",
        "codex_user_prompt_submit_read",
        "get_context_item",
        "search_context",
    }

    smoke.validate_packaged_mcp_surface("codex_read", expected)

    with pytest.raises(RuntimeError, match="read-only profile"):
        smoke.validate_packaged_mcp_surface("", expected)
    with pytest.raises(RuntimeError, match=r"unexpected=\['propose_memory'\]"):
        smoke.validate_packaged_mcp_surface("codex_read", expected | {"propose_memory"})
    with pytest.raises(RuntimeError, match=r"missing=\['search_context'\]"):
        smoke.validate_packaged_mcp_surface("codex_read", expected - {"search_context"})

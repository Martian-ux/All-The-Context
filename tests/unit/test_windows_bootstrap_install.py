from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext import windows_bootstrap_install as bootstrap


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[dict[str, Path], Path, Path]:
    source_root = tmp_path / "bundle"
    source_root.mkdir()
    values = {
        "main": b"new-main",
        "mcp": b"new-mcp",
        "recovery": b"new-recovery",
        "updater": b"new-updater",
    }
    sources = {}
    for role, value in values.items():
        path = source_root / f"{role}.source"
        path.write_bytes(value)
        sources[role] = path
    return sources, tmp_path / "install", tmp_path / "journal"


def _install(
    sources: dict[str, Path],
    install_root: Path,
    journal_root: Path,
    *,
    core_was_running: bool = False,
    stop_core=None,
    restart_core=None,
) -> bootstrap.BootstrapInstallResult:
    return bootstrap.install_windows_components(
        sources,
        install_root,
        core_was_running=core_was_running,
        stop_core=stop_core,
        restart_core=restart_core,
        journal_root=journal_root,
    )


def test_first_install_commits_exact_canonical_set_and_leaves_user_files(
    bundle: tuple[dict[str, Path], Path, Path],
) -> None:
    sources, install_root, journal_root = bundle
    install_root.mkdir()
    sentinel = install_root / "user-owned.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = _install(sources, install_root, journal_root)

    assert result.targets == bootstrap.canonical_targets(install_root)
    assert bootstrap.is_complete_install(sources, install_root)
    assert sorted(path.name for path in install_root.iterdir()) == [
        "AllTheContext.exe",
        "AllTheContextMCP.exe",
        "AllTheContextRecovery.exe",
        "AllTheContextUpdater.exe",
        "user-owned.txt",
    ]
    assert not (journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME).exists()
    assert list(journal_root.glob("????????????????????????")) == []
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_reinstall_is_idempotent_and_does_not_stop_core(
    bundle: tuple[dict[str, Path], Path, Path],
) -> None:
    sources, install_root, journal_root = bundle
    _install(sources, install_root, journal_root)
    stopped: list[bool] = []

    _install(sources, install_root, journal_root, stop_core=lambda: stopped.append(True))

    assert not stopped
    assert not (journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME).exists()


@pytest.mark.parametrize("role", ["main", "mcp", "recovery", "updater"])
def test_each_locked_canonical_component_rolls_back_without_mcp_sidecar(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    sources, install_root, journal_root = bundle
    install_root.mkdir()
    targets = bootstrap.canonical_targets(install_root)
    for target in targets.values():
        target.write_bytes(b"old-" + target.name.encode())
    sentinel = install_root / "keep.db"
    sentinel.write_bytes(b"vault-shaped unrelated file")
    original_replace = Path.replace

    def locked_cutover(path: Path, destination: Path) -> Path:
        if path.parent.name == "staged" and destination == targets[role]:
            raise PermissionError("canonical component is locked")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", locked_cutover)
    with pytest.raises(bootstrap.BootstrapInstallError):
        _install(sources, install_root, journal_root)

    assert all(target.read_bytes().startswith(b"old-") for target in targets.values())
    assert not list(install_root.glob("AllTheContextMCP-*.exe"))
    assert sentinel.read_bytes() == b"vault-shaped unrelated file"


def test_first_install_partial_failure_restores_absent_preimages(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    vault = install_root.parent / "vault"
    vault.mkdir()
    database = vault / "core.sqlite3"
    database.write_bytes(b"do not delete")
    original_copy = bootstrap._copy_verified

    def fail_second_source(source: Path, target: Path, expected, **kwargs):
        if source == sources["mcp"]:
            raise bootstrap.BootstrapInstallError("disk_full")
        return original_copy(source, target, expected, **kwargs)

    monkeypatch.setattr(bootstrap, "_copy_verified", fail_second_source)
    with pytest.raises(bootstrap.BootstrapInstallError, match="disk_full"):
        _install(sources, install_root, journal_root)

    assert not install_root.exists() or not list(install_root.iterdir())
    assert not (journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME).exists()
    assert database.read_bytes() == b"do not delete"


def test_existing_install_rollback_restores_all_four_components(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    install_root.mkdir()
    targets = bootstrap.canonical_targets(install_root)
    old = {role: f"old-{role}".encode() for role in targets}
    for role, target in targets.items():
        target.write_bytes(old[role])
    original_replace = Path.replace

    def fail_mcp_cutover(path: Path, destination: Path) -> Path:
        if path.parent.name == "staged" and destination == targets["mcp"]:
            raise OSError("copy interrupted")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_mcp_cutover)
    with pytest.raises(bootstrap.BootstrapInstallError):
        _install(sources, install_root, journal_root)

    assert {role: target.read_bytes() for role, target in targets.items()} == old
    assert not (journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME).exists()


def test_source_mutation_after_staging_is_rejected_before_target_cutover(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    original_copy = bootstrap._copy_verified
    mutated = False

    def mutate_after_main_stage(source: Path, target: Path, expected, **kwargs):
        nonlocal mutated
        result = original_copy(source, target, expected, **kwargs)
        if source == sources["main"] and target.parent.name == "staged" and not mutated:
            source.write_bytes(b"tampered source")
            mutated = True
        return result

    monkeypatch.setattr(bootstrap, "_copy_verified", mutate_after_main_stage)
    with pytest.raises(bootstrap.BootstrapInstallError, match="source_changed"):
        _install(sources, install_root, journal_root)

    assert not install_root.exists() or not list(install_root.iterdir())
    assert (journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME).exists() is False


def test_interrupted_cutover_is_recovered_on_next_start(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    targets = bootstrap.canonical_targets(install_root)
    original_replace = Path.replace
    interrupted = False

    def interrupt_after_main_replace(path: Path, destination: Path) -> Path:
        nonlocal interrupted
        result = original_replace(path, destination)
        if path.parent.name == "staged" and destination == targets["main"] and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(Path, "replace", interrupt_after_main_replace)
    with pytest.raises(KeyboardInterrupt):
        _install(sources, install_root, journal_root)
    journal_path = journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME
    assert journal_path.exists()

    monkeypatch.setattr(Path, "replace", original_replace)
    _install(sources, install_root, journal_root)

    assert bootstrap.is_complete_install(sources, install_root)
    assert not journal_path.exists()


def test_committed_set_retries_cleanup_without_rolling_back(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    original_cleanup = bootstrap._remove_transaction_tree
    failed = False

    def fail_cleanup(journal: bootstrap.BootstrapInstallJournal) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("cleanup interrupted")
        original_cleanup(journal)

    monkeypatch.setattr(bootstrap, "_remove_transaction_tree", fail_cleanup)
    with pytest.raises(bootstrap.BootstrapInstallError, match="retry_required"):
        _install(sources, install_root, journal_root)

    journal_path = journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "committed"
    assert bootstrap.is_complete_install(sources, install_root)

    _install(sources, install_root, journal_root)
    assert not journal_path.exists()


def test_target_substitution_preserves_retry_required_evidence(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    targets = bootstrap.canonical_targets(install_root)
    original_replace = Path.replace

    def substitute_main(path: Path, destination: Path) -> Path:
        if path.parent.name == "staged" and destination == targets["main"]:
            destination.unlink(missing_ok=True)
            destination.write_bytes(b"unexpected target")
            raise OSError("target changed during replacement")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", substitute_main)
    with pytest.raises(bootstrap.BootstrapInstallError, match="retry_required"):
        _install(sources, install_root, journal_root)

    journal_path = journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME
    assert journal_path.exists()
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "retry_required"
    assert targets["main"].read_bytes() == b"unexpected target"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_linked_target_is_refused_without_external_deletion(
    bundle: tuple[dict[str, Path], Path, Path],
    kind: str,
) -> None:
    sources, install_root, journal_root = bundle
    install_root.mkdir()
    targets = bootstrap.canonical_targets(install_root)
    external = install_root.parent / "external"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    target = targets["mcp"]
    try:
        if kind == "symlink":
            target.symlink_to(sentinel)
        else:
            target.hardlink_to(sentinel)
    except OSError:
        pytest.skip(f"this account cannot create {kind}")

    with pytest.raises(bootstrap.BootstrapInstallError):
        _install(sources, install_root, journal_root)

    assert sentinel.read_text(encoding="utf-8") == "must survive"
    assert target.is_symlink() if kind == "symlink" else target.exists()


def test_core_is_stopped_before_cutover_and_restarted_only_after_rollback(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    install_root.mkdir()
    targets = bootstrap.canonical_targets(install_root)
    for target in targets.values():
        target.write_bytes(b"prior")
    events: list[str] = []
    original_replace = Path.replace

    def fail_after_main(path: Path, destination: Path) -> Path:
        if path.parent.name == "staged" and destination == targets["main"]:
            events.append("replace")
            raise OSError("injected failure")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_after_main)
    with pytest.raises(bootstrap.BootstrapInstallError):
        _install(
            sources,
            install_root,
            journal_root,
            core_was_running=True,
            stop_core=lambda: events.append("stop"),
            restart_core=lambda: events.append("restart"),
        )

    assert events == ["stop", "replace", "restart"]
    assert all(target.read_bytes() == b"prior" for target in targets.values())


def test_failed_core_restart_keeps_retry_state_and_does_not_launch_uncertain_set(
    bundle: tuple[dict[str, Path], Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, install_root, journal_root = bundle
    install_root.mkdir()
    targets = bootstrap.canonical_targets(install_root)
    for target in targets.values():
        target.write_bytes(b"prior")

    def fail_restart() -> None:
        raise OSError("restart unavailable")

    original_replace = Path.replace

    def fail_cutover(path: Path, destination: Path) -> Path:
        if path.parent.name == "staged" and destination == targets["main"]:
            raise OSError("cutover unavailable")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_cutover)

    with pytest.raises(bootstrap.BootstrapInstallError):
        _install(
            sources,
            install_root,
            journal_root,
            core_was_running=True,
            stop_core=lambda: None,
            restart_core=fail_restart,
        )

    journal_path = journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME
    assert json.loads(journal_path.read_text(encoding="utf-8"))["phase"] == "retry_required"
    assert all(target.read_bytes() == b"prior" for target in targets.values())


def test_malformed_journal_is_preserved_and_cannot_redirect_targets(
    bundle: tuple[dict[str, Path], Path, Path],
) -> None:
    sources, install_root, journal_root = bundle
    journal_root.mkdir()
    journal_path = journal_root / bootstrap.BOOTSTRAP_JOURNAL_NAME
    journal_path.write_text('{"install_root":"C:\\\\outside"}', encoding="utf-8")

    with pytest.raises(bootstrap.BootstrapInstallError, match="journal_invalid"):
        _install(sources, install_root, journal_root)

    assert journal_path.read_text(encoding="utf-8") == '{"install_root":"C:\\\\outside"}'
    assert not any(
        target.exists() or target.is_symlink()
        for target in bootstrap.canonical_targets(install_root).values()
    )


def test_reparse_install_root_is_refused_without_touching_external_data(
    bundle: tuple[dict[str, Path], Path, Path],
) -> None:
    sources, install_root, journal_root = bundle
    external = install_root.parent / "external-install"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    try:
        install_root.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create a directory reparse link")

    with pytest.raises(bootstrap.BootstrapInstallError):
        _install(sources, install_root, journal_root)

    assert sentinel.read_text(encoding="utf-8") == "must survive"

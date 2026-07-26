"""Cross-shell administration CLI for the local Core and optional Relay."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from allthecontext.client_config import repair_managed_runtime_bindings
from allthecontext.config import DEFAULT_MAX_IMPORT_BYTES, MAX_IMPORT_BYTES, CoreConfig
from allthecontext.credentials import (
    DEVELOPMENT_FALLBACK_ENV,
    DevelopmentFileCredentialStore,
    KeyringCredentialStore,
)
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import (
    AI_CLIENT_SCOPES,
    DESKTOP_CLIENT_NAME,
    authenticated_dashboard_url,
    ensure_client_access,
    launch_core,
    local_timezone,
    open_dashboard,
    recover_administrator_access,
)
from allthecontext.export import create_export, restore_export
from allthecontext.importers import ArchiveImportService
from allthecontext.models import (
    ApprovalRequest,
    ApprovalStatus,
    Availability,
    ClientCreate,
    ObservationDisposition,
    SearchRequest,
)
from allthecontext.retrieval import RetrievalEngine
from allthecontext.storage import CoreStore


def _config(args: argparse.Namespace) -> CoreConfig:
    data_dir = getattr(args, "data_dir", None)
    return CoreConfig.in_directory(Path(data_dir)) if data_dir else CoreConfig.default()


def _store(args: argparse.Namespace) -> CoreStore:
    config = _config(args)
    config.prepare()
    store = CoreStore(config.database_path)
    store.migrate()
    return store


def _dump(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _import_byte_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("import byte limit must be an integer") from error
    if not 1 <= limit <= MAX_IMPORT_BYTES:
        raise argparse.ArgumentTypeError(
            f"import byte limit must be between 1 and {MAX_IMPORT_BYTES}"
        )
    return limit


def _passphrase(args: argparse.Namespace) -> str:
    environment_name = getattr(args, "passphrase_env", "ATC_EXPORT_PASSPHRASE")
    value = os.environ.get(environment_name)
    if value:
        return value
    if not sys.stdin.isatty():
        raise RuntimeError(f"set {environment_name} when running without an interactive terminal")
    return getpass.getpass("Export passphrase: ")


def _render_mcp_config(
    config: CoreConfig,
    client_id: str,
    *,
    token: str | None = None,
    target: str | None = None,
) -> str:
    executable = str(Path(sys.executable).resolve())
    values: dict[str, str] = {
        "ATC_TARGET_URL": target or f"http://{config.host}:{config.port}",
        "ATC_CORE_DATA_DIR": str(config.data_dir),
        "ATC_CLIENT_ID": client_id,
    }
    if token:
        values["ATC_CLIENT_TOKEN"] = token
    env = ", ".join(f"{key} = {json.dumps(value)}" for key, value in values.items())
    return "\n".join(
        [
            "[mcp_servers.all_the_context]",
            f"command = {json.dumps(executable)}",
            'args = ["-m", "allthecontext.mcp_adapter"]',
            f"env = {{ {env} }}",
            "required = true",
        ]
    )


def _render_cli_command(command: str) -> str:
    executable_name = "atc.exe" if os.name == "nt" else "atc"
    executable = Path(sys.executable).resolve().with_name(executable_name)
    if os.name == "nt":
        return f'& "{executable}" {command}'
    return f"{shlex.quote(str(executable))} {command}"


def _persist_cli_client_credential(
    store: CoreStore,
    config: CoreConfig,
    client_id: str,
    token: str,
    *,
    insecure_development_file: bool,
) -> bool:
    """Persist a new CLI credential or revoke its principal before returning failure."""

    name = f"client:{client_id}"
    try:
        if insecure_development_file:
            DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json").set(
                name, token
            )
            return False
        credential_store = KeyringCredentialStore()
        credential_store.set(name, token)
        if credential_store.get(name) != token:
            raise RuntimeError("the operating-system credential did not round trip")
        return True
    except BaseException:
        store.revoke_client(client_id)
        with suppress(RuntimeError):
            KeyringCredentialStore().delete(name)
        with suppress(OSError, RuntimeError, ValueError):
            DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json").delete(
                name
            )
        raise


def _cmd_init(args: argparse.Namespace) -> None:
    if args.no_keyring:
        os.environ[DEVELOPMENT_FALLBACK_ENV] = "1"
    config = _config(args)
    config.prepare()
    store = CoreStore(config.database_path)
    vault_id = store.initialize_vault(args.name, args.timezone or local_timezone())
    if store.client_count() == 0:
        principal, token = store.create_client(
            ClientCreate(
                name=args.client_name,
                scopes=[
                    "admin",
                    "context:read",
                    "context:status",
                    "context:ingest",
                    "context:propose",
                    "*",
                ],
            )
        )
        stored_in_keyring = _persist_cli_client_credential(
            store,
            config,
            principal.id,
            token,
            insecure_development_file=args.no_keyring,
        )
        mcp_access = ensure_client_access(
            store,
            config,
            name="All The Context CLI MCP",
            scopes=AI_CLIENT_SCOPES,
        )
        mcp_config = _render_mcp_config(
            config,
            mcp_access.client_id,
            token=(
                None
                if mcp_access.credential_storage == "operating-system credential store"
                else mcp_access.token
            ),
        )
        result = {
            "initialized": True,
            "vault_id": vault_id,
            "data_dir": str(config.data_dir),
            "core_url": f"http://{config.host}:{config.port}",
            "client_id": principal.id,
            "client_token": token,
            "stored_in_os_keyring": stored_in_keyring,
            "credential_notice": "Shown once; MCP can load it from the OS keyring when stored.",
            "mcp_config": mcp_config,
            "next": (
                "Run the local Core and open its authenticated dashboard with: "
                f"{_render_cli_command('open-dashboard')}"
            ),
        }
    else:
        result = {
            "initialized": False,
            "vault_id": vault_id,
            "data_dir": str(config.data_dir),
            "message": "Existing vault retained; no new credential was created.",
        }
    _dump(result)
    if result.get("initialized") and not args.json_only:
        print("\n# Paste this block into your MCP client configuration")
        print(result["mcp_config"])
        print("\n# Then start the local Core")
        print(_render_cli_command("open-dashboard"))


def _cmd_open_dashboard(args: argparse.Namespace) -> None:
    config = _config(args)
    access = recover_administrator_access(config)
    if access is None:
        raise RuntimeError("No recoverable local administrator was found. Run 'atc init' first.")
    runtime = RuntimeCommand.current()
    repair_managed_runtime_bindings(runtime, config)
    launch_core(runtime, config)
    url = authenticated_dashboard_url(config, access.token)
    if args.print_only:
        print(url)
        return
    if not open_dashboard(url):
        raise RuntimeError(f"The browser could not be opened automatically. Open this link: {url}")


def _cmd_config_mcp(args: argparse.Namespace) -> None:
    config = _config(args)
    print(_render_mcp_config(config, args.client_id, token=args.token, target=args.target))


def _import_operations(args: argparse.Namespace) -> Any:
    from allthecontext.import_operations import ImportOperationService

    config = _config(args)
    store = _store(args)
    max_bytes = getattr(args, "max_bytes", None) or config.max_import_bytes
    imports = ArchiveImportService(store, max_bytes=max_bytes)
    return ImportOperationService(
        store,
        imports,
        data_dir=config.data_dir,
        max_bytes=max_bytes,
    )


def _cmd_import(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser().resolve()
    service = _import_operations(args)
    finished = service.import_path_via_operation(
        path,
        source_service=args.provider,
        provider=None if args.provider == "auto" else args.provider,
    )
    if finished.get("result"):
        _dump(finished["result"])
        return
    _dump(finished)


def _cmd_import_progress(args: argparse.Namespace) -> None:
    from allthecontext.storage import NotFoundError

    identifier = args.source_id
    operations = _import_operations(args)
    # Prefer operation id when it matches a durable import operation.
    try:
        _dump(operations.get_operation(identifier))
        return
    except NotFoundError:
        pass
    service = ArchiveImportService(_store(args))
    _dump(service.import_progress(identifier))


def _cmd_cancel_import(args: argparse.Namespace) -> None:
    from allthecontext.storage import NotFoundError

    identifier = args.source_id
    operations = _import_operations(args)
    try:
        _dump(operations.cancel_operation(identifier))
        return
    except NotFoundError:
        pass
    service = ArchiveImportService(_store(args))
    _dump(service.cancel_import(identifier))


def _cmd_reprocess_source(args: argparse.Namespace) -> None:
    from allthecontext.storage import NotFoundError

    identifier = args.source_id
    operations = _import_operations(args)
    try:
        operations.get_operation(identifier)
    except NotFoundError:
        service = ArchiveImportService(_store(args), max_bytes=args.max_bytes)
        _dump(service.reprocess_source(identifier))
        return
    _dump(operations.retry_operation(identifier))


def _cmd_import_boundary(args: argparse.Namespace) -> None:
    from allthecontext.boundary_canary import (
        BOUNDARY_CANARY_GENERATOR_VERSION,
        BOUNDARY_CANARY_SIZE_BYTES,
        checkpoint_offsets,
    )
    from allthecontext.import_boundary import expected_chunk_count, scale_profile
    from allthecontext.provider_shapes import provider_claim_manifest

    _dump(
        {
            "scale_profile": scale_profile(),
            "boundary_canary": {
                "generator_version": BOUNDARY_CANARY_GENERATOR_VERSION,
                "size_bytes": BOUNDARY_CANARY_SIZE_BYTES,
                "expected_chunk_count": expected_chunk_count(BOUNDARY_CANARY_SIZE_BYTES),
                "checkpoint_offsets": list(checkpoint_offsets(BOUNDARY_CANARY_SIZE_BYTES)),
                "exact_artifact_acceptance": "pending",
            },
            "provider_claims": provider_claim_manifest(),
        }
    )


def _cmd_observations(args: argparse.Namespace) -> None:
    disposition = ObservationDisposition(args.disposition) if args.disposition is not None else None
    items, total = _store(args).list_observations(
        disposition=disposition,
        limit=args.limit,
        offset=args.offset,
    )
    _dump({"items": [item.model_dump(mode="json") for item in items], "total": total})


def _cmd_candidates(args: argparse.Namespace) -> None:
    status = None if args.status == "all" else ApprovalStatus(args.status)
    items, total = _store(args).list_candidates(status=status, limit=args.limit, offset=args.offset)
    _dump({"items": [item.model_dump(mode="json") for item in items], "total": total})


def _cmd_approve(args: argparse.Namespace) -> None:
    request = ApprovalRequest(
        content=args.content,
        entity_key=args.entity_key,
        attribute_key=args.attribute_key,
        availability=Availability(args.availability) if args.availability else None,
        explicit_sensitive_replication=args.confirm_sensitive_replication,
        reason=args.reason,
    )
    _dump(_store(args).approve_candidate(args.candidate_id, request))


def _cmd_reject(args: argparse.Namespace) -> None:
    _dump(_store(args).reject_candidate(args.candidate_id, reason=args.reason))


def _cmd_search(args: argparse.Namespace) -> None:
    engine = RetrievalEngine(_store(args))
    request = SearchRequest(
        query=args.query,
        scopes=args.scope,
        kinds=args.kind,
        as_of=args.as_of,
        current_project=args.current_project,
        limit=args.limit,
    )
    result = (
        engine.diagnose_search(request, local_administrator=True)
        if args.explain
        else engine.search(request)
    )
    _dump(result)


def _cmd_availability(args: argparse.Namespace) -> None:
    _dump(
        _store(args).change_availability(
            args.record_id,
            Availability(args.availability),
            explicit_sensitive_replication=args.confirm_sensitive_replication,
        )
    )


def _cmd_correct(args: argparse.Namespace) -> None:
    _dump(
        _store(args).correct_record(
            args.record_id,
            content=args.content,
            reason=args.reason,
            entity_key=args.entity_key,
            attribute_key=args.attribute_key,
        )
    )


def _cmd_delete(args: argparse.Namespace) -> None:
    _dump(_store(args).delete_record(args.record_id, reason=args.reason))


def _cmd_restore_record(args: argparse.Namespace) -> None:
    _dump(
        _store(args).restore_record(
            args.record_id,
            version=args.version,
            reason=args.reason,
        )
    )


def _cmd_integrity_groups(args: argparse.Namespace) -> None:
    _dump(
        _store(args).list_integrity_groups(status=args.status, limit=args.limit, offset=args.offset)
    )


def _cmd_purge(args: argparse.Namespace) -> None:
    _dump(
        _store(args).purge(
            args.target_type,
            args.target_id,
            confirmation=args.confirmation,
            compact=not args.no_compact,
        )
    )


def _cmd_purge_resume(args: argparse.Namespace) -> None:
    _dump({"completed": _store(args).resume_purge_jobs(limit=args.limit)})


def _cmd_clients(args: argparse.Namespace) -> None:
    _dump({"items": _store(args).list_clients()})


def _cmd_client_add(args: argparse.Namespace) -> None:
    store = _store(args)
    config = _config(args)
    principal, token = store.create_client(
        ClientCreate(name=args.name, scopes=args.scope, auto_approve=args.auto_approve)
    )
    stored_in_keyring = _persist_cli_client_credential(
        store,
        config,
        principal.id,
        token,
        insecure_development_file=args.no_keyring,
    )
    _dump(
        {
            "client": {
                "id": principal.id,
                "name": principal.name,
                "scopes": sorted(principal.scopes),
            },
            "client_token": token,
            "stored_in_os_keyring": stored_in_keyring,
            "credential_notice": "Shown once. Store in the OS credential manager.",
        }
    )


def _cmd_client_revoke(args: argparse.Namespace) -> None:
    _store(args).revoke_client(args.client_id)
    _dump({"revoked": True, "client_id": args.client_id})


def _cmd_status(args: argparse.Namespace) -> None:
    _dump(_store(args).status())


def _cmd_legacy_edge_status(args: argparse.Namespace) -> None:
    """Report residual local Edge state without connecting outbound."""

    from allthecontext.edge_connection import EdgeConnectionStore

    config = _config(args)
    config.prepare()
    connections = EdgeConnectionStore(config)
    try:
        state = connections.state()
    except RuntimeError as error:
        _dump({"product_surface": "legacy_cleanup_only", "state": "degraded", "error": str(error)})
        return
    try:
        material = connections.material()
    except RuntimeError as error:
        _dump({"product_surface": "legacy_cleanup_only", "state": "degraded", "error": str(error)})
        return
    edge_url = state.edge_url if state is not None else None
    _dump(
        {
            "product_surface": "legacy_cleanup_only",
            "active_operation_available": False,
            "configured": edge_url is not None and material is not None,
            "remote_present": edge_url is not None,
            "credential_available": material is not None,
            "state": (
                "not_configured" if state is None else "prepared" if edge_url is None else "paired"
            ),
            "edge_url": edge_url,
            "detail": (
                "Ordinary Edge enroll/connect/sync and Relay serve are outside the V1 "
                "Core product boundary. Only isolated decommission or local forget remain."
            ),
        }
    )


def _cmd_legacy_edge_decommission(args: argparse.Namespace) -> None:
    """Decommission a pre-existing residual Edge without creating a new authority."""

    from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager

    config = _config(args)
    config.prepare()
    store = CoreStore(config.database_path)
    store.migrate()
    connections = EdgeConnectionStore(config)
    try:
        state = connections.state()
        material = connections.material()
    except RuntimeError as error:
        raise RuntimeError(str(error)) from error
    if state is None or material is None or state.edge_url is None:
        raise RuntimeError(
            "No residual paired Edge is configured. Core will not open a new hosted "
            "connection or create a second authority."
        )
    EdgeSyncManager(connections, store).decommission()
    store.revoke_all_remote_edge_clients()
    _dump(
        {
            "status": "decommissioned",
            "active_records_remaining": 0,
            "remote_access_revoked": True,
            "product_surface": "legacy_cleanup_only",
        }
    )


def _cmd_legacy_edge_forget(args: argparse.Namespace) -> None:
    """Forget local residual Edge credentials after explicit confirmation."""

    from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager

    if args.confirmation != "DELETE HOSTED EDGE":
        raise RuntimeError('confirmation must be exactly "DELETE HOSTED EDGE"')
    config = _config(args)
    config.prepare()
    store = CoreStore(config.database_path)
    store.migrate()
    connections = EdgeConnectionStore(config)
    try:
        state = connections.state()
        material = connections.material()
    except RuntimeError:
        state = None
        material = None
    if (
        state is not None
        and state.edge_url is not None
        and state.last_error is None
        and material is not None
    ):
        raise RuntimeError(
            "Edge is paired and manageable. Use legacy-edge decommission before forget."
        )
    EdgeSyncManager(connections, store).forget_local()
    _dump({"status": "forgotten", "product_surface": "legacy_cleanup_only"})


def _cmd_export(args: argparse.Namespace) -> None:
    config = _config(args)
    store = CoreStore(config.database_path)
    store.migrate()
    store.repair_preledger_secrets()
    _dump(
        create_export(
            config.database_path,
            Path(args.destination),
            _passphrase(args),
            include_sources=args.include_sources,
            include_audit=args.include_audit,
        )
    )


def _cmd_restore(args: argparse.Namespace) -> None:
    config = _config(args)
    if args.dry_run:
        _dump(
            restore_export(
                Path(args.source),
                config.database_path,
                _passphrase(args),
                dry_run=True,
            )
        )
        return
    store = CoreStore(config.database_path)
    store.migrate()
    result = restore_export(
        Path(args.source),
        config.database_path,
        _passphrase(args),
        dry_run=False,
    )
    store.initialize_vault()
    while store.evaluate_staged_observations():
        pass
    store.rebuild_integrity_groups()
    _dump(result)


def _cmd_doctor(args: argparse.Namespace) -> None:
    store = _store(args)
    with store.connect() as connection:
        fts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_fts'"
        ).fetchone()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    config = _config(args)
    _dump(
        {
            "ok": bool(fts) and integrity == "ok",
            "python": sys.version.split()[0],
            "database": str(config.database_path),
            "integrity": integrity,
            "fts5": bool(fts),
            "bind_default": config.host,
            "status": store.status(),
        }
    )


def _cmd_serve_core(args: argparse.Namespace) -> None:
    if args.data_dir:
        os.environ["ATC_CORE_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["ATC_CORE_HOST"] = args.host
    os.environ["ATC_CORE_PORT"] = str(args.port)
    from allthecontext.core.app import main as core_main

    core_main()


def _common_data(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", help="Override the per-user Core data directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atc",
        description="Inspect and administer the local All The Context Core",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize the local vault and first client")
    _common_data(init)
    init.add_argument("--name", default="My Context")
    init.add_argument("--timezone")
    init.add_argument("--client-name", default=DESKTOP_CLIENT_NAME)
    init.add_argument(
        "--no-keyring",
        action="store_true",
        help="development only: store credentials in an insecure plaintext app-data file",
    )
    init.add_argument("--json-only", action="store_true", help="omit the copyable MCP block")
    init.set_defaults(handler=_cmd_init)

    dashboard = commands.add_parser(
        "open-dashboard",
        help="Start Core and open an authenticated local dashboard",
    )
    _common_data(dashboard)
    dashboard.add_argument("--print-only", action="store_true")
    dashboard.set_defaults(handler=_cmd_open_dashboard)

    serve = commands.add_parser("serve-core", help="Run the loopback Core service")
    _common_data(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=7337, type=int)
    serve.set_defaults(handler=_cmd_serve_core)

    legacy_edge = commands.add_parser(
        "legacy-edge",
        help=(
            "Isolated residual Edge cleanup only; cannot enroll, deploy, connect, "
            "sync, or serve Relay"
        ),
    )
    legacy_commands = legacy_edge.add_subparsers(dest="legacy_edge_command", required=True)
    legacy_status = legacy_commands.add_parser(
        "status",
        help="Show residual local Edge state without outbound contact",
    )
    _common_data(legacy_status)
    legacy_status.set_defaults(handler=_cmd_legacy_edge_status)
    legacy_decommission = legacy_commands.add_parser(
        "decommission",
        help="Decommission a pre-existing residual Edge connection only",
    )
    _common_data(legacy_decommission)
    legacy_decommission.set_defaults(handler=_cmd_legacy_edge_decommission)
    legacy_forget = legacy_commands.add_parser(
        "forget",
        help="Forget residual local Edge credentials after confirmation",
    )
    _common_data(legacy_forget)
    legacy_forget.add_argument(
        "--confirmation",
        required=True,
        help='Must be exactly "DELETE HOSTED EDGE"',
    )
    legacy_forget.set_defaults(handler=_cmd_legacy_edge_forget)

    config_mcp = commands.add_parser("config-mcp", help="Generate one-time Codex MCP config")
    _common_data(config_mcp)
    config_mcp.add_argument("--client-id", required=True)
    config_mcp.add_argument("--token")
    config_mcp.add_argument("--target")
    config_mcp.set_defaults(handler=_cmd_config_mcp)

    import_command = commands.add_parser(
        "import",
        help="Import local AI archives for automatic context maintenance",
    )
    _common_data(import_command)
    import_command.add_argument("path")
    import_command.add_argument(
        "--provider",
        choices=["auto", "chatgpt", "claude", "grok", "generic"],
        default="auto",
        help="provider hint; auto detects supported export schemas",
    )
    import_command.add_argument(
        "--source-service",
        dest="provider",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    import_command.add_argument(
        "--max-bytes",
        type=_import_byte_limit,
        default=DEFAULT_MAX_IMPORT_BYTES,
    )
    import_command.set_defaults(handler=_cmd_import)

    import_progress = commands.add_parser(
        "import-progress",
        help="Inspect durable progress for an import operation or source id",
    )
    _common_data(import_progress)
    import_progress.add_argument(
        "source_id",
        help="import operation id or source id",
    )
    import_progress.set_defaults(handler=_cmd_import_progress)

    cancel_import = commands.add_parser(
        "cancel-import",
        help="Request cancellation of an in-flight import operation or source import",
    )
    _common_data(cancel_import)
    cancel_import.add_argument(
        "source_id",
        help="import operation id or source id",
    )
    cancel_import.set_defaults(handler=_cmd_cancel_import)

    reprocess = commands.add_parser(
        "reprocess-source",
        help="Retry extraction from a preserved raw source without re-upload",
    )
    _common_data(reprocess)
    reprocess.add_argument(
        "source_id",
        help="import operation id or source id",
    )
    reprocess.add_argument(
        "--max-bytes",
        type=_import_byte_limit,
        default=DEFAULT_MAX_IMPORT_BYTES,
    )
    reprocess.set_defaults(handler=_cmd_reprocess_source)

    import_boundary = commands.add_parser(
        "import-boundary",
        help="Show the frozen import scale profile and provider claim identities",
    )
    import_boundary.set_defaults(handler=_cmd_import_boundary)

    observations = commands.add_parser(
        "observations",
        help="Inspect automatic context-policy observations",
    )
    _common_data(observations)
    observations.add_argument(
        "--disposition",
        choices=[item.value for item in ObservationDisposition],
        help="show only observations with this automatic-policy outcome",
    )
    observations.add_argument("--limit", type=int, default=100)
    observations.add_argument("--offset", type=int, default=0)
    observations.set_defaults(handler=_cmd_observations)

    candidates = commands.add_parser(
        "candidates",
        help="[deprecated compatibility] List legacy approval candidates",
        description=(
            "Deprecated compatibility command for inspecting the legacy approval lifecycle. "
            "Use 'atc observations' for automatic-policy decisions."
        ),
    )
    _common_data(candidates)
    candidates.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "all"],
        default="pending",
    )
    candidates.add_argument("--limit", type=int, default=100)
    candidates.add_argument("--offset", type=int, default=0)
    candidates.set_defaults(handler=_cmd_candidates)

    approve = commands.add_parser(
        "approve",
        help="[deprecated compatibility] Approve one legacy candidate",
        description=(
            "Deprecated compatibility command for old pending candidates. "
            "Normal context maintenance is automatic."
        ),
    )
    _common_data(approve)
    approve.add_argument("candidate_id")
    approve.add_argument("--content")
    approve.add_argument("--entity-key")
    approve.add_argument("--attribute-key")
    approve.add_argument("--availability", choices=[item.value for item in Availability])
    approve.add_argument("--reason")
    approve.add_argument("--confirm-sensitive-replication", action="store_true")
    approve.set_defaults(handler=_cmd_approve)

    reject = commands.add_parser(
        "reject",
        help="[deprecated compatibility] Reject one legacy candidate",
        description=(
            "Deprecated compatibility command for old pending candidates. "
            "Normal context maintenance is automatic."
        ),
    )
    _common_data(reject)
    reject.add_argument("candidate_id")
    reject.add_argument("--reason")
    reject.set_defaults(handler=_cmd_reject)

    search = commands.add_parser("search", help="Search current Core context")
    _common_data(search)
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--scope", action="append", default=[])
    search.add_argument("--kind", action="append", default=[])
    search.add_argument(
        "--as-of",
        help="offset-aware ISO 8601 instant for deterministic historical search",
    )
    search.add_argument("--current-project", help="project hint for task admissibility")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument(
        "--explain",
        action="store_true",
        help="include administrator-only lexical ranking explanations",
    )
    search.set_defaults(handler=_cmd_search)

    availability = commands.add_parser("availability", help="Change record availability")
    _common_data(availability)
    availability.add_argument("record_id")
    availability.add_argument("availability", choices=[item.value for item in Availability])
    availability.add_argument("--confirm-sensitive-replication", action="store_true")
    availability.set_defaults(handler=_cmd_availability)

    correct = commands.add_parser("correct", help="Create a new version of a record")
    _common_data(correct)
    correct.add_argument("record_id")
    correct.add_argument("content")
    correct.add_argument("--reason", required=True)
    correct.add_argument("--entity-key")
    correct.add_argument("--attribute-key")
    correct.set_defaults(handler=_cmd_correct)

    delete = commands.add_parser("delete", help="Delete a record and create a tombstone")
    _common_data(delete)
    delete.add_argument("record_id")
    delete.add_argument("--reason", required=True)
    delete.set_defaults(handler=_cmd_delete)

    restore_record = commands.add_parser(
        "restore-record",
        help="Undo a soft deletion or restore a historical context version",
    )
    _common_data(restore_record)
    restore_record.add_argument("record_id")
    restore_record.add_argument(
        "--version",
        type=int,
        help="historical version to copy into current context; omit to undo deletion",
    )
    restore_record.add_argument("--reason", default="restored by user")
    restore_record.set_defaults(handler=_cmd_restore_record)

    integrity = commands.add_parser(
        "integrity-groups",
        help="List duplicate and conflict diagnostics",
    )
    _common_data(integrity)
    integrity.add_argument("--status", choices=["open", "resolved", "all"], default="open")
    integrity.add_argument("--limit", type=int, default=100)
    integrity.add_argument("--offset", type=int, default=0)
    integrity.set_defaults(handler=_cmd_integrity_groups)

    purge = commands.add_parser("purge", help="Irreversibly purge one Core record or source")
    _common_data(purge)
    purge.add_argument("target_type", choices=["record", "source"])
    purge.add_argument("target_id")
    purge.add_argument(
        "--confirmation",
        required=True,
        help='Exact phrase: "PURGE RECORD <id>" or "PURGE SOURCE <id>"',
    )
    purge.add_argument("--no-compact", action="store_true")
    purge.set_defaults(handler=_cmd_purge)

    purge_resume = commands.add_parser(
        "purge-resume", help="Resume pending secure-delete compaction jobs"
    )
    _common_data(purge_resume)
    purge_resume.add_argument("--limit", type=int, default=10)
    purge_resume.set_defaults(handler=_cmd_purge_resume)

    clients = commands.add_parser("clients", help="List clients")
    _common_data(clients)
    clients.set_defaults(handler=_cmd_clients)

    client_add = commands.add_parser("client-add", help="Create a scoped client")
    _common_data(client_add)
    client_add.add_argument("name")
    client_add.add_argument(
        "--scope",
        action="append",
        default=["context:read", "context:status", "context:propose"],
    )
    client_add.add_argument(
        "--auto-approve",
        action="store_true",
        help="deprecated compatibility flag; Core's automatic policy owns decisions",
    )
    client_add.add_argument(
        "--no-keyring",
        action="store_true",
        help="development only: store credentials in an insecure plaintext app-data file",
    )
    client_add.set_defaults(handler=_cmd_client_add)

    client_revoke = commands.add_parser("client-revoke", help="Revoke a client")
    _common_data(client_revoke)
    client_revoke.add_argument("client_id")
    client_revoke.set_defaults(handler=_cmd_client_revoke)

    status = commands.add_parser(
        "status",
        help="Show current-context, observation, and local vault health",
    )
    _common_data(status)
    status.set_defaults(handler=_cmd_status)

    export = commands.add_parser("export", help="Create an encrypted portable export")
    _common_data(export)
    export.add_argument("destination")
    export.add_argument("--passphrase-env", default="ATC_EXPORT_PASSPHRASE")
    export.add_argument("--include-sources", action="store_true")
    export.add_argument("--include-audit", action="store_true")
    export.set_defaults(handler=_cmd_export)

    restore = commands.add_parser(
        "restore",
        help="Restore an encrypted portable vault export",
    )
    _common_data(restore)
    restore.add_argument("source")
    restore.add_argument("--passphrase-env", default="ATC_EXPORT_PASSPHRASE")
    restore.add_argument("--dry-run", action="store_true")
    restore.set_defaults(handler=_cmd_restore)

    doctor = commands.add_parser("doctor", help="Verify local initialization and SQLite")
    _common_data(doctor)
    doctor.set_defaults(handler=_cmd_doctor)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except Exception as exc:
        _dump({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}})
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

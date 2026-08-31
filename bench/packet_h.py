"""Run the disposable Packet H-A source-admission proof.

This runner is intentionally foreground-only.  It composes the existing local
workspace adapter, capture coordinator, and registered-source sink over a
temporary Core vault and publishes bounded content-free aggregate JSON.  It does
not wire a provider into startup, scheduler, CoreService, or production
configuration.
"""

from __future__ import annotations

# The checkout source path is inserted before third-party imports so a stale
# editable install cannot silently satisfy this proof.
# ruff: noqa: E402, I001

import argparse
import hashlib
import json
import re
import stat
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import cast

# Force this checkout ahead of any stale editable allthecontext install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SOURCE = _REPO_ROOT / "packages" / "allthecontext" / "src"
if not (_LOCAL_SOURCE / "allthecontext" / "__init__.py").is_file():
    raise RuntimeError("packet H proof requires the repository source tree")
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_LOCAL_SOURCE))

import allthecontext
from allthecontext.capture import (
    CaptureApplicationReceipt,
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureError,
    CaptureEvent,
    CaptureRunHandle,
    CaptureRunResult,
    CaptureSource,
)
from allthecontext.capture_runtime import _workspace_state_reader
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    MAX_SCAN_DEPTH,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from allthecontext.memory_policy import (
    REGISTERED_SOURCE_CODE_OWNED_SCOPES,
    REGISTERED_SOURCE_EXTRACTOR_ID,
    REGISTERED_SOURCE_EXTRACTOR_VERSION,
    REGISTERED_SOURCE_FACT_CLASSES,
    REGISTERED_SOURCE_FACT_SCHEMA,
    REGISTERED_SOURCE_FACT_SENTENCES,
    registered_source_fact_evidence,
)
from allthecontext.registered_source_admission import RegisteredSourceCaptureApplicationSink
from allthecontext.storage import CoreStore

from tests.fixtures.local_git_workspace import create_sanitized_workspace


def _require_checkout_allthecontext() -> None:
    """Fail closed if imported allthecontext is not this checkout's source tree."""

    try:
        Path(allthecontext.__file__ or "").resolve().relative_to(_LOCAL_SOURCE / "allthecontext")
    except ValueError as error:
        raise RuntimeError("packet H proof resolved allthecontext outside this checkout") from error


_require_checkout_allthecontext()

_DISPOSABLE_PREFIX = "atc-packet-h-"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DISPOSABLE_ROOT_ERROR = "packet_h_requires_disposable_temporary_root"
_PLAIN_PATH_TYPE = type(Path())
_REAL_TEMPORARY_DIRECTORY_TYPE = TemporaryDirectory
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]+$")
_EVENT_PAYLOAD_KEYS = frozenset(
    {
        "relative_path",
        "root_id",
        "kind",
        "size",
        "content_sha256",
        "content_truncated",
        "hash_scope",
    }
)
_PACKET_H_A_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "boundary",
        "capture",
        "incomplete_probe",
        "acceptance",
        "aggregate_receipt",
    }
)
_PACKET_H_A_CAPTURE_KEYS = frozenset(
    {
        "manifest_coverage",
        "manifest_availability",
        "network_access",
        "data_egress_count",
        "scan",
        "first_run",
        "recovery_run",
        "replay_run",
        "after_recovery",
        "after_replay",
    }
)
_PACKET_H_A_SCAN_KEYS = frozenset(
    {
        "files_considered",
        "items_emitted",
        "excluded_paths",
        "credential_like_paths",
        "incomplete",
    }
)
_PACKET_H_A_RUN_KEYS = frozenset(
    {
        "status",
        "error_code",
        "pages",
        "events",
        "applied_events",
        "duplicate_events",
        "failures",
    }
)
_PACKET_H_A_AGGREGATE_KEYS = frozenset(
    {
        "event_count",
        "candidate_count",
        "record_count",
        "receipt_counts",
        "fact_class_counts",
        "payload_shape_ok",
        "content_free_projection",
    }
)
_PACKET_H_A_INCOMPLETE_PROBE_KEYS = frozenset(
    {
        "manifest_coverage",
        "manifest_availability",
        "run",
        "scan_incomplete",
        "scan_items_emitted",
        "candidate_count",
        "record_count",
    }
)
_PACKET_H_A_ACCEPTANCE_KEYS = frozenset(
    {
        "bounded_admission",
        "deterministic_no_fact",
        "partial_coverage_truth",
        "local_only_capability",
        "incomplete_fails_closed",
        "restart_replay_idempotent",
        "content_free_identifier_safe",
    }
)
_PACKET_H_A_RECEIPT_KEYS = frozenset(
    {
        "receipt_type",
        "status",
        "identifier_digest",
    }
)
_PACKET_H_A_RECEIPT_COUNT_KEYS = frozenset(
    {
        "registered-source-fact",
        "registered-source-no-fact",
    }
)
_PACKET_H_A_FACT_CLASS_COUNT_KEYS = frozenset(
    {
        "markdown_documentation",
        "python_source",
        "shell_script",
    }
)
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_RAW_MATERIAL = (
    "# Sample workspace",
    "Use deterministic local fixtures",
    "def answer()",
    "metadata-only",
    "not-for-capture",
    "AKIAIOSFODNN7EXAMPLE",
    "FIXTURE_SECRET",
    "src/app.py",
    "docs/decision.md",
    "scripts/build.sh",
    "README.md",
    "workspace-source-",
)


class _DisposableRootCapability:
    """Internal proof that ``run`` owns the active temporary root."""

    _root: Path
    _sealed: bool
    _temporary_directory: TemporaryDirectory[str]
    _temporary_name: str

    __slots__ = ("_root", "_sealed", "_temporary_directory", "_temporary_name")

    def __init__(
        self,
        temporary_directory: object,
        *,
        runner_token: object | None = None,
    ) -> None:
        if not _valid_capability_construction_token(runner_token):
            raise ValueError(_DISPOSABLE_ROOT_ERROR)
        if type(temporary_directory) is not _REAL_TEMPORARY_DIRECTORY_TYPE:
            raise ValueError(_DISPOSABLE_ROOT_ERROR)
        directory = cast(TemporaryDirectory[str], temporary_directory)
        temporary_name = directory.name
        if type(temporary_name) is not str:
            raise ValueError(_DISPOSABLE_ROOT_ERROR)
        object.__setattr__(self, "_root", Path(temporary_name).resolve())
        object.__setattr__(self, "_temporary_name", temporary_name)
        object.__setattr__(self, "_temporary_directory", directory)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("packet_h_disposable_root_capability_is_immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("packet_h_disposable_root_capability_is_immutable")
        object.__delattr__(self, name)

    def authorizes(self, root: Path) -> bool:
        if type(root) is not _PLAIN_PATH_TYPE:
            return False
        if type(self._root) is not _PLAIN_PATH_TYPE or type(self._temporary_name) is not str:
            return False
        if type(self._temporary_directory) is not _REAL_TEMPORARY_DIRECTORY_TYPE:
            return False
        current_name = self._temporary_directory.name
        if type(current_name) is not str:
            return False
        return (
            current_name == self._temporary_name
            and self._root == root
            and Path(self._temporary_name).resolve() == root
        )


def _build_disposable_root_helpers() -> tuple[
    Callable[[object], bool],
    Callable[[str], AbstractContextManager[tuple[Path, _DisposableRootCapability]]],
]:
    """Build the lexical construction guard and its sole capability factory."""

    construction_sentinel = object()

    def valid_capability_construction_token(candidate: object) -> bool:
        return candidate is construction_sentinel

    @contextmanager
    def runner_owned_temporary_root(
        prefix: str,
    ) -> Iterator[tuple[Path, _DisposableRootCapability]]:
        """Create the only temporary-root capability accepted by the proof runners."""

        temporary_directory = TemporaryDirectory(prefix=prefix)
        with temporary_directory:
            root = Path(temporary_directory.name).resolve()
            ownership = _DisposableRootCapability(
                temporary_directory,
                runner_token=construction_sentinel,
            )
            yield root, ownership

    return valid_capability_construction_token, runner_owned_temporary_root


_valid_capability_construction_token, _runner_owned_temporary_root = (
    _build_disposable_root_helpers()
)
del _build_disposable_root_helpers


class _CrashAfterFirstAdmission:
    """Disposable failure injector used to exercise real sink replay."""

    def __init__(self, sink: RegisteredSourceCaptureApplicationSink) -> None:
        self._sink = sink
        self._crashed = False

    def apply(
        self,
        event: CaptureEvent,
        *,
        source_id: str,
        event_id: str,
        run_handle: CaptureRunHandle,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str | CaptureApplicationReceipt:
        receipt = self._sink.apply(
            event,
            source_id=source_id,
            event_id=event_id,
            run_handle=run_handle,
            canonical_record_id=canonical_record_id,
            idempotency_key=idempotency_key,
        )
        if not self._crashed:
            self._crashed = True
            raise CaptureError("capture_sink_failed")
        return receipt


def _assert_disposable_root(
    root: Path,
    *,
    ownership: object | None = None,
) -> Path:
    """Reject paths unless the active runner owns a fresh temporary directory."""

    if type(ownership) is not _DisposableRootCapability:
        raise ValueError(_DISPOSABLE_ROOT_ERROR)
    if type(root) is not _PLAIN_PATH_TYPE:
        raise ValueError(_DISPOSABLE_ROOT_ERROR)
    capability = ownership

    candidate = root.expanduser()
    try:
        root_stat = candidate.lstat()
    except OSError as exc:
        raise ValueError(_DISPOSABLE_ROOT_ERROR) from exc
    attributes = int(getattr(root_stat, "st_file_attributes", 0))
    if stat.S_ISLNK(root_stat.st_mode) or bool(attributes & _REPARSE_POINT):
        raise ValueError(_DISPOSABLE_ROOT_ERROR)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(_DISPOSABLE_ROOT_ERROR)

    resolved = candidate.resolve()
    temporary_parent = Path(gettempdir()).resolve()
    if (
        resolved.parent != temporary_parent
        or not resolved.name.casefold().startswith(_DISPOSABLE_PREFIX)
        or resolved == temporary_parent
        or not capability.authorizes(resolved)
    ):
        raise ValueError(_DISPOSABLE_ROOT_ERROR)
    try:
        next(resolved.iterdir())
    except StopIteration:
        return capability._root
    except OSError as exc:
        raise ValueError(_DISPOSABLE_ROOT_ERROR) from exc
    raise ValueError(_DISPOSABLE_ROOT_ERROR)


def _new_store(
    root: Path,
    *,
    ownership: object | None = None,
) -> CoreStore:
    disposable_root = _assert_disposable_root(root, ownership=ownership)
    return CoreStore(disposable_root / "core.sqlite3")


def _close_core_stores(*stores: CoreStore | None) -> None:
    for store in stores:
        if store is not None:
            store.close()


def _walk_strings(value: object) -> Iterator[str]:
    if type(value) is str:
        yield value
        return
    if type(value) is dict:
        for key, nested in value.items():
            if type(key) is str:
                yield key
            yield from _walk_strings(nested)
        return
    if type(value) is list:
        for nested in value:
            yield from _walk_strings(nested)


def _contains_forbidden_raw_material(value: object) -> bool:
    return any(
        fragment in text
        for text in _walk_strings(value)
        for fragment in _FORBIDDEN_RAW_MATERIAL
        if fragment
    )


def _registered_source_row_is_content_free(
    content: object,
    structured: object,
    evidence: object,
) -> bool:
    if type(content) is not str or type(evidence) is not str or type(structured) is not dict:
        return False
    structured_dict = cast(dict[str, object], structured)
    if set(structured_dict) != {
        "binding_hash",
        "extractor",
        "extractor_version",
        "fact_class",
        "schema",
    }:
        return False
    fact_class = structured_dict.get("fact_class")
    binding_hash = structured_dict.get("binding_hash")
    if type(fact_class) is not str or fact_class not in REGISTERED_SOURCE_FACT_CLASSES:
        return False
    if type(binding_hash) is not str or _HEX_DIGEST.fullmatch(binding_hash) is None:
        return False
    return (
        content == REGISTERED_SOURCE_FACT_SENTENCES[fact_class]
        and evidence == registered_source_fact_evidence(fact_class, binding_hash)
        and structured_dict.get("extractor") == REGISTERED_SOURCE_EXTRACTOR_ID
        and structured_dict.get("extractor_version") == REGISTERED_SOURCE_EXTRACTOR_VERSION
        and structured_dict.get("schema") == REGISTERED_SOURCE_FACT_SCHEMA
        and not _contains_forbidden_raw_material(
            {"content": content, "evidence": evidence, "structured": structured_dict}
        )
    )


def _create_source(
    coordinator: CaptureCoordinator,
    adapter: LocalGitWorkspaceCaptureProviderAdapter,
) -> CaptureSource:
    source = coordinator.create_source(
        provider=LOCAL_GIT_WORKSPACE_PROVIDER,
        account_label="packet-h-fixture",
        account_fingerprint=adapter.source_identity,
        requested_scopes=REGISTERED_SOURCE_CODE_OWNED_SCOPES,
        local_only_acknowledged=True,
    )
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)
    coordinator.enable(source.id)
    return source


def _run_result(result: CaptureRunResult) -> dict[str, object]:
    """Keep coordinator outcomes to stable, content-free fields."""

    return {
        "status": result.status,
        "error_code": result.error_code,
        "pages": result.pages,
        "events": result.events,
        "applied_events": result.applied_events,
        "duplicate_events": result.duplicate_events,
        "failures": result.failures,
    }


def _accepts_local_only_capability(manifest: CaptureCapabilityManifest) -> bool:
    return (
        manifest.coverage == "partial"
        and manifest.availability == "partial"
        and manifest.network_access == "denied"
        and manifest.data_egress == ()
    )


def _receipt_family(receipt: str) -> str:
    if receipt == "registered-source-no-fact":
        return receipt
    if receipt.startswith("registered-source-fact:"):
        return "registered-source-fact"
    if receipt in {"registered-source-purged", "registered-source-withdrawn"}:
        return receipt
    raise AssertionError("unexpected registered-source receipt family")


def _query_capture_aggregate(store: CoreStore, source_id: str) -> dict[str, object]:
    with store.connect() as connection:
        events = connection.execute(
            "SELECT normalized_payload_json,application_receipt,operation "
            "FROM capture_events WHERE source_id=? ORDER BY id",
            (source_id,),
        ).fetchall()
        candidates = connection.execute(
            "SELECT content,structured_value_json,evidence "
            "FROM context_candidates WHERE capture_source_id=? ORDER BY id",
            (source_id,),
        ).fetchall()
        records = connection.execute(
            "SELECT r.content,r.structured_value_json,r.evidence "
            "FROM context_records r JOIN context_candidates c ON c.id=r.candidate_id "
            "WHERE c.capture_source_id=? ORDER BY r.id",
            (source_id,),
        ).fetchall()

    receipt_counts: Counter[str] = Counter()
    fact_class_counts: Counter[str] = Counter()
    payload_shape_ok = True
    for event in events:
        receipt_counts[_receipt_family(str(event["application_receipt"]))] += 1
        payload = json.loads(str(event["normalized_payload_json"]))
        if payload:
            payload_shape_ok = payload_shape_ok and set(payload) == _EVENT_PAYLOAD_KEYS
        if str(event["operation"]) == "delete":
            payload_shape_ok = payload_shape_ok and not payload

    content_free_projection = True
    for row in candidates:
        content = row["content"]
        evidence = row["evidence"]
        structured = json.loads(str(row["structured_value_json"]))
        content_free_projection = (
            content_free_projection
            and _registered_source_row_is_content_free(content, structured, evidence)
        )
        fact_class = structured.get("fact_class") if isinstance(structured, dict) else None
        if isinstance(fact_class, str) and fact_class in REGISTERED_SOURCE_FACT_CLASSES:
            fact_class_counts[fact_class] += 1

    for row in records:
        content_free_projection = (
            content_free_projection
            and _registered_source_row_is_content_free(
                row["content"],
                json.loads(str(row["structured_value_json"])),
                row["evidence"],
            )
        )

    return {
        "event_count": len(events),
        "candidate_count": len(candidates),
        "record_count": len(records),
        "receipt_counts": dict(sorted(receipt_counts.items())),
        "fact_class_counts": dict(sorted(fact_class_counts.items())),
        "payload_shape_ok": payload_shape_ok,
        "content_free_projection": content_free_projection,
    }


def _overflow_probe(root: Path, store: CoreStore) -> dict[str, object]:
    overflow_root = root / "incomplete-workspace"
    overflow_root.mkdir()
    too_deep = overflow_root
    for index in range(MAX_SCAN_DEPTH + 2):
        too_deep = too_deep / f"level-{index:02d}"
        too_deep.mkdir()
    (too_deep / "item.txt").write_text(
        "sanitized incomplete fixture\n", encoding="utf-8", newline="\n"
    )

    adapter = LocalGitWorkspaceCaptureProviderAdapter((overflow_root,))
    coordinator = CaptureCoordinator(store, sink=RegisteredSourceCaptureApplicationSink(store))
    source = _create_source(coordinator, adapter)
    result = coordinator.run(source.id)
    scan = adapter.last_scan_report
    if scan is None:
        raise AssertionError("overflow probe did not produce a scan report")
    aggregate = _query_capture_aggregate(store, source.id)
    return {
        "manifest_coverage": adapter.capability_manifest.coverage,
        "manifest_availability": adapter.capability_manifest.availability,
        "run": _run_result(result),
        "scan_incomplete": scan.incomplete,
        "scan_items_emitted": scan.items_emitted,
        "candidate_count": aggregate["candidate_count"],
        "record_count": aggregate["record_count"],
    }


def _stable_digest(material: Mapping[str, object]) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _has_exact_keys(value: object, expected: frozenset[str]) -> bool:
    return type(value) is dict and set(value) == expected


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _allowed_string(value: object, allowed: frozenset[str]) -> bool:
    return type(value) is str and value in allowed


def _identifier_safe_report(report: Mapping[str, object]) -> bool:
    def safe(value: object) -> bool:
        if value is None or type(value) in {bool, int}:
            return True
        if type(value) is str:
            return _IDENTIFIER.fullmatch(value) is not None
        if type(value) is dict:
            return all(
                type(key) is str and _IDENTIFIER.fullmatch(key) is not None and safe(nested)
                for key, nested in value.items()
            )
        return False

    return safe(report)


def _validate_count_map(value: object, expected: frozenset[str]) -> bool:
    if not _has_exact_keys(value, expected):
        return False
    counts = cast(dict[str, object], value)
    return all(type(key) is str and _nonnegative_int(count) for key, count in counts.items())


def _validate_run(value: object) -> bool:
    if not _has_exact_keys(value, _PACKET_H_A_RUN_KEYS):
        return False
    run = cast(dict[str, object], value)
    if not _allowed_string(run["status"], frozenset({"failed", "completed"})):
        return False
    if run["error_code"] is not None and (
        type(run["error_code"]) is not str
        or run["error_code"]
        not in {
            "capture_adapter_unavailable",
            "capture_page_limit_exceeded",
            "capture_sink_failed",
        }
    ):
        return False
    if not all(_nonnegative_int(run[field]) for field in ("pages", "events")):
        return False
    if not all(
        _nonnegative_int(run[field]) for field in ("applied_events", "duplicate_events", "failures")
    ):
        return False
    applied_events = cast(int, run["applied_events"])
    duplicate_events = cast(int, run["duplicate_events"])
    events = cast(int, run["events"])
    if applied_events + duplicate_events > events:
        return False
    if run["status"] == "completed":
        return run["error_code"] is None and run["failures"] == 0
    failures = cast(int, run["failures"])
    return type(run["error_code"]) is str and failures > 0


def _validate_scan(value: object) -> bool:
    if not _has_exact_keys(value, _PACKET_H_A_SCAN_KEYS):
        return False
    scan = cast(dict[str, object], value)
    if not (
        _nonnegative_int(scan["files_considered"])
        and _nonnegative_int(scan["items_emitted"])
        and _nonnegative_int(scan["excluded_paths"])
        and _nonnegative_int(scan["credential_like_paths"])
        and type(scan["incomplete"]) is bool
    ):
        return False
    files_considered = cast(int, scan["files_considered"])
    items_emitted = cast(int, scan["items_emitted"])
    return items_emitted <= files_considered


def _validate_aggregate(value: object) -> bool:
    if not _has_exact_keys(value, _PACKET_H_A_AGGREGATE_KEYS):
        return False
    aggregate = cast(dict[str, object], value)
    if not all(
        _nonnegative_int(aggregate[field])
        for field in ("event_count", "candidate_count", "record_count")
    ):
        return False
    if not _validate_count_map(aggregate["receipt_counts"], _PACKET_H_A_RECEIPT_COUNT_KEYS):
        return False
    if not _validate_count_map(aggregate["fact_class_counts"], _PACKET_H_A_FACT_CLASS_COUNT_KEYS):
        return False
    if (
        type(aggregate["payload_shape_ok"]) is not bool
        or type(aggregate["content_free_projection"]) is not bool
    ):
        return False
    receipt_counts = cast(dict[str, int], aggregate["receipt_counts"])
    fact_class_counts = cast(dict[str, int], aggregate["fact_class_counts"])
    event_count = cast(int, aggregate["event_count"])
    candidate_count = cast(int, aggregate["candidate_count"])
    record_count = cast(int, aggregate["record_count"])
    return (
        sum(receipt_counts.values()) == event_count
        and sum(fact_class_counts.values()) == candidate_count
        and candidate_count == record_count
    )


def _run_has_values(run: dict[str, object], expected: Mapping[str, object]) -> bool:
    return all(run[key] == value for key, value in expected.items())


def _h_a_digest_material(
    capture: Mapping[str, object],
    incomplete_probe: Mapping[str, object],
    acceptance: Mapping[str, object],
) -> dict[str, object]:
    return {
        "capture": capture,
        "overflow": incomplete_probe,
        "acceptance": acceptance,
    }


def _packet_h_a_report_ready(report: object, *, require_passing: bool = True) -> bool:
    """Validate the bounded H-A report and, optionally, require readiness for H-C."""

    if not _has_exact_keys(report, _PACKET_H_A_REPORT_KEYS):
        return False
    report_dict = cast(dict[str, object], report)
    if report_dict["schema_version"] != 1 or type(report_dict["schema_version"]) is not int:
        return False
    if report_dict["boundary"] != "packet-h-a-source-admission":
        return False

    capture = report_dict["capture"]
    if not _has_exact_keys(capture, _PACKET_H_A_CAPTURE_KEYS):
        return False
    capture_dict = cast(dict[str, object], capture)
    if (
        not _allowed_string(
            capture_dict["manifest_coverage"], frozenset({"complete", "partial", "unavailable"})
        )
        or not _allowed_string(
            capture_dict["manifest_availability"],
            frozenset({"complete", "partial", "unavailable"}),
        )
        or not _allowed_string(
            capture_dict["network_access"], frozenset({"allowed", "denied", "unknown"})
        )
        or type(capture_dict["data_egress_count"]) is not int
    ):
        return False
    if capture_dict["data_egress_count"] < -1:
        return False
    if not _validate_scan(capture_dict["scan"]):
        return False
    runs = {key: capture_dict[key] for key in ("first_run", "recovery_run", "replay_run")}
    if not all(_validate_run(value) for value in runs.values()):
        return False
    aggregates = {key: capture_dict[key] for key in ("after_recovery", "after_replay")}
    if not all(_validate_aggregate(value) for value in aggregates.values()):
        return False

    incomplete_probe = report_dict["incomplete_probe"]
    if not _has_exact_keys(incomplete_probe, _PACKET_H_A_INCOMPLETE_PROBE_KEYS):
        return False
    incomplete_dict = cast(dict[str, object], incomplete_probe)
    if (
        incomplete_dict["manifest_coverage"] != capture_dict["manifest_coverage"]
        or incomplete_dict["manifest_availability"] != capture_dict["manifest_availability"]
        or not _validate_run(incomplete_dict["run"])
        or type(incomplete_dict["scan_incomplete"]) is not bool
        or not _nonnegative_int(incomplete_dict["scan_items_emitted"])
        or not _nonnegative_int(incomplete_dict["candidate_count"])
        or not _nonnegative_int(incomplete_dict["record_count"])
    ):
        return False

    acceptance = report_dict["acceptance"]
    if type(acceptance) is not dict or not set(acceptance).issuperset(_PACKET_H_A_ACCEPTANCE_KEYS):
        return False
    acceptance_dict = cast(dict[str, object], acceptance)
    if any(type(value) is not bool for value in acceptance_dict.values()):
        return False

    receipt = report_dict["aggregate_receipt"]
    if not _has_exact_keys(receipt, _PACKET_H_A_RECEIPT_KEYS):
        return False
    receipt_dict = cast(dict[str, object], receipt)
    if (
        receipt_dict["receipt_type"] != "packet-h-a-aggregate"
        or not _allowed_string(receipt_dict["status"], frozenset({"pass", "fail"}))
        or type(receipt_dict["identifier_digest"]) is not str
        or _HEX_DIGEST.fullmatch(receipt_dict["identifier_digest"]) is None
    ):
        return False
    if not _identifier_safe_report(report_dict):
        return False

    first_run = cast(dict[str, object], runs["first_run"])
    recovery_run = cast(dict[str, object], runs["recovery_run"])
    replay_run = cast(dict[str, object], runs["replay_run"])
    after_recovery = cast(dict[str, object], aggregates["after_recovery"])
    after_replay = cast(dict[str, object], aggregates["after_replay"])
    overflow_run = cast(dict[str, object], incomplete_dict["run"])
    recomputed_acceptance = {
        "bounded_admission": (
            after_recovery["candidate_count"] == 4
            and after_recovery["record_count"] == 4
            and after_recovery["fact_class_counts"]
            == {
                "markdown_documentation": 2,
                "python_source": 1,
                "shell_script": 1,
            }
        ),
        "deterministic_no_fact": (
            after_recovery["receipt_counts"]
            == {
                "registered-source-fact": 4,
                "registered-source-no-fact": 1,
            }
            and after_recovery["event_count"] == 5
        ),
        "partial_coverage_truth": (
            capture_dict["manifest_coverage"] == "partial"
            and capture_dict["manifest_availability"] == "partial"
        ),
        "local_only_capability": (
            capture_dict["manifest_coverage"] == "partial"
            and capture_dict["manifest_availability"] == "partial"
            and capture_dict["network_access"] == "denied"
            and capture_dict["data_egress_count"] == 0
        ),
        "incomplete_fails_closed": (
            _run_has_values(
                overflow_run,
                {
                    "status": "failed",
                    "error_code": "capture_adapter_unavailable",
                    "pages": 0,
                    "events": 0,
                    "applied_events": 0,
                    "duplicate_events": 0,
                    "failures": 1,
                },
            )
            and incomplete_dict["scan_incomplete"] is True
            and incomplete_dict["scan_items_emitted"] == 0
            and incomplete_dict["candidate_count"] == 0
            and incomplete_dict["record_count"] == 0
        ),
        "restart_replay_idempotent": (
            _run_has_values(
                first_run,
                {
                    "status": "failed",
                    "error_code": "capture_sink_failed",
                    "pages": 1,
                    "events": 1,
                    "applied_events": 0,
                    "duplicate_events": 0,
                    "failures": 1,
                },
            )
            and _run_has_values(
                recovery_run,
                {
                    "status": "completed",
                    "error_code": None,
                    "pages": 2,
                    "events": 5,
                    "applied_events": 5,
                    "duplicate_events": 0,
                    "failures": 0,
                },
            )
            and _run_has_values(
                replay_run,
                {
                    "status": "completed",
                    "error_code": None,
                    "pages": 1,
                    "events": 0,
                    "applied_events": 0,
                    "duplicate_events": 0,
                    "failures": 0,
                },
            )
            and after_recovery == after_replay
        ),
        "content_free_identifier_safe": (
            after_recovery["payload_shape_ok"] is True
            and after_recovery["content_free_projection"] is True
        ),
    }
    if any(acceptance_dict[key] is not expected for key, expected in recomputed_acceptance.items()):
        return False
    expected_status = "pass" if all(value is True for value in acceptance_dict.values()) else "fail"
    if receipt_dict["status"] != expected_status:
        return False
    if receipt_dict["identifier_digest"] != _stable_digest(
        _h_a_digest_material(capture_dict, incomplete_dict, acceptance_dict)
    ):
        return False
    return not require_passing or all(value is True for value in acceptance_dict.values())


def _run_disposable(
    root: Path,
    *,
    ownership: object | None = None,
) -> dict[str, object]:
    root = _assert_disposable_root(root, ownership=ownership)
    store: CoreStore | None = None
    restarted_store: CoreStore | None = None
    try:
        store = _new_store(root, ownership=ownership)
        workspace = create_sanitized_workspace(root / "workspace")
        (workspace / "notes" / "metadata.json").write_text(
            '{"fixture_kind": "metadata-only"}\n', encoding="utf-8", newline="\n"
        )

        store.initialize_vault()
        adapter = LocalGitWorkspaceCaptureProviderAdapter((workspace,))
        real_sink = RegisteredSourceCaptureApplicationSink(store)
        first_coordinator = CaptureCoordinator(store, sink=_CrashAfterFirstAdmission(real_sink))
        source = _create_source(first_coordinator, adapter)
        first = first_coordinator.run(source.id)

        restarted_store = CoreStore(root / "core.sqlite3")
        restarted_coordinator = CaptureCoordinator(
            restarted_store,
            sink=RegisteredSourceCaptureApplicationSink(restarted_store),
        )
        restarted_adapter = LocalGitWorkspaceCaptureProviderAdapter(
            (workspace,),
            state_reader=_workspace_state_reader(restarted_store),
        )
        restarted_coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, restarted_adapter)
        restarted_coordinator.resume(source.id)
        recovery = restarted_coordinator.run(source.id)
        after_recovery = _query_capture_aggregate(restarted_store, source.id)
        replay = restarted_coordinator.run(source.id)
        after_replay = _query_capture_aggregate(restarted_store, source.id)

        overflow = _overflow_probe(root, restarted_store)
        overflow_run = cast(dict[str, object], overflow["run"])
        manifest = restarted_adapter.capability_manifest
        scan = restarted_adapter.last_scan_report
        if scan is None:
            raise AssertionError("capture probe did not produce a scan report")

        capture = {
            "manifest_coverage": manifest.coverage,
            "manifest_availability": manifest.availability,
            "network_access": manifest.network_access,
            "data_egress_count": 0 if manifest.data_egress == () else -1,
            "scan": {
                "files_considered": scan.files_considered,
                "items_emitted": scan.items_emitted,
                "excluded_paths": scan.excluded_paths,
                "credential_like_paths": scan.credential_like_paths,
                "incomplete": scan.incomplete,
            },
            "first_run": _run_result(first),
            "recovery_run": _run_result(recovery),
            "replay_run": _run_result(replay),
            "after_recovery": after_recovery,
            "after_replay": after_replay,
        }
        acceptance = {
            "bounded_admission": (
                after_recovery["candidate_count"] == 4
                and after_recovery["record_count"] == 4
                and after_recovery["fact_class_counts"]
                == {
                    "markdown_documentation": 2,
                    "python_source": 1,
                    "shell_script": 1,
                }
            ),
            "deterministic_no_fact": (
                after_recovery["receipt_counts"]
                == {
                    "registered-source-fact": 4,
                    "registered-source-no-fact": 1,
                }
                and after_recovery["event_count"] == 5
            ),
            "partial_coverage_truth": capture["manifest_coverage"] == "partial"
            and capture["manifest_availability"] == "partial",
            "local_only_capability": _accepts_local_only_capability(manifest),
            "incomplete_fails_closed": (
                overflow_run["status"] == "failed"
                and overflow_run["error_code"] == "capture_adapter_unavailable"
                and overflow["scan_incomplete"] is True
                and overflow["candidate_count"] == 0
                and overflow["record_count"] == 0
            ),
            "restart_replay_idempotent": (
                first.status == "failed"
                and recovery.status == "completed"
                and replay.status == "completed"
                and after_recovery == after_replay
                and replay.applied_events == 0
            ),
            "content_free_identifier_safe": (
                bool(after_recovery["payload_shape_ok"])
                and bool(after_recovery["content_free_projection"])
            ),
        }
        report: dict[str, object] = {
            "schema_version": 1,
            "boundary": "packet-h-a-source-admission",
            "capture": capture,
            "incomplete_probe": overflow,
            "acceptance": acceptance,
            "aggregate_receipt": {
                "receipt_type": "packet-h-a-aggregate",
                "status": "pass" if all(acceptance.values()) else "fail",
                "identifier_digest": _stable_digest(
                    _h_a_digest_material(capture, overflow, acceptance)
                ),
            },
        }
        if not _packet_h_a_report_ready(report, require_passing=False):
            raise AssertionError("packet H-A report is not identifier-safe")
        return report
    finally:
        _close_core_stores(restarted_store, store)


def run() -> dict[str, object]:
    """Execute H-A entirely inside a temporary, runner-owned Core vault."""

    with _runner_owned_temporary_root(_DISPOSABLE_PREFIX) as (root, ownership):
        report = _run_disposable(root, ownership=ownership)
    if root.exists():
        raise RuntimeError("packet_h_temporary_state_not_removed")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    receipt = report.get("aggregate_receipt")
    return 0 if isinstance(receipt, dict) and receipt.get("status") == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

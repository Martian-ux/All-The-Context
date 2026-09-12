"""Core-owned admission for the closed registered-source fact seam.

This module is intentionally an internal sink.  CoreService and the
contributor CLI inject it through ``capture_runtime`` composition.  Workspace
payloads are treated as untrusted metadata and never become fact text,
evidence, or structured values.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, NoReturn

from .capture import (
    CaptureApplicationReceipt,
    CaptureApplicationSink,
    CaptureError,
    CaptureEvent,
    CaptureRunHandle,
    _canonical_lineage,
    _idempotency_key,
)
from .ids import utc_now
from .memory_policy import (
    AUTOMATIC_POLICY_VERSION,
    REGISTERED_SOURCE_CODE_OWNED_SCOPES,
    REGISTERED_SOURCE_EXTRACTOR_ID,
    REGISTERED_SOURCE_EXTRACTOR_VERSION,
    REGISTERED_SOURCE_FACT_CLASSES,
    REGISTERED_SOURCE_FACT_KIND,
    REGISTERED_SOURCE_FACT_SCHEMA,
    REGISTERED_SOURCE_FACT_SENTENCES,
    REGISTERED_SOURCE_MAX_SCOPES,
    REGISTERED_SOURCE_PROVIDER,
    REGISTERED_SOURCE_SCOPE_RE,
    REGISTERED_SOURCE_TYPE,
    ObservationOrigin,
    registered_source_fact_evidence,
    registered_source_reference,
)
from .models import Availability, CandidateInput, ObservationDisposition, Sensitivity
from .storage import CoreStore, _hash_text, _json

REGISTERED_SOURCE_CAPTURE_SCHEMA_VERSION = 1
_MAX_RELATIVE_PATH_CHARS = 512
_MAX_FILE_BYTES = 256 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_WORKSPACE_FINGERPRINT = re.compile(r"^workspace-source-[0-9a-f]{64}$")
_RECEIPT = re.compile(r"^registered-source-[a-z-]+(?::[0-9a-f]{64})?$")

_PROJECT_MANIFESTS = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "go.sum",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "pipfile",
        "poetry.lock",
        "makefile",
        "cmakelists.txt",
    }
)


def _reject() -> NoReturn:
    """Raise a bounded error without exposing validation material."""

    raise CaptureError("capture_sink_failed")


def _safe_receipt(value: str) -> str:
    if len(value) > 96 or _RECEIPT.fullmatch(value) is None:
        _reject()
    return value


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _safe_scopes(value: str) -> tuple[str, ...]:
    loaded = _json_object(value)
    if loaded is not None:
        _reject()
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        _reject()
    if (
        not isinstance(raw, list)
        or not raw
        or len(raw) > REGISTERED_SOURCE_MAX_SCOPES
        or any(
            not isinstance(item, str) or REGISTERED_SOURCE_SCOPE_RE.fullmatch(item) is None
            for item in raw
        )
        or tuple(raw) != REGISTERED_SOURCE_CODE_OWNED_SCOPES
    ):
        _reject()
    return REGISTERED_SOURCE_CODE_OWNED_SCOPES


def _fact_class(payload: Mapping[str, Any]) -> str | None:
    relative_path = payload.get("relative_path")
    if (
        not isinstance(relative_path, str)
        or not 1 <= len(relative_path) <= _MAX_RELATIVE_PATH_CHARS
        or relative_path.startswith(("/", "\\"))
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in PurePosixPath(relative_path).parts)
    ):
        return None
    if payload.get("kind") != "text":
        return None
    size = payload.get("size")
    content_hash = payload.get("content_sha256")
    truncated = payload.get("content_truncated")
    hash_scope = payload.get("hash_scope")
    if (
        type(size) is not int
        or not 0 <= size <= _MAX_FILE_BYTES * 1024
        or not isinstance(content_hash, str)
        or _HEX64.fullmatch(content_hash) is None
        or type(truncated) is not bool
        or hash_scope not in {"full", "sample"}
    ):
        return None
    basename = PurePosixPath(relative_path).name.casefold()
    suffix = PurePosixPath(relative_path).suffix.casefold()
    if basename in _PROJECT_MANIFESTS:
        return "project_manifest"
    if suffix in {".py", ".pyw"}:
        return "python_source"
    if suffix in {".md", ".markdown"}:
        return "markdown_documentation"
    if suffix in {".sh", ".bash"}:
        return "shell_script"
    if suffix in {".ps1", ".psm1", ".psd1"}:
        return "powershell_script"
    if suffix in {".txt", ".rst"}:
        return "generic_text_file"
    return None


_EXTRACTOR_REGISTRY: Mapping[str, Callable[[Mapping[str, Any]], str | None]] = MappingProxyType(
    {REGISTERED_SOURCE_PROVIDER: _fact_class}
)


def _binding_hash(
    *,
    vault_id: str,
    capture_source_id: str,
    provider: str,
    account_fingerprint: str,
    event: sqlite3.Row,
    extractor_id: str,
    extractor_version: int,
    fact_class: str,
) -> str:
    material = [
        "registered-source-binding-v1",
        vault_id,
        capture_source_id,
        provider,
        account_fingerprint,
        str(event["id"]),
        str(event["provider_event_id"]),
        str(event["provider_item_id"]),
        int(event["generation"]),
        str(event["order_key"]),
        str(event["operation"]),
        str(event["payload_hash"]),
        str(event["idempotency_key"]),
        extractor_id,
        extractor_version,
        fact_class,
    ]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


class RegisteredSourceCaptureApplicationSink(CaptureApplicationSink):
    """Concrete Core-owned sink for local-git-workspace structural facts."""

    def __init__(self, store: CoreStore, *, clock: Callable[[], str] = utc_now) -> None:
        self.store = store
        self.clock = clock

    def _validate_run_source_event(
        self,
        connection: sqlite3.Connection,
        event: CaptureEvent,
        *,
        source_id: str,
        event_id: str,
        run_handle: CaptureRunHandle,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row, str, str, bool]:
        if not isinstance(run_handle, CaptureRunHandle):
            _reject()
        vault = connection.execute("SELECT id FROM vaults ORDER BY created_at LIMIT 1").fetchone()
        source = connection.execute(
            "SELECT * FROM capture_sources WHERE id=?", (source_id,)
        ).fetchone()
        if vault is None or source is None or str(source["vault_id"]) != str(vault["id"]):
            _reject()
        if (
            str(source["provider"]) != REGISTERED_SOURCE_PROVIDER
            or not bool(source["local_only"])
            or not bool(source["local_only_acknowledged"])
            or str(source["lifecycle_state"]) != "reconciling"
            or not isinstance(source["account_fingerprint"], str)
            or _WORKSPACE_FINGERPRINT.fullmatch(str(source["account_fingerprint"])) is None
        ):
            _reject()
        _safe_scopes(str(source["requested_scopes_json"]))
        now = self.clock()
        run = connection.execute(
            "SELECT r.id,r.source_id,r.lease_token,r.state,r.lease_expires_at "
            "FROM capture_runs r WHERE r.id=? AND r.source_id=?",
            (run_handle.run_id, source_id),
        ).fetchone()
        if (
            run is None
            or str(run["lease_token"]) != run_handle.lease_token
            or str(run["state"]) != "running"
            or str(run["lease_expires_at"]) <= now
            or run_handle.source_id != source_id
        ):
            raise CaptureError("capture_lease_expired")
        stored = connection.execute(
            "SELECT * FROM capture_events WHERE id=? AND source_id=?", (event_id, source_id)
        ).fetchone()
        if stored is None:
            _reject()
        payload_json, payload_hash = event.normalized()
        purged = (
            connection.execute(
                "SELECT 1 FROM purge_tombstones WHERE vault_id=? AND target_type='record' "
                "AND stable_id=?",
                (str(vault["id"]), canonical_record_id),
            ).fetchone()
            is not None
        )
        if (
            str(stored["provider_event_id"]) != event.provider_event_id
            or str(stored["provider_item_id"]) != event.provider_item_id
            or str(stored["operation"]) != event.operation
            or int(stored["generation"]) != event.generation
            or str(stored["order_key"]) != event.order_key
            or (str(stored["payload_hash"]) != payload_hash and not purged)
            or str(stored["idempotency_key"]) != idempotency_key
            or idempotency_key != _idempotency_key(source_id, event.provider_event_id)
            or canonical_record_id != _canonical_lineage(source_id, event.provider_item_id)
        ):
            _reject()
        if not purged and str(stored["normalized_payload_json"]) != payload_json:
            _reject()
        return stored, source, str(vault["id"]), payload_json, purged

    @staticmethod
    def _projection_matches(
        row: sqlite3.Row,
        candidate: CandidateInput,
        *,
        event_id: str,
        source_id: str,
        binding_hash: str,
    ) -> bool:
        expected = candidate.model_dump(mode="json")
        json_fields = {
            "structured_value_json": _json(candidate.structured_value),
            "scopes_json": _json(candidate.scopes),
            "tags_json": _json(candidate.tags),
            "allowed_clients_json": _json(candidate.allowed_clients),
            "denied_clients_json": _json(candidate.denied_clients),
        }
        fields = {
            "source_id": None,
            "source_reference": expected["source_reference"],
            "kind": expected["kind"],
            "content": expected["content"],
            "source_service": expected["source_service"],
            "source_type": expected["source_type"],
            "evidence": expected["evidence"],
            "confidence": expected["confidence"],
            "sensitivity": expected["sensitivity"],
            "availability": expected["availability"],
            "explicit_user_statement": 0,
            "schema_version": expected["schema_version"],
            "capture_source_id": source_id,
            "capture_event_id": event_id,
            "capture_binding_hash": binding_hash,
            "idempotency_key": expected["idempotency_key"],
            "observed_at": expected["observed_at"],
        }
        return all(row[column] == value for column, value in fields.items()) and all(
            row[column] == value for column, value in json_fields.items()
        )

    @staticmethod
    def _record_matches(
        row: sqlite3.Row,
        *,
        canonical_record_id: str,
        candidate: CandidateInput,
        candidate_id: str,
    ) -> bool:
        expected = candidate.model_dump(mode="json")
        json_fields = {
            "structured_value_json": _json(candidate.structured_value),
            "scopes_json": _json(candidate.scopes),
            "tags_json": _json(candidate.tags),
            "allowed_clients_json": _json(candidate.allowed_clients),
            "denied_clients_json": _json(candidate.denied_clients),
        }
        fields = {
            "id": canonical_record_id,
            "candidate_id": candidate_id,
            "source_id": None,
            "source_reference": expected["source_reference"],
            "kind": expected["kind"],
            "content": expected["content"],
            "source_service": expected["source_service"],
            "source_type": REGISTERED_SOURCE_TYPE,
            "evidence": expected["evidence"],
            "confidence": expected["confidence"],
            "sensitivity": expected["sensitivity"],
            "availability": expected["availability"],
            "valid_from": expected["valid_from"],
            "expires_at": expected["expires_at"],
            "supersedes": expected["supersedes"],
            "explicit_user_statement": 0,
            "approval_status": "approved",
            "content_hash": _hash_text(candidate.content),
            "schema_version": expected["schema_version"],
            "observed_at": expected["observed_at"],
            "observation_origin": ObservationOrigin.REGISTERED_SOURCE.value,
            "policy_version": "automatic-v1",
        }
        return (
            all(row[column] == value for column, value in fields.items())
            and all(row[column] == value for column, value in json_fields.items())
            and (row["record_key"] is None and row["deleted_at"] is None)
        )

    def _existing_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        event: sqlite3.Row,
        candidate: sqlite3.Row | None,
        expected_candidate: CandidateInput | None,
        source_id: str,
        event_id: str,
        binding_hash: str | None,
        canonical_record_id: str,
        validate_record_projection: bool,
    ) -> CaptureApplicationReceipt:
        if candidate is not None and (
            expected_candidate is None
            or binding_hash is None
            or not self._projection_matches(
                candidate,
                expected_candidate,
                event_id=event_id,
                source_id=source_id,
                binding_hash=binding_hash,
            )
        ):
            _reject()
        raw_receipt = str(event["application_receipt"] or "")
        if raw_receipt:
            receipt = _safe_receipt(raw_receipt)
        elif expected_candidate is not None and binding_hash is not None and candidate is not None:
            receipt = (
                "registered-source-no-influence"
                if str(candidate["disposition"]) == ObservationDisposition.IGNORED.value
                else "registered-source-fact:" + binding_hash
            )
        else:
            _reject()
        if candidate is not None and str(candidate["record_id"] or ""):
            if str(candidate["record_id"]) != canonical_record_id:
                _reject()
            if (
                str(candidate["disposition"]) == ObservationDisposition.IGNORED.value
                or not validate_record_projection
            ):
                return CaptureApplicationReceipt(
                    (
                        "registered-source-no-influence"
                        if str(candidate["disposition"]) == ObservationDisposition.IGNORED.value
                        else receipt
                    ),
                    canonical_record_id,
                )
            record = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (candidate["record_id"],)
            ).fetchone()
            if (
                expected_candidate is None
                or record is None
                or not self._record_matches(
                    record,
                    canonical_record_id=canonical_record_id,
                    candidate=expected_candidate,
                    candidate_id=str(candidate["id"]),
                )
            ):
                _reject()
        return CaptureApplicationReceipt(receipt, canonical_record_id)

    def _consume_registered_source_barrier(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
        canonical_record_id: str,
        reason: str,
    ) -> CaptureApplicationReceipt:
        record_exists = connection.execute(
            "SELECT 1 FROM context_records WHERE id=?", (canonical_record_id,)
        ).fetchone()
        self.store._set_observation_decision_tx(
            connection,
            candidate_id,
            disposition=ObservationDisposition.IGNORED,
            reason=f"registered source influence blocked: {reason}",
            policy_version=AUTOMATIC_POLICY_VERSION,
            origin=ObservationOrigin.REGISTERED_SOURCE,
            record_id=canonical_record_id if record_exists is not None else None,
            actor="local-core",
        )
        if record_exists is not None:
            self.store._link_observation_tx(
                connection,
                candidate_id,
                canonical_record_id,
                "blocked_by_registered_source_barrier",
            )
        self.store._recompute_integrity(connection)
        return CaptureApplicationReceipt("registered-source-no-influence", canonical_record_id)

    def apply(
        self,
        event: CaptureEvent,
        *,
        source_id: str,
        event_id: str,
        run_handle: CaptureRunHandle,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> CaptureApplicationReceipt:
        try:
            with self.store.transaction() as connection:
                stored, source, vault_id, payload_json, purged = self._validate_run_source_event(
                    connection,
                    event,
                    source_id=source_id,
                    event_id=event_id,
                    run_handle=run_handle,
                    canonical_record_id=canonical_record_id,
                    idempotency_key=idempotency_key,
                )
                if purged:
                    connection.execute(
                        "UPDATE capture_events SET normalized_payload_json='{}' WHERE id=?",
                        (event_id,),
                    )
                    return CaptureApplicationReceipt(
                        "registered-source-purged", canonical_record_id
                    )
                payload = _json_object(payload_json)
                if payload is None:
                    _reject()
                extractor = _EXTRACTOR_REGISTRY.get(str(source["provider"]))
                fact_class = (
                    extractor(payload)
                    if extractor is not None and event.operation == "upsert"
                    else None
                )
                if str(stored["status"]) == "applied":
                    expected = None
                    binding_hash = None
                    if fact_class is not None:
                        binding_hash = _binding_hash(
                            vault_id=vault_id,
                            capture_source_id=source_id,
                            provider=str(source["provider"]),
                            account_fingerprint=str(source["account_fingerprint"]),
                            event=stored,
                            extractor_id=REGISTERED_SOURCE_EXTRACTOR_ID,
                            extractor_version=REGISTERED_SOURCE_EXTRACTOR_VERSION,
                            fact_class=fact_class,
                        )
                        expected = self._candidate(
                            source,
                            event,
                            binding_hash=binding_hash,
                            fact_class=fact_class,
                            observed_at=str(stored["received_at"]),
                        )
                    candidate = connection.execute(
                        "SELECT * FROM context_candidates WHERE capture_event_id=?",
                        (event_id,),
                    ).fetchone()
                    return self._existing_receipt(
                        connection,
                        event=stored,
                        candidate=candidate,
                        expected_candidate=expected,
                        source_id=source_id,
                        event_id=event_id,
                        binding_hash=binding_hash,
                        canonical_record_id=canonical_record_id,
                        validate_record_projection=False,
                    )
                if event.operation == "delete":
                    self.store._withdraw_registered_source_record_tx(
                        connection,
                        record_id=canonical_record_id,
                        capture_source_id=source_id,
                        provider_item_id=event.provider_item_id,
                        received_at=str(stored["received_at"]),
                    )
                    return CaptureApplicationReceipt(
                        "registered-source-withdrawn", canonical_record_id
                    )
                if fact_class is None or fact_class not in REGISTERED_SOURCE_FACT_CLASSES:
                    self.store._withdraw_registered_source_record_tx(
                        connection,
                        record_id=canonical_record_id,
                        capture_source_id=source_id,
                        provider_item_id=event.provider_item_id,
                        received_at=str(stored["received_at"]),
                    )
                    return CaptureApplicationReceipt(
                        "registered-source-no-fact", canonical_record_id
                    )
                binding_hash = _binding_hash(
                    vault_id=vault_id,
                    capture_source_id=source_id,
                    provider=str(source["provider"]),
                    account_fingerprint=str(source["account_fingerprint"]),
                    event=stored,
                    extractor_id=REGISTERED_SOURCE_EXTRACTOR_ID,
                    extractor_version=REGISTERED_SOURCE_EXTRACTOR_VERSION,
                    fact_class=fact_class,
                )
                candidate_input = self._candidate(
                    source,
                    event,
                    binding_hash=binding_hash,
                    fact_class=fact_class,
                    observed_at=str(stored["received_at"]),
                )
                existing = connection.execute(
                    "SELECT * FROM context_candidates WHERE capture_event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    barrier = self.store._registered_source_influence_barrier_tx(
                        connection,
                        canonical_record_id=canonical_record_id,
                        capture_source_id=source_id,
                        source_reference=str(candidate_input.source_reference),
                    )
                    if barrier is not None:
                        return self._consume_registered_source_barrier(
                            connection,
                            candidate_id=str(existing["id"]),
                            canonical_record_id=canonical_record_id,
                            reason=barrier,
                        )
                    return self._existing_receipt(
                        connection,
                        event=stored,
                        candidate=existing,
                        expected_candidate=candidate_input,
                        source_id=source_id,
                        event_id=event_id,
                        binding_hash=binding_hash,
                        canonical_record_id=canonical_record_id,
                        validate_record_projection=True,
                    )
                candidate_id = self.store._insert_candidate(
                    connection,
                    candidate_input,
                    None,
                    None,
                    capture_source_id=source_id,
                    capture_event_id=event_id,
                    capture_binding_hash=binding_hash,
                )
                self.store._evaluate_observation_tx(
                    connection,
                    candidate_id,
                    origin=ObservationOrigin.REGISTERED_SOURCE,
                    actor="local-core",
                    canonical_record_id=canonical_record_id,
                )
                candidate = connection.execute(
                    "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
                ).fetchone()
                record = connection.execute(
                    "SELECT * FROM context_records WHERE id=?", (canonical_record_id,)
                ).fetchone()
                if candidate is not None and (
                    str(candidate["disposition"]) == ObservationDisposition.IGNORED.value
                ):
                    return CaptureApplicationReceipt(
                        "registered-source-no-influence", canonical_record_id
                    )
                if (
                    candidate is None
                    or record is None
                    or str(candidate["record_id"]) != canonical_record_id
                    or not self._projection_matches(
                        candidate,
                        candidate_input,
                        event_id=event_id,
                        source_id=source_id,
                        binding_hash=binding_hash,
                    )
                    or not self._record_matches(
                        record,
                        canonical_record_id=canonical_record_id,
                        candidate=candidate_input,
                        candidate_id=candidate_id,
                    )
                ):
                    _reject()
                return CaptureApplicationReceipt(
                    "registered-source-fact:" + binding_hash,
                    canonical_record_id,
                )
        except CaptureError:
            raise
        except Exception as error:
            del error
            raise CaptureError("capture_sink_failed") from None

    @staticmethod
    def _candidate(
        source: sqlite3.Row,
        event: CaptureEvent,
        *,
        binding_hash: str,
        fact_class: str,
        observed_at: str,
    ) -> CandidateInput:
        scopes = _safe_scopes(str(source["requested_scopes_json"]))
        safe_schema = {
            "binding_hash": binding_hash,
            "extractor": REGISTERED_SOURCE_EXTRACTOR_ID,
            "extractor_version": REGISTERED_SOURCE_EXTRACTOR_VERSION,
            "fact_class": fact_class,
            "schema": REGISTERED_SOURCE_FACT_SCHEMA,
        }
        return CandidateInput(
            kind=REGISTERED_SOURCE_FACT_KIND,
            content=REGISTERED_SOURCE_FACT_SENTENCES[fact_class],
            structured_value=safe_schema,
            scopes=list(scopes),
            tags=[],
            source_id=None,
            source_reference=registered_source_reference(str(source["id"]), event.provider_item_id),
            source_service=str(source["provider"]),
            source_type=REGISTERED_SOURCE_TYPE,
            evidence=registered_source_fact_evidence(fact_class, binding_hash),
            confidence=1.0,
            sensitivity=Sensitivity.NORMAL,
            availability=Availability.CORE,
            allowed_clients=[],
            denied_clients=[],
            observed_at=observed_at,
            explicit_user_statement=False,
            idempotency_key=_idempotency_key(str(source["id"]), event.provider_event_id),
            schema_version=REGISTERED_SOURCE_CAPTURE_SCHEMA_VERSION,
        )


__all__ = ["RegisteredSourceCaptureApplicationSink"]

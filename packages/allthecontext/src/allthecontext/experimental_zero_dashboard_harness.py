"""Disposable Wave 2 Packet D zero-dashboard synthetic journey.

This module is an evidence harness, not a product runtime.  It composes the
existing capture ledger/coordinator, Core observation policy, lifecycle
contract, and Retrieval V3 facade over one temporary Core database.  Its
isolated projection-contract check is content-free component evidence only;
it is not Memory Lab M3, Core, or Retrieval integration.  The only state kept
beside Core is receipt correlation needed by the injected test sink; it is
never a source, cursor, observation, record, or retrieval authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from unittest.mock import patch

from . import retrieval as retrieval_module
from . import storage as storage_module
from .capture import (
    CaptureApplicationReceipt,
    CaptureCapabilityManifest,
    CaptureCoordinator,
    CaptureEvent,
    CapturePage,
    CaptureRetryableError,
    CaptureSource,
    DeterministicFakeAdapter,
    IdempotentFakeSink,
)
from .client_runtime import (
    ALL_LIFECYCLE_HOOKS,
    ClientLifecycleEnvelope,
    ContextDeliveryReceipt,
    ContextRequestPayload,
    DeterministicFakeClientRuntimeHost,
    DirectUserTurnPayload,
    PayloadReference,
    UnsupportedHookReport,
)
from .experimental_event_observation import (
    AuthorizationApplicability,
    ContentInterpretation,
    EventLineage,
    EventObservationInput,
    EvidenceClass,
    FormationResult,
    ItemLineage,
    PayloadKind,
    RetentionPolicy,
    SourceLineage,
    WitnessClass,
    form_observation,
    narrow_proposal_authorization,
)
from .experimental_event_observation import (
    ObservationDisposition as FormationDisposition,
)
from .experimental_event_observation import (
    RetentionClass as FormationRetentionClass,
)
from .experimental_event_reconciliation import (
    DependencyWithdrawal,
    EventReconciliationInput,
    normalize_capture_event,
    normalize_lifecycle_event,
)
from .experimental_projection_contract import (
    DependencyDeclaration,
    InvalidationAction,
    InvalidationCause,
    InvalidationDeclaration,
    ProjectionDeclaration,
    ProjectionKind,
    ProjectionPlan,
    ProjectionSeed,
    dependency_closure,
    rebuild_projection,
)
from .memory_lab_m3 import InfluenceClass
from .models import (
    Availability,
    BootstrapRequest,
    CandidateInput,
    CandidateOut,
    ContextRecordOut,
    MemoryTruthStatus,
    Sensitivity,
)
from .retrieval import RetrievalEngine
from .security import ClientPrincipal
from .storage import CoreStore, NotFoundError

ZERO_DASHBOARD_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
ZERO_DASHBOARD_AFTER_EXPIRY = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
ZERO_DASHBOARD_EXPIRY = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
ZERO_DASHBOARD_CAPTURE_PROVIDER = "fake"
ZERO_DASHBOARD_PROJECT_SCOPE = "project:atlas"


@dataclass(frozen=True, slots=True)
class SyntheticDirectTurn:
    """Sanitized direct-turn data paired with an opaque host payload reference."""

    reference: str
    content: str
    kind: str = "interaction_preference"


@dataclass(frozen=True, slots=True)
class ZeroDashboardFixture:
    """Typed, deterministic fixture input; all text remains inert evidence data."""

    pages: tuple[CapturePage, ...]
    direct_turns: tuple[SyntheticDirectTurn, ...]

    @classmethod
    def from_json(cls, path: Path) -> ZeroDashboardFixture:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("zero-dashboard fixture must be an object")
        pages_raw = raw.get("pages")
        turns_raw = raw.get("direct_turns")
        if not isinstance(pages_raw, list) or not isinstance(turns_raw, list):
            raise ValueError("zero-dashboard fixture has invalid top-level shape")
        pages = tuple(_fixture_page(item) for item in pages_raw)
        turns = tuple(_fixture_turn(item) for item in turns_raw)
        if not pages or len(turns) < 2:
            raise ValueError("zero-dashboard fixture is incomplete")
        return cls(pages=pages, direct_turns=turns)


@dataclass(frozen=True, slots=True)
class ZeroDashboardScorecard:
    """Non-compensable phase gates; ``passed`` is an all-gates conjunction."""

    required_first_useful_context: bool
    context_correctness: bool
    correction_propagation: bool
    replay_duplicates: bool
    user_intervention: bool
    resume_behavior: bool
    capability_truth: bool
    formation_and_projection_contract: bool
    secret_refusal: bool
    retention_and_expiry: bool
    ordinary_delete: bool
    terminal_purge: bool
    compile_latency_ms: float
    restart_context_latency_ms: float
    compile_latency_bound_ms: float = 5_000.0
    restart_context_latency_bound_ms: float = 5_000.0

    @property
    def passed(self) -> bool:
        return all(
            (
                self.required_first_useful_context,
                self.context_correctness,
                self.correction_propagation,
                self.replay_duplicates,
                self.user_intervention,
                self.resume_behavior,
                self.capability_truth,
                self.formation_and_projection_contract,
                self.secret_refusal,
                self.retention_and_expiry,
                self.ordinary_delete,
                self.terminal_purge,
                self.compile_latency_ms <= self.compile_latency_bound_ms,
                self.restart_context_latency_ms <= self.restart_context_latency_bound_ms,
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "required_first_useful_context": self.required_first_useful_context,
            "context_correctness": self.context_correctness,
            "correction_propagation": self.correction_propagation,
            "replay_duplicates": self.replay_duplicates,
            "user_intervention": self.user_intervention,
            "resume_behavior": self.resume_behavior,
            "capability_truth": self.capability_truth,
            "formation_and_projection_contract": self.formation_and_projection_contract,
            "secret_refusal": self.secret_refusal,
            "retention_and_expiry": self.retention_and_expiry,
            "ordinary_delete": self.ordinary_delete,
            "terminal_purge": self.terminal_purge,
            "compile_latency_ms": self.compile_latency_ms,
            "restart_context_latency_ms": self.restart_context_latency_ms,
            "compile_latency_bound_ms": self.compile_latency_bound_ms,
            "restart_context_latency_bound_ms": self.restart_context_latency_bound_ms,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class ZeroDashboardJourneyReceipt:
    """Sanitized evidence returned by one disposable journey."""

    scorecard: ZeroDashboardScorecard
    first_context: tuple[str, ...]
    corrected_context: tuple[str, ...]
    viewer_before_context: tuple[str, ...]
    pre_purge_context: tuple[str, ...]
    final_context: tuple[str, ...]
    viewer_context: tuple[str, ...]
    capture_source_id: str
    capture_event_count: int
    observation_count: int
    current_record_count: int
    restart_context_latency_ms: float


@dataclass(frozen=True, slots=True)
class _DirectTurnResult:
    candidate: CandidateOut
    formation_supersedes_observation_ref: str | None


def _fixture_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 16_384:
        raise ValueError(f"invalid fixture {field}")
    return value


def _fixture_page(value: object) -> CapturePage:
    if not isinstance(value, Mapping):
        raise ValueError("invalid zero-dashboard page")
    events_raw = value.get("events")
    if not isinstance(events_raw, list):
        raise ValueError("invalid zero-dashboard page events")
    events = tuple(_fixture_event(item) for item in events_raw)
    next_cursor = value.get("next_cursor")
    if next_cursor is not None:
        next_cursor = _fixture_text(next_cursor, field="cursor")
    return CapturePage(
        generation=_fixture_int(value.get("generation"), field="generation"),
        page_order=_fixture_int(value.get("page_order"), field="page_order"),
        events=events,
        next_cursor=next_cursor,
        done=_fixture_bool(value.get("done"), field="done"),
    )


def _fixture_event(value: object) -> CaptureEvent:
    if not isinstance(value, Mapping):
        raise ValueError("invalid zero-dashboard event")
    payload = value.get("payload", {})
    if not isinstance(payload, Mapping):
        raise ValueError("invalid zero-dashboard event payload")
    operation = value.get("operation", "upsert")
    if operation not in {"upsert", "delete"}:
        raise ValueError("invalid zero-dashboard operation")
    return CaptureEvent(
        provider_event_id=_fixture_text(value.get("provider_event_id"), field="event id"),
        provider_item_id=_fixture_text(value.get("provider_item_id"), field="item id"),
        order_key=_fixture_text(value.get("order_key"), field="order key"),
        operation=cast(Any, operation),
        payload=cast(Mapping[str, Any], payload),
        generation=_fixture_int(value.get("generation"), field="generation"),
    )


def _fixture_turn(value: object) -> SyntheticDirectTurn:
    if not isinstance(value, Mapping):
        raise ValueError("invalid zero-dashboard direct turn")
    return SyntheticDirectTurn(
        reference=_fixture_text(value.get("reference"), field="turn reference"),
        content=_fixture_text(value.get("content"), field="turn content"),
        kind=_fixture_text(value.get("kind", "interaction_preference"), field="turn kind"),
    )


def _fixture_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid fixture {field}")
    return value


def _fixture_bool(value: object, *, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"invalid fixture {field}")
    return value


def default_zero_dashboard_fixture() -> ZeroDashboardFixture:
    """Return the fixed in-code control fixture for callers outside the test tree."""

    return ZeroDashboardFixture(
        pages=(
            CapturePage(
                generation=1,
                page_order=0,
                done=False,
                next_cursor="cursor-1",
                events=(
                    CaptureEvent(
                        provider_event_id="capture-project",
                        provider_item_id="item-project",
                        order_key="1",
                        generation=1,
                        payload={
                            "kind": "project_decision",
                            "content": "Atlas uses deterministic local retrieval.",
                            "entity_key": "atlas",
                            "attribute_key": "retrieval_mode",
                            "scopes": [ZERO_DASHBOARD_PROJECT_SCOPE],
                            "source_reference": "fixture-project",
                            "explicit_user_statement": True,
                            "project_ref": ZERO_DASHBOARD_PROJECT_SCOPE,
                        },
                    ),
                    CaptureEvent(
                        provider_event_id="capture-private",
                        provider_item_id="item-private",
                        order_key="2",
                        generation=1,
                        payload={
                            "kind": "project_decision",
                            "content": "Atlas private staging uses a bounded fixture.",
                            "entity_key": "atlas",
                            "attribute_key": "private_fixture",
                            "scopes": [ZERO_DASHBOARD_PROJECT_SCOPE],
                            "source_reference": "fixture-private",
                            "explicit_user_statement": True,
                            "restrict_to_client": True,
                            "project_ref": ZERO_DASHBOARD_PROJECT_SCOPE,
                        },
                    ),
                    CaptureEvent(
                        provider_event_id="capture-delete-target",
                        provider_item_id="item-delete-target",
                        order_key="3",
                        generation=1,
                        payload={
                            "kind": "working_state",
                            "content": "Temporary deletion fixture for Atlas.",
                            "entity_key": "atlas",
                            "attribute_key": "delete_fixture",
                            "scopes": [ZERO_DASHBOARD_PROJECT_SCOPE],
                            "source_reference": "fixture-delete-target",
                            "explicit_user_statement": True,
                            "project_ref": ZERO_DASHBOARD_PROJECT_SCOPE,
                        },
                    ),
                ),
            ),
            CapturePage(
                generation=1,
                page_order=1,
                done=True,
                events=(
                    CaptureEvent(
                        provider_event_id="capture-other-project",
                        provider_item_id="item-other-project",
                        order_key="4",
                        generation=1,
                        payload={
                            "kind": "project_decision",
                            "content": "Atlas uses a separate Neptune source.",
                            "entity_key": "neptune",
                            "attribute_key": "retrieval_mode",
                            "scopes": ["project:neptune"],
                            "source_reference": "fixture-other-project",
                            "explicit_user_statement": True,
                            "project_ref": "project:neptune",
                        },
                    ),
                    CaptureEvent(
                        provider_event_id="capture-expired",
                        provider_item_id="item-expired",
                        order_key="5",
                        generation=1,
                        payload={
                            "kind": "working_state",
                            "content": "Expired Atlas working-state fixture.",
                            "entity_key": "atlas",
                            "attribute_key": "expired_fixture",
                            "scopes": [ZERO_DASHBOARD_PROJECT_SCOPE],
                            "source_reference": "fixture-expired",
                            "expires_at": "2026-08-22T13:00:00+00:00",
                            "explicit_user_statement": True,
                            "project_ref": ZERO_DASHBOARD_PROJECT_SCOPE,
                        },
                    ),
                    CaptureEvent(
                        provider_event_id="capture-delete",
                        provider_item_id="item-delete-target",
                        order_key="6",
                        generation=1,
                        operation="delete",
                        payload={},
                    ),
                ),
            ),
        ),
        direct_turns=(
            SyntheticDirectTurn(
                reference="turn-preference-1",
                content="I prefer concise context packs.",
            ),
            SyntheticDirectTurn(
                reference="turn-correction-1",
                content="Atlas uses bounded local retrieval.",
                kind="correction",
            ),
        ),
    )


class _CursorSemanticsAdapter:
    """Select deterministic pages by persisted cursor, with an optional failure."""

    def __init__(
        self,
        pages: Sequence[CapturePage],
        *,
        manifest: CaptureCapabilityManifest,
        fail_once_cursor: str | None = None,
    ) -> None:
        self._pages_by_cursor: dict[str | None, CapturePage] = {}
        cursor: str | None = None
        for page in pages:
            if cursor in self._pages_by_cursor:
                raise ValueError("synthetic fixture has duplicate cursor boundary")
            self._pages_by_cursor[cursor] = page
            cursor = page.next_cursor
        self._manifest = manifest
        self._fail_once_cursor = fail_once_cursor
        self._failed = False
        self.calls: list[tuple[str | None, int]] = []
        self.pages_seen: list[CapturePage] = []

    @property
    def capability_manifest(self) -> CaptureCapabilityManifest:
        return self._manifest

    def fetch_page(self, source: CaptureSource, cursor: str | None, page_order: int) -> CapturePage:
        self.calls.append((cursor, page_order))
        if (
            self._fail_once_cursor is not None
            and self._fail_once_cursor == cursor
            and not self._failed
        ):
            self._failed = True
            raise CaptureRetryableError(retry_after_seconds=1)
        try:
            selected = self._pages_by_cursor[cursor]
        except KeyError as error:
            raise CaptureRetryableError(retry_after_seconds=1) from error
        delegate = DeterministicFakeAdapter((selected,), capability_manifest=self._manifest)
        page = delegate.fetch_page(source, cursor, page_order)
        self.pages_seen.append(page)
        return page


class _FormationCaptureSink:
    """Inject formation into Core while retaining only receipt correlations."""

    def __init__(self, store: CoreStore, principal: ClientPrincipal, core_source_id: str) -> None:
        self.store = store
        self.principal = principal
        self.core_source_id = core_source_id
        self.delegate = IdempotentFakeSink()
        self.normalized_events: list[EventReconciliationInput] = []
        self.formed: list[FormationResult] = []
        self.formed_retention: dict[str, RetentionPolicy] = {}
        self.delete_verified_before = False
        self.delete_verified_after = False

    def _durable_item_ids(self, provider_item_id: str) -> tuple[str | None, str | None]:
        candidates, _total = self.store.list_candidates(
            status=None,
            source_id=self.core_source_id,
            limit=500,
        )
        matches = [
            candidate for candidate in candidates if candidate.source_reference == provider_item_id
        ]
        if len(matches) > 1:
            raise RuntimeError("synthetic source item has ambiguous durable observations")
        if not matches:
            return None, None
        candidate = matches[0]
        return candidate.id, candidate.record_id

    def observation_id_for_item(self, provider_item_id: str) -> str:
        observation_id, _record_id = self._durable_item_ids(provider_item_id)
        if observation_id is None:
            raise RuntimeError("synthetic source item has no durable observation")
        return observation_id

    def record_id_for_item(self, provider_item_id: str) -> str:
        _observation_id, record_id = self._durable_item_ids(provider_item_id)
        if record_id is None:
            raise RuntimeError("synthetic source item has no durable current record")
        return record_id

    def apply(
        self,
        event: CaptureEvent,
        *,
        source_id: str,
        canonical_record_id: str,
        idempotency_key: str,
    ) -> str | CaptureApplicationReceipt:
        if idempotency_key not in self.delegate.receipts:
            payload = event.payload
            authorization = _authorization_from_payload(payload)
            retention = _retention_from_payload(payload)
            withdrawal = (
                (
                    DependencyWithdrawal(
                        dependency_ref=event.provider_item_id,
                        cause=InvalidationCause.ORDINARY_DELETE,
                        authorization_ref="synthetic-delete-authorization",
                        provider_item_id=event.provider_item_id,
                    ),
                )
                if event.operation == "delete"
                else ()
            )
            normalized = normalize_capture_event(
                event,
                source_id=source_id,
                source_cursor="cursor-synthetic",
                source_sequence=int(event.order_key),
                idempotency_key=idempotency_key,
                account_ref="synthetic-account",
                project_ref=(
                    str(payload["project_ref"])
                    if isinstance(payload.get("project_ref"), str)
                    else None
                ),
                event_time=ZERO_DASHBOARD_TIME,
                observed_time=ZERO_DASHBOARD_TIME,
                retention=retention,
                authorization=authorization,
                dependency_withdrawals=withdrawal,
            )
            self.normalized_events.append(normalized)
            if event.operation == "delete":
                _observation_id, record_id = self._durable_item_ids(event.provider_item_id)
                if record_id is None:
                    raise RuntimeError("synthetic delete has no durable current record")
                try:
                    before = self.store.get_record(record_id)
                except NotFoundError as error:
                    raise RuntimeError("synthetic delete target was not current") from error
                if before.id != record_id:
                    raise RuntimeError("synthetic delete durable lookup returned wrong record")
                self.delete_verified_before = True
                self.store.delete_record(
                    record_id,
                    reason="synthetic capture deletion",
                    actor="synthetic-capture",
                )
                try:
                    self.store.get_record(record_id)
                except NotFoundError:
                    self.delete_verified_after = True
                else:
                    raise RuntimeError("synthetic delete target remained retrievable")
            else:
                proposal_result = form_observation(
                    _capture_observation_input(event, normalized, authorization),
                    as_of=ZERO_DASHBOARD_TIME,
                    refusal_ref=f"formation-{event.provider_event_id}",
                )
                self.formed.append(proposal_result)
                if not proposal_result.accepted or proposal_result.proposal is None:
                    raise RuntimeError("synthetic capture formation refused")
                proposal = proposal_result.proposal
                if proposal.retention != normalized.retention:
                    raise RuntimeError("synthetic retention lineage changed during formation")
                self.formed_retention[event.provider_event_id] = proposal.retention
                if payload.get("restrict_to_client") is True:
                    proposal = narrow_proposal_authorization(
                        proposal,
                        AuthorizationApplicability(
                            allowed_principals=frozenset({self.principal.id})
                        ),
                    )
                candidate = _candidate_from_proposal(
                    proposal,
                    payload=payload,
                    source_id=self.core_source_id,
                    source_reference=event.provider_item_id,
                    source_type="synthetic_capture",
                    idempotency_key=idempotency_key,
                )
                applied = self.store.add_candidate(candidate, client=self.principal)
                if applied.record_id is None:
                    raise RuntimeError("synthetic source observation did not become current")
            receipt = self.delegate.apply(
                event,
                source_id=source_id,
                canonical_record_id=canonical_record_id,
                idempotency_key=idempotency_key,
            )
        else:
            receipt = self.delegate.apply(
                event,
                source_id=source_id,
                canonical_record_id=canonical_record_id,
                idempotency_key=idempotency_key,
            )
        return CaptureApplicationReceipt(receipt, canonical_record_id)


def run_zero_dashboard_journey(
    database_path: Path,
    *,
    fixture: ZeroDashboardFixture | None = None,
) -> ZeroDashboardJourneyReceipt:
    """Run the complete disposable journey against ``database_path``.

    The caller owns the temporary path.  This function never opens a dashboard,
    operator vault, network connection, provider client, or existing Core path.
    """

    if not isinstance(database_path, Path):
        raise TypeError("database_path must be a pathlib.Path")
    selected_fixture = fixture or default_zero_dashboard_fixture()
    store = CoreStore(database_path)
    restarted_store: CoreStore | None = None
    storage_clock = patch.object(storage_module, "utc_now", return_value=_iso(ZERO_DASHBOARD_TIME))
    storage_clock.start()
    try:
        store.initialize_vault()
        principal, _token = store.create_client(_client_request())
        core_source = store.add_source(
            b"synthetic zero-dashboard capture source",
            source_service="synthetic-capture",
            source_type="fixture",
        )
        manifest = CaptureCapabilityManifest(
            provider=ZERO_DASHBOARD_CAPTURE_PROVIDER,
            source_deletion="coordinated",
            purge_coordination="coordinated",
            network_access="denied",
            data_egress=(),
        )
        sink = _FormationCaptureSink(store, principal, core_source.id)
        coordinator = CaptureCoordinator(store, sink=sink, clock=lambda: _iso(ZERO_DASHBOARD_TIME))
        source = coordinator.create_source(
            provider=ZERO_DASHBOARD_CAPTURE_PROVIDER,
            account_label="synthetic-local-account",
            requested_scopes=(ZERO_DASHBOARD_PROJECT_SCOPE,),
            local_only_acknowledged=True,
        )
        coordinator.enable(source.id)
        interrupted_adapter = _CursorSemanticsAdapter(
            selected_fixture.pages,
            manifest=manifest,
            fail_once_cursor="cursor-1",
        )
        coordinator.register_adapter(ZERO_DASHBOARD_CAPTURE_PROVIDER, interrupted_adapter)
        first_capture = coordinator.run(source.id)
        checkpoint_after_failure = coordinator.ledger._checkpoint(source.id)
        if first_capture.error_code != "capture_retryable_failure":
            raise RuntimeError("synthetic retry fixture did not fail at the cursor boundary")
        if checkpoint_after_failure["cursor"] != "cursor-1":
            raise RuntimeError("synthetic capture cursor was not committed before retry")
        if interrupted_adapter.calls != [(None, 0), ("cursor-1", 1)]:
            raise RuntimeError("synthetic retry adapter did not use cursor semantics")

        store.close()
        recovery_started = perf_counter()
        restarted_store = CoreStore(database_path)
        restarted_store.initialize_vault()
        recovery_sink = _FormationCaptureSink(restarted_store, principal, core_source.id)
        recovery_coordinator = CaptureCoordinator(
            restarted_store,
            sink=recovery_sink,
            clock=lambda: _iso(ZERO_DASHBOARD_TIME),
        )
        recovery_coordinator.resume(source.id)
        recovery_adapter = _CursorSemanticsAdapter(
            selected_fixture.pages,
            manifest=manifest,
        )
        recovery_coordinator.register_adapter(ZERO_DASHBOARD_CAPTURE_PROVIDER, recovery_adapter)
        recovered_capture = recovery_coordinator.run(source.id)
        if recovered_capture.status != "completed":
            raise RuntimeError("synthetic capture did not recover")
        if recovery_adapter.calls[:1] != [("cursor-1", 0)]:
            raise RuntimeError("synthetic resumed adapter did not start from persisted cursor")
        if not recovery_adapter.pages_seen or recovery_adapter.pages_seen[0].page_order != 1:
            raise RuntimeError("synthetic resumed adapter selected the wrong page")

        runtime = DeterministicFakeClientRuntimeHost.for_level(
            "L2", client_id=principal.id, session_id="synthetic-session-1"
        )
        unsupported_consequence_report = runtime.record_consequence_checkpoint(
            checkpoint_kind="response_emitted",
            status="not_observed",
        )
        retrieval = RetrievalEngine(restarted_store)
        first_context, first_latency = _compile_before_generation(
            runtime,
            retrieval,
            principal,
            generation_id="generation-1",
            as_of=ZERO_DASHBOARD_TIME,
        )
        restart_context_latency = (perf_counter() - recovery_started) * 1_000
        public_record_id = recovery_sink.record_id_for_item("item-project")
        public_observation_id = recovery_sink.observation_id_for_item("item-project")
        private_record_id = recovery_sink.record_id_for_item("item-private")
        delete_record_id = recovery_sink.record_id_for_item("item-delete-target")

        viewer = ClientPrincipal(
            "synthetic-viewer", "Synthetic viewer", frozenset({"context:read"})
        )
        viewer_request = BootstrapRequest(
            task_description="Atlas",
            requested_scopes=[ZERO_DASHBOARD_PROJECT_SCOPE],
            current_project="atlas",
            character_budget=4_000,
        )
        viewer_before_context, _viewer_before_latency = _bootstrap_at(
            retrieval,
            viewer_request,
            viewer,
            as_of=ZERO_DASHBOARD_TIME,
        )

        _add_direct_turn(
            store=restarted_store,
            host=runtime,
            principal=principal,
            turn=selected_fixture.direct_turns[0],
        )
        correction = _add_direct_turn(
            store=restarted_store,
            host=runtime,
            principal=principal,
            turn=selected_fixture.direct_turns[1],
            supersedes_observation_ref=public_observation_id,
            supersedes_record_id=public_record_id,
        )
        corrected_context, corrected_latency = _compile_before_generation(
            runtime,
            retrieval,
            principal,
            generation_id="generation-2",
            as_of=ZERO_DASHBOARD_TIME,
        )

        transition = runtime.record_session_transition(
            transition="restart",
            previous_session_id=runtime.current_session_id,
            next_session_id="synthetic-session-2",
        )
        if not isinstance(transition, ClientLifecycleEnvelope):
            raise RuntimeError("synthetic runtime restart hook was not exercised")
        normalized_transition = normalize_lifecycle_event(
            transition,
            event_time=ZERO_DASHBOARD_TIME,
            observed_time=ZERO_DASHBOARD_TIME,
        )

        replay_pre_counts = _core_counts(restarted_store)
        restarted_store.close()
        restarted_store = CoreStore(database_path)
        restarted_store.initialize_vault()
        replay_sink = _FormationCaptureSink(restarted_store, principal, core_source.id)
        replay_coordinator = CaptureCoordinator(
            restarted_store,
            sink=replay_sink,
            clock=lambda: _iso(ZERO_DASHBOARD_TIME),
        )
        replay_adapter = _CursorSemanticsAdapter(selected_fixture.pages, manifest=manifest)
        replay_coordinator.register_adapter(ZERO_DASHBOARD_CAPTURE_PROVIDER, replay_adapter)
        replay_capture = replay_coordinator.run(source.id)
        replay_post_counts = _core_counts(restarted_store)
        restarted_retrieval = RetrievalEngine(restarted_store)

        purge_candidate = restarted_store.add_candidate(
            CandidateInput(
                kind="project_decision",
                content="Terminal purge fixture for Atlas.",
                entity_key="atlas",
                attribute_key="purge_fixture",
                scopes=[ZERO_DASHBOARD_PROJECT_SCOPE],
                explicit_user_statement=True,
                source_reference="fixture-purge",
                source_type="synthetic_harness",
                idempotency_key="synthetic-purge-candidate",
            ),
            client=principal,
        )
        if purge_candidate.record_id is None:
            raise RuntimeError("purge fixture did not become current")
        purge_record_id = purge_candidate.record_id
        pre_purge_record = restarted_store.get_record(purge_record_id)
        tentative_ok = _exercise_tentative_import(restarted_store, principal)
        post_restart_runtime = DeterministicFakeClientRuntimeHost.for_level(
            "L2", client_id=principal.id, session_id="synthetic-session-2"
        )
        pre_purge_context, pre_purge_latency = _compile_before_generation(
            post_restart_runtime,
            restarted_retrieval,
            principal,
            generation_id="generation-3",
            as_of=ZERO_DASHBOARD_AFTER_EXPIRY,
        )
        restarted_store.purge(
            "record",
            purge_record_id,
            confirmation=restarted_store.purge_confirmation_phrase("record", purge_record_id),
            actor="synthetic-harness",
        )
        try:
            restarted_store.get_record(purge_record_id)
        except NotFoundError:
            purge_absent_after = True
        else:
            purge_absent_after = False
        secret_ok = _exercise_secret_boundary(
            restarted_store,
            principal,
            SyntheticDirectTurn(
                reference="turn-secret-1",
                content="Synthetic password=never-store",
                kind="secret",
            ),
        )
        final_context, final_latency = _compile_before_generation(
            post_restart_runtime,
            restarted_retrieval,
            principal,
            generation_id="generation-4",
            as_of=ZERO_DASHBOARD_AFTER_EXPIRY,
        )
        viewer_response_items, _viewer_after_latency = _bootstrap_at(
            restarted_retrieval,
            viewer_request,
            viewer,
            as_of=ZERO_DASHBOARD_AFTER_EXPIRY,
        )

        projection_ok = _exercise_projection_contract(principal)
        core_counts = _core_counts(restarted_store)
        capture_counts = _capture_counts(restarted_store, source.id)
        first_contents = tuple(item.content for item in first_context)
        corrected_contents = tuple(item.content for item in corrected_context)
        viewer_before_contents = tuple(item.content for item in viewer_before_context)
        pre_purge_contents = tuple(item.content for item in pre_purge_context)
        final_contents = tuple(item.content for item in final_context)
        viewer_contents = tuple(item.content for item in viewer_response_items)
        expired_retention = recovery_sink.formed_retention.get("capture-expired")
        retention_lineage_ok = (
            expired_retention is not None
            and expired_retention.expires_at == ZERO_DASHBOARD_EXPIRY
            and not expired_retention.is_expired(ZERO_DASHBOARD_TIME)
            and expired_retention.is_expired(ZERO_DASHBOARD_AFTER_EXPIRY)
        )
        old_content = "Atlas uses deterministic local retrieval."
        new_content = "Atlas uses bounded local retrieval."
        phase_contexts_safe = _phase_contexts_are_safe(
            first_context=first_contents,
            corrected_context=corrected_contents,
            viewer_before_context=viewer_before_contents,
            pre_purge_context=pre_purge_contents,
            final_context=final_contents,
            viewer_context=viewer_contents,
        )
        compile_latency = max(first_latency, corrected_latency, pre_purge_latency, final_latency)
        capabilities = runtime.capabilities
        capability_truth = (
            capabilities.contract_version == "client-runtime-v0"
            and capabilities.provider_support_claim is False
            and capabilities.stable_sdk_claim is False
            and tuple(item.hook for item in capabilities.hooks) == ALL_LIFECYCLE_HOOKS
            and all(
                item.status in {"unsupported", "best_effort", "supported"}
                for item in capabilities.hooks
            )
            and capabilities.for_hook("pre_generation_context_request").status == "supported"
            and capabilities.for_hook("direct_user_turn").status == "supported"
            and capabilities.for_hook("restart_session_transition").status == "supported"
            and capabilities.for_hook("manual_context_request").status == "best_effort"
            and capabilities.for_hook("consequence_checkpoint").status == "unsupported"
            and isinstance(unsupported_consequence_report, UnsupportedHookReport)
            and unsupported_consequence_report.hook == "consequence_checkpoint"
            and unsupported_consequence_report.status == "unsupported"
            and replay_coordinator.capability_conformance(source.id).valid
            and replay_coordinator.capability_manifest(source.id).network_access == "denied"
            and replay_coordinator.capability_manifest(source.id).data_egress == ()
            and isinstance(normalized_transition, EventReconciliationInput)
        )
        formation_events = (*sink.normalized_events, *recovery_sink.normalized_events)
        formation_results = (*sink.formed, *recovery_sink.formed)
        scorecard = ZeroDashboardScorecard(
            required_first_useful_context={
                "Atlas uses deterministic local retrieval.",
                "Atlas private staging uses a bounded fixture.",
            }.issubset(first_contents),
            context_correctness=(
                phase_contexts_safe
                and old_content in first_contents
                and "Expired Atlas working-state fixture." in first_contents
                and "Terminal purge fixture for Atlas." in pre_purge_contents
                and "Atlas uses deterministic local retrieval." in viewer_before_contents
                and private_record_id not in {item.id for item in viewer_response_items}
                and public_record_id not in {item.id for item in viewer_response_items}
                and len(final_context) == len({item.id for item in final_context})
            ),
            correction_propagation=(
                new_content in corrected_contents
                and old_content not in corrected_contents
                and correction.candidate.record_id == public_record_id
                and correction.candidate.supersedes == public_record_id
                and correction.formation_supersedes_observation_ref == public_observation_id
                and public_observation_id != public_record_id
            ),
            replay_duplicates=(
                replay_capture.status == "completed"
                and replay_capture.duplicate_events == capture_counts["event_count"]
                and capture_counts["event_count"] == capture_counts["distinct_event_count"]
                and replay_pre_counts == replay_post_counts
                and core_counts["observation_count"]
                == core_counts["distinct_observation_key_count"]
                and core_counts["current_record_count"] == core_counts["distinct_record_key_count"]
                and not replay_sink.delegate.calls
            ),
            user_intervention=(
                bool(runtime.trace)
                and all(
                    item.action in {"context_delivered", "generation_started"}
                    for item in runtime.trace
                )
            ),
            resume_behavior=(
                recovered_capture.status == "completed"
                and recovery_adapter.calls[:1] == [("cursor-1", 0)]
                and recovery_adapter.pages_seen[0].page_order == 1
                and replay_capture.status == "completed"
                and replay_adapter.calls[:1] == [(None, 0)]
                and replay_adapter.pages_seen[0].page_order == 0
                and bool(runtime.trace)
                and bool(post_restart_runtime.trace)
            ),
            capability_truth=capability_truth,
            formation_and_projection_contract=(
                projection_ok
                and tentative_ok
                and len(formation_events) == 6
                and len(formation_results) == 5
                and all(item.accepted for item in formation_results)
                and retention_lineage_ok
            ),
            secret_refusal=(
                secret_ok and all("never-store" not in content for content in final_contents)
            ),
            retention_and_expiry=(
                retention_lineage_ok
                and "Expired Atlas working-state fixture." in first_contents
                and "Expired Atlas working-state fixture." not in final_contents
            ),
            ordinary_delete=(
                recovery_sink.delete_verified_before
                and recovery_sink.delete_verified_after
                and restarted_retrieval.get(delete_record_id, principal) is None
                and delete_record_id not in {item.id for item in final_context}
            ),
            terminal_purge=(
                pre_purge_record.id == purge_record_id
                and "Terminal purge fixture for Atlas." in pre_purge_contents
                and purge_absent_after
                and restarted_retrieval.get(purge_record_id, principal) is None
                and purge_record_id not in {item.id for item in final_context}
            ),
            compile_latency_ms=compile_latency,
            restart_context_latency_ms=restart_context_latency,
        )
        return ZeroDashboardJourneyReceipt(
            scorecard=scorecard,
            first_context=first_contents,
            corrected_context=corrected_contents,
            viewer_before_context=viewer_before_contents,
            pre_purge_context=pre_purge_contents,
            final_context=final_contents,
            viewer_context=viewer_contents,
            capture_source_id=source.id,
            capture_event_count=capture_counts["event_count"],
            observation_count=core_counts["observation_count"],
            current_record_count=core_counts["current_record_count"],
            restart_context_latency_ms=restart_context_latency,
        )
    finally:
        store.close()
        if restarted_store is not None:
            restarted_store.close()
        storage_clock.stop()


def _client_request() -> Any:
    from .models import ClientCreate

    return ClientCreate(
        name="Synthetic lifecycle client",
        scopes=["context:read", "context:propose", "witness:explicit_user_statement"],
    )


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _authorization_from_payload(payload: Mapping[str, Any]) -> AuthorizationApplicability:
    allowed = payload.get("allowed_clients", ())
    denied = payload.get("denied_clients", ())
    if not isinstance(allowed, (list, tuple)) or not isinstance(denied, (list, tuple)):
        raise ValueError("synthetic authorization fixture is not a list")
    return AuthorizationApplicability(
        allowed_principals=frozenset(cast(str, item) for item in allowed) or None,
        denied_principals=frozenset(cast(str, item) for item in denied),
    )


def _retention_from_payload(payload: Mapping[str, Any]) -> RetentionPolicy:
    expires_at = payload.get("expires_at")
    if expires_at is None:
        return RetentionPolicy(FormationRetentionClass.SOURCE_LIFETIME)
    if not isinstance(expires_at, str):
        raise ValueError("synthetic expiry is not text")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("synthetic expiry is not an ISO timestamp") from error
    return RetentionPolicy(FormationRetentionClass.EXPLICIT_EXPIRY, expires_at=expiry)


def _capture_observation_input(
    event: CaptureEvent,
    normalized: EventReconciliationInput,
    authorization: AuthorizationApplicability,
) -> EventObservationInput:
    payload = event.payload
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValueError("synthetic upsert has no content")
    source_id = cast(str, normalized.source_id)
    event_id = normalized.event_id
    source = SourceLineage(source_id=source_id, generation=cast(int, normalized.source_generation))
    event_lineage = EventLineage(
        event_id=event_id,
        source_id=source_id,
        generation=cast(int, normalized.source_generation),
        sequence=cast(int, normalized.source_sequence),
    )
    item = ItemLineage(item_id=event.provider_item_id, source_id=source_id)
    return EventObservationInput(
        source=source,
        event=event_lineage,
        item=item,
        witness_class=WitnessClass.AUTHORITATIVE_SOURCE,
        evidence_class=EvidenceClass.SOURCE_ITEM,
        retention=normalized.retention,
        authorization=authorization,
        observed_at=cast(datetime, normalized.observed_time),
        content=content,
        payload_kind=PayloadKind.BOUNDED_INLINE,
        content_interpretation=ContentInterpretation.EVIDENCE_DATA,
        disposition=FormationDisposition.TENTATIVE,
    )


def _candidate_from_proposal(
    proposal: Any,
    *,
    payload: Mapping[str, Any],
    source_id: str | None,
    source_reference: str,
    source_type: str,
    idempotency_key: str,
) -> CandidateInput:
    if proposal.content is None:
        raise ValueError("bounded synthetic proposal must contain content")
    sensitivity = Sensitivity(str(payload.get("sensitivity", Sensitivity.NORMAL.value)))
    return CandidateInput(
        kind=_fixture_text(payload.get("kind"), field="candidate kind"),
        content=proposal.content,
        structured_value=(
            cast(dict[str, Any], payload["structured_value"])
            if isinstance(payload.get("structured_value"), Mapping)
            else None
        ),
        entity_key=(
            cast(str, payload["entity_key"]) if isinstance(payload.get("entity_key"), str) else None
        ),
        attribute_key=(
            cast(str, payload["attribute_key"])
            if isinstance(payload.get("attribute_key"), str)
            else None
        ),
        scopes=[cast(str, item) for item in payload.get("scopes", [])],
        source_id=source_id,
        source_reference=source_reference,
        source_service="synthetic-capture",
        source_type=source_type,
        evidence="bounded synthetic source evidence",
        confidence=1.0,
        sensitivity=sensitivity,
        availability=Availability.CORE,
        allowed_clients=sorted(proposal.authorization.allowed_principals or ()),
        denied_clients=sorted(proposal.authorization.denied_principals),
        observed_at=_iso(proposal.observed_at),
        expires_at=(
            _iso(proposal.retention.expires_at)
            if proposal.retention.expires_at is not None
            else None
        ),
        explicit_user_statement=bool(payload.get("explicit_user_statement", False)),
        idempotency_key=idempotency_key,
    )


def _resolve_direct_turn_content(
    payload: DirectUserTurnPayload,
    turn: SyntheticDirectTurn,
) -> str:
    reference = payload.turn_ref
    encoded = turn.content.encode("utf-8")
    if (
        reference.reference != turn.reference
        or reference.size_bytes != len(encoded)
        or reference.sha256 != hashlib.sha256(encoded).hexdigest()
    ):
        raise RuntimeError("synthetic direct turn reference commitment did not match")
    return turn.content


def _direct_turn_lineage(
    host: DeterministicFakeClientRuntimeHost,
    principal: ClientPrincipal,
    turn: SyntheticDirectTurn,
) -> tuple[EventReconciliationInput, str]:
    encoded = turn.content.encode("utf-8")
    reference = PayloadReference(
        turn.reference,
        "user_turn",
        size_bytes=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )
    envelope = host.observe_direct_user_turn(reference, conversation_id="conversation-atlas")
    if not isinstance(envelope, ClientLifecycleEnvelope):
        raise RuntimeError("synthetic direct-user hook was not supported")
    if not isinstance(envelope.payload, DirectUserTurnPayload):
        raise RuntimeError("synthetic direct-user payload was not typed")
    content = _resolve_direct_turn_content(envelope.payload, turn)
    normalized = normalize_lifecycle_event(
        envelope,
        project_ref=ZERO_DASHBOARD_PROJECT_SCOPE,
        authorization=AuthorizationApplicability(
            allowed_principals=frozenset({principal.id}),
            allowed_scopes=frozenset({ZERO_DASHBOARD_PROJECT_SCOPE}),
        ),
        event_time=ZERO_DASHBOARD_TIME,
        observed_time=ZERO_DASHBOARD_TIME,
    )
    return normalized, content


def _add_direct_turn(
    store: CoreStore,
    host: DeterministicFakeClientRuntimeHost,
    principal: ClientPrincipal,
    turn: SyntheticDirectTurn,
    *,
    supersedes_observation_ref: str | None = None,
    supersedes_record_id: str | None = None,
) -> _DirectTurnResult:
    normalized, resolved_content = _direct_turn_lineage(host, principal, turn)
    source_id = f"client-source:{principal.id}"
    source = SourceLineage(source_id=source_id, generation=1, revision="session-1")
    event_lineage = EventLineage(
        event_id=normalized.event_id,
        source_id=source_id,
        generation=1,
        sequence=cast(int, normalized.sequence),
        revision="session-1",
    )
    item = ItemLineage(
        item_id=turn.reference,
        source_id=source_id,
        revision="session-1",
    )
    formation = form_observation(
        EventObservationInput(
            source=source,
            event=event_lineage,
            item=item,
            witness_class=WitnessClass.DIRECT_USER,
            evidence_class=EvidenceClass.DIRECT_ASSERTION,
            retention=normalized.retention,
            authorization=normalized.authorization,
            observed_at=cast(datetime, normalized.observed_time),
            content=resolved_content,
            payload_kind=PayloadKind.BOUNDED_INLINE,
            content_interpretation=ContentInterpretation.EVIDENCE_DATA,
            disposition=FormationDisposition.TENTATIVE,
            supersedes_observation_ref=supersedes_observation_ref,
        ),
        as_of=ZERO_DASHBOARD_TIME,
        refusal_ref=f"formation-{turn.reference}",
    )
    if not formation.accepted or formation.proposal is None:
        raise RuntimeError("synthetic direct-user formation refused")
    payload = {
        "kind": turn.kind,
        "entity_key": "user" if turn.kind == "interaction_preference" else None,
        "attribute_key": "answer_style" if turn.kind == "interaction_preference" else None,
        "scopes": [ZERO_DASHBOARD_PROJECT_SCOPE],
    }
    candidate = _candidate_from_proposal(
        formation.proposal,
        payload=payload,
        source_id=None,
        source_reference=turn.reference,
        source_type="synthetic-client",
        idempotency_key=f"lifecycle:{normalized.event_id}",
    ).model_copy(
        update={
            "supersedes": supersedes_record_id,
            "explicit_user_statement": True,
        }
    )
    return _DirectTurnResult(
        candidate=store.add_candidate(candidate, client=principal),
        formation_supersedes_observation_ref=formation.proposal.supersedes_observation_ref,
    )


def _compile_before_generation(
    host: DeterministicFakeClientRuntimeHost,
    retrieval: RetrievalEngine,
    principal: ClientPrincipal,
    *,
    generation_id: str,
    as_of: datetime,
) -> tuple[tuple[ContextRecordOut, ...], float]:
    request = host.request_pre_generation_context(
        generation_id=generation_id,
        requested_scopes=(ZERO_DASHBOARD_PROJECT_SCOPE,),
        budget_chars=4_000,
        conversation_id="conversation-atlas",
        project_id="atlas",
    )
    if not isinstance(request, ClientLifecycleEnvelope):
        raise RuntimeError("synthetic pre-generation hook was not supported")
    if not isinstance(request.payload, ContextRequestPayload):
        raise RuntimeError("synthetic context request payload was not typed")
    if request.payload.generation_id != generation_id:
        raise RuntimeError("synthetic context request targeted the wrong generation")
    started = perf_counter()
    bootstrap_request = BootstrapRequest(
        task_description="Atlas",
        requested_scopes=list(request.payload.requested_scopes),
        current_project=request.project_id,
        character_budget=request.payload.budget_chars,
    )
    with patch.object(retrieval_module, "utc_now", return_value=_iso(as_of)):
        response = retrieval.bootstrap(bootstrap_request, principal)
    elapsed_ms = (perf_counter() - started) * 1_000
    references = tuple(
        PayloadReference(
            item.id,
            "context_pack",
            size_bytes=len(item.content.encode("utf-8")),
            sha256=hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
        )
        for item in response.items
    )
    delivered = host.deliver_context(request, references)
    if not isinstance(delivered, ContextDeliveryReceipt):
        raise RuntimeError("synthetic context delivery was not supported")
    generation = host.begin_generation(generation_id=generation_id)
    if not generation.pre_generation_delivery:
        raise RuntimeError("synthetic context was not delivered before generation")
    return tuple(response.items), elapsed_ms


def _bootstrap_at(
    retrieval: RetrievalEngine,
    request: BootstrapRequest,
    principal: ClientPrincipal,
    *,
    as_of: datetime,
) -> tuple[tuple[ContextRecordOut, ...], float]:
    started = perf_counter()
    with patch.object(retrieval_module, "utc_now", return_value=_iso(as_of)):
        response = retrieval.bootstrap(request, principal)
    return tuple(response.items), (perf_counter() - started) * 1_000


def _phase_contexts_are_safe(
    *,
    first_context: Sequence[str],
    corrected_context: Sequence[str],
    viewer_before_context: Sequence[str],
    pre_purge_context: Sequence[str],
    final_context: Sequence[str],
    viewer_context: Sequence[str],
) -> bool:
    """Apply phase-aware, non-compensable content safety gates."""

    wrong_project = "Atlas uses a separate Neptune source."
    secret_like = "Synthetic password=never-store"
    inert_import = "Imported fixture text is inert evidence data."
    old_content = "Atlas uses deterministic local retrieval."
    expired_content = "Expired Atlas working-state fixture."
    deleted_content = "Temporary deletion fixture for Atlas."
    purge_content = "Terminal purge fixture for Atlas."
    private_content = "Atlas private staging uses a bounded fixture."
    permanent_exclusions = {wrong_project, secret_like, inert_import}
    all_packs = (
        tuple(first_context),
        tuple(corrected_context),
        tuple(viewer_before_context),
        tuple(pre_purge_context),
        tuple(final_context),
        tuple(viewer_context),
    )
    if any(permanent_exclusions.intersection(pack) for pack in all_packs):
        return False
    if old_content in (
        *tuple(corrected_context),
        *tuple(pre_purge_context),
        *tuple(final_context),
        *tuple(viewer_context),
    ):
        return False
    if expired_content in (
        *tuple(pre_purge_context),
        *tuple(final_context),
        *tuple(viewer_context),
    ):
        return False
    if any(deleted_content in pack for pack in all_packs):
        return False
    if purge_content in (
        *tuple(first_context),
        *tuple(corrected_context),
        *tuple(viewer_before_context),
        *tuple(final_context),
        *tuple(viewer_context),
    ):
        return False
    return not (
        private_content in tuple(viewer_before_context) or private_content in tuple(viewer_context)
    )


def _exercise_projection_contract(principal: ClientPrincipal) -> bool:
    invalidations = tuple(
        InvalidationDeclaration(
            cause=cause,
            action=InvalidationAction.ERASE
            if cause is InvalidationCause.TERMINAL_PURGE
            else InvalidationAction.WITHDRAW_AND_REBUILD,
        )
        for cause in sorted(InvalidationCause, key=lambda item: item.value)
        if cause.value in {item.value for item in InvalidationCause}
    )
    plan = ProjectionPlan(
        declarations=(
            ProjectionDeclaration(
                projection_ref="synthetic-index",
                kind=ProjectionKind.INDEX,
                dependencies=(
                    DependencyDeclaration("capture:item-project", InfluenceClass.CONTENT),
                ),
                invalidation_declarations=invalidations,
            ),
            ProjectionDeclaration(
                projection_ref="synthetic-capsule",
                kind=ProjectionKind.CAPSULE,
                dependencies=(DependencyDeclaration("synthetic-index", InfluenceClass.SELECTION),),
                invalidation_declarations=invalidations,
            ),
        ),
        external_refs=frozenset({"capture:item-project"}),
    )
    seeds = (
        ProjectionSeed(
            node_ref="capture:item-project",
            version=1,
            semantic_commitment="fixture-commitment-project",
            authorization=AuthorizationApplicability(allowed_principals=frozenset({principal.id})),
        ),
    )
    values = rebuild_projection(
        plan,
        seeds,
        principal=principal.id,
        policy_generation=1,
        required_scopes=(ZERO_DASHBOARD_PROJECT_SCOPE,),
    )
    correction_closure = dependency_closure(
        plan, ("capture:item-project",), InvalidationCause.CORRECTION
    )
    purge_closure = dependency_closure(
        plan, ("capture:item-project",), InvalidationCause.TERMINAL_PURGE
    )
    return (
        tuple(item.projection_ref for item in values) == ("synthetic-capsule", "synthetic-index")
        and correction_closure == ("synthetic-capsule", "synthetic-index")
        and purge_closure == ("synthetic-capsule", "synthetic-index")
    )


def _exercise_secret_boundary(
    store: CoreStore,
    principal: ClientPrincipal,
    turn: SyntheticDirectTurn,
) -> bool:
    secret_host = DeterministicFakeClientRuntimeHost.for_level(
        "L2", client_id=principal.id, session_id="synthetic-secret-session"
    )
    normalized, resolved_content = _direct_turn_lineage(secret_host, principal, turn)
    source = SourceLineage("secret-fixture-source")
    event = EventLineage("secret-fixture-event", "secret-fixture-source")
    item = ItemLineage("secret-fixture-item", "secret-fixture-source")
    result = form_observation(
        EventObservationInput(
            source=source,
            event=event,
            item=item,
            witness_class=WitnessClass.DIRECT_USER,
            evidence_class=EvidenceClass.DIRECT_ASSERTION,
            retention=normalized.retention,
            authorization=normalized.authorization,
            observed_at=cast(datetime, normalized.observed_time),
            content=resolved_content,
        ),
        as_of=ZERO_DASHBOARD_TIME,
        refusal_ref=f"formation-{turn.reference}",
    )
    refusal = store.refuse_direct_value(
        resolved_content,
        route="zero-dashboard-fixture",
        operation_id="secret-fixture-operation",
        client=principal,
    )
    candidates, _candidate_total = store.list_candidates(status=None, limit=500)
    all_truth = store.list_memory_truth(status=None, limit=500)
    current_truth = store.list_memory_truth(status=MemoryTruthStatus.CURRENT, limit=500)
    candidate_leaks = any("never-store" in candidate.content for candidate in candidates)
    durable_truth_leaks = any(
        "never-store" in item.record.content for item in all_truth.items
    ) or any("never-store" in item.observation.content for item in all_truth.tentative_observations)
    current_record_leaks = any("never-store" in item.record.content for item in current_truth.items)
    envelope_text = json.dumps(secret_host.events[0].as_dict(), sort_keys=True)
    return (
        not result.accepted
        and refusal is not None
        and not candidate_leaks
        and not durable_truth_leaks
        and not current_record_leaks
        and "never-store" not in envelope_text
    )


def _exercise_tentative_import(store: CoreStore, principal: ClientPrincipal) -> bool:
    source = SourceLineage("import-fixture-source")
    event = EventLineage("import-fixture-event", "import-fixture-source")
    item = ItemLineage("import-fixture-item", "import-fixture-source")
    formation = form_observation(
        EventObservationInput(
            source=source,
            event=event,
            item=item,
            witness_class=WitnessClass.UNTRUSTED_IMPORTED_TEXT,
            evidence_class=EvidenceClass.SOURCE_ITEM,
            retention=RetentionPolicy(FormationRetentionClass.SOURCE_LIFETIME),
            authorization=AuthorizationApplicability(),
            observed_at=ZERO_DASHBOARD_TIME,
            content="Imported fixture text is inert evidence data.",
            disposition=FormationDisposition.DERIVED,
        ),
        as_of=ZERO_DASHBOARD_TIME,
        refusal_ref="import-fixture-refusal",
    )
    if not formation.accepted or formation.proposal is None:
        return False
    candidate = CandidateInput(
        kind="imported_note",
        content=formation.proposal.content or "",
        source_id=None,
        source_reference="import-fixture-item",
        source_type="synthetic-import",
        explicit_user_statement=False,
        idempotency_key="import-fixture-idempotency",
    )
    created = store.add_candidate(candidate, client=principal)
    return created.disposition.value == "tentative" and created.record_id is None


def _capture_counts(store: CoreStore, source_id: str) -> dict[str, int]:
    with store.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS event_count, "
            "COUNT(DISTINCT provider_event_id) AS distinct_event_count "
            "FROM capture_events WHERE source_id=?",
            (source_id,),
        ).fetchone()
    return {
        "event_count": int(row["event_count"]),
        "distinct_event_count": int(row["distinct_event_count"]),
    }


def _core_counts(store: CoreStore) -> dict[str, int]:
    with store.connect() as connection:
        observation = connection.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT idempotency_key) AS distinct_keys "
            "FROM context_candidates WHERE idempotency_key IS NOT NULL"
        ).fetchone()
        records = connection.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT record_key) AS distinct_keys "
            "FROM context_records WHERE deleted_at IS NULL AND record_key IS NOT NULL"
        ).fetchone()
    return {
        "observation_count": int(observation["total"]),
        "distinct_observation_key_count": int(observation["distinct_keys"]),
        "current_record_count": int(records["total"]),
        "distinct_record_key_count": int(records["distinct_keys"]),
    }


__all__ = [
    "SyntheticDirectTurn",
    "ZeroDashboardFixture",
    "ZeroDashboardJourneyReceipt",
    "ZeroDashboardScorecard",
    "default_zero_dashboard_fixture",
    "run_zero_dashboard_journey",
]

"""Experimental, provider-neutral client runtime lifecycle contracts.

This module is a v0 seam for a future lifecycle-aware client integration.  It
does not integrate a provider, run an SDK, persist events, or create Core
records.  Payloads are represented by bounded opaque references so imported,
workspace, client, and provider content remains inert untrusted data.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

MAX_RUNTIME_ID_CHARS = 128
MAX_REFERENCE_CHARS = 256
MAX_SCOPE_CHARS = 64
MAX_SCOPE_COUNT = 32
MAX_REFERENCE_BYTES = 64 * 1024
MAX_DIAGNOSTIC_CHARS = 160
MAX_DIAGNOSTICS = 8
MAX_SEQUENCE = (1 << 63) - 1
MAX_CONTEXT_BUDGET_CHARS = 128 * 1024

CONTRACT_VERSION: Literal["client-runtime-v0"] = "client-runtime-v0"

CapabilityLevel = Literal["L0", "L1", "L2", "L3"]
CapabilityStatus = Literal["unsupported", "best_effort", "supported"]
OrderingGuarantee = Literal["none", "monotonic_sequence", "before_generation"]
LifecycleHook = Literal[
    "manual_context_request",
    "pre_generation_context_request",
    "direct_user_turn",
    "tool_observable_result",
    "response_emission",
    "compaction_task_checkpoint",
    "restart_session_transition",
    "completion_abandonment",
    "consequence_checkpoint",
]
RequiredLifecycleHook = Literal[
    "pre_generation_context_request",
    "direct_user_turn",
    "tool_observable_result",
    "response_emission",
    "compaction_task_checkpoint",
    "restart_session_transition",
    "completion_abandonment",
    "consequence_checkpoint",
]
EvidenceWitness = Literal[
    "direct_user",
    "host_observation",
    "model_provider_self_attestation",
    "system_observation",
]
DiagnosticSeverity = Literal["info", "warning", "error"]
RetentionClass = Literal["ephemeral", "bounded", "checkpoint"]
ReferenceKind = Literal[
    "context_pack",
    "user_turn",
    "tool_result",
    "response",
    "working_checkpoint",
    "outcome",
    "attestation",
    "external_artifact",
]

ALL_LIFECYCLE_HOOKS: tuple[LifecycleHook, ...] = (
    "manual_context_request",
    "pre_generation_context_request",
    "direct_user_turn",
    "tool_observable_result",
    "response_emission",
    "compaction_task_checkpoint",
    "restart_session_transition",
    "completion_abandonment",
    "consequence_checkpoint",
)
REQUIRED_LIFECYCLE_HOOKS: tuple[RequiredLifecycleHook, ...] = (
    "pre_generation_context_request",
    "direct_user_turn",
    "tool_observable_result",
    "response_emission",
    "compaction_task_checkpoint",
    "restart_session_transition",
    "completion_abandonment",
    "consequence_checkpoint",
)
CONSEQUENCE_CHECKPOINT_KINDS = (
    "context_delivered",
    "tool_result_observed",
    "response_emitted",
    "task_outcome_observed",
    "correction_available",
)
_HOOK_MINIMUM_LEVELS: dict[LifecycleHook, CapabilityLevel] = {
    "manual_context_request": "L0",
    "pre_generation_context_request": "L1",
    "direct_user_turn": "L1",
    "tool_observable_result": "L2",
    "response_emission": "L2",
    "compaction_task_checkpoint": "L2",
    "restart_session_transition": "L2",
    "completion_abandonment": "L2",
    "consequence_checkpoint": "L3",
}
_HOOK_ORDERINGS: dict[LifecycleHook, OrderingGuarantee] = {
    "manual_context_request": "none",
    "pre_generation_context_request": "before_generation",
    "direct_user_turn": "monotonic_sequence",
    "tool_observable_result": "monotonic_sequence",
    "response_emission": "monotonic_sequence",
    "compaction_task_checkpoint": "monotonic_sequence",
    "restart_session_transition": "monotonic_sequence",
    "completion_abandonment": "monotonic_sequence",
    "consequence_checkpoint": "monotonic_sequence",
}

_LEVEL_ORDER: dict[CapabilityLevel, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+?=&%-]{0,255}$")
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_MARKER = re.compile(
    r"(?i)(bearer\s+|basic\s+|\bsk-[a-z0-9]|\bgh[pousr]_[a-z0-9]|"
    r"\bAIza[a-z0-9]|secret\s*[:=]|password\s*[:=]|credential\s*[:=]|"
    r"token\s*[:=])"
)
_HIDDEN_REASONING_MARKER = re.compile(
    r"(?i)(chain[_ -]?of[_ -]?thought|hidden\s+reasoning|internal\s+reasoning|"
    r"private\s+thought|scratchpad)"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ClientRuntimeContractError(ValueError):
    """Raised when a v0 runtime envelope or host transition is invalid."""


class OrderingViolation(ClientRuntimeContractError):
    """Raised when a lifecycle-aware host starts generation too early."""


class EvidenceBoundaryError(ClientRuntimeContractError):
    """Raised when evidence would be mislabelled or expose hidden reasoning."""


def _bounded_token(value: str, *, maximum: int, label: str) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ClientRuntimeContractError(f"{label} is outside its bound")
    if (
        _CONTROL.search(value)
        or _SECRET_MARKER.search(value)
        or _HIDDEN_REASONING_MARKER.search(value)
    ):
        raise EvidenceBoundaryError(f"{label} contains a forbidden value")
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise ClientRuntimeContractError(f"{label} has an invalid form")
    return value


def _bounded_diagnostic(value: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_DIAGNOSTIC_CHARS:
        raise ClientRuntimeContractError("diagnostic detail is outside its bound")
    if (
        _CONTROL.search(value)
        or _SECRET_MARKER.search(value)
        or _HIDDEN_REASONING_MARKER.search(value)
    ):
        raise EvidenceBoundaryError("diagnostic detail contains a forbidden value")
    return value


def _bounded_scope(value: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_SCOPE_CHARS:
        raise ClientRuntimeContractError("context scope is outside its bound")
    if _SAFE_SCOPE.fullmatch(value) is None:
        raise ClientRuntimeContractError("context scope has an invalid form")
    return value


def _require_literal(value: object, allowed: set[str], *, label: str) -> None:
    if type(value) is not str or value not in allowed:
        raise ClientRuntimeContractError(f"{label} is not a supported literal")


def _bounded_scopes(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ClientRuntimeContractError("context scopes must be a sequence of labels")
    if len(values) > MAX_SCOPE_COUNT:
        raise ClientRuntimeContractError("context scopes exceed their bound")
    bounded = tuple(values)
    if len(bounded) > MAX_SCOPE_COUNT:
        raise ClientRuntimeContractError("context scopes exceed their bound")
    return tuple(_bounded_scope(value) for value in bounded)


def _bounded_context_refs(values: Sequence[PayloadReference]) -> tuple[PayloadReference, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ClientRuntimeContractError("context references must be typed references")
    if len(values) == 0 or len(values) > MAX_SCOPE_COUNT:
        raise ClientRuntimeContractError("context delivery references exceed their bound")
    bounded = tuple(values)
    if any(not isinstance(ref, PayloadReference) or ref.kind != "context_pack" for ref in bounded):
        raise ClientRuntimeContractError("context delivery references are invalid or unbounded")
    return bounded


def _validate_identifier(value: str | None, *, label: str) -> None:
    if value is not None:
        _bounded_token(value, maximum=MAX_RUNTIME_ID_CHARS, label=label)


def _validate_sequence(value: int, *, label: str = "sequence") -> None:
    if type(value) is not int or not 1 <= value <= MAX_SEQUENCE:
        raise ClientRuntimeContractError(f"{label} is outside its bound")


def _validate_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> None:
    if type(diagnostics) is not tuple or len(diagnostics) > MAX_DIAGNOSTICS:
        raise ClientRuntimeContractError("too many diagnostics")
    if any(not isinstance(item, Diagnostic) for item in diagnostics):
        raise ClientRuntimeContractError("diagnostics must be typed Diagnostic values")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A bounded operational diagnostic; it is not a content or log field."""

    code: str
    severity: DiagnosticSeverity = "warning"
    detail: str | None = None

    def __post_init__(self) -> None:
        _bounded_token(self.code, maximum=64, label="diagnostic code")
        _require_literal(
            self.severity,
            {"info", "warning", "error"},
            label="diagnostic severity",
        )
        if self.detail is not None:
            _bounded_diagnostic(self.detail)

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PayloadReference:
    """A reference to untrusted content without retaining that content here."""

    reference: str
    kind: ReferenceKind
    size_bytes: int | None = None
    sha256: str | None = None
    untrusted: Literal[True] = True

    def __post_init__(self) -> None:
        _bounded_token(self.reference, maximum=MAX_REFERENCE_CHARS, label="payload reference")
        _require_literal(
            self.kind,
            {
                "context_pack",
                "user_turn",
                "tool_result",
                "response",
                "working_checkpoint",
                "outcome",
                "attestation",
                "external_artifact",
            },
            label="payload reference kind",
        )
        if self.size_bytes is not None and (
            type(self.size_bytes) is not int or not 0 <= self.size_bytes <= MAX_REFERENCE_BYTES
        ):
            raise ClientRuntimeContractError("payload reference size is outside its bound")
        if self.sha256 is not None and (
            type(self.sha256) is not str or _SHA256.fullmatch(self.sha256) is None
        ):
            raise ClientRuntimeContractError(
                "payload reference digest is not a lowercase SHA-256"
            )
        if self.untrusted is not True:
            raise EvidenceBoundaryError("payload references must remain untrusted")

    def as_dict(self) -> dict[str, object]:
        return {
            "reference": self.reference,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "untrusted": True,
        }


@dataclass(frozen=True, slots=True)
class HookCapability:
    """One explicit hook declaration; unsupported never means inferred."""

    hook: LifecycleHook
    status: CapabilityStatus
    minimum_level: CapabilityLevel
    ordering: OrderingGuarantee
    supported_consequence_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_literal(self.hook, set(ALL_LIFECYCLE_HOOKS), label="lifecycle hook")
        _require_literal(
            self.status,
            {"unsupported", "best_effort", "supported"},
            label="capability status",
        )
        _require_literal(self.minimum_level, set(_LEVEL_ORDER), label="minimum capability level")
        _require_literal(
            self.ordering,
            {"none", "monotonic_sequence", "before_generation"},
            label="ordering guarantee",
        )
        if self.minimum_level != _HOOK_MINIMUM_LEVELS[self.hook]:
            raise ClientRuntimeContractError("hook has the wrong minimum capability level")
        if self.status == "unsupported" and self.ordering != "none":
            raise ClientRuntimeContractError("unsupported hooks cannot claim ordering guarantees")
        if self.status != "unsupported" and self.ordering != _HOOK_ORDERINGS[self.hook]:
            raise ClientRuntimeContractError("hook has the wrong ordering guarantee")
        if self.status == "best_effort" and self.hook != "manual_context_request":
            raise ClientRuntimeContractError("only manual context may be best effort")
        if self.hook == "manual_context_request" and self.status == "supported":
            raise ClientRuntimeContractError("manual context is best effort, not guaranteed")
        if type(self.supported_consequence_kinds) is not tuple:
            raise ClientRuntimeContractError("consequence kinds must be a tuple")
        if self.hook != "consequence_checkpoint" and self.supported_consequence_kinds:
            raise ClientRuntimeContractError("consequence kinds belong only to consequence hooks")
        if any(type(kind) is not str for kind in self.supported_consequence_kinds):
            raise ClientRuntimeContractError("consequence kinds must be string literals")
        if any(
            kind not in CONSEQUENCE_CHECKPOINT_KINDS
            for kind in self.supported_consequence_kinds
        ):
            raise ClientRuntimeContractError("unknown consequence checkpoint kind")
        if self.status == "unsupported" and self.supported_consequence_kinds:
            raise ClientRuntimeContractError("unsupported hooks cannot declare consequence kinds")
        if (
            self.hook == "consequence_checkpoint"
            and self.status == "supported"
            and not self.supported_consequence_kinds
        ):
            raise ClientRuntimeContractError(
                "supported consequence hooks must declare a consequence kind"
            )

    @property
    def available(self) -> bool:
        return self.status != "unsupported"

    def as_dict(self) -> dict[str, object]:
        return {
            "hook": self.hook,
            "status": self.status,
            "minimum_level": self.minimum_level,
            "ordering": self.ordering,
            "supported_consequence_kinds": list(self.supported_consequence_kinds),
        }


def _profile_status(level: CapabilityLevel, minimum_level: CapabilityLevel) -> CapabilityStatus:
    return "supported" if _LEVEL_ORDER[level] >= _LEVEL_ORDER[minimum_level] else "unsupported"


@dataclass(frozen=True, slots=True)
class ClientRuntimeCapabilities:
    """Truthful, versioned capability declaration for an experimental host."""

    level: CapabilityLevel
    hooks: tuple[HookCapability, ...]
    contract_version: Literal["client-runtime-v0"] = CONTRACT_VERSION
    provider_support_claim: Literal[False] = False
    stable_sdk_claim: Literal[False] = False

    def __post_init__(self) -> None:
        _require_literal(self.level, set(_LEVEL_ORDER), label="capability level")
        _require_literal(
            self.contract_version,
            {CONTRACT_VERSION},
            label="runtime contract version",
        )
        if type(self.hooks) is not tuple or any(
            not isinstance(item, HookCapability) for item in self.hooks
        ):
            raise ClientRuntimeContractError("capabilities must be typed hook declarations")
        if tuple(item.hook for item in self.hooks) != ALL_LIFECYCLE_HOOKS:
            raise ClientRuntimeContractError("capabilities must declare every v0 hook exactly once")
        for capability in self.hooks:
            if (
                _LEVEL_ORDER[self.level] < _LEVEL_ORDER[capability.minimum_level]
                and capability.status != "unsupported"
            ):
                raise ClientRuntimeContractError(
                    "capability declaration overstates support beyond its level"
                )
        if self.provider_support_claim is not False or self.stable_sdk_claim is not False:
            raise EvidenceBoundaryError("v0 cannot make provider or stable SDK claims")

    @classmethod
    def for_level(cls, level: CapabilityLevel) -> ClientRuntimeCapabilities:
        _require_literal(level, set(_LEVEL_ORDER), label="capability level")
        hooks = tuple(
            HookCapability(
                hook=hook,
                status=(
                    "best_effort"
                    if hook == "manual_context_request"
                    else _profile_status(level, _HOOK_MINIMUM_LEVELS[hook])
                ),
                minimum_level=_HOOK_MINIMUM_LEVELS[hook],
                ordering=(
                    _HOOK_ORDERINGS[hook]
                    if hook == "manual_context_request"
                    or _LEVEL_ORDER[level] >= _LEVEL_ORDER[_HOOK_MINIMUM_LEVELS[hook]]
                    else "none"
                ),
                supported_consequence_kinds=(
                    CONSEQUENCE_CHECKPOINT_KINDS
                    if hook == "consequence_checkpoint" and _LEVEL_ORDER[level] >= 3
                    else ()
                ),
            )
            for hook in ALL_LIFECYCLE_HOOKS
        )
        return cls(level=level, hooks=hooks)

    def for_hook(self, hook: LifecycleHook) -> HookCapability:
        for capability in self.hooks:
            if capability.hook == hook:
                return capability
        raise ClientRuntimeContractError("hook is missing from capability declaration")

    def supports(self, hook: LifecycleHook) -> bool:
        return self.for_hook(hook).available

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "level": self.level,
            "provider_support_claim": False,
            "stable_sdk_claim": False,
            "hooks": [hook.as_dict() for hook in self.hooks],
        }


@dataclass(frozen=True, slots=True)
class UnsupportedHookReport:
    """Content-free report returned instead of inferring an absent hook."""

    hook: LifecycleHook
    required_level: CapabilityLevel
    declared_level: CapabilityLevel
    reason: str
    status: Literal["unsupported"] = "unsupported"
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        _bounded_token(self.reason, maximum=MAX_DIAGNOSTIC_CHARS, label="unsupported hook reason")
        _require_literal(self.hook, set(ALL_LIFECYCLE_HOOKS), label="unsupported hook")
        _require_literal(
            self.required_level,
            set(_LEVEL_ORDER),
            label="unsupported hook required level",
        )
        _require_literal(
            self.declared_level,
            set(_LEVEL_ORDER),
            label="unsupported hook declared level",
        )
        _require_literal(self.status, {"unsupported"}, label="unsupported hook status")
        _validate_diagnostics(self.diagnostics)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "hook": self.hook,
            "required_level": self.required_level,
            "declared_level": self.declared_level,
            "reason": self.reason,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class ContextRequestPayload:
    request_id: str
    generation_id: str | None
    requested_scopes: tuple[str, ...] = ()
    budget_chars: int = 8_000
    delivery_mode: Literal["manual", "pre_generation"] = "pre_generation"

    def __post_init__(self) -> None:
        _bounded_token(self.request_id, maximum=MAX_RUNTIME_ID_CHARS, label="context request ID")
        _validate_identifier(self.generation_id, label="generation ID")
        if type(self.requested_scopes) is not tuple:
            raise ClientRuntimeContractError("context scopes must be a tuple")
        _bounded_scopes(self.requested_scopes)
        if (
            type(self.budget_chars) is not int
            or not 0 <= self.budget_chars <= MAX_CONTEXT_BUDGET_CHARS
        ):
            raise ClientRuntimeContractError("context budget is outside its bound")
        _require_literal(
            self.delivery_mode,
            {"manual", "pre_generation"},
            label="context delivery mode",
        )
        if self.delivery_mode == "pre_generation" and self.generation_id is None:
            raise ClientRuntimeContractError("pre-generation context requires a generation ID")
        if self.delivery_mode == "manual" and self.generation_id is not None:
            raise ClientRuntimeContractError("manual context cannot claim a generation ID")

    def as_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "generation_id": self.generation_id,
            "requested_scopes": list(self.requested_scopes),
            "budget_chars": self.budget_chars,
            "delivery_mode": self.delivery_mode,
        }


@dataclass(frozen=True, slots=True)
class DirectUserTurnPayload:
    turn_ref: PayloadReference
    evidence_role: Literal["direct_user_statement"] = "direct_user_statement"

    def __post_init__(self) -> None:
        _require_literal(
            self.evidence_role,
            {"direct_user_statement"},
            label="direct user evidence role",
        )
        if not isinstance(self.turn_ref, PayloadReference):
            raise ClientRuntimeContractError("direct user turn requires a payload reference")
        if self.turn_ref.kind != "user_turn":
            raise EvidenceBoundaryError("direct user evidence requires a user-turn reference")

    def as_dict(self) -> dict[str, object]:
        return {"turn_ref": self.turn_ref.as_dict(), "evidence_role": self.evidence_role}


@dataclass(frozen=True, slots=True)
class ToolObservableResultPayload:
    tool_name: str
    result_ref: PayloadReference
    result_kind: Literal["tool_result", "observable_result"] = "tool_result"

    def __post_init__(self) -> None:
        _bounded_token(self.tool_name, maximum=MAX_RUNTIME_ID_CHARS, label="tool name")
        _require_literal(
            self.result_kind,
            {"tool_result", "observable_result"},
            label="observable result kind",
        )
        if not isinstance(self.result_ref, PayloadReference):
            raise ClientRuntimeContractError("observable result requires a payload reference")
        if self.result_ref.kind not in {"tool_result", "external_artifact"}:
            raise ClientRuntimeContractError("tool result requires a result reference")

    def as_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "result_ref": self.result_ref.as_dict(),
            "result_kind": self.result_kind,
        }


@dataclass(frozen=True, slots=True)
class ResponseEmissionPayload:
    response_ref: PayloadReference
    emission_state: Literal["started", "completed", "abandoned"] = "completed"

    def __post_init__(self) -> None:
        _require_literal(
            self.emission_state,
            {"started", "completed", "abandoned"},
            label="response emission state",
        )
        if not isinstance(self.response_ref, PayloadReference):
            raise ClientRuntimeContractError("response emission requires a payload reference")
        if self.response_ref.kind != "response":
            raise ClientRuntimeContractError("response emission requires a response reference")

    def as_dict(self) -> dict[str, object]:
        return {
            "response_ref": self.response_ref.as_dict(),
            "emission_state": self.emission_state,
        }


@dataclass(frozen=True, slots=True)
class CompactionTaskCheckpointPayload:
    checkpoint_ref: PayloadReference
    checkpoint_kind: Literal["compaction", "task_checkpoint"]
    checkpoint_state: Literal["created", "completed"] = "completed"

    def __post_init__(self) -> None:
        _require_literal(
            self.checkpoint_kind,
            {"compaction", "task_checkpoint"},
            label="checkpoint kind",
        )
        _require_literal(
            self.checkpoint_state,
            {"created", "completed"},
            label="checkpoint state",
        )
        if not isinstance(self.checkpoint_ref, PayloadReference):
            raise ClientRuntimeContractError("checkpoint requires a payload reference")
        if self.checkpoint_ref.kind != "working_checkpoint":
            raise ClientRuntimeContractError("checkpoint requires a working-checkpoint reference")

    def as_dict(self) -> dict[str, object]:
        return {
            "checkpoint_ref": self.checkpoint_ref.as_dict(),
            "checkpoint_kind": self.checkpoint_kind,
            "checkpoint_state": self.checkpoint_state,
        }


@dataclass(frozen=True, slots=True)
class RestartSessionTransitionPayload:
    transition: Literal["session_start", "session_end", "restart", "session_transition"]
    previous_session_id: str | None = None
    next_session_id: str | None = None

    def __post_init__(self) -> None:
        _require_literal(
            self.transition,
            {"session_start", "session_end", "restart", "session_transition"},
            label="session transition",
        )
        _validate_identifier(self.previous_session_id, label="previous session ID")
        _validate_identifier(self.next_session_id, label="next session ID")
        if self.transition == "session_start" and (
            self.previous_session_id is not None or self.next_session_id is None
        ):
            raise ClientRuntimeContractError(
                "session_start requires no previous and a next session"
            )
        if self.transition == "session_end" and (
            self.previous_session_id is None or self.next_session_id is not None
        ):
            raise ClientRuntimeContractError(
                "session_end requires a previous and no next session"
            )
        if self.transition in {"restart", "session_transition"} and (
            self.previous_session_id is None
            or self.next_session_id is None
            or self.previous_session_id == self.next_session_id
        ):
            raise ClientRuntimeContractError(
                "session transition requires distinct previous and next sessions"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "transition": self.transition,
            "previous_session_id": self.previous_session_id,
            "next_session_id": self.next_session_id,
        }


@dataclass(frozen=True, slots=True)
class CompletionAbandonmentPayload:
    terminal_state: Literal["completed", "abandoned"]
    reason_code: Literal[
        "completed",
        "user_abandoned",
        "host_shutdown",
        "error",
        "unknown",
    ] = "completed"

    def __post_init__(self) -> None:
        _require_literal(
            self.terminal_state,
            {"completed", "abandoned"},
            label="terminal state",
        )
        _require_literal(
            self.reason_code,
            {"completed", "user_abandoned", "host_shutdown", "error", "unknown"},
            label="terminal reason",
        )
        if self.terminal_state == "completed" and self.reason_code != "completed":
            raise ClientRuntimeContractError("completed task requires completed reason")
        if self.terminal_state == "abandoned" and self.reason_code == "completed":
            raise ClientRuntimeContractError("abandoned task requires an abandonment reason")

    def as_dict(self) -> dict[str, object]:
        return {"terminal_state": self.terminal_state, "reason_code": self.reason_code}


@dataclass(frozen=True, slots=True)
class ConsequenceCheckpointPayload:
    checkpoint_kind: Literal[
        "context_delivered",
        "tool_result_observed",
        "response_emitted",
        "task_outcome_observed",
        "correction_available",
    ]
    status: Literal["observed", "not_observed"]
    evidence_ref: PayloadReference | None = None

    def __post_init__(self) -> None:
        _require_literal(
            self.checkpoint_kind,
            set(CONSEQUENCE_CHECKPOINT_KINDS),
            label="consequence checkpoint kind",
        )
        _require_literal(
            self.status,
            {"observed", "not_observed"},
            label="consequence checkpoint status",
        )
        if self.status == "not_observed" and self.evidence_ref is not None:
            raise ClientRuntimeContractError(
                "not-observed consequence checkpoints cannot carry evidence"
            )
        if self.status == "observed" and not isinstance(self.evidence_ref, PayloadReference):
            raise ClientRuntimeContractError(
                "observed consequence checkpoints require evidence"
            )
        allowed_kinds: dict[str, set[ReferenceKind]] = {
            "context_delivered": {"context_pack"},
            "tool_result_observed": {"tool_result", "external_artifact"},
            "response_emitted": {"response"},
            "task_outcome_observed": {"outcome", "external_artifact"},
            "correction_available": {"user_turn"},
        }
        if self.evidence_ref is not None and self.evidence_ref.kind not in allowed_kinds[
            self.checkpoint_kind
        ]:
            raise ClientRuntimeContractError(
                "consequence evidence reference does not match checkpoint kind"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "checkpoint_kind": self.checkpoint_kind,
            "status": self.status,
            "evidence_ref": self.evidence_ref.as_dict() if self.evidence_ref else None,
        }


type LifecyclePayload = (
    ContextRequestPayload
    | DirectUserTurnPayload
    | ToolObservableResultPayload
    | ResponseEmissionPayload
    | CompactionTaskCheckpointPayload
    | RestartSessionTransitionPayload
    | CompletionAbandonmentPayload
    | ConsequenceCheckpointPayload
)
_PAYLOAD_TYPES: dict[LifecycleHook, type[object]] = {
    "manual_context_request": ContextRequestPayload,
    "pre_generation_context_request": ContextRequestPayload,
    "direct_user_turn": DirectUserTurnPayload,
    "tool_observable_result": ToolObservableResultPayload,
    "response_emission": ResponseEmissionPayload,
    "compaction_task_checkpoint": CompactionTaskCheckpointPayload,
    "restart_session_transition": RestartSessionTransitionPayload,
    "completion_abandonment": CompletionAbandonmentPayload,
    "consequence_checkpoint": ConsequenceCheckpointPayload,
}


@dataclass(frozen=True, slots=True)
class ClientLifecycleEnvelope:
    """One ordered, bounded, content-reference-only lifecycle envelope."""

    event_id: str
    sequence: int
    hook: LifecycleHook
    session_id: str
    client_id: str
    payload: LifecyclePayload
    conversation_id: str | None = None
    task_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    witness: EvidenceWitness = "host_observation"
    content_ownership: Literal["external_untrusted"] = "external_untrusted"
    retention_class: RetentionClass = "bounded"
    observed_at: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    contract_version: Literal["client-runtime-v0"] = CONTRACT_VERSION

    def __post_init__(self) -> None:
        _bounded_token(self.event_id, maximum=MAX_RUNTIME_ID_CHARS, label="event ID")
        _validate_sequence(self.sequence)
        _bounded_token(self.session_id, maximum=MAX_RUNTIME_ID_CHARS, label="session ID")
        _bounded_token(self.client_id, maximum=MAX_RUNTIME_ID_CHARS, label="client ID")
        for value, label in (
            (self.conversation_id, "conversation ID"),
            (self.task_id, "task ID"),
            (self.workspace_id, "workspace ID"),
            (self.project_id, "project ID"),
            (self.observed_at, "observed time"),
        ):
            _validate_identifier(value, label=label)
        _require_literal(self.hook, set(ALL_LIFECYCLE_HOOKS), label="lifecycle hook")
        if not isinstance(self.payload, _PAYLOAD_TYPES[self.hook]):
            raise ClientRuntimeContractError("payload type does not match lifecycle hook")
        if self.hook == "manual_context_request" and (
            not isinstance(self.payload, ContextRequestPayload)
            or self.payload.delivery_mode != "manual"
        ):
            raise ClientRuntimeContractError("manual context hook requires manual delivery mode")
        if self.hook == "pre_generation_context_request" and (
            not isinstance(self.payload, ContextRequestPayload)
            or self.payload.delivery_mode != "pre_generation"
        ):
            raise ClientRuntimeContractError(
                "pre-generation context hook requires pre-generation delivery mode"
            )
        _require_literal(
            self.witness,
            {
                "direct_user",
                "host_observation",
                "model_provider_self_attestation",
                "system_observation",
            },
            label="evidence witness",
        )
        if self.hook == "direct_user_turn" and self.witness != "direct_user":
            raise EvidenceBoundaryError("direct user turns require direct-user witness evidence")
        if self.hook != "direct_user_turn" and self.witness == "direct_user":
            raise EvidenceBoundaryError("direct-user witness is reserved for direct user turns")
        _require_literal(
            self.content_ownership,
            {"external_untrusted"},
            label="content ownership",
        )
        _require_literal(
            self.retention_class,
            {"ephemeral", "bounded", "checkpoint"},
            label="retention class",
        )
        _require_literal(
            self.contract_version,
            {CONTRACT_VERSION},
            label="runtime contract version",
        )
        _validate_diagnostics(self.diagnostics)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "hook": self.hook,
            "session_id": self.session_id,
            "client_id": self.client_id,
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "witness": self.witness,
            "content_ownership": self.content_ownership,
            "retention_class": self.retention_class,
            "observed_at": self.observed_at,
            "payload": self.payload.as_dict(),
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


LifecycleEnvelope = ClientLifecycleEnvelope
type HookResult = ClientLifecycleEnvelope | UnsupportedHookReport


@dataclass(frozen=True, slots=True)
class ModelProviderSelfAttestation:
    """A provider/model claim that is explicitly not direct user evidence."""

    attestation_ref: PayloadReference
    claimed_turn_ref: PayloadReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attestation_ref, PayloadReference):
            raise ClientRuntimeContractError("self-attestation requires a payload reference")
        if self.attestation_ref.kind != "attestation":
            raise EvidenceBoundaryError("self-attestation requires an attestation reference")
        if self.claimed_turn_ref is not None and not isinstance(
            self.claimed_turn_ref, PayloadReference
        ):
            raise ClientRuntimeContractError("claimed user turn requires a payload reference")
        if self.claimed_turn_ref is not None and self.claimed_turn_ref.kind != "user_turn":
            raise EvidenceBoundaryError("claimed user turn requires a user-turn reference")


@dataclass(frozen=True, slots=True)
class ContextDeliveryReceipt:
    request_event_id: str
    generation_id: str
    delivery_id: str
    delivery_sequence: int
    context_refs: tuple[PayloadReference, ...]
    delivered_before_generation: Literal[True] = True

    def __post_init__(self) -> None:
        _bounded_token(
            self.request_event_id,
            maximum=MAX_RUNTIME_ID_CHARS,
            label="request event ID",
        )
        _bounded_token(self.generation_id, maximum=MAX_RUNTIME_ID_CHARS, label="generation ID")
        _bounded_token(self.delivery_id, maximum=MAX_RUNTIME_ID_CHARS, label="delivery ID")
        _validate_sequence(self.delivery_sequence, label="delivery sequence")
        if type(self.context_refs) is not tuple:
            raise ClientRuntimeContractError("context delivery references must be a tuple")
        _bounded_context_refs(self.context_refs)
        if self.delivered_before_generation is not True:
            raise OrderingViolation("context delivery cannot claim post-generation delivery")


@dataclass(frozen=True, slots=True)
class GenerationReceipt:
    generation_id: str
    generation_sequence: int
    context_request_event_id: str | None
    context_delivery_sequence: int | None
    pre_generation_delivery: bool

    def __post_init__(self) -> None:
        _bounded_token(self.generation_id, maximum=MAX_RUNTIME_ID_CHARS, label="generation ID")
        _validate_sequence(self.generation_sequence, label="generation sequence")
        if self.context_request_event_id is not None:
            _bounded_token(
                self.context_request_event_id,
                maximum=MAX_RUNTIME_ID_CHARS,
                label="context request event ID",
            )
        if self.context_delivery_sequence is not None:
            _validate_sequence(self.context_delivery_sequence, label="context delivery sequence")
        if type(self.pre_generation_delivery) is not bool:
            raise ClientRuntimeContractError("pre-generation delivery must be a boolean")
        if self.pre_generation_delivery and (
            self.context_request_event_id is None
            or self.context_delivery_sequence is None
            or self.context_delivery_sequence >= self.generation_sequence
        ):
            raise OrderingViolation("pre-generation delivery must precede generation")

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "generation_sequence": self.generation_sequence,
            "context_request_event_id": self.context_request_event_id,
            "context_delivery_sequence": self.context_delivery_sequence,
            "pre_generation_delivery": self.pre_generation_delivery,
        }


TraceAction = Literal["context_delivered", "generation_started"]


@dataclass(frozen=True, slots=True)
class HostTraceEntry:
    sequence: int
    action: TraceAction
    reference_id: str

    def __post_init__(self) -> None:
        _validate_sequence(self.sequence, label="trace sequence")
        _require_literal(
            self.action,
            {"context_delivered", "generation_started"},
            label="trace action",
        )
        _bounded_token(self.reference_id, maximum=MAX_RUNTIME_ID_CHARS, label="trace reference ID")


@runtime_checkable
class ClientRuntimeAdapterV0(Protocol):
    """Structural contract for a future lifecycle-aware client adapter."""

    @property
    def capabilities(self) -> ClientRuntimeCapabilities: ...

    @property
    def events(self) -> tuple[ClientLifecycleEnvelope, ...]: ...

    def request_manual_context(
        self,
        *,
        requested_scopes: Sequence[str] = (),
        budget_chars: int = 8_000,
    ) -> HookResult: ...

    def request_pre_generation_context(
        self,
        *,
        generation_id: str,
        requested_scopes: Sequence[str] = (),
        budget_chars: int = 8_000,
    ) -> HookResult: ...

    def observe_direct_user_turn(self, turn_ref: PayloadReference) -> HookResult: ...

    def observe_tool_result(
        self,
        *,
        tool_name: str,
        result_ref: PayloadReference,
        result_kind: Literal["tool_result", "observable_result"] = "tool_result",
        task_id: str | None = None,
    ) -> HookResult: ...

    def observe_response_emission(
        self,
        response_ref: PayloadReference,
        *,
        emission_state: Literal["started", "completed", "abandoned"] = "completed",
        task_id: str | None = None,
    ) -> HookResult: ...

    def record_checkpoint(
        self,
        *,
        checkpoint_ref: PayloadReference,
        checkpoint_kind: Literal["compaction", "task_checkpoint"],
        checkpoint_state: Literal["created", "completed"] = "completed",
        task_id: str | None = None,
    ) -> HookResult: ...

    def record_session_transition(
        self,
        *,
        transition: Literal["session_start", "session_end", "restart", "session_transition"],
        previous_session_id: str | None = None,
        next_session_id: str | None = None,
    ) -> HookResult: ...

    def record_completion_or_abandonment(
        self,
        *,
        terminal_state: Literal["completed", "abandoned"],
        reason_code: Literal[
            "completed",
            "user_abandoned",
            "host_shutdown",
            "error",
            "unknown",
        ] = "completed",
        task_id: str | None = None,
    ) -> HookResult: ...

    def record_consequence_checkpoint(
        self,
        *,
        checkpoint_kind: Literal[
            "context_delivered",
            "tool_result_observed",
            "response_emitted",
            "task_outcome_observed",
            "correction_available",
        ],
        status: Literal["observed", "not_observed"],
        evidence_ref: PayloadReference | None = None,
        task_id: str | None = None,
    ) -> HookResult: ...


@runtime_checkable
class ClientRuntimeReferenceHostV0(ClientRuntimeAdapterV0, Protocol):
    """Optional reference-host proof surface, separate from the adapter seam."""

    def deliver_context(
        self,
        request: ClientLifecycleEnvelope,
        context_refs: Sequence[PayloadReference],
    ) -> ContextDeliveryReceipt | UnsupportedHookReport: ...

    def begin_generation(self, *, generation_id: str) -> GenerationReceipt: ...


class DeterministicFakeClientRuntimeHost:
    """In-repository contract host with deterministic, non-persistent traces."""

    def __init__(
        self,
        capabilities: ClientRuntimeCapabilities | None = None,
        *,
        client_id: str = "fake-client-v0",
        session_id: str = "fake-session-v0",
    ) -> None:
        if capabilities is None:
            self._capabilities = ClientRuntimeCapabilities.for_level("L3")
        elif not isinstance(capabilities, ClientRuntimeCapabilities):
            raise ClientRuntimeContractError("fake host requires typed runtime capabilities")
        else:
            self._capabilities = capabilities
        _bounded_token(client_id, maximum=MAX_RUNTIME_ID_CHARS, label="client ID")
        _bounded_token(session_id, maximum=MAX_RUNTIME_ID_CHARS, label="session ID")
        self._client_id = client_id
        self._session_id = session_id
        self._sequence = 0
        self._events: list[ClientLifecycleEnvelope] = []
        self._trace: list[HostTraceEntry] = []
        self._pending_context: dict[str, ClientLifecycleEnvelope] = {}
        self._delivered_context: dict[str, ContextDeliveryReceipt] = {}
        self._started_generations: set[str] = set()

    @classmethod
    def for_level(
        cls,
        level: CapabilityLevel,
        *,
        client_id: str = "fake-client-v0",
        session_id: str = "fake-session-v0",
    ) -> DeterministicFakeClientRuntimeHost:
        return cls(
            ClientRuntimeCapabilities.for_level(level),
            client_id=client_id,
            session_id=session_id,
        )

    @property
    def capabilities(self) -> ClientRuntimeCapabilities:
        return self._capabilities

    @property
    def events(self) -> tuple[ClientLifecycleEnvelope, ...]:
        return tuple(self._events)

    @property
    def current_session_id(self) -> str:
        return self._session_id

    @property
    def trace(self) -> tuple[HostTraceEntry, ...]:
        return tuple(self._trace)

    def _next_sequence(self) -> int:
        if self._sequence >= MAX_SEQUENCE:
            raise ClientRuntimeContractError("runtime sequence exhausted")
        self._sequence += 1
        return self._sequence

    def _unsupported(
        self,
        hook: LifecycleHook,
        *,
        reason: str = "hook_not_declared",
    ) -> UnsupportedHookReport | None:
        capability = self._capabilities.for_hook(hook)
        if capability.status == "unsupported":
            return UnsupportedHookReport(
                hook=hook,
                required_level=capability.minimum_level,
                declared_level=self._capabilities.level,
                reason=reason,
            )
        return None

    def _emit(
        self,
        *,
        hook: LifecycleHook,
        payload: LifecyclePayload,
        witness: EvidenceWitness = "host_observation",
        conversation_id: str | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        retention_class: RetentionClass = "bounded",
    ) -> ClientLifecycleEnvelope:
        sequence = self._next_sequence()
        envelope = ClientLifecycleEnvelope(
            event_id=f"evt-{sequence:06d}",
            sequence=sequence,
            hook=hook,
            session_id=self._session_id,
            client_id=self._client_id,
            payload=payload,
            conversation_id=conversation_id,
            task_id=task_id,
            workspace_id=workspace_id,
            project_id=project_id,
            witness=witness,
            retention_class=retention_class,
        )
        self._events.append(envelope)
        return envelope

    def request_manual_context(
        self,
        *,
        requested_scopes: Sequence[str] = (),
        budget_chars: int = 8_000,
    ) -> HookResult:
        report = self._unsupported("manual_context_request")
        if report is not None:
            return report
        payload = ContextRequestPayload(
            request_id=f"manual-{self._sequence + 1:06d}",
            generation_id=None,
            requested_scopes=_bounded_scopes(requested_scopes),
            budget_chars=budget_chars,
            delivery_mode="manual",
        )
        return self._emit(hook="manual_context_request", payload=payload)

    def request_pre_generation_context(
        self,
        *,
        generation_id: str,
        requested_scopes: Sequence[str] = (),
        budget_chars: int = 8_000,
        conversation_id: str | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> HookResult:
        _bounded_token(generation_id, maximum=MAX_RUNTIME_ID_CHARS, label="generation ID")
        report = self._unsupported("pre_generation_context_request")
        if report is not None:
            return report
        if generation_id in self._started_generations:
            raise OrderingViolation("context request cannot follow generation start")
        if generation_id in self._pending_context:
            raise OrderingViolation("generation already has a pending context request")
        payload = ContextRequestPayload(
            request_id=f"context-{self._sequence + 1:06d}",
            generation_id=generation_id,
            requested_scopes=_bounded_scopes(requested_scopes),
            budget_chars=budget_chars,
            delivery_mode="pre_generation",
        )
        envelope = self._emit(
            hook="pre_generation_context_request",
            payload=payload,
            conversation_id=conversation_id,
            task_id=task_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        self._pending_context[generation_id] = envelope
        return envelope

    def deliver_context(
        self,
        request: ClientLifecycleEnvelope,
        context_refs: Sequence[PayloadReference],
    ) -> ContextDeliveryReceipt | UnsupportedHookReport:
        report = self._unsupported("pre_generation_context_request")
        if report is not None:
            return report
        if not isinstance(request, ClientLifecycleEnvelope):
            raise ClientRuntimeContractError("context delivery requires a typed request envelope")
        if request.hook != "pre_generation_context_request" or not isinstance(
            request.payload, ContextRequestPayload
        ):
            raise OrderingViolation("context delivery requires a pre-generation request")
        generation_id = request.payload.generation_id
        if generation_id is None or self._pending_context.get(generation_id) != request:
            raise OrderingViolation("context delivery does not match a pending request")
        if generation_id in self._started_generations:
            raise OrderingViolation("context delivery cannot follow generation start")
        if generation_id in self._delivered_context:
            raise OrderingViolation("context can be delivered only once per generation")
        refs = _bounded_context_refs(context_refs)
        sequence = self._next_sequence()
        receipt = ContextDeliveryReceipt(
            request_event_id=request.event_id,
            generation_id=generation_id,
            delivery_id=f"delivery-{sequence:06d}",
            delivery_sequence=sequence,
            context_refs=refs,
        )
        self._delivered_context[generation_id] = receipt
        self._trace.append(
            HostTraceEntry(
                sequence=sequence,
                action="context_delivered",
                reference_id=receipt.delivery_id,
            )
        )
        return receipt

    def begin_generation(self, *, generation_id: str) -> GenerationReceipt:
        _bounded_token(generation_id, maximum=MAX_RUNTIME_ID_CHARS, label="generation ID")
        if generation_id in self._started_generations:
            raise OrderingViolation("generation cannot start twice")
        capability = self._capabilities.for_hook("pre_generation_context_request")
        request = self._pending_context.get(generation_id)
        delivery = self._delivered_context.get(generation_id)
        if capability.status == "supported" and (request is None or delivery is None):
            raise OrderingViolation("L1 or higher generation requires delivered context first")
        sequence = self._next_sequence()
        pre_generation_delivery = (
            capability.status == "supported"
            and request is not None
            and delivery is not None
            and delivery.delivery_sequence < sequence
        )
        receipt = GenerationReceipt(
            generation_id=generation_id,
            generation_sequence=sequence,
            context_request_event_id=request.event_id if request else None,
            context_delivery_sequence=delivery.delivery_sequence if delivery else None,
            pre_generation_delivery=pre_generation_delivery,
        )
        self._started_generations.add(generation_id)
        self._trace.append(
            HostTraceEntry(
                sequence=sequence,
                action="generation_started",
                reference_id=generation_id,
            )
        )
        return receipt

    def observe_direct_user_turn(
        self,
        turn_ref: PayloadReference,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("direct_user_turn")
        if report is not None:
            return report
        payload = DirectUserTurnPayload(turn_ref)
        return self._emit(
            hook="direct_user_turn",
            payload=payload,
            witness="direct_user",
            conversation_id=conversation_id,
            task_id=task_id,
        )

    def observe_user_turn_attestation(
        self,
        attestation: ModelProviderSelfAttestation,
    ) -> UnsupportedHookReport:
        if not isinstance(attestation, ModelProviderSelfAttestation):
            raise ClientRuntimeContractError("self-attestation must use its typed envelope")
        capability = self._capabilities.for_hook("direct_user_turn")
        return UnsupportedHookReport(
            hook="direct_user_turn",
            required_level=capability.minimum_level,
            declared_level=self._capabilities.level,
            reason="model_provider_self_attestation_is_not_direct_user_evidence",
        )

    def observe_tool_result(
        self,
        *,
        tool_name: str,
        result_ref: PayloadReference,
        result_kind: Literal["tool_result", "observable_result"] = "tool_result",
        task_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("tool_observable_result")
        if report is not None:
            return report
        return self._emit(
            hook="tool_observable_result",
            payload=ToolObservableResultPayload(tool_name, result_ref, result_kind),
            task_id=task_id,
        )

    def observe_response_emission(
        self,
        response_ref: PayloadReference,
        *,
        emission_state: Literal["started", "completed", "abandoned"] = "completed",
        task_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("response_emission")
        if report is not None:
            return report
        return self._emit(
            hook="response_emission",
            payload=ResponseEmissionPayload(response_ref, emission_state),
            task_id=task_id,
        )

    def record_checkpoint(
        self,
        *,
        checkpoint_ref: PayloadReference,
        checkpoint_kind: Literal["compaction", "task_checkpoint"],
        checkpoint_state: Literal["created", "completed"] = "completed",
        task_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("compaction_task_checkpoint")
        if report is not None:
            return report
        return self._emit(
            hook="compaction_task_checkpoint",
            payload=CompactionTaskCheckpointPayload(
                checkpoint_ref,
                checkpoint_kind,
                checkpoint_state,
            ),
            task_id=task_id,
            retention_class="checkpoint",
        )

    def record_session_transition(
        self,
        *,
        transition: Literal["session_start", "session_end", "restart", "session_transition"],
        previous_session_id: str | None = None,
        next_session_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("restart_session_transition")
        if report is not None:
            return report
        payload = RestartSessionTransitionPayload(transition, previous_session_id, next_session_id)
        if payload.previous_session_id is not None and (
            payload.previous_session_id != self._session_id
        ):
            raise OrderingViolation(
                "session transition previous session does not match current state"
            )
        result = self._emit(
            hook="restart_session_transition",
            payload=payload,
            retention_class="checkpoint",
        )
        if next_session_id is not None:
            self._session_id = next_session_id
        return result

    def record_completion_or_abandonment(
        self,
        *,
        terminal_state: Literal["completed", "abandoned"],
        reason_code: Literal[
            "completed",
            "user_abandoned",
            "host_shutdown",
            "error",
            "unknown",
        ] = "completed",
        task_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("completion_abandonment")
        if report is not None:
            return report
        return self._emit(
            hook="completion_abandonment",
            payload=CompletionAbandonmentPayload(terminal_state, reason_code),
            task_id=task_id,
            retention_class="checkpoint",
        )

    def record_consequence_checkpoint(
        self,
        *,
        checkpoint_kind: Literal[
            "context_delivered",
            "tool_result_observed",
            "response_emitted",
            "task_outcome_observed",
            "correction_available",
        ],
        status: Literal["observed", "not_observed"],
        evidence_ref: PayloadReference | None = None,
        task_id: str | None = None,
    ) -> HookResult:
        report = self._unsupported("consequence_checkpoint")
        if report is not None:
            return report
        capability = self._capabilities.for_hook("consequence_checkpoint")
        if checkpoint_kind not in capability.supported_consequence_kinds:
            return UnsupportedHookReport(
                hook="consequence_checkpoint",
                required_level=capability.minimum_level,
                declared_level=self._capabilities.level,
                reason="consequence_checkpoint_kind_not_declared",
            )
        return self._emit(
            hook="consequence_checkpoint",
            payload=ConsequenceCheckpointPayload(checkpoint_kind, status, evidence_ref),
            task_id=task_id,
            retention_class="checkpoint",
        )


FakeClientRuntimeHost = DeterministicFakeClientRuntimeHost

__all__ = [
    "ALL_LIFECYCLE_HOOKS",
    "CONSEQUENCE_CHECKPOINT_KINDS",
    "CONTRACT_VERSION",
    "REQUIRED_LIFECYCLE_HOOKS",
    "CapabilityLevel",
    "CapabilityStatus",
    "ClientLifecycleEnvelope",
    "ClientRuntimeAdapterV0",
    "ClientRuntimeCapabilities",
    "ClientRuntimeContractError",
    "ClientRuntimeReferenceHostV0",
    "CompactionTaskCheckpointPayload",
    "CompletionAbandonmentPayload",
    "ConsequenceCheckpointPayload",
    "ContextDeliveryReceipt",
    "ContextRequestPayload",
    "DeterministicFakeClientRuntimeHost",
    "Diagnostic",
    "DiagnosticSeverity",
    "DirectUserTurnPayload",
    "EvidenceBoundaryError",
    "EvidenceWitness",
    "FakeClientRuntimeHost",
    "GenerationReceipt",
    "HookCapability",
    "HookResult",
    "HostTraceEntry",
    "LifecycleEnvelope",
    "LifecycleHook",
    "LifecyclePayload",
    "ModelProviderSelfAttestation",
    "OrderingGuarantee",
    "OrderingViolation",
    "PayloadReference",
    "ReferenceKind",
    "RequiredLifecycleHook",
    "ResponseEmissionPayload",
    "RestartSessionTransitionPayload",
    "RetentionClass",
    "ToolObservableResultPayload",
    "TraceAction",
    "UnsupportedHookReport",
]

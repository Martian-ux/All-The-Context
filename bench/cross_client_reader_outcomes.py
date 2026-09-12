"""Thin packet/result bridge for the six-cell reader-outcome sanity pilot.

The bridge is deliberately not a reader, model runner, tokenizer, Core client,
or experiment framework.  It builds the exact prompt packets a later reader
would receive, validates externally supplied results and usage provenance, and
grades two symbolic task oracles without exposing those oracles to the packet.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

BRIDGE_SCHEMA = "atc.cross_client_reader_outcomes.v1"
PACKET_SCHEMA = "atc.cross_client_reader_outcomes.packet.v1"
RESULT_SCHEMA = "atc.cross_client_reader_outcomes.result.v1"
ARM_IDS = (
    "simple_no_memory",
    "maintained_context_file",
    "actual_atc_bootstrap",
)
TASK_IDS = ("changed_preference", "unknown_deployment_region")
EVIDENCE_KINDS = {"non_model_fixture", "external_reader"}
ARM_CONTEXT_SOURCES = MappingProxyType(
    {
        "simple_no_memory": "no_memory",
        "maintained_context_file": "maintained_context_file",
        "actual_atc_bootstrap": "actual_atc_bootstrap",
    }
)
ARM_ACCESS_RETRIEVAL = MappingProxyType(
    {
        "simple_no_memory": (0, 0),
        "maintained_context_file": (0, 0),
        "actual_atc_bootstrap": (1, 1),
    }
)
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "tool_calls",
    "model_calls",
    "provider_calls",
    "network_calls",
    "cost_microunits",
)


class ReceiptValidationError(ValueError):
    """Raised when a packet or externally supplied result is not self-consistent."""


@dataclass(frozen=True)
class ReaderLimits:
    """The common reader ceiling; it contains no model or price commitment."""

    max_input_tokens: int = 2_048
    max_output_tokens: int = 32
    tool_calls: int = 0
    overflow_policy: str = "fail_before_provider"
    model_config_placeholder: str = "deferred_external_reader_config"
    tokenizer_config_placeholder: str = "deferred_external_tokenizer_config"
    padding_policy: str = "no_padding"
    truncation_policy: str = "no_truncation"

    def __post_init__(self) -> None:
        expected = {
            "max_input_tokens": 2_048,
            "max_output_tokens": 32,
            "tool_calls": 0,
            "overflow_policy": "fail_before_provider",
            "model_config_placeholder": "deferred_external_reader_config",
            "tokenizer_config_placeholder": "deferred_external_tokenizer_config",
            "padding_policy": "no_padding",
            "truncation_policy": "no_truncation",
        }
        actual = {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "tool_calls": self.tool_calls,
            "overflow_policy": self.overflow_policy,
            "model_config_placeholder": self.model_config_placeholder,
            "tokenizer_config_placeholder": self.tokenizer_config_placeholder,
            "padding_policy": self.padding_policy,
            "truncation_policy": self.truncation_policy,
        }
        if any(
            type(actual[name]) is not type(expected[name]) or actual[name] != expected[name]
            for name in expected
        ):
            raise ReceiptValidationError("reader limits are fixed for this pilot")

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "tool_calls": self.tool_calls,
            "overflow_policy": self.overflow_policy,
            "model_config_placeholder": self.model_config_placeholder,
            "tokenizer_config_placeholder": self.tokenizer_config_placeholder,
            "padding_policy": self.padding_policy,
            "truncation_policy": self.truncation_policy,
        }


@dataclass(frozen=True)
class TaskPrompt:
    task_key: str
    instruction: str


@dataclass(frozen=True)
class GroundTruth:
    """Evaluation-only oracle, never serialized into a reader packet."""

    task_key: str
    expected_answer: str


@dataclass(frozen=True)
class MaintainedContextFile:
    items: tuple[str, ...]
    construction_units: int
    event_count: int
    query_blind: bool = True
    source_kind: str = "authoritative_event_stream"


@dataclass(frozen=True)
class ReaderPacket:
    task_key: str
    instruction: str
    context_items: tuple[str, ...]
    context_source: str
    prompt: str
    reader_limits: ReaderLimits
    construction_units: int
    access_units: int
    retrieval_units: int
    packet_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": PACKET_SCHEMA,
            "task_key": self.task_key,
            "instruction": self.instruction,
            "context_items": list(self.context_items),
            "context_source": self.context_source,
            "prompt": self.prompt,
            "reader_limits": self.reader_limits.as_dict(),
            "construction_units": self.construction_units,
            "access_units": self.access_units,
            "retrieval_units": self.retrieval_units,
            "packet_sha256": self.packet_sha256,
        }


@dataclass(frozen=True)
class ReaderUsage:
    """Only usage fields actually exposed by a reader route are retained."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: int | None = None
    model_calls: int | None = None
    provider_calls: int | None = None
    network_calls: int | None = None
    cost_microunits: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReaderUsage:
        unknown_fields = set(value).difference(USAGE_FIELDS)
        if unknown_fields:
            raise ReceiptValidationError("usage contains unknown fields")
        values: dict[str, int | None] = {}
        for field in USAGE_FIELDS:
            raw = value.get(field)
            if raw is None:
                values[field] = None
            elif isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ReceiptValidationError(f"usage.{field} must be a non-negative integer")
            else:
                values[field] = raw
        return cls(**values)

    def as_dict(self) -> dict[str, int]:
        return {
            field: value for field in USAGE_FIELDS if (value := getattr(self, field)) is not None
        }

    @property
    def observed_fields(self) -> tuple[str, ...]:
        return tuple(self.as_dict())


@dataclass(frozen=True)
class ReaderResult:
    task_key: str
    packet_sha256: str
    answer: str
    evidence_kind: str
    config: Mapping[str, Any]
    usage: ReaderUsage
    provenance: Mapping[str, Any]
    result_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": RESULT_SCHEMA,
            "task_key": self.task_key,
            "packet_sha256": self.packet_sha256,
            "answer": self.answer,
            "evidence_kind": self.evidence_kind,
            "config": dict(self.config),
            "usage": self.usage.as_dict(),
            "provenance": dict(self.provenance),
            "result_sha256": self.result_sha256,
        }


TASK_PROMPTS = MappingProxyType(
    {
        "changed_preference": TaskPrompt(
            task_key="changed_preference",
            instruction=(
                "State the user's current answer-style preference exactly, "
                "or reply UNKNOWN if context does not establish it."
            ),
        ),
        "unknown_deployment_region": TaskPrompt(
            task_key="unknown_deployment_region",
            instruction=(
                "State the configured deployment region exactly, "
                "or reply UNKNOWN if context does not establish it."
            ),
        ),
    }
)
GROUND_TRUTHS = {
    "changed_preference": GroundTruth(
        task_key="changed_preference",
        expected_answer="I prefer evidence-backed answers.",
    ),
    "unknown_deployment_region": GroundTruth(
        task_key="unknown_deployment_region",
        expected_answer="UNKNOWN",
    ),
}


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReceiptValidationError("value is not deterministically JSON serializable") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ReceiptValidationError(f"{field_name} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ReceiptValidationError(f"{field_name} must be a SHA-256 hex digest") from exc
    return value.lower()


def _nonnegative(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReceiptValidationError(f"{field_name} must be a non-negative integer")
    return cast(int, value)


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ReceiptValidationError(f"{field_name} must be text")
    return value


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptValidationError(f"{field_name} must be an object")
    return value


def _normalise_answer(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _context_text(items: Sequence[str]) -> str:
    return "\n".join(items) if items else "(none supplied)"


def _packet_payload(
    *,
    task: TaskPrompt,
    context_items: tuple[str, ...],
    context_source: str,
    prompt: str,
    reader_limits: ReaderLimits,
    construction_units: int,
    access_units: int,
    retrieval_units: int,
) -> dict[str, Any]:
    return {
        "schema": PACKET_SCHEMA,
        "task_key": task.task_key,
        "instruction": task.instruction,
        "context_items": list(context_items),
        "context_source": context_source,
        "prompt": prompt,
        "reader_limits": reader_limits.as_dict(),
        "construction_units": construction_units,
        "access_units": access_units,
        "retrieval_units": retrieval_units,
    }


def _context_values(items: Sequence[str | Mapping[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for index, item in enumerate(items):
        if isinstance(item, Mapping):
            value = item.get("content")
            if not isinstance(value, str):
                raise ReceiptValidationError(f"context_items[{index}].content must be text")
        else:
            value = _text(item, f"context_items[{index}]")
        values.append(value)
    return tuple(values)


def build_packet(
    task: TaskPrompt,
    context_items: Sequence[str | Mapping[str, Any]],
    *,
    context_source: str,
    construction_units: int,
    access_units: int,
    retrieval_units: int,
    reader_limits: ReaderLimits | None = None,
    input_token_count: int | None = None,
) -> ReaderPacket:
    """Build a packet whose reader-visible prompt has no arm or oracle metadata."""

    if task.task_key not in TASK_IDS:
        raise ReceiptValidationError("unsupported task key")
    if task.instruction != TASK_PROMPTS[task.task_key].instruction:
        raise ReceiptValidationError("task instruction must match the canonical task exactly")
    limits = reader_limits or ReaderLimits()
    if limits != ReaderLimits():
        raise ReceiptValidationError("reader limits are fixed for this pilot")
    items = _context_values(context_items)
    construction = _nonnegative(construction_units, "construction_units")
    access = _nonnegative(access_units, "access_units")
    retrieval = _nonnegative(retrieval_units, "retrieval_units")
    if input_token_count is not None:
        observed_input = _nonnegative(input_token_count, "input_token_count")
        if observed_input > limits.max_input_tokens:
            raise ReceiptValidationError("input overflow must fail before provider execution")
    prompt = f"{task.instruction}\n\nContext:\n{_context_text(items)}"
    payload = _packet_payload(
        task=task,
        context_items=items,
        context_source=_text(context_source, "context_source"),
        prompt=prompt,
        reader_limits=limits,
        construction_units=construction,
        access_units=access,
        retrieval_units=retrieval,
    )
    return ReaderPacket(
        task_key=task.task_key,
        instruction=task.instruction,
        context_items=items,
        context_source=context_source,
        prompt=prompt,
        reader_limits=limits,
        construction_units=construction,
        access_units=access,
        retrieval_units=retrieval,
        packet_sha256=_sha256(payload),
    )


def _validate_packet(packet: ReaderPacket) -> None:
    if not isinstance(packet, ReaderPacket):
        raise ReceiptValidationError("reader packet object is invalid")
    if packet.task_key not in TASK_IDS:
        raise ReceiptValidationError("unsupported packet task key")
    if packet.instruction != TASK_PROMPTS[packet.task_key].instruction:
        raise ReceiptValidationError("packet task instruction is not canonical")
    if not isinstance(packet.context_items, tuple) or any(
        not isinstance(item, str) for item in packet.context_items
    ):
        raise ReceiptValidationError("packet context items must be an immutable text tuple")
    if not isinstance(packet.context_source, str):
        raise ReceiptValidationError("packet context source must be text")
    if not isinstance(packet.reader_limits, ReaderLimits) or packet.reader_limits != ReaderLimits():
        raise ReceiptValidationError("packet reader limits are not the fixed pilot limits")
    _nonnegative(packet.construction_units, "packet.construction_units")
    _nonnegative(packet.access_units, "packet.access_units")
    _nonnegative(packet.retrieval_units, "packet.retrieval_units")
    expected_prompt = f"{packet.instruction}\n\nContext:\n{_context_text(packet.context_items)}"
    if packet.prompt != expected_prompt:
        raise ReceiptValidationError("packet prompt does not match its contents")
    expected_hash = _sha256(
        _packet_payload(
            task=TaskPrompt(packet.task_key, packet.instruction),
            context_items=packet.context_items,
            context_source=packet.context_source,
            prompt=packet.prompt,
            reader_limits=packet.reader_limits,
            construction_units=packet.construction_units,
            access_units=packet.access_units,
            retrieval_units=packet.retrieval_units,
        )
    )
    if _sha256_token(packet.packet_sha256, "packet.packet_sha256") != expected_hash:
        raise ReceiptValidationError("packet hash does not match its contents")


def context_items_from_bootstrap(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract only reader-visible content from a real bootstrap response."""

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ReceiptValidationError("bootstrap payload.items must be a list")
    return _context_values(raw_items)


def maintain_context_file(events: Sequence[Mapping[str, Any]]) -> MaintainedContextFile:
    """Maintain a query-blind file by last explicit value per declared slot."""

    latest: dict[str, tuple[int, int, str]] = {}
    for index, event in enumerate(events):
        slot = _text(event.get("slot"), f"events[{index}].slot")
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ReceiptValidationError(f"events[{index}].sequence must be an integer")
        content = _text(event.get("content"), f"events[{index}].content")
        explicit = event.get("explicit_user_statement")
        if not isinstance(explicit, bool):
            raise ReceiptValidationError(f"events[{index}].explicit_user_statement must be boolean")
        if explicit:
            previous = latest.get(slot)
            if previous is None or (sequence, index) >= previous[:2]:
                latest[slot] = (sequence, index, content)
    return MaintainedContextFile(
        items=tuple(value[2] for value in sorted(latest.values(), key=lambda item: item[:2])),
        construction_units=len(events),
        event_count=len(events),
    )


def _result_payload(
    *,
    task_key: str,
    packet_sha256: str,
    answer: str,
    evidence_kind: str,
    config: Mapping[str, Any],
    usage: ReaderUsage,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "task_key": task_key,
        "packet_sha256": packet_sha256,
        "answer": answer,
        "evidence_kind": evidence_kind,
        "config": dict(config),
        "usage": usage.as_dict(),
        "provenance": dict(provenance),
    }


def build_reader_result(
    packet: ReaderPacket,
    answer: str,
    *,
    evidence_kind: str = "non_model_fixture",
    config: Mapping[str, Any] | None = None,
    usage: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> ReaderResult:
    """Create a self-hashed canned or externally supplied result.

    The canned path deliberately has no usage fields.  It does not fabricate
    model calls, tokenizer counts, provider calls, or costs.
    """

    if evidence_kind not in EVIDENCE_KINDS:
        raise ReceiptValidationError("unsupported reader evidence kind")
    actual_config = dict(config or {})
    actual_usage = ReaderUsage.from_mapping(_mapping(usage or {}, "usage"))
    actual_provenance = dict(provenance or {})
    if not actual_provenance or not isinstance(actual_provenance.get("source"), str):
        raise ReceiptValidationError("reader provenance.source is required")
    if evidence_kind == "non_model_fixture" and any(actual_usage.as_dict().values()):
        raise ReceiptValidationError("non-model fixture results cannot claim model usage")
    payload = _result_payload(
        task_key=packet.task_key,
        packet_sha256=packet.packet_sha256,
        answer=_text(answer, "answer"),
        evidence_kind=evidence_kind,
        config=actual_config,
        usage=actual_usage,
        provenance=actual_provenance,
    )
    return ReaderResult(
        task_key=packet.task_key,
        packet_sha256=packet.packet_sha256,
        answer=answer,
        evidence_kind=evidence_kind,
        config=actual_config,
        usage=actual_usage,
        provenance=actual_provenance,
        result_sha256=_sha256(payload),
    )


def reader_result_from_mapping(value: Mapping[str, Any]) -> ReaderResult:
    """Validate a result received from a later reader route."""

    raw = _mapping(value, "reader result")
    if raw.get("schema") != RESULT_SCHEMA:
        raise ReceiptValidationError("unsupported reader result schema")
    task_key = _text(raw.get("task_key"), "result.task_key")
    packet_sha256 = _sha256_token(raw.get("packet_sha256"), "result.packet_sha256")
    answer = _text(raw.get("answer"), "result.answer")
    evidence_kind = raw.get("evidence_kind")
    if evidence_kind not in EVIDENCE_KINDS:
        raise ReceiptValidationError("unsupported reader evidence kind")
    config = _mapping(raw.get("config", {}), "result.config")
    usage = ReaderUsage.from_mapping(_mapping(raw.get("usage", {}), "result.usage"))
    provenance = _mapping(raw.get("provenance"), "result.provenance")
    if not isinstance(provenance.get("source"), str):
        raise ReceiptValidationError("result.provenance.source is required")
    if "semantic_use" in raw:
        raise ReceiptValidationError("semantic_use is not an accepted reader field")
    if evidence_kind == "non_model_fixture" and any(usage.as_dict().values()):
        raise ReceiptValidationError("non-model fixture results cannot claim model usage")
    payload = _result_payload(
        task_key=task_key,
        packet_sha256=packet_sha256,
        answer=answer,
        evidence_kind=evidence_kind,
        config=config,
        usage=usage,
        provenance=provenance,
    )
    result_sha256 = _sha256_token(raw.get("result_sha256"), "result.result_sha256")
    if result_sha256 != _sha256(payload):
        raise ReceiptValidationError("reader result hash does not match its contents")
    return ReaderResult(
        task_key=task_key,
        packet_sha256=packet_sha256,
        answer=answer,
        evidence_kind=evidence_kind,
        config=config,
        usage=usage,
        provenance=provenance,
        result_sha256=result_sha256,
    )


def _validate_reader_result(result: ReaderResult) -> None:
    if not isinstance(result, ReaderResult):
        raise ReceiptValidationError("reader result object is invalid")
    if result.task_key not in TASK_IDS:
        raise ReceiptValidationError("unsupported result task key")
    _text(result.answer, "result.answer")
    if result.evidence_kind not in EVIDENCE_KINDS:
        raise ReceiptValidationError("unsupported reader evidence kind")
    if not isinstance(result.config, Mapping):
        raise ReceiptValidationError("result.config must be an object")
    if not isinstance(result.usage, ReaderUsage):
        raise ReceiptValidationError("result.usage object is invalid")
    for field in USAGE_FIELDS:
        observed = getattr(result.usage, field)
        if observed is not None and (
            isinstance(observed, bool) or not isinstance(observed, int) or observed < 0
        ):
            raise ReceiptValidationError(f"usage.{field} must be a non-negative integer")
    if not isinstance(result.provenance, Mapping):
        raise ReceiptValidationError("result.provenance must be an object")
    if not isinstance(result.provenance.get("source"), str):
        raise ReceiptValidationError("result.provenance.source is required")
    if result.evidence_kind == "non_model_fixture" and any(result.usage.as_dict().values()):
        raise ReceiptValidationError("non-model fixture results cannot claim model usage")
    expected_hash = _sha256(
        _result_payload(
            task_key=result.task_key,
            packet_sha256=result.packet_sha256,
            answer=result.answer,
            evidence_kind=result.evidence_kind,
            config=result.config,
            usage=result.usage,
            provenance=result.provenance,
        )
    )
    if _sha256_token(result.result_sha256, "result.result_sha256") != expected_hash:
        raise ReceiptValidationError("reader result hash does not match its contents")


def _contains_normalised(answer: str, candidate: str) -> bool:
    needle = _normalise_answer(candidate)
    return bool(needle) and needle in _normalise_answer(answer)


def grade_reader_result(
    packet: ReaderPacket,
    result: ReaderResult | Mapping[str, Any],
    ground_truth: GroundTruth,
    *,
    stale_preference: str | None = None,
    irrelevant_context: Sequence[str] = (),
) -> dict[str, Any]:
    """Grade answer semantics while keeping stale use separate from unsupported answers."""

    _validate_packet(packet)
    checked = result if isinstance(result, ReaderResult) else reader_result_from_mapping(result)
    _validate_reader_result(checked)
    if checked.task_key != packet.task_key or checked.packet_sha256 != packet.packet_sha256:
        raise ReceiptValidationError("reader result is not bound to this packet")
    if ground_truth.task_key != packet.task_key:
        raise ReceiptValidationError("ground truth is not bound to this packet task")
    expected = _normalise_answer(ground_truth.expected_answer)
    observed = _normalise_answer(checked.answer)
    stale = stale_preference or ""
    usage = checked.usage
    limit_failure: str | None = None
    if (
        usage.input_tokens is not None
        and usage.input_tokens > packet.reader_limits.max_input_tokens
    ):
        limit_failure = "input_overflow"
    elif (
        usage.output_tokens is not None
        and usage.output_tokens > packet.reader_limits.max_output_tokens
    ):
        limit_failure = "output_overflow"
    elif usage.tool_calls is not None and usage.tool_calls > packet.reader_limits.tool_calls:
        limit_failure = "tool_budget_escape"
    if limit_failure is not None:
        classification = limit_failure
        success = False
    elif observed == expected:
        classification = "exact_match"
        success = True
    elif observed == "unknown" and expected != "unknown":
        classification = "safe_abstention"
        success = False
    elif stale and _contains_normalised(checked.answer, stale):
        classification = "stale_preference_use"
        success = False
    elif expected == "unknown" and any(
        _contains_normalised(checked.answer, item) for item in irrelevant_context
    ):
        classification = "inappropriate_irrelevant_disclosure"
        success = False
    else:
        classification = "unsupported_answer"
        success = False
    return {
        "task_key": packet.task_key,
        "success": success,
        "classification": classification,
        "safe_abstention_not_comparative_evidence": classification == "safe_abstention",
        "packet_sha256": packet.packet_sha256,
        "result_sha256": checked.result_sha256,
        "config_sha256": _sha256(dict(checked.config)),
        "evidence_kind": checked.evidence_kind,
        "usage": checked.usage.as_dict(),
        "usage_fields_observed": list(checked.usage.observed_fields),
        "construction_units": packet.construction_units,
        "access_units": packet.access_units,
        "retrieval_units": packet.retrieval_units,
        "observed_answer_sha256": _sha256(checked.answer),
    }


def _cell_key(arm_id: str, task_key: str) -> tuple[str, str]:
    return arm_id, task_key


def grade_six_cells(
    packets: Mapping[tuple[str, str], ReaderPacket],
    results: Mapping[tuple[str, str], ReaderResult | Mapping[str, Any]],
    *,
    construction_units_by_arm: Mapping[str, int],
    stale_preference: str | None = None,
    irrelevant_context_by_task: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Grade exactly two tasks across the three fixed arms as a sanity pilot."""

    expected_keys = {_cell_key(arm_id, task_key) for arm_id in ARM_IDS for task_key in TASK_IDS}
    if set(packets) != expected_keys or set(results) != expected_keys:
        raise ReceiptValidationError("six-cell grading requires exactly the fixed arm/task matrix")
    if set(construction_units_by_arm) != set(ARM_IDS):
        raise ReceiptValidationError("construction accounting must name each arm exactly once")
    for arm_id, units in construction_units_by_arm.items():
        if not isinstance(units, int) or isinstance(units, bool) or units < 0:
            raise ReceiptValidationError(f"construction units for {arm_id} must be non-negative")
    for (arm_id, _task_key), packet in packets.items():
        _validate_packet(packet)
        expected_source = ARM_CONTEXT_SOURCES[arm_id]
        if packet.context_source != expected_source:
            raise ReceiptValidationError(
                f"{arm_id} packet must use context source {expected_source!r}"
            )
        expected_access, expected_retrieval = ARM_ACCESS_RETRIEVAL[arm_id]
        if packet.access_units != expected_access or packet.retrieval_units != expected_retrieval:
            raise ReceiptValidationError(f"{arm_id} packet access/retrieval accounting is invalid")
        if arm_id == "simple_no_memory" and packet.context_items:
            raise ReceiptValidationError("simple_no_memory packets must have empty context")
    if any(packet.construction_units for packet in packets.values()):
        raise ReceiptValidationError(
            "shared construction must be accounted once per arm, not on cell packets"
        )
    if construction_units_by_arm["simple_no_memory"] != 0:
        raise ReceiptValidationError("simple_no_memory construction must be zero")
    if construction_units_by_arm["actual_atc_bootstrap"] != 0:
        raise ReceiptValidationError("actual_atc_bootstrap construction must be zero")
    if (
        len(
            {
                packets[_cell_key("maintained_context_file", task_key)].context_items
                for task_key in TASK_IDS
            }
        )
        != 1
    ):
        raise ReceiptValidationError("maintained_context_file must be query-blind across tasks")
    for task_key in TASK_IDS:
        task_packets = [packets[_cell_key(arm_id, task_key)] for arm_id in ARM_IDS]
        if any(packet.task_key != task_key for packet in task_packets):
            raise ReceiptValidationError("packet task key does not match its cell")
        if len({packet.instruction for packet in task_packets}) != 1:
            raise ReceiptValidationError("task instruction is not identical across arms")
        if len({_canonical(packet.reader_limits.as_dict()) for packet in task_packets}) != 1:
            raise ReceiptValidationError("reader limits are not identical across arms")
    irrelevant_by_task = irrelevant_context_by_task or {}
    outcomes: list[dict[str, Any]] = []
    construction_totals: dict[str, Counter[str]] = {arm_id: Counter() for arm_id in ARM_IDS}
    for cell_number, (arm_id, task_key) in enumerate(
        (
            key
            for task_key in TASK_IDS
            for key in (_cell_key(arm_id, task_key) for arm_id in ARM_IDS)
        ),
        start=1,
    ):
        packet = packets[(arm_id, task_key)]
        result = results[(arm_id, task_key)]
        outcome = grade_reader_result(
            packet,
            result,
            GROUND_TRUTHS[task_key],
            stale_preference=stale_preference,
            irrelevant_context=irrelevant_by_task.get(task_key, ()),
        )
        outcome.update(
            {
                "cell_number": cell_number,
                "arm_id": arm_id,
                "reader_prompt_sha256": _sha256(packet.prompt),
            }
        )
        outcomes.append(outcome)
        construction_totals[arm_id]["construction_units"] += packet.construction_units
        construction_totals[arm_id]["access_units"] += packet.access_units
        construction_totals[arm_id]["retrieval_units"] += packet.retrieval_units
    for arm_id, units in construction_units_by_arm.items():
        construction_totals[arm_id]["construction_units"] = units
    successes = sum(bool(outcome["success"]) for outcome in outcomes)
    classifications = Counter(str(outcome["classification"]) for outcome in outcomes)
    limits = packets[(ARM_IDS[0], TASK_IDS[0])].reader_limits
    return {
        "schema": BRIDGE_SCHEMA,
        "pilot_kind": "six_cell_pipeline_sanity_only",
        "reader_limits": limits.as_dict(),
        "ground_truth_separate_from_packets": True,
        "model_execution_by_harness": False,
        "provider_execution_by_harness": False,
        "raw_six_cell_outcomes_only": True,
        "outcomes": outcomes,
        "denominators": {
            "cell_count": 6,
            "task_count": 2,
            "arm_count": 3,
            "success_count": successes,
        },
        "classification_counts": dict(sorted(classifications.items())),
        "construction_access_retrieval_accounting": {
            arm_id: dict(values) for arm_id, values in construction_totals.items()
        },
        "comparative_interpretation": {
            "superiority_claim_permitted": False,
            "noninferiority_claim_permitted": False,
            "percentage_point_claims_permitted": False,
            "safe_abstention_is_not_memory_comparison_evidence": True,
        },
    }


def write_report(report: Mapping[str, Any], output: Path) -> None:
    """Write a caller-supplied report without introducing timestamps or state."""

    output.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

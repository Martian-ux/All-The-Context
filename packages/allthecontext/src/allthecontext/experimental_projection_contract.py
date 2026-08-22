"""Isolated Wave 1 Packet C projection dependency/invalidation contracts.

The module reuses the semantic labels from the research-only M3 closure
(``InfluenceClass`` and ``MutationKind``) but does not instantiate its engine,
store canonical records, or add another replay/current-record authority.
Inputs and outputs are bounded opaque references and commitments only.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from .experimental_event_observation import AuthorizationApplicability
from .memory_lab_m3 import InfluenceClass, MutationKind

MAX_PROJECTION_REFERENCES = 256
MAX_DEPENDENCIES = 32
MAX_INVALIDATION_DECLARATIONS = 32
MAX_COMMITMENT_CHARS = 128


class ProjectionErrorCode(StrEnum):
    """Content-free projection contract failure vocabulary."""

    INVALID_FIELD = "invalid_field"
    DUPLICATE_REFERENCE = "duplicate_reference"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    CYCLIC_DEPENDENCY = "cyclic_dependency"
    INVALID_SCHEDULE = "invalid_schedule"
    INVALID_SEED = "invalid_seed"
    UNKNOWN_SEED = "unknown_seed"
    EMPTY_DEPENDENCIES = "empty_dependencies"
    MISSING_INVALIDATION = "missing_invalidation"
    EMPTY_INPUT = "empty_input"


class ProjectionContractViolation(ValueError):
    """A bounded error whose message never includes supplied identifiers."""

    def __init__(self, code: ProjectionErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _reference(value: object, *, maximum: int = MAX_COMMITMENT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    return value


def _references(
    values: Iterable[str], *, maximum: int = MAX_PROJECTION_REFERENCES
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    result = tuple(_reference(value) for value in values)
    if len(result) > maximum:
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    if len(result) != len(set(result)):
        raise ProjectionContractViolation(ProjectionErrorCode.DUPLICATE_REFERENCE)
    return result


class ProjectionKind(StrEnum):
    """Future derived surfaces covered by one dependency contract."""

    INDEX = "index"
    SUMMARY = "summary"
    CAPSULE = "capsule"
    CHECKPOINT = "checkpoint"
    RELATION = "relation"
    PROCEDURE = "procedure"
    USAGE_STATISTICS = "usage_statistics"


class InvalidationCause(StrEnum):
    """Mutations that can withdraw future influence from derived state."""

    CORRECTION = "correction"
    SUPERSESSION = "supersession"
    SOURCE_DRIFT = "source_drift"
    SCOPE_NARROWING = "scope_narrowing"
    PERMISSION_REVOCATION = "permission_revocation"
    RETENTION_EXPIRY = "retention_expiry"
    ORDINARY_DELETE = "ordinary_delete"
    DELETE = "ordinary_delete"
    DELETION = "ordinary_delete"
    TERMINAL_PURGE = "terminal_purge"
    DESTRUCTIVE_PURGE = "terminal_purge"
    PURGE = "terminal_purge"
    POLICY_GENERATION_CHANGE = "policy_generation_change"


class InvalidationAction(StrEnum):
    """What a projection must do after a declared cause."""

    WITHDRAW_AND_REBUILD = "withdraw_and_rebuild"
    WITHDRAW_ONLY = "withdraw_only"
    ERASE = "erase"


MANDATORY_INVALIDATION_CAUSES = frozenset(
    {
        InvalidationCause.CORRECTION,
        InvalidationCause.SUPERSESSION,
        InvalidationCause.SOURCE_DRIFT,
        InvalidationCause.SCOPE_NARROWING,
        InvalidationCause.PERMISSION_REVOCATION,
        InvalidationCause.RETENTION_EXPIRY,
        InvalidationCause.ORDINARY_DELETE,
        InvalidationCause.TERMINAL_PURGE,
        InvalidationCause.POLICY_GENERATION_CHANGE,
    }
)


class ProjectionSeedState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    DELETED = "deleted"
    PURGED = "purged"


@dataclass(frozen=True, slots=True)
class DependencyDeclaration:
    """One direct predecessor and its M3-compatible influence class."""

    predecessor_ref: str
    influence_class: InfluenceClass

    def __post_init__(self) -> None:
        _reference(self.predecessor_ref)
        if not isinstance(self.influence_class, InfluenceClass):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)


@dataclass(frozen=True, slots=True)
class InvalidationDeclaration:
    """One cause/action pair for a derived projection."""

    cause: InvalidationCause
    action: InvalidationAction = InvalidationAction.WITHDRAW_AND_REBUILD

    def __post_init__(self) -> None:
        if not isinstance(self.cause, InvalidationCause) or not isinstance(
            self.action, InvalidationAction
        ):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)


@dataclass(frozen=True, slots=True)
class ProjectionDeclaration:
    """Immutable recipe and invalidation inventory for one future surface."""

    projection_ref: str
    kind: ProjectionKind
    dependencies: tuple[DependencyDeclaration, ...] = ()
    invalidation_declarations: tuple[InvalidationDeclaration, ...] = ()

    def __post_init__(self) -> None:
        _reference(self.projection_ref)
        if not isinstance(self.kind, ProjectionKind):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if not isinstance(self.dependencies, tuple):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        dependencies = self.dependencies
        if any(not isinstance(item, DependencyDeclaration) for item in dependencies):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if not dependencies:
            raise ProjectionContractViolation(ProjectionErrorCode.EMPTY_DEPENDENCIES)
        if len(dependencies) > MAX_DEPENDENCIES:
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if len({item.predecessor_ref for item in dependencies}) != len(dependencies):
            raise ProjectionContractViolation(ProjectionErrorCode.DUPLICATE_REFERENCE)
        if any(item.predecessor_ref == self.projection_ref for item in dependencies):
            raise ProjectionContractViolation(ProjectionErrorCode.CYCLIC_DEPENDENCY)
        if not isinstance(self.invalidation_declarations, tuple):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        invalidations = self.invalidation_declarations
        if any(not isinstance(item, InvalidationDeclaration) for item in invalidations):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if len(invalidations) > MAX_INVALIDATION_DECLARATIONS:
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if len({item.cause for item in invalidations}) != len(invalidations):
            raise ProjectionContractViolation(ProjectionErrorCode.DUPLICATE_REFERENCE)
        if MANDATORY_INVALIDATION_CAUSES - {item.cause for item in invalidations}:
            raise ProjectionContractViolation(ProjectionErrorCode.MISSING_INVALIDATION)
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "invalidation_declarations", invalidations)

    @property
    def invalidation_causes(self) -> frozenset[InvalidationCause]:
        return frozenset(item.cause for item in self.invalidation_declarations)

    def invalidation_for(self, cause: InvalidationCause) -> InvalidationDeclaration | None:
        if not isinstance(cause, InvalidationCause):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        return next(
            (item for item in self.invalidation_declarations if item.cause is cause),
            None,
        )


@dataclass(frozen=True, slots=True)
class ProjectionPlan:
    """Validated dependency inventory with deterministic closure operations."""

    declarations: tuple[ProjectionDeclaration, ...]
    external_refs: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.declarations, tuple):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        declarations = self.declarations
        if not declarations:
            raise ProjectionContractViolation(ProjectionErrorCode.EMPTY_INPUT)
        if any(not isinstance(item, ProjectionDeclaration) for item in declarations):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        refs = tuple(item.projection_ref for item in declarations)
        if len(refs) != len(set(refs)):
            raise ProjectionContractViolation(ProjectionErrorCode.DUPLICATE_REFERENCE)
        if not isinstance(self.external_refs, frozenset):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        external_refs = frozenset(_references(self.external_refs))
        if set(refs) & external_refs:
            raise ProjectionContractViolation(ProjectionErrorCode.DUPLICATE_REFERENCE)
        known = set(refs) | external_refs
        for declaration in declarations:
            if any(item.predecessor_ref not in known for item in declaration.dependencies):
                raise ProjectionContractViolation(ProjectionErrorCode.UNKNOWN_DEPENDENCY)
        _topological_order(declarations, external_refs=external_refs)
        object.__setattr__(self, "declarations", declarations)
        object.__setattr__(self, "external_refs", external_refs)

    @property
    def by_ref(self) -> Mapping[str, ProjectionDeclaration]:
        return {item.projection_ref: item for item in self.declarations}

    def dependency_closure(
        self,
        changed_refs: Iterable[str],
        cause: InvalidationCause,
    ) -> tuple[str, ...]:
        """Return all declared descendants affected by one mutation cause."""

        if not isinstance(cause, InvalidationCause):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        changed = _references(changed_refs)
        reverse: dict[str, set[str]] = {}
        for declaration in self.declarations:
            for dependency in declaration.dependencies:
                reverse.setdefault(dependency.predecessor_ref, set()).add(
                    declaration.projection_ref
                )
        affected: set[str] = set()
        queue: deque[str] = deque(changed)
        by_ref = self.by_ref
        while queue:
            predecessor = queue.popleft()
            for successor in sorted(reverse.get(predecessor, ())):
                declaration = by_ref[successor]
                if cause not in declaration.invalidation_causes or successor in affected:
                    continue
                affected.add(successor)
                queue.append(successor)
        return tuple(sorted(affected))

    def m3_mutation_for(self, cause: InvalidationCause) -> MutationKind | None:
        """Map the six M3 mutations without redefining their semantics."""

        if not isinstance(cause, InvalidationCause):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        return _M3_CAUSE_MAP.get(cause)


@dataclass(frozen=True, slots=True)
class ProjectionSeed:
    """Immutable opaque input snapshot supplied by an external authority."""

    node_ref: str
    version: int
    semantic_commitment: str
    authorization: AuthorizationApplicability
    state: ProjectionSeedState = ProjectionSeedState.ACTIVE

    def __post_init__(self) -> None:
        _reference(self.node_ref)
        _reference(self.semantic_commitment)
        if not isinstance(self.authorization, AuthorizationApplicability):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_SEED)
        if type(self.version) is not int or self.version < 1:
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_SEED)
        if not isinstance(self.state, ProjectionSeedState):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_SEED)

    @property
    def eligible(self) -> bool:
        return self.state is ProjectionSeedState.ACTIVE


@dataclass(frozen=True, slots=True)
class ProjectionValue:
    """Content-free result of one deterministic disposable rebuild."""

    projection_ref: str
    kind: ProjectionKind
    input_refs: tuple[str, ...]
    source_versions: tuple[tuple[str, int], ...]
    authorization: AuthorizationApplicability
    policy_generation: int
    semantic_commitment: str

    def __post_init__(self) -> None:
        _reference(self.projection_ref)
        if not isinstance(self.kind, ProjectionKind):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if not isinstance(self.input_refs, tuple) or not isinstance(
            self.source_versions, tuple
        ):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        object.__setattr__(self, "input_refs", _references(self.input_refs))
        versions = self.source_versions
        if len(versions) > MAX_PROJECTION_REFERENCES:
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        normalized_versions: list[tuple[str, int]] = []
        for item in versions:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
            reference, version = item
            _reference(reference)
            if type(version) is not int or version < 1:
                raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
            normalized_versions.append((reference, version))
        if len(set(normalized_versions)) != len(normalized_versions):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        object.__setattr__(self, "source_versions", tuple(sorted(normalized_versions)))
        if not isinstance(self.authorization, AuthorizationApplicability):
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        if type(self.policy_generation) is not int or self.policy_generation < 1:
            raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
        _reference(self.semantic_commitment)


_M3_CAUSE_MAP: Mapping[InvalidationCause, MutationKind] = {
    InvalidationCause.CORRECTION: MutationKind.CORRECTION,
    InvalidationCause.SCOPE_NARROWING: MutationKind.SCOPE_NARROWING,
    InvalidationCause.PERMISSION_REVOCATION: MutationKind.PERMISSION_REVOCATION,
    InvalidationCause.ORDINARY_DELETE: MutationKind.ORDINARY_DELETE,
    InvalidationCause.TERMINAL_PURGE: MutationKind.TERMINAL_PURGE,
    InvalidationCause.POLICY_GENERATION_CHANGE: MutationKind.POLICY_GENERATION_CHANGE,
}


def _topological_order(
    declarations: Sequence[ProjectionDeclaration],
    *,
    external_refs: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    by_ref = {item.projection_ref: item for item in declarations}
    remaining = set(by_ref)
    resolved: set[str] = set()
    ordered: list[str] = []
    while remaining:
        ready = sorted(
                reference
                for reference in remaining
                if all(
                    dependency.predecessor_ref in resolved
                    or dependency.predecessor_ref in external_refs
                    for dependency in by_ref[reference].dependencies
                )
        )
        if not ready:
            raise ProjectionContractViolation(ProjectionErrorCode.CYCLIC_DEPENDENCY)
        for reference in ready:
            remaining.remove(reference)
            resolved.add(reference)
            ordered.append(reference)
    return tuple(ordered)


def _combine_authorization(
    values: Sequence[AuthorizationApplicability],
) -> AuthorizationApplicability:
    result = values[0]
    for value in values[1:]:
        result = result.narrowed_by(value)
    return result


def _commitment(
    declaration: ProjectionDeclaration,
    input_values: Sequence[tuple[str, str]],
    source_versions: Sequence[tuple[str, int]],
    policy_generation: int,
) -> str:
    fields = [
        "atc-packet-c-projection-v1",
        declaration.projection_ref,
        declaration.kind.value,
        str(policy_generation),
        *(f"{reference}={value}" for reference, value in sorted(input_values)),
        *(f"{reference}@{version}" for reference, version in sorted(source_versions)),
    ]
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()


def rebuild_projection(
    plan: ProjectionPlan,
    seeds: Sequence[ProjectionSeed],
    *,
    principal: str,
    policy_generation: int,
    required_scopes: Iterable[str] = (),
    schedule: Sequence[str] | None = None,
) -> tuple[ProjectionValue, ...]:
    """Rebuild all eligible projections from opaque seeds in fixed-point order.

    This is a disposable clean-build control, not a runtime projection engine.
    It intentionally reads no incremental artifact state and returns no content.
    """

    if not isinstance(plan, ProjectionPlan):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    if type(policy_generation) is not int or policy_generation < 1:
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    principal_ref = _reference(principal)
    required_scope_refs = _references(required_scopes, maximum=MAX_DEPENDENCIES)
    if isinstance(seeds, (str, bytes)) or not isinstance(seeds, Sequence):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_SEED)
    seed_values = tuple(seeds)
    if any(not isinstance(item, ProjectionSeed) for item in seed_values):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_SEED)
    seed_refs = tuple(item.node_ref for item in seed_values)
    if len(seed_refs) != len(set(seed_refs)):
        raise ProjectionContractViolation(ProjectionErrorCode.DUPLICATE_REFERENCE)
    if set(seed_refs) - plan.external_refs:
        raise ProjectionContractViolation(ProjectionErrorCode.UNKNOWN_SEED)
    seed_by_ref = {item.node_ref: item for item in seed_values}
    ordering = (
        _references(schedule)
        if schedule is not None
        else tuple(sorted(item.projection_ref for item in plan.declarations))
    )
    if set(ordering) != set(item.projection_ref for item in plan.declarations) or len(
        ordering
    ) != len(plan.declarations):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_SCHEDULE)
    by_ref = plan.by_ref
    output: dict[str, ProjectionValue] = {}
    unresolved = set(by_ref)
    while unresolved:
        progressed = False
        for projection_ref in ordering:
            if projection_ref not in unresolved:
                continue
            declaration = by_ref[projection_ref]
            if any(
                dependency.predecessor_ref in unresolved
                for dependency in declaration.dependencies
                if dependency.predecessor_ref in by_ref
            ):
                continue
            input_values: list[tuple[str, str]] = []
            input_versions: set[tuple[str, int]] = set()
            input_authorizations: list[AuthorizationApplicability] = []
            for dependency in declaration.dependencies:
                predecessor_ref = dependency.predecessor_ref
                seed = seed_by_ref.get(predecessor_ref)
                if seed is not None:
                    if not seed.eligible or not seed.authorization.applies_to(
                        principal_ref,
                        required_scopes=required_scope_refs,
                    ):
                        continue
                    input_values.append((predecessor_ref, seed.semantic_commitment))
                    input_versions.add((seed.node_ref, seed.version))
                    input_authorizations.append(seed.authorization)
                    continue
                predecessor = output.get(predecessor_ref)
                if predecessor is None:
                    continue
                input_values.append((predecessor_ref, predecessor.semantic_commitment))
                input_versions.update(predecessor.source_versions)
                input_authorizations.append(predecessor.authorization)
            if input_values:
                versions = tuple(sorted(input_versions))
                output[projection_ref] = ProjectionValue(
                    projection_ref=projection_ref,
                    kind=declaration.kind,
                    input_refs=tuple(sorted(reference for reference, _ in input_values)),
                    source_versions=versions,
                    authorization=_combine_authorization(input_authorizations),
                    policy_generation=policy_generation,
                    semantic_commitment=_commitment(
                        declaration,
                        input_values,
                        versions,
                        policy_generation,
                    ),
                )
            unresolved.remove(projection_ref)
            progressed = True
        if not progressed:
            raise ProjectionContractViolation(ProjectionErrorCode.CYCLIC_DEPENDENCY)
    return tuple(output[reference] for reference in sorted(output))


def dependency_closure(
    plan: ProjectionPlan,
    changed_refs: Iterable[str],
    cause: InvalidationCause,
) -> tuple[str, ...]:
    """Functional shorthand for the declared transitive invalidation closure."""

    if not isinstance(plan, ProjectionPlan):
        raise ProjectionContractViolation(ProjectionErrorCode.INVALID_FIELD)
    return plan.dependency_closure(changed_refs, cause)

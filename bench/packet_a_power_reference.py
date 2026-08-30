"""Deterministic Packet A power-method reference implementation.

The public functions implement one replicate, candidate power estimation, and
derived-N selection.  They contain no filesystem, product, provider, fixture,
manifest, or result side effects.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Literal

POWER_REFERENCE_VERSION = "packet-a-power-reference-v1"
SIMULATION_REPETITIONS = 100_000
BOOTSTRAP_REPLICATES = 10_000
PERMUTATION_REPLICATES = 10_000
ALPHA = 0.05
POWER_TARGET = 0.90
CAOS_TARGET_EFFECT = 0.10
UTILITY_TARGET_RELATIVE_EFFECT = 0.05
CAOS_NONINFERIORITY_MARGIN = -0.02
INFRASTRUCTURE_LOSS_ALLOWANCE = 0.15
BASE_CELL_COUNT = 96
TASK_FAMILY_COUNT = 6
REPOSITORY_COUNT = 4
STRATUM_COUNT = 4
MINIMUM_CANDIDATE_N = 384
MAXIMUM_CANDIDATE_N = 9_600

PAIR_STATUS = Literal["VALID", "LOST", "MISSING", "INVALID"]
VALID_STATUS: PAIR_STATUS = "VALID"
LOST_STATUS: PAIR_STATUS = "LOST"
MISSING_STATUS: PAIR_STATUS = "MISSING"
INVALID_STATUS: PAIR_STATUS = "INVALID"
ALL_PAIR_STATUSES: tuple[PAIR_STATUS, ...] = (
    VALID_STATUS,
    LOST_STATUS,
    MISSING_STATUS,
    INVALID_STATUS,
)

CAOS_JOINT_DISTRIBUTION: tuple[tuple[int, int, float], ...] = (
    (0, 0, 0.10),
    (0, 1, 0.15),
    (1, 0, 0.05),
    (1, 1, 0.70),
)
UTILITY_LEVELS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
UTILITY_JOINT_MATRIX: tuple[tuple[float, ...], ...] = (
    (0.008, 0.002, 0.0, 0.0, 0.0),
    (0.002, 0.018, 0.02, 0.0, 0.0),
    (0.0, 0.005, 0.04, 0.075, 0.0),
    (0.0, 0.0, 0.005, 0.1, 0.175),
    (0.0, 0.0, 0.0, 0.0, 0.55),
)
DRAW_KINDS: tuple[str, ...] = (
    "paired_outcome",
    "utility_pair",
    "infrastructure_loss",
    "permutation_sign",
    "bootstrap_resample",
)

_COUNTER_DOMAIN = b"ATC-PACKET-A-POWER"
_COUNTER_VERSION = 1
_FIELD_DELIMITER = b"\x1e"
_VALUE_DELIMITER = b"\x1f"


@dataclass(frozen=True)
class PairObservation:
    cell_index: int
    status: PAIR_STATUS
    control: float
    alternative: float


@dataclass(frozen=True)
class ContrastDecision:
    p_value: float
    lower_bound: float | None
    adjusted_p_value: float
    passed: bool


@dataclass(frozen=True)
class PrimaryContrastEvaluation:
    caos: ContrastDecision
    utility: ContrastDecision
    joint_holm_pass: bool


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_counter_int(value: object, name: str) -> int:
    _require(
        type(value) is int and 0 <= value <= 0xFFFFFFFFFFFFFFFF,
        f"{name} is invalid",
    )
    return value


def _require_draw_kind(draw_kind: object) -> str:
    _require(type(draw_kind) is str and draw_kind in DRAW_KINDS, "draw kind is invalid")
    return draw_kind


def _pack_field(name: str, type_tag: bytes, value: bytes) -> bytes:
    name_bytes = name.encode("ascii")
    return (
        _FIELD_DELIMITER
        + struct.pack(">H", len(name_bytes))
        + name_bytes
        + type_tag
        + _VALUE_DELIMITER
        + struct.pack(">I", len(value))
        + value
    )


def serialize_counter_tuple(
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
    resample_index: int,
    episode_index: int,
    draw_kind: str,
) -> bytes:
    """Serialize one counter using fixed ASCII names and big-endian lengths."""

    fields = (
        ("simulation_seed", simulation_seed),
        ("replicate_index", replicate_index),
        ("candidate_n", candidate_n),
        ("resample_index", resample_index),
        ("episode_index", episode_index),
    )
    draw_kind = _require_draw_kind(draw_kind)
    encoded = _COUNTER_DOMAIN + bytes([_COUNTER_VERSION])
    for name, value in fields:
        encoded += _pack_field(
            name,
            b"I",
            struct.pack(">Q", _require_counter_int(value, name)),
        )
    encoded += _pack_field("draw_kind", b"S", draw_kind.encode("ascii"))
    return encoded


def counter_digest(
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
    resample_index: int,
    episode_index: int,
    draw_kind: str,
) -> str:
    return hashlib.sha256(
        serialize_counter_tuple(
            simulation_seed,
            replicate_index,
            candidate_n,
            resample_index,
            episode_index,
            draw_kind,
        )
    ).hexdigest()


def counter_uniform(
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
    resample_index: int,
    episode_index: int,
    draw_kind: str,
) -> float:
    digest = bytes.fromhex(
        counter_digest(
            simulation_seed,
            replicate_index,
            candidate_n,
            resample_index,
            episode_index,
            draw_kind,
        )
    )
    first_53_bits = int.from_bytes(digest[:8], "big") >> 11
    return first_53_bits / 2**53


def candidate_n_grid() -> tuple[int, ...]:
    return tuple(range(MINIMUM_CANDIDATE_N, MAXIMUM_CANDIDATE_N + 1, BASE_CELL_COUNT))


def episode_cell_index(episode_index: int, candidate_n: int) -> int:
    _require_counter_int(episode_index, "episode index")
    _require(type(candidate_n) is int, "candidate N is invalid")
    _require(candidate_n in candidate_n_grid(), "candidate N is invalid")
    _require(episode_index < candidate_n, "episode index is outside candidate N")
    return episode_index % BASE_CELL_COUNT


def cell_coordinates(cell_index: int) -> tuple[int, int, int]:
    _require(type(cell_index) is int and 0 <= cell_index < BASE_CELL_COUNT, "cell index is invalid")
    return cell_index // 16, (cell_index % 16) // 4, cell_index % 4


def draw_caos_pair(
    simulation_seed: int, replicate_index: int, candidate_n: int, episode_index: int
) -> tuple[int, int]:
    """Draw from the row-major joint table using left-closed intervals [a, b)."""

    uniform = counter_uniform(
        simulation_seed, replicate_index, candidate_n, 0, episode_index, "paired_outcome"
    )
    cumulative = 0.0
    for control, alternative, probability in CAOS_JOINT_DISTRIBUTION:
        cumulative += probability
        if uniform < cumulative:
            return control, alternative
    return CAOS_JOINT_DISTRIBUTION[-1][:2]


def draw_utility_pair(
    simulation_seed: int, replicate_index: int, candidate_n: int, episode_index: int
) -> tuple[float, float]:
    """Draw from the row-major control-by-alternative table using [a, b)."""

    uniform = counter_uniform(
        simulation_seed, replicate_index, candidate_n, 0, episode_index, "utility_pair"
    )
    cumulative = 0.0
    for row_index, row in enumerate(UTILITY_JOINT_MATRIX):
        for column_index, probability in enumerate(row):
            cumulative += probability
            if uniform < cumulative:
                return UTILITY_LEVELS[row_index], UTILITY_LEVELS[column_index]
    return UTILITY_LEVELS[-1], UTILITY_LEVELS[-1]


def _validated_observations(
    observations: tuple[PairObservation, ...],
) -> tuple[PairObservation, ...]:
    _require(type(observations) is tuple, "observations are invalid")
    for observation in observations:
        _require(type(observation) is PairObservation, "observation is invalid")
        _require(
            type(observation.cell_index) is int and 0 <= observation.cell_index < BASE_CELL_COUNT,
            "observation cell is invalid",
        )
        _require(observation.status in ALL_PAIR_STATUSES, "observation status is invalid")
    return observations


def _usable_pairs(
    observations: tuple[PairObservation, ...],
) -> tuple[tuple[tuple[float, float, int], ...], bool]:
    excluded = False
    pairs: list[tuple[float, float, int]] = []
    for observation in _validated_observations(observations):
        if observation.status == LOST_STATUS:
            # Infrastructure loss is unavailable efficacy data: retain it in
            # the opportunity/loss ledger, but never manufacture a zero pair.
            continue
        if observation.status in (MISSING_STATUS, INVALID_STATUS):
            excluded = True
            continue
        pairs.append((observation.control, observation.alternative, observation.cell_index))
    return tuple(pairs), excluded


def effective_pair_count(observations: tuple[PairObservation, ...]) -> int:
    """Return the VALID-pair denominator after unavailable data is removed."""

    pairs, _ = _usable_pairs(observations)
    return len(pairs)


def _binary_pairs(
    observations: tuple[PairObservation, ...],
) -> tuple[tuple[tuple[int, int, int], ...], bool]:
    pairs, excluded = _usable_pairs(observations)
    binary: list[tuple[int, int, int]] = []
    for control, alternative, cell_index in pairs:
        _require(type(control) is int and control in (0, 1), "control binary value is invalid")
        _require(
            type(alternative) is int and alternative in (0, 1),
            "alternative binary value is invalid",
        )
        binary.append((control, alternative, cell_index))
    return tuple(binary), excluded


def _utility_pairs(
    observations: tuple[PairObservation, ...],
) -> tuple[tuple[tuple[float, float, int], ...], bool]:
    pairs, excluded = _usable_pairs(observations)
    for control, alternative, _ in pairs:
        _require(control in UTILITY_LEVELS, "control utility value is invalid")
        _require(alternative in UTILITY_LEVELS, "alternative utility value is invalid")
    return pairs, excluded


def paired_binary_difference(observations: tuple[PairObservation, ...]) -> float | None:
    pairs, _ = _binary_pairs(observations)
    if not pairs:
        return None
    return sum(alternative - control for control, alternative, _ in pairs) / len(pairs)


def exact_paired_binary_pvalue(observations: tuple[PairObservation, ...]) -> float:
    """Use the inclusive one-sided conditional sign tail over discordant pairs."""

    pairs, excluded = _binary_pairs(observations)
    if not pairs or excluded:
        return 1.0
    discordant_by_cell: dict[int, tuple[int, int]] = {}
    for control, alternative, cell_index in pairs:
        difference = alternative - control
        if difference:
            positive, total = discordant_by_cell.get(cell_index, (0, 0))
            discordant_by_cell[cell_index] = (positive + (difference > 0), total + 1)
    if not discordant_by_cell:
        return 1.0
    positive_observed = sum(positive for positive, _ in discordant_by_cell.values())
    distribution = [1]
    for _, (_, discordant_count) in sorted(discordant_by_cell.items()):
        next_distribution = [0] * (len(distribution) + discordant_count)
        for previous_positive, previous_count in enumerate(distribution):
            for positive_count in range(discordant_count + 1):
                next_distribution[previous_positive + positive_count] += previous_count * math.comb(
                    discordant_count, positive_count
                )
        distribution = next_distribution
    total = 2 ** sum(total for _, total in discordant_by_cell.values())
    tail = sum(distribution[positive_observed:])
    return tail / total


def _linear_percentile(values: tuple[float, ...], quantile: float) -> float:
    _require(values and 0.0 <= quantile <= 1.0, "percentile input is invalid")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    fraction = position - lower_index
    return ordered[lower_index] + fraction * (ordered[upper_index] - ordered[lower_index])


def _bootstrap_pairs(
    pairs: tuple[tuple[float, float, int], ...],
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
    resample_index: int,
) -> tuple[tuple[float, float, int], ...]:
    by_cell: dict[int, list[tuple[float, float, int]]] = {}
    for pair in pairs:
        by_cell.setdefault(pair[2], []).append(pair)
    resampled: list[tuple[float, float, int]] = []
    for cell_index in sorted(by_cell):
        bucket = by_cell[cell_index]
        for output_index in range(len(bucket)):
            uniform = counter_uniform(
                simulation_seed,
                replicate_index,
                candidate_n,
                resample_index,
                output_index,
                "bootstrap_resample",
            )
            resampled.append(bucket[min(int(uniform * len(bucket)), len(bucket) - 1)])
    return tuple(resampled)


def paired_binary_lower_bound(
    observations: tuple[PairObservation, ...],
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
) -> float | None:
    pairs, excluded = _binary_pairs(observations)
    if not pairs or excluded:
        return None
    values = tuple(
        sum(
            alternative - control
            for control, alternative, _ in _bootstrap_pairs(
                tuple((float(c), float(a), cell) for c, a, cell in pairs),
                simulation_seed,
                replicate_index,
                candidate_n,
                resample_index,
            )
        )
        / len(pairs)
        for resample_index in range(BOOTSTRAP_REPLICATES)
    )
    return _linear_percentile(values, 0.05)


def relative_utility_effect(observations: tuple[PairObservation, ...]) -> float | None:
    pairs, _ = _utility_pairs(observations)
    if not pairs:
        return None
    control_mean = sum(control for control, _, _ in pairs) / len(pairs)
    if control_mean == 0.0:
        return None
    return (
        sum(alternative - control for control, alternative, _ in pairs) / len(pairs) / control_mean
    )


def _studentized_difference(values: tuple[float, ...]) -> float:
    _require(values, "studentized statistic input is empty")
    mean = sum(values) / len(values)
    if len(values) == 1:
        standard_error = 0.0
    else:
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        standard_error = math.sqrt(variance / len(values))
    if standard_error == 0.0:
        return math.inf if mean > 0.0 else (-math.inf if mean < 0.0 else 0.0)
    return mean / standard_error


def studentized_utility_pvalue(
    observations: tuple[PairObservation, ...],
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
) -> float:
    pairs, excluded = _utility_pairs(observations)
    if not pairs or excluded:
        return 1.0
    differences = tuple(alternative - control for control, alternative, _ in pairs)
    observed = _studentized_difference(differences)
    exceedances = 0
    for resample_index in range(PERMUTATION_REPLICATES):
        signed = tuple(
            difference
            * (
                1.0
                if counter_uniform(
                    simulation_seed,
                    replicate_index,
                    candidate_n,
                    resample_index,
                    episode_index,
                    "permutation_sign",
                )
                >= 0.5
                else -1.0
            )
            # The counter episode index is the zero-based usable-pair ordinal;
            # missing/invalid observations were excluded before this method.
            for episode_index, difference in enumerate(differences)
        )
        if _studentized_difference(signed) >= observed:
            exceedances += 1
    return (1 + exceedances) / (PERMUTATION_REPLICATES + 1)


def paired_utility_lower_bound(
    observations: tuple[PairObservation, ...],
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
) -> float | None:
    pairs, excluded = _utility_pairs(observations)
    if not pairs or excluded or sum(control for control, _, _ in pairs) == 0.0:
        return None
    values: list[float] = []
    pair_count = len(pairs)
    for resample_index in range(BOOTSTRAP_REPLICATES):
        resampled = _bootstrap_pairs(
            pairs, simulation_seed, replicate_index, candidate_n, resample_index
        )
        control_mean = sum(control for control, _, _ in resampled) / pair_count
        if control_mean == 0.0:
            return None
        values.append(
            sum(alternative - control for control, alternative, _ in resampled)
            / pair_count
            / control_mean
        )
    return _linear_percentile(tuple(values), 0.05)


def holm_adjusted_p_values(p_values: tuple[float, float]) -> tuple[float, float]:
    _require(type(p_values) is tuple and len(p_values) == 2, "p-values are invalid")
    for p_value in p_values:
        _require(type(p_value) is float and 0.0 <= p_value <= 1.0, "p-value is invalid")
    ordered = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    adjusted = [0.0, 0.0]
    running_max = 0.0
    for rank, (index, p_value) in enumerate(ordered):
        running_max = max(running_max, min(1.0, (len(p_values) - rank) * p_value))
        adjusted[index] = running_max
    return adjusted[0], adjusted[1]


def evaluate_primary_contrasts(
    caos_observations: tuple[PairObservation, ...],
    utility_observations: tuple[PairObservation, ...],
    simulation_seed: int,
    replicate_index: int,
    candidate_n: int,
) -> PrimaryContrastEvaluation:
    caos_p = exact_paired_binary_pvalue(caos_observations)
    utility_p = studentized_utility_pvalue(
        utility_observations, simulation_seed, replicate_index, candidate_n
    )
    adjusted_caos, adjusted_utility = holm_adjusted_p_values((caos_p, utility_p))
    caos_lower = paired_binary_lower_bound(
        caos_observations, simulation_seed, replicate_index, candidate_n
    )
    utility_lower = paired_utility_lower_bound(
        utility_observations, simulation_seed, replicate_index, candidate_n
    )
    caos_excluded = any(
        observation.status in (MISSING_STATUS, INVALID_STATUS) for observation in caos_observations
    )
    utility_excluded = any(
        observation.status in (MISSING_STATUS, INVALID_STATUS)
        for observation in utility_observations
    )
    caos_pass = (
        not caos_excluded
        and adjusted_caos <= ALPHA
        and caos_lower is not None
        and caos_lower > CAOS_NONINFERIORITY_MARGIN
        and caos_lower >= CAOS_TARGET_EFFECT
    )
    utility_pass = (
        not utility_excluded
        and adjusted_utility <= ALPHA
        and utility_lower is not None
        and utility_lower >= UTILITY_TARGET_RELATIVE_EFFECT
    )
    return PrimaryContrastEvaluation(
        ContrastDecision(caos_p, caos_lower, adjusted_caos, caos_pass),
        ContrastDecision(utility_p, utility_lower, adjusted_utility, utility_pass),
        caos_pass and utility_pass,
    )


def simulate_replicate(
    simulation_seed: int, replicate_index: int, candidate_n: int
) -> PrimaryContrastEvaluation:
    _require(candidate_n in candidate_n_grid(), "candidate N is invalid")
    caos: list[PairObservation] = []
    utility: list[PairObservation] = []
    for episode_index in range(candidate_n):
        cell_index = episode_cell_index(episode_index, candidate_n)
        loss = (
            counter_uniform(
                simulation_seed,
                replicate_index,
                candidate_n,
                0,
                episode_index,
                "infrastructure_loss",
            )
            < INFRASTRUCTURE_LOSS_ALLOWANCE
        )
        caos_control, caos_alternative = draw_caos_pair(
            simulation_seed, replicate_index, candidate_n, episode_index
        )
        utility_control, utility_alternative = draw_utility_pair(
            simulation_seed, replicate_index, candidate_n, episode_index
        )
        status: PAIR_STATUS = LOST_STATUS if loss else VALID_STATUS
        caos.append(PairObservation(cell_index, status, caos_control, caos_alternative))
        utility.append(PairObservation(cell_index, status, utility_control, utility_alternative))
    return evaluate_primary_contrasts(
        tuple(caos), tuple(utility), simulation_seed, replicate_index, candidate_n
    )


def estimate_candidate_power(simulation_seed: int, candidate_n: int) -> tuple[float, float, float]:
    """Return CAOS, utility, and joint pass rates over all fixed replicates."""

    _require(candidate_n in candidate_n_grid(), "candidate N is invalid")
    caos_passes = 0
    utility_passes = 0
    joint_passes = 0
    for replicate_index in range(SIMULATION_REPETITIONS):
        evaluation = simulate_replicate(simulation_seed, replicate_index, candidate_n)
        caos_passes += evaluation.caos.passed
        utility_passes += evaluation.utility.passed
        joint_passes += evaluation.joint_holm_pass
    denominator = float(SIMULATION_REPETITIONS)
    return caos_passes / denominator, utility_passes / denominator, joint_passes / denominator


def power_gate_passes(caos_power: float, utility_power: float) -> bool:
    """Apply the declared per-contrast power gate monotonically."""

    _require(
        type(caos_power) is float
        and type(utility_power) is float
        and math.isfinite(caos_power)
        and math.isfinite(utility_power)
        and 0.0 <= caos_power <= 1.0
        and 0.0 <= utility_power <= 1.0,
        "power estimate is invalid",
    )
    return caos_power >= POWER_TARGET and utility_power >= POWER_TARGET


def select_derived_n(simulation_seed: int) -> int | None:
    """Select the smallest eligible grid value after evaluating the full grid."""

    selected: int | None = None
    for candidate_n in candidate_n_grid():
        caos_power, utility_power, _ = estimate_candidate_power(simulation_seed, candidate_n)
        if selected is None and power_gate_passes(caos_power, utility_power):
            selected = candidate_n
    return selected

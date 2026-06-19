"""Semantic types for the cold-joint RL env (tensorization is sub-project #2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hangarfit.models import Aircraft, GroundObject, Placement
from hangarfit.towplanner import Pose, SegmentKind

__all__ = [
    "Pose",
    "Primitive",
    "Park",
    "Action",
    "ParkedObject",
    "ActiveObject",
    "Observation",
    "DifficultyConfig",
    "RewardWeights",
    "StepInfo",
]


@dataclass(frozen=True, slots=True)
class Primitive:
    """One movement primitive applied to the active object.

    ``magnitude`` is continuous: metres for ``S``/``T`` and own-gear arcs; radians of
    pivot for a cart turn (``L``/``R`` at turn_radius 0). ``gear`` is +1 forward / -1
    reverse (ADR-0010). The policy's discrete magnitude bins + the ``Primitive``-decode
    live in ``ml.action_space`` (sub-project #3).
    """

    kind: SegmentKind
    magnitude: float
    gear: Literal[1, -1] = 1


@dataclass(frozen=True, slots=True)
class Park:
    """Commit the active object's pose and advance to the next object."""


Action = Primitive | Park


@dataclass(frozen=True, slots=True)
class ParkedObject:
    """An already-frozen object (immovable obstacle)."""

    object_id: str
    placement: Placement


@dataclass(frozen=True, slots=True)
class ActiveObject:
    """The object currently being driven in."""

    object_id: str
    body: Aircraft | GroundObject
    pose: Pose
    on_carts: bool


@dataclass(frozen=True, slots=True)
class Observation:
    """Semantic snapshot the agent sees each step (sub-project #2 tensorizes this)."""

    active: ActiveObject | None  # None only at a terminal state
    parked: tuple[ParkedObject, ...]
    unplaced_ids: tuple[str, ...]
    steps_this_object: int
    steps_total: int


@dataclass(frozen=True, slots=True)
class DifficultyConfig:
    """Curriculum knobs (spec §7). All optional; defaults = the full real task."""

    max_objects: int | None = None  # cap the requested set size (None = all)
    per_object_step_budget: int = 60  # primitives before an object is "unplaceable"
    total_step_budget: int = 600  # global per-episode primitive cap
    # #712 seed-anchor start-state graft: pre-park the first ``seed_anchor_k`` requested
    # objects at their committed-witness poses (a k-prefix of a valid witness layout is
    # provably valid), drive the remaining N-k in. (Step 1 wires a single k=1 rung; later
    # rungs can anneal k->0 across the curriculum.) 0 => no anchored objects => byte-identical
    # to the empty-start env. The env reads the witness poses from its ``anchor_placements``
    # (threaded by stage_builder from the rung's witness).
    seed_anchor_k: int = 0
    # #712 mixed-start rung: when set, each episode draws k = seed_anchor_k with this
    # probability else 0 (drawn from the curriculum's seeded stream), keeping empty-start
    # episodes in the training mix. None => fixed-k rung (use seed_anchor_k as-is) =>
    # byte-identical to the pre-change env. anchor_prob is P(k = seed_anchor_k) per episode.
    anchor_prob: float | None = None


@dataclass(frozen=True, slots=True)
class RewardWeights:
    """Reward weights (spec §5). Defaults are placeholders tuned in #4; the ORDERING
    invariant (any hard term dominates the soft sum) is enforced and tested here."""

    w_col: float = 100.0  # hard: collision overlap area
    w_oob: float = 100.0  # hard: out-of-bounds / notch / keep-out intrusion area
    w_egress: float = 100.0  # hard: Caddy egress violation
    w_move: float = 0.1  # movement: per-metre + per-cusp cost scale
    cusp_penalty: float = 10.0  # mirrors towplanner.CUSP_PENALTY (#480)
    w_gap: float = 1.0  # soft: inter-object min gap
    w_seq: float = 1.0  # soft: requested door-order deviation
    w_region: float = 1.0  # soft: region preference
    r_terminal: float = 50.0  # terminal: per fraction-placed
    gamma: float = 0.99  # shaping discount
    r_valid_park: float = 0.0  # bonus paid in Park ONLY when the layout is valid (basin escape)
    # When > 0, the r_valid_park bonus is GRADED by a near-miss "misfit" (overlap + out-of-bounds
    # intrusion) instead of paid all-or-nothing: r_valid_park * exp(-misfit / scale) on a Park
    # step (withheld entirely on an egress-blocked Park — egress is a binary hard failure). This
    # turns the flat valid-only plateau into an uphill gradient INTO the witness slot, so a Park
    # that lands CLOSE to valid earns partial credit and the rare near-miss pays a learnable
    # return. Default 0.0 -> the exact binary path -> byte-identical (#720 L5 economics lever).
    valid_park_grade_scale: float = 0.0
    # One-time bonus paid the FIRST time an episode reaches a valid placement (the env flips
    # RewardContext.first_valid_now on exactly that Park step). A discrete kick that makes the
    # breakthrough off the place-nothing pole pay a learnable return; paid once, not per Park.
    # Default 0.0 -> the term is x0 (the env still computes first_valid_now, but it contributes
    # nothing) -> byte-identical (#720 L5 economics lever).
    r_first_valid: float = 0.0
    dense_slot_potential: bool = False  # add an in-hangar nearest-free-pocket shaping term
    # terminal: penalty per UNPLACED fraction (1 - terminal_fraction). Default 0.0 -> byte-
    # identical. Non-zero charges abandonment so "drive to budget exhaustion" is no longer
    # free relative to committing a Park (the #710 Park/drive-out economics-rebalance lever).
    r_unplaced_penalty: float = 0.0
    # When True, the terminal credits the VALID placed fraction instead of the raw
    # fraction_placed: an invalid terminal layout scores effective-fraction 0 (so an
    # overlapping pile no longer books +r_terminal). Default False -> byte-identical. Fixes
    # the #714 commit-everything-invalidly attractor on multi-object rungs (the terminal was
    # validity-blind, invisible at N=1 where fraction is 0/1). Validity = the same whole-layout
    # product checker (collisions.check + Caddy egress) that drives the valid_placed gate.
    validity_conditional_terminal: bool = False


@dataclass(frozen=True, slots=True)
class StepInfo:
    """`info` dict payload: reward-term breakdown + live verdict (spec §9)."""

    terms: dict[str, float]
    valid: bool
    placed: int
    total: int
    reason: str = ""  # termination reason when done

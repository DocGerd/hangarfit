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
    seed_anchor: bool = False  # spawn near a known-valid anchor (curriculum, NOT BC)


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
    dense_slot_potential: bool = False  # add an in-hangar nearest-free-pocket shaping term


@dataclass(frozen=True, slots=True)
class StepInfo:
    """`info` dict payload: reward-term breakdown + live verdict (spec §9)."""

    terms: dict[str, float]
    valid: bool
    placed: int
    total: int
    reason: str = ""  # termination reason when done

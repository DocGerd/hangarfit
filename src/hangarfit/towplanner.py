"""Tow-path planner — empty-hangar fill (Phase 3a).

Answers *how* each plane reaches its target slot: a deterministic entry
order plus a closed-form Dubins arc per plane. See ADR-0007 and
docs/spikes/tow-path-planning.md. Wave 1 = the leaf primitives only.
"""

from __future__ import annotations

import math
import typing
from dataclasses import dataclass
from typing import Literal

from hangarfit.models import Placement

SegmentKind = Literal["L", "S", "R"]
_VALID_SEGMENT_KINDS = frozenset(typing.get_args(SegmentKind))


@dataclass(frozen=True, slots=True)
class Pose:
    """A planar pose. Deliberately omits ``plane_id``/``on_carts`` — path
    samples carry neither identity (the caller knows it) nor cart state
    (it does not change mid-arc). ``heading_deg`` follows the ADR-0002
    compass convention (from world +y, CW positive)."""

    x_m: float
    y_m: float
    heading_deg: float

    @classmethod
    def from_placement(cls, p: Placement) -> Pose:
        return cls(x_m=p.x_m, y_m=p.y_m, heading_deg=p.heading_deg)


@dataclass(frozen=True, slots=True)
class Segment:
    """One leg of a Dubins path. ``kind`` is ``L`` (left turn), ``S``
    (straight), or ``R`` (right turn). ``length_m`` is the arc length of
    the leg in metres (always >= 0)."""

    kind: SegmentKind
    length_m: float

    def __post_init__(self) -> None:
        if self.kind not in _VALID_SEGMENT_KINDS:
            raise ValueError(
                f"Segment.kind must be one of {sorted(_VALID_SEGMENT_KINDS)}, got {self.kind!r}"
            )
        if self.length_m < 0.0 or not math.isfinite(self.length_m):
            raise ValueError(f"Segment.length_m must be finite and >= 0, got {self.length_m}")


@dataclass(frozen=True, slots=True)
class DubinsArc:
    """Closed-form shortest path between two oriented poses under a minimum
    turn radius. ``turn_radius_m = 0`` denotes a cart-borne pivot-in-place
    (ADR-0007). ``segments`` is the ordered leg decomposition."""

    start: Pose
    end: Pose
    turn_radius_m: float
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        # 0.0 is the cart pivot-in-place sentinel (ADR-0007) and is valid.
        if self.turn_radius_m < 0.0 or not math.isfinite(self.turn_radius_m):
            raise ValueError(
                f"DubinsArc.turn_radius_m must be finite and >= 0, got {self.turn_radius_m}"
            )
        if not self.segments:
            raise ValueError("DubinsArc.segments must be non-empty")

    @property
    def length_m(self) -> float:
        return math.fsum(s.length_m for s in self.segments)


@dataclass(frozen=True, slots=True)
class Move:
    """One plane's entry: from the door-cone entry pose to its target slot."""

    plane_id: str
    target_slot: Pose
    path: DubinsArc

    def __post_init__(self) -> None:
        if not self.plane_id:
            raise ValueError("Move.plane_id must be non-empty")


@dataclass(frozen=True, slots=True)
class MovesPlan:
    """A full entry plan: the target layout plus the moves in execution order.

    Deliberately carries no sequence-level cart-usage tally (ADR-0007
    open question). The ``target_layout`` type is ``Layout`` at runtime;
    typed loosely here to keep Wave 1's leaf module import-light."""

    target_layout: object
    moves: tuple[Move, ...]

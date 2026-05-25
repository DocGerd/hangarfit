"""Tow-path planner — empty-hangar fill (Phase 3a).

Answers *how* each plane reaches its target slot: a deterministic entry
order plus a closed-form Dubins arc per plane. See ADR-0007 and
docs/spikes/tow-path-planning.md. Wave 1 = the leaf primitives only.
"""

from __future__ import annotations

import math
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from hangarfit.collisions import check as _check
from hangarfit.models import Aircraft, Conflict, Hangar, Layout, Placement

SegmentKind = Literal["L", "S", "R"]
_VALID_SEGMENT_KINDS = frozenset(typing.get_args(SegmentKind))

# A Dubins "word": the ordered kinds of its three legs (e.g. ("L", "S", "R")).
_DubinsWord = tuple[SegmentKind, SegmentKind, SegmentKind]


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

    def pose_at(self, s_m: float) -> Pose:
        """Pose at arc-length ``s_m`` from the start, walking the segments.

        Integrates in the standard math frame internally (CCW-positive,
        angle from ``+x``) and returns a compass-convention :class:`Pose`.
        For a turn segment, ``s_m`` consumed is metres of arc length when
        ``turn_radius_m > 0`` and **radians of pivot** when it is ``0``
        (the cart pivot-in-place encoding — see :func:`plan_dubins`).
        """
        x = self.start.x_m
        y = self.start.y_m
        theta = compass_to_math_rad(self.start.heading_deg)
        r = self.turn_radius_m
        remaining = s_m
        for seg in self.segments:
            step = min(seg.length_m, remaining)
            if seg.kind == "S":
                x += step * math.cos(theta)
                y += step * math.sin(theta)
            else:  # "L"/"R": arc of radius r; r == 0 => cart pivot in place
                sign = 1.0 if seg.kind == "L" else -1.0
                if r == 0.0:
                    # pivot: position fixed; `step` is radians of turn.
                    theta += sign * step
                else:
                    cx = x - sign * r * math.sin(theta)
                    cy = y + sign * r * math.cos(theta)
                    theta += sign * step / r
                    x = cx + sign * r * math.sin(theta)
                    y = cy - sign * r * math.cos(theta)
            remaining -= step
            if remaining <= 1e-12:
                break
        return Pose(x_m=x, y_m=y, heading_deg=math_rad_to_compass(theta))

    def sample(self, *, step_m: float = 0.05, step_deg: float = 1.0) -> Iterator[Pose]:
        """Yield integrated poses from start to end.

        Sample density is the FINER of ``step_m`` translation or ``step_deg``
        heading change along the arc (spike Q4), so zero-translation pivots
        still get angular resolution. The final pose is ``pose_at(length)`` —
        the INTEGRATED endpoint, NOT the stored ``self.end`` — so a wrong
        closed form surfaces as a mismatch instead of being masked.
        """
        total = self.length_m  # the "progress" parameter pose_at walks
        trans_len = 0.0  # true translation distance (excludes pivots)
        sweep_deg = 0.0  # total heading sweep
        for s in self.segments:
            if s.kind == "S":
                trans_len += s.length_m
            elif self.turn_radius_m > 0.0:  # real arc: length_m is arc length
                trans_len += s.length_m
                sweep_deg += math.degrees(s.length_m / self.turn_radius_m)
            else:  # r == 0 pivot: length_m is radians
                sweep_deg += math.degrees(s.length_m)
        n = max(
            1,
            math.ceil(trans_len / step_m) if trans_len > 0.0 else 0,
            math.ceil(sweep_deg / step_deg) if sweep_deg > 0.0 else 0,
        )
        yield self.start
        for i in range(1, n):
            yield self.pose_at(total * i / n)
        yield self.pose_at(total)


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


# ---------------------------------------------------------------------------
# Heading-convention adapter (ADR-0002)
#
# Dubins literature uses CCW-positive radians measured from world +x. The
# hangarfit world (x right, y deeper) is itself a standard right-handed
# plane, so POSITIONS pass through unchanged; only the heading convention
# differs. ``Placement.heading_deg`` is compass-style (from +y, CW positive),
# so the math angle of that same direction is theta = 90deg - heading. The
# determinant-(-1) reflection of the parts transform lives entirely inside
# ``geometry.aircraft_parts_world`` and never enters this module's math.
# ---------------------------------------------------------------------------


def compass_to_math_rad(heading_deg: float) -> float:
    """ADR-0002 compass heading (from +y, CW+) → standard math angle
    (from +x, CCW+) in radians. θ = 90° − heading."""
    return math.radians(90.0 - heading_deg)


def math_rad_to_compass(theta_rad: float) -> float:
    """Inverse of :func:`compass_to_math_rad`, normalised to [0, 360)."""
    return (90.0 - math.degrees(theta_rad)) % 360.0


def _mod2pi(theta: float) -> float:
    """Normalise an angle (radians) to ``[0, 2π)``."""
    two_pi = 2.0 * math.pi
    return theta - two_pi * math.floor(theta / two_pi)


# ---------------------------------------------------------------------------
# Closed-form Dubins set (Shkel & Lumelsky / Walker `dubins.c` formulation).
#
# Each solver takes the normalised configuration (alpha, beta, d) and returns
# the normalised leg parameters (t, p, q) for its word, or ``None`` when that
# word is infeasible. Turn legs (t, q — and p for CCC words) are angles in
# radians; the straight leg p of a CSC word is a length in units of the turn
# radius. Multiplying every parameter by the radius yields metres of arc
# length (turn) / straight length, which is what ``Segment.length_m`` stores.
# The forward integrator ``DubinsArc.pose_at`` walks this same formulation, so
# a correct (word, t, p, q) reproduces the goal pose — a property enforced by
# ``test_dubins_roundtrip_grid`` across all six words and three radii.
# ---------------------------------------------------------------------------


def _lsl(alpha: float, beta: float, d: float) -> tuple[float, float, float] | None:
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    p_sq = 2.0 + d * d - 2.0 * math.cos(alpha - beta) + 2.0 * d * (sa - sb)
    if p_sq < 0.0:
        return None
    tmp = math.atan2(cb - ca, d + sa - sb)
    return _mod2pi(tmp - alpha), math.sqrt(p_sq), _mod2pi(beta - tmp)


def _rsr(alpha: float, beta: float, d: float) -> tuple[float, float, float] | None:
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    p_sq = 2.0 + d * d - 2.0 * math.cos(alpha - beta) + 2.0 * d * (sb - sa)
    if p_sq < 0.0:
        return None
    tmp = math.atan2(ca - cb, d - sa + sb)
    return _mod2pi(alpha - tmp), math.sqrt(p_sq), _mod2pi(tmp - beta)


def _lsr(alpha: float, beta: float, d: float) -> tuple[float, float, float] | None:
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    p_sq = -2.0 + d * d + 2.0 * math.cos(alpha - beta) + 2.0 * d * (sa + sb)
    if p_sq < 0.0:
        return None
    p = math.sqrt(p_sq)
    tmp = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2.0, p)
    return _mod2pi(tmp - alpha), p, _mod2pi(tmp - _mod2pi(beta))


def _rsl(alpha: float, beta: float, d: float) -> tuple[float, float, float] | None:
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    p_sq = -2.0 + d * d + 2.0 * math.cos(alpha - beta) - 2.0 * d * (sa + sb)
    if p_sq < 0.0:
        return None
    p = math.sqrt(p_sq)
    tmp = math.atan2(ca + cb, d - sa - sb) - math.atan2(2.0, p)
    return _mod2pi(alpha - tmp), p, _mod2pi(beta - tmp)


def _rlr(alpha: float, beta: float, d: float) -> tuple[float, float, float] | None:
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    tmp = (6.0 - d * d + 2.0 * math.cos(alpha - beta) + 2.0 * d * (sa - sb)) / 8.0
    if abs(tmp) > 1.0:
        return None
    p = _mod2pi(2.0 * math.pi - math.acos(tmp))
    t = _mod2pi(alpha - math.atan2(ca - cb, d - sa + sb) + p / 2.0)
    q = _mod2pi(alpha - beta - t + p)
    return t, p, q


def _lrl(alpha: float, beta: float, d: float) -> tuple[float, float, float] | None:
    sa, sb, ca, cb = math.sin(alpha), math.sin(beta), math.cos(alpha), math.cos(beta)
    tmp = (6.0 - d * d + 2.0 * math.cos(alpha - beta) + 2.0 * d * (sb - sa)) / 8.0
    if abs(tmp) > 1.0:
        return None
    p = _mod2pi(2.0 * math.pi - math.acos(tmp))
    t = _mod2pi(-alpha - math.atan2(ca - cb, d + sa - sb) + p / 2.0)
    q = _mod2pi(_mod2pi(beta) - alpha - t + p)
    return t, p, q


# Fixed iteration order ⇒ deterministic tie-breaking (ADR-0003): a strict
# `<` comparison keeps the earliest-listed word on EXACT cost ties (e.g. the
# four-way collinear tie LSL/RSR/LSR/RSL). Geometrically-equal paths whose
# float costs differ by a ULP still resolve deterministically — just to
# whichever rounded smaller, not necessarily the earliest-listed.
_WordSolver = Callable[[float, float, float], "tuple[float, float, float] | None"]
_WORD_SOLVERS: tuple[tuple[_DubinsWord, _WordSolver], ...] = (
    (("L", "S", "L"), _lsl),
    (("R", "S", "R"), _rsr),
    (("L", "S", "R"), _lsr),
    (("R", "S", "L"), _rsl),
    (("R", "L", "R"), _rlr),
    (("L", "R", "L"), _lrl),
)


def _dubins_shortest(
    start: Pose, end: Pose, turn_radius_m: float
) -> tuple[_DubinsWord, tuple[float, float, float]]:
    """Shortest feasible Dubins word and its normalised (t, p, q) legs.

    Works in the standard math frame: positions pass through unchanged,
    headings convert via :func:`compass_to_math_rad`.
    """
    dx = end.x_m - start.x_m
    dy = end.y_m - start.y_m
    dist = math.hypot(dx, dy)
    d = dist / turn_radius_m
    theta = math.atan2(dy, dx) if dist > 0.0 else 0.0
    alpha = _mod2pi(compass_to_math_rad(start.heading_deg) - theta)
    beta = _mod2pi(compass_to_math_rad(end.heading_deg) - theta)

    best: tuple[float, _DubinsWord, tuple[float, float, float]] | None = None
    for word, solver in _WORD_SOLVERS:
        sol = solver(alpha, beta, d)
        if sol is None:
            continue
        cost = sol[0] + sol[1] + sol[2]
        if best is None or cost < best[0]:
            best = (cost, word, sol)
    if best is None:  # pragma: no cover - a Dubins path always exists
        raise ValueError(f"no feasible Dubins path between {start} and {end} (r={turn_radius_m})")
    return best[1], best[2]


def plan_dubins(start: Pose, end: Pose, *, turn_radius_m: float) -> DubinsArc:
    """Closed-form shortest arc-line-arc path from ``start`` to ``end``.

    ``turn_radius_m == 0`` is the cart case (ADR-0007: a cart is own-gear with
    a zero turn radius). When ``start`` and ``end`` positions coincide it is a
    pure pivot-in-place — a single turn segment whose ``length_m`` encodes the
    heading change in **radians**. When they differ it is the r->0 Dubins
    limit: pivot to the goal bearing, drive straight, pivot to the final
    heading (a ``(turn, S, turn)`` arc, zero-length pivots dropped). For
    ``turn_radius_m > 0`` the standard Dubins set is solved and the shortest
    feasible word returned; collinear same-heading inputs collapse to a
    single ``"S"`` segment.
    """
    if turn_radius_m < 0.0 or not math.isfinite(turn_radius_m):
        raise ValueError(f"turn_radius_m must be finite and >= 0, got {turn_radius_m}")

    if turn_radius_m == 0.0:
        dx = end.x_m - start.x_m
        dy = end.y_m - start.y_m
        dist = math.hypot(dx, dy)
        if dist <= 1e-9:
            # Pure pivot-in-place (positions coincide): a single turn segment
            # whose length_m encodes the short-arc heading change in radians.
            # Compass is CW-positive, so a positive delta is a right turn ("R")
            # in the math frame the integrator walks; the sign is pinned by
            # test_zero_radius_is_pivot_in_place.
            dtheta_deg = (end.heading_deg - start.heading_deg + 180.0) % 360.0 - 180.0
            pivot_kind: SegmentKind = "R" if dtheta_deg >= 0.0 else "L"
            return DubinsArc(start, end, 0.0, (Segment(pivot_kind, abs(math.radians(dtheta_deg))),))
        # Cart translation (ADR-0007 r->0 limit): a cart is own-gear with a zero
        # turn radius, so the shortest path is pivot to the goal bearing, drive
        # straight, then pivot to the final heading. All three legs live in one
        # turn_radius_m=0 DubinsArc; pose_at already integrates an r=0 turn as a
        # pivot-in-place (position held) and an "S" leg as translation. The goal
        # bearing as a compass heading: math angle atan2(dy, dx) -> compass.
        bearing_deg = math_rad_to_compass(math.atan2(dy, dx))
        cart_segs: list[Segment] = []
        seg1_deg = (bearing_deg - start.heading_deg + 180.0) % 360.0 - 180.0
        if abs(seg1_deg) > 1e-9:
            k1: SegmentKind = "R" if seg1_deg >= 0.0 else "L"
            cart_segs.append(Segment(k1, abs(math.radians(seg1_deg))))
        cart_segs.append(Segment("S", dist))
        seg3_deg = (end.heading_deg - bearing_deg + 180.0) % 360.0 - 180.0
        if abs(seg3_deg) > 1e-9:
            k3: SegmentKind = "R" if seg3_deg >= 0.0 else "L"
            cart_segs.append(Segment(k3, abs(math.radians(seg3_deg))))
        return DubinsArc(start, end, 0.0, tuple(cart_segs))

    word, (t, p, q) = _dubins_shortest(start, end, turn_radius_m)
    r = turn_radius_m
    raw = (
        Segment(word[0], t * r),
        Segment(word[1], p * r),
        Segment(word[2], q * r),
    )
    # Collapse zero-length legs: a collinear same-heading path comes back as
    # (turn 0, "S", turn 0); drop the zeros so it is ("S",) and the ["S"]
    # segment-kind assertions hold. Keep at least one segment.
    segs = tuple(s for s in raw if s.length_m > 1e-9) or (Segment("S", 0.0),)
    return DubinsArc(start, end, r, segs)


# ---------------------------------------------------------------------------
# Entry ordering (spike Q2)
# ---------------------------------------------------------------------------


def back_first_order(placements: tuple[Placement, ...]) -> tuple[Placement, ...]:
    """Deepest target slot first. Deterministic total order: ``y`` descending,
    then ``x`` ascending, then ``plane_id`` ascending (ADR-0003 determinism;
    spike Q2). Shallower slots become obstacles for deeper ones, so deeper
    planes enter first."""
    return tuple(sorted(placements, key=lambda p: (-p.y_m, p.x_m, p.plane_id)))


# ---------------------------------------------------------------------------
# Door-cone entry pose (spike Q6 / ADR-0007: the door is a motion gate)
# ---------------------------------------------------------------------------


def entry_pose(target: Placement, hangar: Hangar) -> Pose:
    """Door-cone entry pose for a plane heading to ``target`` (spike Q6).

    The plane enters at the front boundary (``y = 0``) pointing straight into
    the hangar (``heading_deg = 0`` ⇒ nose toward ``+y``). The entry ``x`` is
    the target slot's ``x`` clamped into the door interval
    ``[center − width/2, center + width/2]`` — a deterministic choice (ADR-0003)
    that keeps the approach as straight as the door allows. This promotes the
    door from a visual marker to a towplanner-level motion gate; ``collisions``
    semantics are untouched (ADR-0007).
    """
    door = hangar.door
    half = door.width_m / 2.0
    x = min(max(target.x_m, door.center_x_m - half), door.center_x_m + half)
    return Pose(x_m=x, y_m=0.0, heading_deg=0.0)


# ---------------------------------------------------------------------------
# Sampled collision-during-motion (spike Q4)
# ---------------------------------------------------------------------------


def path_first_conflict(
    arc: DubinsArc,
    mover: Aircraft,
    *,
    mover_on_carts: bool,
    placed: Layout,
    step_m: float = 0.05,
    step_deg: float = 1.0,
) -> Conflict | None:
    """First conflict naming ``mover`` while it tows along ``arc``, else ``None``.

    Samples the arc; at each pose the mover is placed at that pose and checked
    against the already-placed ``placed`` layout via :func:`collisions.check`.
    This reuses the static oracle wholesale, so parts-overlap, hangar-bounds,
    and bay-intrusion are all honoured *during motion* (spike Q4) — the path
    planner does not re-derive any geometry. ``mover_on_carts`` is constant
    along the arc (cart state does not change mid-tow), and conflicts that do
    not involve ``mover`` (e.g. a pre-existing clash among placed planes) are
    skipped so the mover is never blamed for them.

    Precondition: ``mover.id`` must exist in ``placed.fleet`` — each per-sample
    :class:`Layout` references it, so an unknown id raises ``ValueError`` from
    ``Layout`` construction rather than being silently skipped. The Wave 2
    caller (#196) builds ``placed`` from the full target fleet, which satisfies
    this.
    """
    for pose in arc.sample(step_m=step_m, step_deg=step_deg):
        moving = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=mover_on_carts)
        # Rebuilding the Layout per sample re-runs Layout.__post_init__ (cart
        # cap, cart↔mode consistency, unique ids). Because placed.placements ∪
        # {mover} is a subset of a valid target layout those invariants hold;
        # a future caller that violates them gets a real ValueError, not a
        # suppressed one.
        sample_layout = Layout(
            fleet=placed.fleet,
            hangar=placed.hangar,
            placements=(*placed.placements, moving),
            maintenance_plane=placed.maintenance_plane,
        )
        for conflict in _check(sample_layout).conflicts:
            if mover.id in conflict.planes:
                return conflict
    return None

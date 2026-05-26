"""Tow-path planner — empty-hangar fill (Phase 3a).

Answers *how* each plane reaches its target slot: a deterministic entry order
(:func:`back_first_order`) plus an in-bounds, obstacle-free tow path per plane
found by a deterministic Hybrid-A* search (:func:`plan_path`, #222) over Dubins
motion primitives — emitted as a single :class:`DubinsArc`. An unobstructed
plane still finishes in one closed-form Dubins shot (the search's analytic
expansion). See ADR-0002 (heading convention), ADR-0007 (cart = own-gear, r=0),
and docs/spikes/tow-path-planning.md.
"""

from __future__ import annotations

import heapq
import math
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from shapely.geometry import Polygon

# `_parts_conflict` is the exact oracle's per-pair predicate (collisions.py). The
# fast in-search checker `_motion_clear` reuses it verbatim — rather than
# re-deriving the polygon-clearance + z-gap rule — so the two can never diverge.
# Importing a sibling-module private is the same pattern as `check as _check`.
from hangarfit.collisions import _parts_conflict
from hangarfit.collisions import check as _check
from hangarfit.geometry import WorldPart, aircraft_parts_world
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
    open question)."""

    target_layout: Layout
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
# Door-cone entry poses (spike Q6 / ADR-0007 / #262: the door is a motion gate)
# ---------------------------------------------------------------------------

# The five headings of the forward-admissible entry cone: straight-in ±30°
# in 15° steps.  All five point generally inward (nose toward +y hemisphere).
# Rear-entry headings (near 180°) are out of scope here — see issue #261.
_CONE_HEADINGS: tuple[float, ...] = (330.0, 345.0, 0.0, 15.0, 30.0)


def entry_poses(target: Placement, hangar: Hangar) -> tuple[Pose, ...]:
    """All door-cone entry poses for a plane heading to ``target`` (#262).

    Returns a **fixed, deterministic grid** of up to 15 candidate start poses
    (3 x-samples × 5 headings, deduplicated):

    **Headings** — the 5-heading forward-admissible cone:
    ``{330°, 345°, 0°, 15°, 30°}`` (straight-in ±30° in 15° steps; all point
    generally inward).  Near-180° rear-entry headings are out of scope here (#261).

    **X-samples** — three values within the door interval
    ``[center − width/2, center + width/2]``:

    1. The door centre (``center_x_m``).
    2. The clamped target x — same as :func:`entry_pose`'s output (the v1 choice).
    3. The midpoint between those two.

    All three are clamped into the door interval; duplicates (when the target x
    equals the centre or its midpoint) are removed while preserving the
    deterministic order: x-outer loop, heading-inner loop, emit in
    ``(x_sample_idx_asc, heading_idx_asc)`` order.

    **Emit order** (fixed for ADR-0003 determinism):

    - Outer loop: x-sample index 0 (door centre), 1 (clamped target), 2 (midpoint).
    - Inner loop: headings in ``_CONE_HEADINGS`` order (330°, 345°, 0°, 15°, 30°).
    - Duplicate ``(x, heading)`` pairs (exact float equality) are skipped on the
      second occurrence.

    The caller (:func:`plan_path` via ``entries=`` and :func:`plan_fill`) filters
    candidates that clip the side/back walls at the entry pose before seeding the
    search; a straight-in centre pose is always the final fallback so the search
    has at least one start.

    Returns a non-empty :class:`tuple` of :class:`Pose` objects, each with
    ``y_m = 0.0`` and ``heading_deg`` from ``_CONE_HEADINGS``.
    """
    door = hangar.door
    half = door.width_m / 2.0
    lo = door.center_x_m - half
    hi = door.center_x_m + half

    def _clamp(x: float) -> float:
        return min(max(x, lo), hi)

    # The three candidate x values (before dedup).
    x_centre = door.center_x_m
    x_target = _clamp(target.x_m)
    x_mid = _clamp((x_centre + x_target) / 2.0)

    x_samples = (x_centre, x_target, x_mid)

    seen: set[tuple[float, float]] = set()
    poses: list[Pose] = []
    for x in x_samples:
        for h in _CONE_HEADINGS:
            key = (x, h)
            if key in seen:
                continue
            seen.add(key)
            poses.append(Pose(x_m=x, y_m=0.0, heading_deg=h))

    return tuple(poses)


def entry_pose(target: Placement, hangar: Hangar) -> Pose:
    """Single-pose door-cone entry (v1 baseline; spike Q6 / ADR-0007).

    Returns **one** pose: front boundary (``y = 0``), heading straight into
    the hangar (``0°`` ⇒ nose toward ``+y``), x clamped to the door interval.
    This is the straight-in pose that :func:`entry_poses` always includes as one
    of its x-sample candidates.

    Kept for backward compatibility and tests.  :func:`plan_fill` now uses
    :func:`entry_poses` (the full cone) instead.

    See :func:`entry_poses` for the multi-pose searched cone (#262).
    """
    door = hangar.door
    half = door.width_m / 2.0
    x = min(max(target.x_m, door.center_x_m - half), door.center_x_m + half)
    return Pose(x_m=x, y_m=0.0, heading_deg=0.0)


# ---------------------------------------------------------------------------
# Sampled collision-during-motion (spike Q4)
# ---------------------------------------------------------------------------


def _mover_motion_bounds_conflict(
    mover: Aircraft, placement: Placement, hangar: Hangar
) -> Conflict | None:
    """First side/back-wall bounds violation for a plane *in transit*, else ``None``.

    **Front-gap exemption (#222):** a plane being towed through the door
    legitimately protrudes in front of it (``y < 0`` — the conceptual apron,
    spike Q6). So — unlike the static :func:`hangarfit.collisions.check` oracle,
    which forbids ``y < 0`` — the front wall is NOT enforced on the mover
    mid-motion. The side walls (``0 ≤ x ≤ width``) and the back wall
    (``y ≤ length``) still are; the mover's final slot is itself a valid static
    placement, so full bounds hold at rest. Reuses the canonical
    :func:`~hangarfit.geometry.aircraft_parts_world` transform rather than
    re-deriving geometry — the determinant-(-1) trap lives there (ADR-0002).
    """
    for world_part in aircraft_parts_world(mover, placement):
        for x, y in list(world_part.polygon.exterior.coords)[:-1]:
            # The static rule is `0 <= x <= width and 0 <= y <= length`; the
            # only relaxation is dropping the `0 <= y` front-wall lower bound.
            if x < 0.0 or x > hangar.width_m or y > hangar.length_m:
                return Conflict.single(
                    kind="hangar_bounds",
                    plane=mover.id,
                    detail=(
                        f"part {world_part.kind!r} vertex ({x:.3f}, {y:.3f}) "
                        f"outside hangar side/back walls during tow "
                        f"(0..{hangar.width_m:g} x ..{hangar.length_m:g})"
                    ),
                )
    return None


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
    Parts-overlap and bay-intrusion are taken straight from that static oracle
    (spike Q4) — the path planner does not re-derive that geometry. **Hangar
    bounds for the mover are the one exception:** they go through
    :func:`_mover_motion_bounds_conflict` instead, which applies the front-gap
    exemption (#222) so a plane straddling the door at ``y < 0`` during entry is
    not falsely blamed, while the side and back walls stay enforced. The
    oracle's own hangar-bounds verdict on the mover is therefore skipped.
    ``mover_on_carts`` is constant along the arc (cart state does not change
    mid-tow), and conflicts that do not involve ``mover`` (e.g. a pre-existing
    clash among placed planes) are skipped so the mover is never blamed for them.

    Precondition: ``mover.id`` must exist in ``placed.fleet`` — each per-sample
    :class:`Layout` references it, so an unknown id raises ``ValueError`` from
    ``Layout`` construction rather than being silently skipped. The callers
    (``plan_fill`` #196 and ``plan_path`` #222, which re-validates its final
    path here) build ``placed`` from the full target fleet, satisfying this.
    """
    for pose in arc.sample(step_m=step_m, step_deg=step_deg):
        moving = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=mover_on_carts)
        # Mover hangar bounds: front-gap-exempt (a plane being towed in
        # straddles the door at y < 0). Side/back walls still bite.
        bounds_conflict = _mover_motion_bounds_conflict(mover, moving, placed.hangar)
        if bounds_conflict is not None:
            return bounds_conflict
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
            if mover.id not in conflict.planes:
                continue
            # The mover's hangar bounds are governed by the front-gap-exempt
            # rule above, not the static front-wall rule — skip the oracle's
            # mover hangar_bounds so a legitimate door protrusion is not blamed.
            if conflict.kind == "hangar_bounds":
                continue
            return conflict
    return None


# ---------------------------------------------------------------------------
# Empty-hangar fill planner + bounded order-retry (spike Q2 / ADR-0007)
# ---------------------------------------------------------------------------


class NoFeasiblePlanError(Exception):
    """No collision-free entry order exists: the greedy back-first scan found
    no feasible plane for some slot (spike Q2 / Risk #1).

    Carries the plane that could not be placed and the conflict that blocked
    it, so the caller (and the Wave 3 CLI) can name the offender.
    """

    def __init__(self, plane_id: str, conflict: Conflict) -> None:
        super().__init__(
            f"no feasible tow order: plane {plane_id!r} could not be placed "
            f"without collision ({conflict.kind})"
        )
        self.plane_id = plane_id
        self.conflict = conflict


def plan_fill(target: Layout) -> MovesPlan:
    """Plan a collision-free entry order + per-plane path for an empty fill.

    Walks :func:`back_first_order` (deepest slot first); for each plane searches
    for an in-bounds path from the door-cone entry poses (:func:`entry_poses`) to
    its target slot via :func:`plan_path` (a deterministic Hybrid-A* search with
    multi-start door-cone seeding, #262).  All surviving cone candidates are
    seeded at ``g = 0``; the search naturally picks the shortest across the whole
    cone.  Cart-borne planes have ``effective_turn_radius_m() == 0`` and are
    handled by the same search with zero-radius pivot primitives. Each iteration
    scans the not-yet-placed planes in order and commits the first for which
    :func:`plan_path` succeeds (i.e. finds an in-bounds, collision-free arc)
    against the already-placed subset — the spike's "swap with the next-feasible
    plane" as a deterministic scan. If a whole scan finds none feasible the greedy
    is stuck (spike Risk #1) and the plan bails with :class:`NoFeasiblePlanError`
    naming the deepest unplaceable plane.

    No retry *budget* is needed: an already-placed plane is only ever an added
    obstacle, so a plane infeasible against the current obstacles can never
    become feasible later — the scan therefore makes monotonic progress (one
    plane committed per iteration) and cannot spin. Deterministic (ADR-0003):
    the order, the Hybrid-A* primitive fan, the cone grid, and the next-feasible
    scan are all RNG-free, so a given ``target`` always yields the same
    :class:`MovesPlan`.
    """
    ordered = list(back_first_order(target.placements))
    fleet = target.fleet
    hangar = target.hangar

    placed: list[Placement] = []
    moves: list[Move] = []

    while ordered:
        chosen: int | None = None
        chosen_arc: DubinsArc | None = None
        # The deepest unplaced plane is scanned first, so its conflict (if it
        # is rejected) is the one reported when the whole scan finds nothing.
        deepest_conflict: Conflict | None = None
        # `placed` does not change within a scan pass, so the obstacle layout is
        # constant across candidates. Only the already-committed planes are
        # obstacles; plan_path adds the candidate mover per sample via its
        # internal path_first_conflict check.
        placed_layout = Layout(
            fleet=fleet,
            hangar=hangar,
            placements=tuple(placed),
            maintenance_plane=target.maintenance_plane,
        )
        for idx, slot in enumerate(ordered):
            plane = fleet[slot.plane_id]
            try:
                # The full door cone drives the multi-start search (#262). Compute
                # it once and pass it as `entries=`; the positional `entry` arg is
                # ignored by plan_path whenever `entries` is set, so reuse cone[0]
                # for it rather than recomputing entry_pose() (which plan_path would
                # never read here).
                cone = entry_poses(slot, hangar)
                arc = plan_path(
                    plane,
                    cone[0],
                    Pose.from_placement(slot),
                    hangar=hangar,
                    placed=placed_layout,
                    mover_on_carts=slot.on_carts,
                    entries=cone,
                )
            except NoFeasiblePlanError as exc:
                # This plane cannot be routed against the current obstacles; try
                # the next candidate. Remember its conflict for the bail message.
                if deepest_conflict is None:
                    deepest_conflict = exc.conflict
                continue
            chosen, chosen_arc = idx, arc
            break
        if chosen is None:
            # Every remaining plane conflicts: greedy back-first is stuck. The
            # deepest plane (ordered[0], scanned first) is the one we most
            # wanted to place, and deepest_conflict is its own conflict.
            assert deepest_conflict is not None  # ordered non-empty => scan ran
            raise NoFeasiblePlanError(ordered[0].plane_id, deepest_conflict)
        slot = ordered.pop(chosen)
        assert chosen_arc is not None
        moves.append(Move(slot.plane_id, Pose.from_placement(slot), chosen_arc))
        placed.append(slot)

    return MovesPlan(target_layout=target, moves=tuple(moves))


# ── Hybrid-A* tow-path search (spike Q3 v2, #222) ───────────────────────────
# Deterministic search over Dubins motion primitives. Tuning constants; see the
# design spec. All RNG-free (ADR-0003).
_GRID_XY_M = 0.5  # (x, y) cell size for state binning
_GRID_DEG = 15.0  # heading cell size; 360 / 15 = 24 heading bins
_HEADING_BINS = round(360.0 / _GRID_DEG)
_TURN_PENALTY = 0.1  # per-radian g-cost penalty to prefer straighter paths
_MAX_EXPANSIONS = 700  # node-expansion budget per plane before bailing.
# Deterministic (machine-independent) bound on worst-case search cost. The flip
# side: budget exhaustion can also report a genuinely-feasible-but-hard layout
# as NoFeasiblePlanError (a false negative). Accepted v1 tradeoff — see the
# design spec's failure semantics; retune once #197 exercises the planner
# through solve() on real (measured) hangar geometry.
# Sampling resolution for the FAST in-search `_motion_clear` validity checks
# (edges + the analytic-shot screen). Coarser than the exact oracle's default
# (0.05 m / 1°) to keep the search tractable: the worst case is a plane that can
# maneuver freely but cannot reach the goal, which explores the whole budget,
# and the analytic shot is screened at every node. Coarse sampling can only make
# the screen MORE lenient (accept a marginally-clipping pose), never reject a
# valid one — and the full returned path is re-validated by the exact oracle at
# fine resolution before it is returned (the safety net), so coarsening trades a
# little extra search, never correctness.
_SEARCH_STEP_M = 0.25
_SEARCH_STEP_DEG = 5.0


def _primitives(turn_radius_m: float) -> tuple[Segment, ...]:
    """The fixed motion-primitive fan, in deterministic order (L, S, R).

    Own-gear (``r > 0``): a left arc, a straight, and a right arc, each of
    length ``step`` (metres) chosen so a turn changes heading by ~one heading
    cell. Cart (``r == 0``): a left pivot, a straight of ``_GRID_XY_M`` metres,
    and a right pivot — the same (L, S, R) order; each pivot is
    ``math.radians(_GRID_DEG)`` radians (one heading cell; ``length_m`` encodes
    radians for the pivot segments, ADR-0007).
    """
    if turn_radius_m == 0.0:
        dtheta = math.radians(_GRID_DEG)
        return (Segment("L", dtheta), Segment("S", _GRID_XY_M), Segment("R", dtheta))
    step = max(_GRID_XY_M, turn_radius_m * math.radians(_GRID_DEG))
    return (Segment("L", step), Segment("S", step), Segment("R", step))


def _step_pose(pose: Pose, seg: Segment, turn_radius_m: float) -> Pose:
    """Integrate one primitive segment from ``pose`` (reuses ``DubinsArc.pose_at``).

    The temporary arc's ``end`` is a placeholder — ``pose_at`` integrates from
    ``start`` and never reads ``end``.
    """
    return DubinsArc(pose, pose, turn_radius_m, (seg,)).pose_at(seg.length_m)


def _seg_cost(seg: Segment, turn_radius_m: float) -> float:
    """g-cost of one segment: translation metres + a small per-radian turn penalty.

    Straight: ``length_m`` metres, no turn. Turn ``r > 0``: arc length
    ``length_m`` metres plus penalty over ``length_m / r`` radians. Pivot
    ``r == 0``: no translation, penalty over ``length_m`` radians.
    """
    if seg.kind == "S":
        return seg.length_m
    if turn_radius_m > 0.0:
        return seg.length_m + _TURN_PENALTY * (seg.length_m / turn_radius_m)
    return _TURN_PENALTY * seg.length_m  # cart pivot: length_m is radians


def _cell(pose: Pose) -> tuple[int, int, int]:
    """Bin a pose into the search grid: ``(x, y)`` rounded to ``_GRID_XY_M`` and
    heading rounded to ``_GRID_DEG`` (wrapped into ``_HEADING_BINS``)."""
    return (
        round(pose.x_m / _GRID_XY_M),
        round(pose.y_m / _GRID_XY_M),
        round(pose.heading_deg / _GRID_DEG) % _HEADING_BINS,
    )


# ── Hybrid-A* search core ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _SearchNode:
    """A Hybrid-A* node: a pose, its g-cost from start, and the parent link +
    the primitive segment that produced it (for path reconstruction).

    Frozen: a node is built once and only read thereafter (heap ordering is the
    ``(f, counter)`` tuple, never the node). Freezing forecloses the classic A*
    bug of mutating ``g`` in place after a node is on the heap, which would
    silently corrupt the ``best_g`` stale-entry check. **Invariant:** ``seg`` is
    ``None`` iff ``parent`` is ``None`` (the start node); every child carries
    both — :func:`_reconstruct_segments` relies on this to stop at the root."""

    pose: Pose
    g: float
    seg: Segment | None
    parent: _SearchNode | None


def _reconstruct_segments(node: _SearchNode) -> list[Segment]:
    """Segments from the start pose to ``node``, in travel order."""
    out: list[Segment] = []
    cur: _SearchNode | None = node
    while cur is not None and cur.seg is not None:
        out.append(cur.seg)
        cur = cur.parent
    out.reverse()
    return out


def _root_pose(node: _SearchNode) -> Pose:
    """The start pose of the search branch that produced ``node``.

    Walks the parent chain to the root (the node where ``seg is None``).  In a
    single-start search the root is always the ``entry`` pose; in a multi-start
    search (#262) different nodes may have different roots, and the returned arc's
    ``start`` must reflect the actual root, not the arbitrarily-chosen positional
    ``entry`` argument.
    """
    cur = node
    while cur.parent is not None:
        cur = cur.parent
    return cur.pose


# ── Precomputed obstacle set + fast per-pose checker (#222, Task 5) ──────────
#
# The exact oracle `path_first_conflict` rebuilds a Layout and runs the full
# Shapely `collisions.check` for EVERY sampled pose — correct but slow for the
# inner search loop. `_motion_clear` is a fast equivalent: it precomputes the
# static obstacle geometry once per plane-search (`_build_obstacles`) and checks
# a single mover pose against it, mirroring the oracle's three relevant rules
# (mover hangar bounds, bay intrusion, pairwise parts overlap). It MUST yield the
# same clear/conflict verdict as the oracle for the mover at a single pose. The
# exact oracle remains the AUTHORITY on the final returned path (`plan_path`
# re-validates the chosen arc with `path_first_conflict` before returning), so a
# fast-vs-exact divergence can never SHIP a bad path.


@dataclass(frozen=True, slots=True)
class _Obstacles:
    """Precomputed static geometry for one plane-search: placed planes' world
    parts and the (optional) maintenance-bay keep-out rectangle.

    Placed planes do not move while a single plane is routed, so this is built
    once per :func:`plan_path` invocation and reused for every sampled pose.
    """

    world_parts: tuple[WorldPart, ...]
    # AABBs parallel to ``world_parts`` (same index), precomputed because the
    # obstacle polygons are static across every sampled pose of a search — only
    # the mover's AABB changes per pose. Avoids recomputing ``poly.bounds`` for
    # each obstacle on every :func:`_motion_clear` call.
    world_part_aabbs: tuple[tuple[float, float, float, float], ...]
    bay_xmin: float
    bay_xmax: float
    bay_ymin: float
    bay_active: bool

    def __post_init__(self) -> None:
        # The parallel-array pairing is load-bearing: _motion_clear zips
        # world_parts with world_part_aabbs by index. Assert parity at build
        # time so a divergence fails loudly here rather than deep in the search.
        if len(self.world_parts) != len(self.world_part_aabbs):
            raise ValueError(
                f"_Obstacles: world_parts ({len(self.world_parts)}) and "
                f"world_part_aabbs ({len(self.world_part_aabbs)}) must be equal-length"
            )


def _build_obstacles(placed: Layout, *, mover_id: str) -> _Obstacles:
    """Compute the static obstacle set once (placed planes don't move while one
    plane is routed). The bay is a keep-out only when a maintenance plane is set
    (mirrors :func:`hangarfit.collisions._bay_intrusion_conflicts`).

    The mover itself (``mover_id``) is excluded from the obstacle parts — it is
    the thing being moved, not an obstacle. The maintenance occupant is absent
    from ``placed.placements`` by Layout invariant, so the mover is correctly
    subject to the bay keep-out (it is never the occupant).
    """
    parts: list[WorldPart] = []
    for placement in placed.placements:
        if placement.plane_id == mover_id:
            continue
        parts.extend(aircraft_parts_world(placed.fleet[placement.plane_id], placement))
    bay = placed.hangar.maintenance_bay
    return _Obstacles(
        world_parts=tuple(parts),
        world_part_aabbs=tuple(_aabb(p.polygon) for p in parts),
        bay_xmin=bay.center_x_m - bay.width_m / 2,
        bay_xmax=bay.center_x_m + bay.width_m / 2,
        bay_ymin=placed.hangar.length_m - bay.depth_m,
        bay_active=placed.maintenance_plane is not None,
    )


def _aabb(poly: Polygon) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a polygon as ``(xmin, ymin, xmax, ymax)``.

    A cheap plan-view pre-filter: two parts whose AABBs are separated by more
    than the clearance cannot conflict, so the (relatively costly) exact
    polygon predicate is skipped for them. Uses Shapely's ``bounds``.
    """
    xmin, ymin, xmax, ymax = poly.bounds
    return xmin, ymin, xmax, ymax


def _motion_clear(mover: Aircraft, pose: Pose, obstacles: _Obstacles, hangar: Hangar) -> bool:
    """Fast per-pose verdict: ``True`` iff the mover at ``pose`` is collision-free.

    Mirrors the EXACT oracle (:func:`path_first_conflict` → ``collisions.check``)
    for the mover at one pose, across the three rules that can name the mover:

    (A) **Hangar bounds** — via :func:`_mover_motion_bounds_conflict` (side/back
        walls enforced; front ``y < 0`` exempt during tow, #222). Same predicate
        the oracle uses for the mover.
    (B) **Pairwise parts overlap** — reuses :func:`collisions._parts_conflict`
        verbatim (the oracle's own per-pair predicate) so the polygon-clearance
        and z-gap-vs-``wing_layer_clearance_m`` rules can never diverge. A z and
        an AABB pre-filter cut the candidate set first; both are conservative
        (they only skip pairs ``_parts_conflict`` would also reject), so the
        final verdict is identical to checking every pair.
    (C) **Bay intrusion** — mirrors :func:`collisions._first_vertex_in_bay`
        exactly (strict ``<`` on left/right/front edges; no upper-``y`` test —
        the back edge is the hangar wall) when the bay is closed.

    ``on_carts`` is fixed to ``False`` here: :func:`~hangarfit.geometry.aircraft_parts_world`
    derives part geometry from pose only (``x``/``y``/``heading``) and the parts,
    NOT from ``on_carts``, so the part polygons are identical to the oracle's
    regardless of cart state. (Verified by reading ``aircraft_parts_world``.)

    The exact oracle remains the authority on the final returned path — see the
    module note above and the safety-net re-check in :func:`plan_path`.
    """
    placement = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=False)
    mover_parts = aircraft_parts_world(mover, placement)

    # (A) Hangar bounds (front-gap-exempt for the mover).
    if _mover_motion_bounds_conflict(mover, placement, hangar) is not None:
        return False

    clearance = hangar.clearance_m
    wlc = hangar.wing_layer_clearance_m
    for mp in mover_parts:
        # (C) Bay intrusion: any exterior vertex strictly inside the closed bay.
        if obstacles.bay_active:
            for x, y in list(mp.polygon.exterior.coords)[:-1]:
                if obstacles.bay_xmin < x < obstacles.bay_xmax and y > obstacles.bay_ymin:
                    return False

        # (B) Pairwise overlap against each placed-plane part.
        if obstacles.world_parts:
            mp_xmin, mp_ymin, mp_xmax, mp_ymax = _aabb(mp.polygon)
            for op, (op_xmin, op_ymin, op_xmax, op_ymax) in zip(
                obstacles.world_parts, obstacles.world_part_aabbs, strict=True
            ):
                # z pre-filter: skip only when z-separated ENOUGH that
                # _parts_conflict's z-gap rule would reject — i.e. gap_z exceeds
                # the active z-threshold. This is the divergence trap: a SMALL
                # positive gap in [0, wlc) must NOT be skipped (the oracle flags
                # it), so the skip threshold is `wlc` (when wlc>0) else 0.
                gap_z = max(mp.z_bottom_m, op.z_bottom_m) - min(mp.z_top_m, op.z_top_m)
                if wlc > 0.0:
                    if gap_z >= wlc:
                        continue
                elif gap_z >= 0.0:
                    continue
                # AABB plan-view pre-filter inflated by clearance: separated by
                # more than clearance => cannot conflict. Obstacle AABBs are
                # precomputed (static across poses). (At clearance==0 a touching
                # AABB edge survives the filter and the exact predicate decides
                # via intersects-and-not-touches.)
                if (
                    mp_xmin - op_xmax > clearance
                    or op_xmin - mp_xmax > clearance
                    or mp_ymin - op_ymax > clearance
                    or op_ymin - mp_ymax > clearance
                ):
                    continue
                # Exact predicate (the oracle's own) — reuse guarantees equivalence.
                if _parts_conflict(mp, op, hangar):
                    return False
    return True


def plan_path(
    mover: Aircraft,
    entry: Pose,
    goal: Pose,
    *,
    hangar: Hangar,
    placed: Layout,
    mover_on_carts: bool,
    entries: tuple[Pose, ...] | None = None,
    max_expansions: int = _MAX_EXPANSIONS,
) -> DubinsArc:
    """Deterministic Hybrid-A* tow path from ``entry`` (or ``entries``) to ``goal``.

    **Single-start mode** (``entries=None``, the default / backward-compatible
    behaviour): seeds the search with the single ``entry`` pose at ``g = 0``.

    **Multi-start / door-cone mode** (``entries`` provided, #262): seeds the
    search frontier with every *surviving* start pose from the cone — a pose
    survives iff its footprint at the front boundary does not clip the side or
    back walls (:func:`_mover_motion_bounds_conflict`; the front-wall ``y < 0``
    exemption still applies).  If ALL candidates are filtered out, the fallback
    is the door-centre straight-in pose (always safe) so the search always has
    at least one start.  Each surviving start is enqueued at ``g = 0`` with its
    own Euclidean heuristic; A* then naturally expands the most-promising start
    first and returns the best total path across the whole cone.  The
    ``DubinsArc.start`` of the returned arc is the cone pose that *won* (from
    :func:`_root_pose`), not necessarily ``entry``.

    Searches continuous ``(x, y, heading)`` with the fixed primitive fan
    (:func:`_primitives`), grid-binned via :func:`_cell`, an admissible Euclidean
    heuristic, and an analytic-expansion shortcut: at every popped node a direct
    ``plan_dubins`` shot to ``goal`` is tried first, so an unobstructed plane
    finishes in one arc. Per-edge and analytic-shot validity during search use
    the FAST per-pose checker :func:`_motion_clear` (against an obstacle set
    precomputed once via :func:`_build_obstacles`) — an edge/shot is valid iff
    every sampled pose is clear. Before the analytic shot is RETURNED it is
    re-validated by the EXACT oracle :func:`path_first_conflict` (the safety net),
    so the returned path is exact-oracle-clean regardless of any fast-vs-exact
    divergence during search. The result is a single :class:`DubinsArc` whose
    segments concatenate the chosen primitives + the final analytic arc (all
    share ``turn_radius_m``). Raises :class:`NoFeasiblePlanError` when no
    in-bounds path is found within ``max_expansions``. RNG-free (ADR-0003):
    fixed primitive order + a monotonic counter tie-break; ``_motion_clear`` is
    pure and deterministic, so determinism is preserved.

    **Merge-conflict note for #261 (Reeds–Shepp / reverse entry):** this
    function's start-seeding region (the code between ``r = ...`` and ``while
    open_heap``) was changed by #262.  The analytic-expansion region (the
    ``final_arc = plan_dubins(...)`` block) is **untouched** here and is owned
    by #261.  Keep that invariant when merging.

    ``hangar`` is consumed directly by :func:`_motion_clear` (bounds, clearances,
    bay rectangle); ``placed.hangar`` is the same object and also reaches the
    exact-oracle safety net via :func:`path_first_conflict`.
    """
    r = mover.effective_turn_radius_m()
    # Static obstacle set, computed once: placed planes don't move while this one
    # is routed. Drives the fast per-pose `_motion_clear` used during search.
    obstacles = _build_obstacles(placed, mover_id=mover.id)
    counter = 0

    # ── Build the effective start set ────────────────────────────────────────
    # Single-start (no cone): use the bare ``entry`` unchanged.
    # Multi-start (cone provided): filter cone candidates that clip side/back walls
    # at the entry pose; fall back to the door-centre straight-in pose if all are
    # filtered so the search always has at least one start.
    if entries is None:
        start_poses: tuple[Pose, ...] = (entry,)
    else:
        # Filter: keep only poses whose footprint at the door boundary is clear of
        # side/back walls (front-wall y<0 exemption already built into the predicate).
        surviving: list[Pose] = []
        for candidate_pose in entries:
            candidate_placement = Placement(
                mover.id,
                candidate_pose.x_m,
                candidate_pose.y_m,
                candidate_pose.heading_deg,
                on_carts=False,
            )
            if _mover_motion_bounds_conflict(mover, candidate_placement, hangar) is None:
                surviving.append(candidate_pose)
        if surviving:
            start_poses = tuple(surviving)
        else:
            # Fallback: straight-in door-centre pose (always fits through the door).
            start_poses = (Pose(x_m=hangar.door.center_x_m, y_m=0.0, heading_deg=0.0),)

    # ── Seed the open heap with all surviving start poses ───────────────────
    # Heuristic: straight-line Euclidean distance. Deliberately looser than the
    # spec's Dubins-distance suggestion — Euclidean ≤ Dubins length ≤ true cost
    # and the g-cost turn penalty is ≥ 0, so it stays admissible (it may expand a
    # few more nodes, never fewer; do NOT "tighten" it to the Dubins shot without
    # re-checking admissibility and the determinism canary).
    open_heap: list[tuple[float, int, _SearchNode]] = []
    best_g: dict[tuple[int, int, int], float] = {}
    for start_pose in start_poses:
        start_node = _SearchNode(start_pose, 0.0, None, None)
        start_key = _cell(start_pose)
        h_start = math.hypot(goal.x_m - start_pose.x_m, goal.y_m - start_pose.y_m)
        # Only seed if not already dominated by a cheaper start in the same cell.
        if best_g.get(start_key, math.inf) - 1e-9 > 0.0:
            best_g[start_key] = 0.0
            heapq.heappush(open_heap, (h_start, counter, start_node))
            counter += 1
    expansions = 0

    while open_heap:
        _, _, node = heapq.heappop(open_heap)
        ckey = _cell(node.pose)
        # Stale heap entry (a cheaper path to this cell was found after pushing).
        if best_g.get(ckey, math.inf) < node.g - 1e-9:
            continue

        # Analytic expansion: try to close to the goal directly. Screen every
        # sample with the fast checker first (short-circuits on the first
        # not-clear sample); only if all clear do we pay for the EXACT oracle as a
        # safety net. Return iff the oracle ALSO confirms clean, so the returned
        # path is exact-oracle-clean no matter what the fast checker accepted
        # during search. The `and` short-circuits left-to-right, so the cheap
        # `_motion_clear` screen always runs before the costly oracle.
        # NOTE: do NOT modify this block — it is owned by #261 (Reeds–Shepp).
        final_arc = plan_dubins(node.pose, goal, turn_radius_m=r)
        if all(
            _motion_clear(mover, p, obstacles, hangar)
            for p in final_arc.sample(step_m=_SEARCH_STEP_M, step_deg=_SEARCH_STEP_DEG)
        ):
            segs = tuple(_reconstruct_segments(node)) + final_arc.segments
            # ``final_arc.segments`` is always non-empty (plan_dubins guarantees
            # it), so ``segs`` is non-empty by construction. Assert it loudly
            # rather than silently substituting a zero-length straight: a future
            # regression that produced empty segs would otherwise return a
            # do-nothing arc that the safety net (sampling only the start pose of
            # a zero-length arc) could not catch.
            assert segs, "plan_path: reconstructed + analytic segments are empty"
            # Use the root of this node's parent chain as start (#262 multi-start):
            # different cone poses may win for different runs/goals, and the arc's
            # ``start`` must reflect the actual winning root, not the positional
            # ``entry`` argument.
            arc_start = _root_pose(node)
            candidate = DubinsArc(arc_start, goal, r, segs)
            # Safety net: validate the WHOLE candidate path (coarse-sampled
            # primitive prefix + analytic suffix) with the EXACT oracle at fine
            # resolution. Returning only on agreement makes the result
            # exact-oracle-clean regardless of any fast-vs-exact divergence the
            # coarse in-search sampling allowed.
            if (
                path_first_conflict(candidate, mover, mover_on_carts=mover_on_carts, placed=placed)
                is None
            ):
                return candidate
        # else (fast screen failed, OR fast passed but the exact oracle rejected
        # the full path): do not ship; fall through to primitive expansion.

        if expansions >= max_expansions:
            break
        expansions += 1

        # Primitive expansion (fixed order L, S, R for determinism). An edge is
        # valid iff every sampled pose is clear per the fast checker.
        for seg in _primitives(r):
            child_pose = _step_pose(node.pose, seg, r)
            edge = DubinsArc(node.pose, child_pose, r, (seg,))
            if not all(
                _motion_clear(mover, p, obstacles, hangar)
                for p in edge.sample(step_m=_SEARCH_STEP_M, step_deg=_SEARCH_STEP_DEG)
            ):
                continue
            child_g = node.g + _seg_cost(seg, r)
            child_key = _cell(child_pose)
            if child_g < best_g.get(child_key, math.inf) - 1e-9:
                best_g[child_key] = child_g
                counter += 1
                h = math.hypot(goal.x_m - child_pose.x_m, goal.y_m - child_pose.y_m)
                heapq.heappush(
                    open_heap,
                    (child_g + h, counter, _SearchNode(child_pose, child_g, seg, node)),
                )

    # The per-edge / analytic validity checks use the fast `_motion_clear`, which
    # returns a boolean rather than a specific :class:`Conflict`, so no per-edge
    # conflict object is available to surface here — and budget exhaustion is not
    # any single named constraint (the blocker is "no path within the budget").
    # Report the honest ``no_feasible_path`` kind rather than mis-labelling it
    # ``hangar_bounds``, so a caller keying on ``conflict.kind`` (plan_fill / the
    # Wave 3 CLI) is not misled about the cause; the mover is still named.
    raise NoFeasiblePlanError(
        mover.id,
        Conflict.single(
            kind="no_feasible_path",
            plane=mover.id,
            detail=f"no in-bounds tow path found within {max_expansions} expansions",
        ),
    )

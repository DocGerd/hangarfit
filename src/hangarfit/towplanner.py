"""Tow-path planner — empty-hangar fill (Phase 3a; Reeds–Shepp motion, v2).

Answers *how* each plane reaches its target slot: a deterministic entry order
(:func:`back_first_order`) plus an in-bounds, obstacle-free tow path per plane
found by a deterministic Hybrid-A* search (:func:`plan_path`, #222) over
**Reeds–Shepp** motion primitives — emitted as a single :class:`DubinsArc`
(the historical container name; its segments now carry a ``gear``). An
unobstructed plane finishes in one closed-form Reeds–Shepp shot (the search's
analytic expansion). Reeds–Shepp = Dubins (forward arc-line-arc) **plus reverse
arcs/straights** (:func:`plan_reeds_shepp`, #261), so a plane can back up to
reorient instead of driving a full turning-circle loop. The planner minimises
*moves*: cost is ``length + CUSP_PENALTY × cusps`` (#480), so a direction change
is what's penalised, not reverse distance — forward is preferred only as the
deterministic tie-break. The closed form is still deterministic, preserving the
ADR-0003 byte-identical-plan contract. See ADR-0002 (heading convention),
ADR-0010 (Reeds–Shepp motion model, supersedes ADR-0007 fork-2 "Dubins-only";
amended for the #480 cusp-cost model), ADR-0007 (cart = own-gear, r=0), and
docs/spikes/tow-path-planning.md.
"""

from __future__ import annotations

import heapq
import math
import typing
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from shapely import box, union_all
from shapely.geometry import Point, Polygon

# `_parts_conflict` is the exact oracle's per-pair predicate (collisions.py). The
# fast in-search checker `_motion_clear` reuses it verbatim — rather than
# re-deriving the polygon-clearance + z-gap rule — so the two can never diverge.
# Importing a sibling-module private is the same pattern as `check as _check`.
from hangarfit.collisions import _parts_conflict
from hangarfit.collisions import check as _check
from hangarfit.geometry import (
    WorldPart,
    cached_parts_world,
    polygon_overlap,
    pose_cache_scope,
)
from hangarfit.models import (
    Aircraft,
    ApronShallowDrop,
    Conflict,
    GroundObject,
    Hangar,
    Layout,
    Placement,
)

# Steering/translation kinds. ``L``/``S``/``R`` are the Reeds–Shepp steerings
# (left arc, straight, right arc). ``T`` is the cart-only **lateral translate**
# (strafe): a slide PERPENDICULAR to the heading, heading unchanged (#599,
# ADR-0010 amendment). ``T`` is emitted only at ``turn_radius_m == 0`` — it lets
# a broadside-parked plane (e.g. an 18 m glider too wide to enter a narrower door
# nose-in) slide in side-on instead of pivoting in place. It never appears in a
# Reeds–Shepp/Dubins word (those stay ``L``/``S``/``R``).
SegmentKind = Literal["L", "S", "R", "T"]
_VALID_SEGMENT_KINDS = frozenset(typing.get_args(SegmentKind))

# A Dubins "word": the ordered kinds of its three legs (e.g. ("L", "S", "R")).
# Always L/S/R — the lateral ``T`` is a cart primitive, never a closed-form word.
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
    """One leg of a Reeds–Shepp path. ``kind`` is the *steering*: ``L`` (left),
    ``S`` (straight), or ``R`` (right). ``gear`` is the *travel direction*:
    ``+1`` forward (default), ``-1`` reverse — steering and gear are
    independent (a reverse-L backs up while the wheels steer left). ``length_m``
    is the leg's arc length in metres (always ``>= 0``); the integrator applies
    ``gear`` to the translation step. Defaulting ``gear`` to ``+1`` keeps every
    forward-only (Dubins-era) ``Segment(kind, length_m)`` call valid (ADR-0010
    supersedes ADR-0007 fork-2 "Dubins-only")."""

    kind: SegmentKind
    length_m: float
    gear: Literal[1, -1] = 1

    def __post_init__(self) -> None:
        if self.kind not in _VALID_SEGMENT_KINDS:
            raise ValueError(
                f"Segment.kind must be one of {sorted(_VALID_SEGMENT_KINDS)}, got {self.kind!r}"
            )
        if self.length_m < 0.0 or not math.isfinite(self.length_m):
            raise ValueError(f"Segment.length_m must be finite and >= 0, got {self.length_m}")
        if self.gear not in (1, -1):
            raise ValueError(f"Segment.gear must be 1 (forward) or -1 (reverse), got {self.gear!r}")


@dataclass(frozen=True, slots=True)
class DubinsArc:
    """Closed-form path container between two oriented poses under a minimum
    turn radius. ``turn_radius_m = 0`` denotes a cart-borne pivot-in-place
    (ADR-0007). ``segments`` is the ordered leg decomposition; a segment may
    carry ``gear = -1`` for a **reverse** leg (the Reeds–Shepp extension,
    ADR-0010), so this now holds Reeds–Shepp paths too — the ``Dubins`` in the
    name is historical (forward-only Dubins was the v1 motion model)."""

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
        (the cart pivot-in-place encoding — see :func:`_plan_cart`, shared by
        :func:`plan_dubins` and :func:`plan_reeds_shepp`).
        """
        x = self.start.x_m
        y = self.start.y_m
        theta = compass_to_math_rad(self.start.heading_deg)
        r = self.turn_radius_m
        remaining = s_m
        for seg in self.segments:
            step = min(seg.length_m, remaining)
            gear = float(seg.gear)  # +1 forward, -1 reverse (ADR-0010)
            if seg.kind == "S":
                # Reverse negates the translation step (drive −cos/−sin).
                x += gear * step * math.cos(theta)
                y += gear * step * math.sin(theta)
            elif seg.kind == "T":
                # Lateral slide (cart strafe, #599 / ADR-0010): translate
                # PERPENDICULAR to the heading; heading is unchanged. The +1
                # strafe direction is the math-left of the heading (θ + 90°) =
                # (−sin θ, cos θ); gear −1 strafes right. At heading 90° (θ = 0)
                # a +1 strafe moves +y — i.e. straight in through the door. Only
                # ever emitted for carts (r == 0), but the integration is
                # radius-independent (a pure translation).
                x += gear * step * -math.sin(theta)
                y += gear * step * math.cos(theta)
            else:  # "L"/"R": arc of radius r; r == 0 => cart pivot in place
                sign = 1.0 if seg.kind == "L" else -1.0
                if r == 0.0:
                    # Pivot: position fixed; `step` is radians of turn. A
                    # pivot-in-place has no travel direction, so gear is left
                    # at the +1 default for the L/R-encoded cart pivot and the
                    # `sign` alone sets the rotation direction.
                    theta += sign * step
                else:
                    # The turning CENTRE is set by STEERING alone (left/right of
                    # the car), independent of gear. Forward advances around it,
                    # reverse retreats: scale the heading sweep AND the position
                    # update by `gear`. (Equivalently, signed arc-length
                    # ds = gear·step, dtheta = sign·ds/r.) The roundtrip grid is
                    # the proof; the sign here is pinned by the reverse 45°
                    # canary so a CW/CCW flip fails loudly (ADR-0002 trap).
                    cx = x - sign * r * math.sin(theta)
                    cy = y + sign * r * math.cos(theta)
                    theta += sign * gear * step / r
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

        Density is **gear-agnostic** (ADR-0010): the accounting below uses
        ``length_m``, which is the un-signed leg distance (``>= 0`` by the
        :class:`Segment` invariant, i.e. ``abs(length)``), so a reverse leg
        gets exactly the density of the equivalent forward leg.
        """
        total = self.length_m  # the "progress" parameter pose_at walks
        trans_len = 0.0  # true translation distance (excludes pivots)
        sweep_deg = 0.0  # total heading sweep
        for s in self.segments:
            if s.kind == "S" or s.kind == "T":
                # "S" along heading, "T" perpendicular (#599) — both are pure
                # translations of `length_m` metres, so both add to trans_len.
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
    """One body's entry: from the door-cone entry pose to its target slot.

    ``path`` is the routed tow arc, or ``None`` for a **deferred** move — a body
    that is enumerated as something the planner must route but whose route search
    has not been run (a #601 placed-routed ground-object mover; the search lands
    in #602). This mirrors the established best-effort idiom where an un-routed
    body is carried as ``None`` rather than dropped (Phase 3a / #197), now at
    per-move granularity. Consumers that draw a path (visualize/scene) skip a
    ``None``-path move."""

    plane_id: str
    target_slot: Pose
    # ``path is None`` marks a DEFERRED move: a placed-routed ground-object mover
    # enumerated in the plan as a body the planner must route, but whose actual
    # route search lands in #602 — today it carries no path (#601 enumerates,
    # #602 routes). A routed aircraft (or, post-#602, a routed mover) always has a
    # DubinsArc here. See the class docstring for the full contract.
    path: DubinsArc | None
    # #865 Rung D: the execution-order index of this leg within its body's tow.
    # A single-leg move (every body today) keeps the default ``0``; a future
    # move-aside relocation (#667 Rung E) emits a staging leg (``leg_index=0``)
    # then the final leg (``leg_index=1``) for the SAME ``plane_id``. The field is
    # ADDITIVE and trailing-defaulted, so every existing positional/keyword
    # ``Move(...)`` constructor and serialization is byte-identical to before.
    leg_index: int = 0

    def __post_init__(self) -> None:
        if not self.plane_id:
            raise ValueError("Move.plane_id must be non-empty")
        if self.leg_index < 0:
            raise ValueError(f"Move.leg_index must be >= 0, got {self.leg_index}")


@dataclass(frozen=True, slots=True)
class MovesPlan:
    """A full entry plan: the target layout plus the moves in execution order.

    Deliberately carries no sequence-level cart-usage tally (ADR-0007
    open question)."""

    target_layout: Layout
    moves: tuple[Move, ...]

    def __post_init__(self) -> None:
        # #667 Rung E (type-design F1): a plane's ROUTED legs must carry distinct
        # leg_index values so scene._timeline's `sorted(..., key=leg_index)` order is
        # well-defined. Deferred (path=None) legs are exempt — build_moves_plan
        # (ml/infer.py) and placed-routed movers legitimately emit a routed leg and a
        # deferred leg both at leg_index=0; mover/deferred target_slots also need not
        # be in `placements`, so NO target_slot membership check is added.
        seen_routed: dict[str, set[int]] = {}
        for m in self.moves:
            if m.path is None:
                continue
            legs = seen_routed.setdefault(m.plane_id, set())
            if m.leg_index in legs:
                raise ValueError(
                    f"MovesPlan: plane {m.plane_id!r} has duplicate routed leg_index {m.leg_index}"
                )
            legs.add(m.leg_index)


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


def _wrap180(deg: float) -> float:
    """Fold an angle (degrees) into the half-open interval ``(-180, 180]``.

    Canonical at the boundary: ``+180`` stays ``+180`` and ``-180`` maps to
    ``+180`` (used by the #480 nose-out gate, which measures distance from 180°)."""
    w = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if w == -180.0 else w


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
        return _plan_cart(start, end, allow_reverse=False)

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
# Cart (r == 0) planner — shared by Dubins (forward-only) and Reeds–Shepp.
#
# A carted plane pivots in place and translates with a zero turn radius
# (ADR-0007). The pivot-in-place case is identical for both motion models. The
# translation case differs only in whether the straight leg may be driven in
# REVERSE: Reeds–Shepp lets a cart back straight out of a slot (gear −1) when
# that is cheaper under the reverse cost weighting, which a forward-only Dubins
# cart cannot. ``length_m`` of a pivot segment encodes RADIANS (ADR-0007); the
# "S" leg is metres of translation.
# ---------------------------------------------------------------------------


def _plan_cart(
    start: Pose, end: Pose, *, allow_reverse: bool, lateral_ok: bool = False
) -> DubinsArc:
    """Zero-radius path (``turn_radius_m == 0``): pivot-in-place, pivot-straight-pivot,
    or — for a plane on a dolly — a lateral slide.

    With ``allow_reverse`` (Reeds–Shepp), the straight leg may be driven in
    reverse — pivot to the *reverse* bearing, back straight, pivot to the final
    heading — and the cheaper of the forward/reverse option is returned by
    unweighted length (#480: no per-metre reverse tax; both options are single
    0-cusp drives, so forward wins only on an exact tie). Without it (Dubins)
    only the forward option is considered, preserving the exact forward-only
    behaviour ``plan_dubins`` shipped.

    ``lateral_ok`` (#599 / ADR-0010) adds a third candidate — *pivot to the final
    heading, then straight (along) + strafe (perpendicular)* — so a plane on a
    dolly/cart slides straight into a broadside slot. It is emitted ONLY for a
    cart-borne mover (``mover_on_carts``); a free-swivel / pivot-in-place plane is
    ``r == 0`` too but cannot strafe, so it keeps ``lateral_ok=False`` (pivots +
    straights only). The candidate only wins when strictly cheaper (broadside
    geometry), so the forward/reverse selection is unchanged otherwise.
    """
    dx = end.x_m - start.x_m
    dy = end.y_m - start.y_m
    dist = math.hypot(dx, dy)
    if dist <= 1e-9:
        # Pure pivot-in-place (positions coincide): a single turn segment whose
        # length_m encodes the short-arc heading change in radians. Compass is
        # CW-positive, so a positive delta is a right turn ("R") in the math
        # frame the integrator walks; the sign is pinned by
        # test_zero_radius_is_pivot_in_place. Gear is irrelevant for a pivot
        # (no translation), so it stays at the +1 default.
        dtheta_deg = (end.heading_deg - start.heading_deg + 180.0) % 360.0 - 180.0
        pivot_kind: SegmentKind = "R" if dtheta_deg >= 0.0 else "L"
        return DubinsArc(start, end, 0.0, (Segment(pivot_kind, abs(math.radians(dtheta_deg))),))

    def _pivot_straight_pivot(*, reverse: bool) -> tuple[Segment, ...]:
        # Bearing the NOSE points along while translating: the goal bearing for
        # a forward leg, its opposite for a reverse leg (the nose faces away
        # from the direction of travel when backing up).
        travel_bearing_deg = math_rad_to_compass(math.atan2(dy, dx))
        nose_bearing_deg = (travel_bearing_deg + 180.0) % 360.0 if reverse else travel_bearing_deg
        gear: Literal[1, -1] = -1 if reverse else 1
        segs: list[Segment] = []
        seg1_deg = (nose_bearing_deg - start.heading_deg + 180.0) % 360.0 - 180.0
        if abs(seg1_deg) > 1e-9:
            k1: SegmentKind = "R" if seg1_deg >= 0.0 else "L"
            segs.append(Segment(k1, abs(math.radians(seg1_deg))))
        segs.append(Segment("S", dist, gear=gear))
        seg3_deg = (end.heading_deg - nose_bearing_deg + 180.0) % 360.0 - 180.0
        if abs(seg3_deg) > 1e-9:
            k3: SegmentKind = "R" if seg3_deg >= 0.0 else "L"
            segs.append(Segment(k3, abs(math.radians(seg3_deg))))
        return tuple(segs)

    def _pivot_then_slide() -> tuple[Segment, ...]:
        # Lateral cart connector (#599 / ADR-0010): pivot in place to the FINAL
        # heading, then reach the goal point with a straight (along heading) +
        # a lateral strafe (perpendicular). Lets a broadside-parked plane slide
        # straight in instead of pivoting twice. Reaches (end.x, end.y,
        # end.heading) exactly (the roundtrip-grid test is the proof).
        segs: list[Segment] = []
        pivot_deg = (end.heading_deg - start.heading_deg + 180.0) % 360.0 - 180.0
        if abs(pivot_deg) > 1e-9:
            pk: SegmentKind = "R" if pivot_deg >= 0.0 else "L"
            segs.append(Segment(pk, abs(math.radians(pivot_deg))))
        # Decompose the displacement in the FINAL heading frame: component along
        # the heading is a straight, perpendicular is a strafe.
        theta_e = compass_to_math_rad(end.heading_deg)
        s_along = dx * math.cos(theta_e) + dy * math.sin(theta_e)
        s_perp = dx * -math.sin(theta_e) + dy * math.cos(theta_e)
        if abs(s_along) > 1e-9:
            segs.append(Segment("S", abs(s_along), gear=1 if s_along >= 0.0 else -1))
        if abs(s_perp) > 1e-9:
            segs.append(Segment("T", abs(s_perp), gear=1 if s_perp >= 0.0 else -1))
        # dist > 1e-9 here (the pure-pivot case returned earlier), so at least
        # one of s_along/s_perp is non-zero and `segs` is non-empty.
        return tuple(segs)

    forward = _pivot_straight_pivot(reverse=False)
    if not allow_reverse:
        # Dubins (forward-only) mode: preserve the exact legacy path — no
        # reverse, no lateral (plan_dubins shipped pivot-straight-pivot only).
        return DubinsArc(start, end, 0.0, forward)
    reverse = _pivot_straight_pivot(reverse=True)
    # The lateral slide is only available to a cart-borne mover (#599); a
    # pivot-in-place plane (free-swivel tailwheel) cannot strafe, so it is offered
    # forward/reverse only.
    candidates = (forward, reverse) + ((_pivot_then_slide(),) if lateral_ok else ())
    # Rank by the #480 fewest-moves cost (cusp-aware _segments_cost). For the
    # forward-vs-reverse pair this is monotonic in total pivot magnitude (equal
    # straight metres, both 0-cusp single drives), so their selection is UNCHANGED
    # from the prior pivot-magnitude (length) ranking; the lateral candidate only
    # wins when strictly cheaper (broadside geometry), where it removes the double
    # pivot. min() keeps the first listed on an exact tie — forward, then reverse,
    # then lateral — for determinism (ADR-0003).
    best = min(candidates, key=lambda segs: _segments_cost(segs, 0.0))
    return DubinsArc(start, end, 0.0, best)


# ---------------------------------------------------------------------------
# Closed-form Reeds–Shepp set (ADR-0010, towplanner v2; supersedes ADR-0007
# fork-2 "Dubins-only"). Reeds–Shepp = Dubins + reverse arcs/straights, so a
# car can back up to reorient instead of driving a full turning-circle loop.
#
# Built by the textbook **base-formula + symmetry-generation** method: a small
# set of base word-solvers (CSC, CCC, CCCC, CCSC, CCSCC) in the NORMALISED math
# frame, then two Reeds–Shepp goal symmetries — TIMEFLIP (reverse all gears;
# the word for the time-reversed goal (-x, y, -phi)) and REFLECT (swap L↔R
# steering; the word for the x-axis-mirrored goal (x, -y, -phi)) — each base
# solver evaluated under the four combinations (identity, timeflip, reflect,
# timeflip+reflect) to enumerate the full word family. This is the standard
# `reeds_shepp` construction; the base formulas follow Reeds & Shepp (1990) /
# the OMPL/PythonRobotics presentation.
#
# Each base solver works on a normalised relative goal ``(x, y, phi)``: the goal
# expressed in the start frame and scaled by ``1/r`` (so the turn radius is 1).
# It returns a list of ``_RSElement(steering, gear, t)`` legs with t a normalised
# length (radians for a turn, units of r for a straight), or ``None`` if
# infeasible. The integrator ``DubinsArc.pose_at`` walks the scaled-back
# ``Segment``s, so a correct word reproduces the goal pose — enforced
# exhaustively by ``test_reeds_shepp_roundtrip_grid``.
# ---------------------------------------------------------------------------


# Additive per-cusp penalty (metres) — the #480 "fewest-moves" cost model
# (supersedes the old multiplicative reverse-length factor, ADR-0010 #480
# amendment). A "cusp" is a travel-direction reversal (forward<->reverse) between
# consecutive TRANSLATING legs; moves = cusps + 1. Cost is
# `length + CUSP_PENALTY * cusps` at all three sites (search g-cost, Reeds–Shepp
# word selection, cart choice). Reverse is no longer taxed per-metre; instead
# each direction change costs a fixed, large-but-finite amount, so the planner
# minimises *moves* and forward preference survives only as the enumeration-order
# tie-break (forward primitives/words enumerated first).
#
# Why 10.0 m? It must (a) keep a genuine nose-out win — a back-in via a
# rear-entry seed is 0 cusps and wins on length alone, and where a 1-cusp back-in
# (~18 m) replaces a forward loop (~32 m) we need CUSP_PENALTY < 14 m — and
# (b) dominate the small length differences between equal-move alternatives so
# the planner doesn't trade a direction change for a couple of saved metres.
# 10 m (order of a plane length / the hangar's short dimension) satisfies both.
# Pinned by test_cusp_penalty_value; changing it requires an ADR-0010 update.
CUSP_PENALTY = 10.0


def _count_cusps(legs: list[tuple[int, bool]]) -> int:
    """Number of travel-direction reversals among the TRANSLATING legs (#480).

    ``legs`` is ``(gear, translates)`` in travel order. Non-translating legs
    (in-place cart pivots — ``r == 0`` turns) are skipped: they are free
    reorientations, not moves. The result is the count of ``gear`` sign-changes
    between consecutive *translating* legs."""
    cusps = 0
    prev: int | None = None
    for gear, translates in legs:
        if not translates:
            continue
        if prev is not None and gear != prev:
            cusps += 1
        prev = gear
    return cusps


@dataclass(frozen=True, slots=True)
class _RSElement:
    """One normalised Reeds–Shepp leg: steering ``L``/``S``/``R``, ``gear``
    ``+1``/``-1``, and ``t`` the normalised length (radians for a turn, units
    of the turn radius for a straight). Always ``t >= 0``."""

    steering: SegmentKind
    gear: Literal[1, -1]
    t: float


_RSWord = list[_RSElement]


def _rs_polar(x: float, y: float) -> tuple[float, float]:
    """``(r, theta)`` of the vector ``(x, y)`` — magnitude and math-angle."""
    return math.hypot(x, y), math.atan2(y, x)


def _rs_mod2pi(theta: float) -> float:
    """Normalise to ``(-pi, pi]`` — the branch the RS base formulas assume."""
    v = _mod2pi(theta)
    if v > math.pi:
        v -= 2.0 * math.pi
    return v


# ── Base word solvers (normalised frame, turn radius 1) ─────────────────────
# Each returns the leg lengths for ONE canonical word shape, or None. The
# symmetry transforms below generate the remaining words from these bases. The
# formulas are the OMPL/PythonRobotics presentation of Reeds & Shepp (1990).


# A "signed-length leg": steering, the word's nominal gear, and a SIGNED length.
# Reeds–Shepp word formulas naturally yield signed leg lengths; a negative length
# means "traverse |length| in the OPPOSITE gear" (the standard sign-flips-gear
# trick). :func:`_rs_signed_to_word` converts a list of signed legs into an
# ``_RSWord`` with non-negative ``t`` and the gear resolved from the sign, then
# the universal :func:`_rs_word_reaches` gate verifies the result actually lands
# on the goal — so a per-formula feasibility guard is unnecessary (and would be a
# transcription-error hiding place); the re-integration is the single oracle.
_SignedLeg = tuple[SegmentKind, "Literal[1, -1]", float]


def _rs_signed_to_word(legs: list[_SignedLeg]) -> _RSWord:
    out: _RSWord = []
    for steering, gear, length in legs:
        g: Literal[1, -1] = gear if length >= 0.0 else (-gear)
        out.append(_RSElement(steering, g, abs(length)))
    return out


def _rs_lsl(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CSC word L S L (Reeds & Shepp 8.1 / PythonRobotics)."""
    u, t = _rs_polar(x - math.sin(phi), y - 1.0 + math.cos(phi))
    v = _rs_mod2pi(phi - t)
    return _rs_signed_to_word([("L", 1, t), ("S", 1, u), ("L", 1, v)])


def _rs_lsr(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CSC word L S R (Reeds & Shepp 8.2 / PythonRobotics)."""
    u1, t1 = _rs_polar(x + math.sin(phi), y - 1.0 - math.cos(phi))
    u1 = u1 * u1
    if u1 < 4.0:
        return None
    u = math.sqrt(u1 - 4.0)
    theta = math.atan2(2.0, u)
    t = _rs_mod2pi(t1 + theta)
    v = _rs_mod2pi(t - phi)
    return _rs_signed_to_word([("L", 1, t), ("S", 1, u), ("R", 1, v)])


def _rs_lrl(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CCC word L R L (Reeds & Shepp 8.3 / PythonRobotics)."""
    u1, t1 = _rs_polar(x - math.sin(phi), y - 1.0 + math.cos(phi))
    if u1 > 4.0:
        return None
    u = -2.0 * math.asin(0.25 * u1)
    t = _rs_mod2pi(t1 + 0.5 * u + math.pi)
    v = _rs_mod2pi(phi - t + u)
    return _rs_signed_to_word([("L", 1, t), ("R", 1, u), ("L", 1, v)])


def _rs_lrlrn(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CCCC word L R L R (Reeds & Shepp 8.7 / PythonRobotics ``CCCC`` n)."""
    xi = x + math.sin(phi)
    eta = y - 1.0 - math.cos(phi)
    rho = 0.25 * (2.0 + math.hypot(xi, eta))
    if rho > 1.0 or rho < 0.0:
        return None
    u = math.acos(rho)
    t, v = _rs_tau_omega(u, -u, xi, eta, phi)
    return _rs_signed_to_word([("L", 1, t), ("R", 1, u), ("L", 1, -u), ("R", 1, v)])


def _rs_lrlrp(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CCCC word L R L R (Reeds & Shepp 8.8 / PythonRobotics ``CCCC`` p)."""
    xi = x + math.sin(phi)
    eta = y - 1.0 - math.cos(phi)
    rho = (20.0 - xi * xi - eta * eta) / 16.0
    if rho > 1.0 or rho < 0.0:
        return None
    u = -math.acos(rho)
    if u < -0.5 * math.pi:
        return None
    t, v = _rs_tau_omega(u, u, xi, eta, phi)
    return _rs_signed_to_word([("L", 1, t), ("R", 1, u), ("L", 1, u), ("R", 1, v)])


def _rs_tau_omega(u: float, v: float, xi: float, eta: float, phi: float) -> tuple[float, float]:
    """The (tau, omega) leg pair shared by the CCCC words (PythonRobotics)."""
    delta = _rs_mod2pi(u - v)
    a = math.sin(u) - math.sin(delta)
    b = math.cos(u) - math.cos(delta) - 1.0
    t1 = math.atan2(eta * a - xi * b, xi * a + eta * b)
    t2 = 2.0 * (math.cos(delta) - math.cos(v) - math.cos(u)) + 3.0
    tau = _rs_mod2pi(t1 + math.pi) if t2 < 0.0 else _rs_mod2pi(t1)
    omega = _rs_mod2pi(tau - u + v - phi)
    return tau, omega


def _rs_lrsr(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CCSC word L R(−π/2) S R (Reeds & Shepp 8.9 / PythonRobotics)."""
    xi = x + math.sin(phi)
    eta = y - 1.0 - math.cos(phi)
    rho, theta = _rs_polar(-eta, xi)
    if rho < 2.0:
        return None
    t = theta
    u = 2.0 - rho
    v = _rs_mod2pi(t + 0.5 * math.pi - phi)
    return _rs_signed_to_word([("L", 1, t), ("R", 1, -0.5 * math.pi), ("S", 1, u), ("R", 1, v)])


def _rs_lrsl(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CCSC word L R(−π/2) S L (Reeds & Shepp 8.10 / PythonRobotics)."""
    xi = x - math.sin(phi)
    eta = y - 1.0 + math.cos(phi)
    rho, theta = _rs_polar(xi, eta)
    if rho < 2.0:
        return None
    r = math.sqrt(rho * rho - 4.0)
    u = 2.0 - r
    t = _rs_mod2pi(theta + math.atan2(r, -2.0))
    v = _rs_mod2pi(phi - 0.5 * math.pi - t)
    return _rs_signed_to_word([("L", 1, t), ("R", 1, -0.5 * math.pi), ("S", 1, u), ("L", 1, v)])


def _rs_lrslr(x: float, y: float, phi: float) -> _RSWord | None:
    """Base CCSCC word L R(−π/2) S L(−π/2) R (Reeds & Shepp 8.11 / PythonRobotics)."""
    xi = x + math.sin(phi)
    eta = y - 1.0 - math.cos(phi)
    rho, _theta = _rs_polar(xi, eta)
    if rho < 2.0:
        return None
    u = 4.0 - math.sqrt(rho * rho - 4.0)
    if u > 0.0:
        return None
    t = _rs_mod2pi(math.atan2((4.0 - u) * xi - 2.0 * eta, -2.0 * xi + (u - 4.0) * eta))
    v = _rs_mod2pi(t - phi)
    return _rs_signed_to_word(
        [
            ("L", 1, t),
            ("R", 1, -0.5 * math.pi),
            ("S", 1, u),
            ("L", 1, -0.5 * math.pi),
            ("R", 1, v),
        ]
    )


# ── Symmetry transforms (operate on a base solver) ──────────────────────────


def _rs_timeflip(word: _RSWord | None) -> _RSWord | None:
    """TIMEFLIP: negate every gear (a base solved for the time-reversed goal
    ``(-x, y, -phi)`` becomes the reverse-gear word for the original goal)."""
    if word is None:
        return None
    return [_RSElement(e.steering, -e.gear, e.t) for e in word]


def _rs_reflect(word: _RSWord | None) -> _RSWord | None:
    """REFLECT: swap L↔R steering (a base solved for the x-axis-reflected goal
    ``(x, -y, -phi)`` becomes the word for the original goal)."""
    if word is None:
        return None
    swap: dict[SegmentKind, SegmentKind] = {"L": "R", "R": "L", "S": "S"}
    return [_RSElement(swap[e.steering], e.gear, e.t) for e in word]


# The base word solvers. Each canonical word shape is enumerated under the four
# (timeflip × reflect) goal symmetries below, which together generate the
# classical Reeds–Shepp 48-word family from this handful of base formulas
# (Reeds & Shepp 1990; the formulas follow the widely-used PythonRobotics
# presentation). The roundtrip grid proves the family is complete — every
# (x, y, phi) is reached by at least one generated word.
_RS_BASE_SOLVERS: tuple[Callable[[float, float, float], _RSWord | None], ...] = (
    _rs_lsl,
    _rs_lsr,
    _rs_lrl,
    _rs_lrlrn,
    _rs_lrlrp,
    _rs_lrsr,
    _rs_lrsl,
    _rs_lrslr,
)


def _rs_solve_normalised(
    x: float, y: float, phi: float, *, cusp_penalty_normalised: float
) -> _RSWord:
    """Fewest-moves feasible Reeds–Shepp word for a normalised goal ``(x, y, phi)``.

    Enumerates :data:`_RS_BASE_SOLVERS` under the timeflip / reflect goal
    symmetries (the standard mechanical generation of the classical word
    family), scoring each feasible word by ``Σ|leg| + cusp_penalty_normalised ×
    cusps`` (#480) — gear-agnostic length plus a fixed per-cusp penalty — and
    returning the minimum with a strict-``<`` tie-break so an exact tie
    deterministically keeps the earliest-enumerated word (determinism, ADR-0003).
    ``cusp_penalty_normalised`` is :data:`CUSP_PENALTY` ``/ r`` (the caller's turn
    radius), so the normalised choice agrees with the metre-space objective. A
    Reeds–Shepp path always exists between any two poses, so some word is always
    feasible — proven exhaustively by ``test_reeds_shepp_roundtrip_grid``.

    Every generated word is gated by :func:`_rs_word_reaches` (an independent
    re-integration) before it can be chosen: a base-formula sign error then
    surfaces as a *missing* candidate, never as a *wrong path that ships*. The
    deterministic enumeration order (base list × the fixed four symmetries) is
    the iteration order the tie-break relies on.
    """

    def _candidates_for(base: Callable[[float, float, float], _RSWord | None]) -> list[_RSWord]:
        out: list[_RSWord | None] = [
            base(x, y, phi),  # identity
            _rs_timeflip(base(-x, y, -phi)),  # timeflip
            _rs_reflect(base(x, -y, -phi)),  # reflect
            _rs_reflect(_rs_timeflip(base(-x, -y, phi))),  # timeflip + reflect
        ]
        return [w for w in out if w is not None]

    best: tuple[float, _RSWord] | None = None
    for base in _RS_BASE_SOLVERS:
        for word in _candidates_for(base):
            if not _rs_word_reaches(word, x, y, phi):
                continue
            cusps = _count_cusps([(e.gear, True) for e in word])  # every RS leg translates
            cost = math.fsum(e.t for e in word) + cusp_penalty_normalised * cusps
            if best is None or cost < best[0]:
                best = (cost, word)
    if best is None:  # pragma: no cover - a Reeds–Shepp path always exists
        raise ValueError(f"no feasible Reeds–Shepp path for normalised goal ({x}, {y}, {phi})")
    return best[1]


def _rs_word_reaches(word: _RSWord, x: float, y: float, phi: float) -> bool:
    """``True`` iff integrating ``word`` in the normalised frame (unit radius,
    start at the origin heading ``+x``) lands on ``(x, y, phi)``.

    A closed-form-independent check using the SAME unicycle integration the
    production :meth:`DubinsArc.pose_at` performs, so a base-formula sign error
    is caught here rather than shipping a wrong path."""
    px, py, pth = 0.0, 0.0, 0.0
    for e in word:
        g = float(e.gear)
        if e.steering == "S":
            px += g * e.t * math.cos(pth)
            py += g * e.t * math.sin(pth)
        else:
            sign = 1.0 if e.steering == "L" else -1.0
            cx = px - sign * math.sin(pth)
            cy = py + sign * math.cos(pth)
            pth += sign * g * e.t
            px = cx + sign * math.sin(pth)
            py = cy - sign * math.cos(pth)
    return (
        abs(px - x) < 1e-6
        and abs(py - y) < 1e-6
        and abs((pth - phi + math.pi) % (2.0 * math.pi) - math.pi) < 1e-6
    )


def plan_reeds_shepp(
    start: Pose, end: Pose, *, turn_radius_m: float, lateral: bool = False
) -> DubinsArc:
    """Closed-form fewest-moves Reeds–Shepp path from ``start`` to ``end`` (ADR-0010).

    Reeds–Shepp extends Dubins with reverse arcs and straights, so a plane can
    back up to reorient rather than driving a full turning-circle loop. Word
    selection minimises ``Σ|leg| + CUSP_PENALTY × cusps`` (#480): gear-agnostic
    length plus a fixed per-cusp (direction-change) penalty, so reverse is not
    taxed per-metre and forward is preferred only as the tie-break. Still
    closed-form and RNG-free, so the ADR-0003 byte-identical-plan contract holds.
    ``turn_radius_m == 0`` is the zero-radius case and delegates to
    :func:`_plan_cart` with reverse enabled (it may back straight out of a slot);
    ``lateral`` (#599) is forwarded so a cart-borne mover may also slide in
    sideways (a free-swivel pivot-in-place plane passes ``lateral=False``).

    Works in the standard math frame: positions pass through unchanged; headings
    convert via :func:`compass_to_math_rad`. The integrated endpoint
    (:meth:`DubinsArc.pose_at`) reaches the goal — the property
    ``test_reeds_shepp_roundtrip_grid`` enforces across a pose/radius grid.
    """
    if turn_radius_m < 0.0 or not math.isfinite(turn_radius_m):
        raise ValueError(f"turn_radius_m must be finite and >= 0, got {turn_radius_m}")

    if turn_radius_m == 0.0:
        return _plan_cart(start, end, allow_reverse=True, lateral_ok=lateral)

    r = turn_radius_m
    # Express the goal in the start frame, scaled to unit turn radius. The math
    # frame: theta0 = compass_to_math_rad(start heading); rotate the world
    # displacement into the start-aligned frame and normalise by r.
    theta0 = compass_to_math_rad(start.heading_deg)
    theta1 = compass_to_math_rad(end.heading_deg)
    dx = (end.x_m - start.x_m) / r
    dy = (end.y_m - start.y_m) / r
    c, s = math.cos(theta0), math.sin(theta0)
    x = c * dx + s * dy
    y = -s * dx + c * dy
    phi = _rs_mod2pi(theta1 - theta0)

    # CUSP_PENALTY is in metres; the word is scored in normalised units (×r to
    # metres), so the normalised per-cusp penalty is CUSP_PENALTY / r — making the
    # word choice agree with the metre-space fewest-moves objective (#480).
    word = _rs_solve_normalised(x, y, phi, cusp_penalty_normalised=CUSP_PENALTY / r)
    raw = tuple(Segment(e.steering, e.t * r, gear=e.gear) for e in word)
    # Collapse zero-length legs (a collinear same-heading path degenerates to a
    # single straight); keep at least one segment.
    segs = tuple(seg for seg in raw if seg.length_m > 1e-9) or (Segment("S", 0.0),)
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
# Staging apron (#412 / ADR-0021): the y < 0 start-region in front of the door
# ---------------------------------------------------------------------------


def _plane_fore_aft_length_m(aircraft: Aircraft) -> float:
    """Fore-aft (plane-local x) extent of a plane from its parts.

    Approximates per-part rotation away (e.g. struts) — adequate for the opt-in
    ``auto`` apron depth, which is a convenience knob, not a tight bound. Pure
    and RNG-free.
    """
    fronts = [p.offset_x_m + p.length_m / 2.0 for p in aircraft.parts]
    backs = [p.offset_x_m - p.length_m / 2.0 for p in aircraft.parts]
    return max(fronts) - min(backs)


def derive_apron_depth(fleet: Mapping[str, Aircraft]) -> float:
    """Fleet-derived staging-apron depth — the opt-in ``auto`` value (ADR-0021).

    ``≈ max(plane fore-aft length) + max(own-gear turn radius)``: the run-up room
    one plane needs to align and slide in through the door. A pure function of
    the fleet (RNG-free), so the resolved depth is deterministic. An empty fleet
    (or an all-cart fleet with no own-gear turn radius) yields just the length
    term; an empty fleet yields ``0.0``.
    """
    if not fleet:
        return 0.0
    max_len = max(_plane_fore_aft_length_m(a) for a in fleet.values())
    radii = [a.turn_radius_m for a in fleet.values() if a.turn_radius_m is not None]
    return max_len + (max(radii) if radii else 0.0)


# ---------------------------------------------------------------------------
# Door-cone entry poses (spike Q6 / ADR-0007 / #262: the door is a motion gate)
# ---------------------------------------------------------------------------

# The five headings of the forward-admissible entry cone: straight-in ±30°
# in 15° steps.  All five point generally inward (nose toward +y hemisphere).
# Rear-entry headings (near 180°) are out of scope here — see issue #261.
_CONE_HEADINGS: tuple[float, ...] = (330.0, 345.0, 0.0, 15.0, 30.0)

# The five rear-entry (nose-out / back-in) headings: 180° ± 30° in 15° steps.
# Emitted iff the TARGET parked heading is nose-out-ish (#480), independent of
# the apron: a nose-out slot can then be backed in tail-first rather than
# pirouetting in the back corner. A nose-in slot never wins a rear-entry seed,
# so it keeps the forward cone only (no wasted expansions). These remain
# additional deterministic seed poses the search chooses among on cost, never a
# forced orientation. Un-gating from the apron deliberately changes the depth-0
# grid for nose-out targets, superseding the #412 depth-0 cross-version
# byte-identity for that case (the ADR-0003 same-input determinism contract is
# intact). See ADR-0010 (#480 amendment).
_REVERSE_CONE_HEADINGS: tuple[float, ...] = (150.0, 165.0, 180.0, 195.0, 210.0)

# Nose-out gate half-angle (#480): the rear cone is emitted iff
# |_wrap180(target.heading_deg - 180)| <= this. ~45° covers the rear cone's own
# ±30° span plus margin (so headings in [135, 225] qualify).
_REAR_CONE_HALF_ANGLE_DEG = 45.0

# Broadside entry cone (#599): emitted iff the TARGET parked heading is broadside
# — within this half-angle of 90° or 270° (side-on to the entry axis). Motivating
# case: a plane too wide to enter nose-in (an 18 m span vs the 13.46 m Herrenteich
# door) that must slide in side-on via the cart lateral primitive (ADR-0010). The
# cone is `_BROADSIDE_CONE_OFFSETS` around BOTH the target heading and
# target + 180° (either side-on approach). Gated like the rear cone, so nose-in
# targets keep the forward cone only and their grid stays byte-identical.
_BROADSIDE_CONE_OFFSETS: tuple[float, ...] = (-30.0, -15.0, 0.0, 15.0, 30.0)
_BROADSIDE_GATE_HALF_ANGLE_DEG = 45.0


def entry_poses(target: Placement, hangar: Hangar) -> tuple[Pose, ...]:
    """All entry / apron start poses for a plane heading to ``target`` (#262, #412).

    Returns a **fixed, deterministic grid** of candidate start poses for the
    multi-start Hybrid-A\\* search. The grid is a 3 × N_y × N_h cross product
    (x-samples × y-samples × headings), deduplicated by exact-float
    ``(x, y, heading)`` key in a fixed emit order.

    **X-samples** — three values within the door interval
    ``[center − width/2, center + width/2]``:

    1. The door centre (``center_x_m``).
    2. The clamped target x — same as :func:`entry_pose`'s output (the v1 choice).
    3. The midpoint between those two.

    **Y-samples** — depend on the staging apron (``hangar.apron_depth_m``,
    ADR-0021):

    - **No apron** (``apron_depth_m == 0`` / absent): a single ``y = 0`` sample
      at the door line — *exactly the pre-apron behaviour, reproduced
      byte-for-byte* (the ``(x, y, heading)`` key collapses to the old
      ``(x, heading)`` key when ``y`` is the constant ``0.0``).
    - **With an apron** (``apron_depth_m > 0``): two samples
      ``{−apron_depth_m/2, −apron_depth_m}`` *on the apron* — the ``y = 0`` door
      line is **excluded** so every plane originates outside the hangar and
      visibly slides in through the door (the #412 viewer motivation; user
      decision 2026-06-07). Were ``y = 0`` kept, the shortest path would always
      pick the door-line start and show no slide-in.

    **Headings** (independent of the apron — #480):

    - **Nose-in target**: the 5-heading forward-admissible cone
      ``{330°, 345°, 0°, 15°, 30°}`` only (straight-in ±30°).
    - **Nose-out target** (``|wrap180(target.heading − 180)| ≤
      _REAR_CONE_HALF_ANGLE_DEG``): the forward cone **followed by** the
      rear-entry cone ``{150°, 165°, 180°, 195°, 210°}`` (180° ± 30°), so the
      search may choose to back the plane in tail-first (#263/#480, never
      forced). This applies with or without an apron, and so changes the depth-0
      grid for nose-out targets (superseding the #412 depth-0 byte-identity for
      that case; the ADR-0003 same-input contract is intact).

    **Emit order** (fixed for ADR-0003 determinism): x-outer, y-middle,
    heading-inner; duplicate ``(x, y, heading)`` triples (exact float equality)
    are skipped on the second occurrence.

    The caller (:func:`plan_path` via ``entries=`` and :func:`plan_fill`) filters
    candidates that clip the side/back walls, fall past the apron south bound, or
    cross the solid front wall beside the door (the apron-aware front-gap rule,
    #411/#412), before seeding the search; a straight-in centre pose is always
    present so the search has at least one start (which may itself be infeasible —
    e.g. a plane wider than the door — leaving the plane un-towable).

    Returns a non-empty :class:`tuple` of :class:`Pose` objects.
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

    # Y-samples — apron only (ADR-0021). depth == 0 ⇒ the single y = 0 door-line
    # sample (byte-identical to the pre-apron grid for a nose-in target). depth >
    # 0 ⇒ the start is forced ONTO the apron (the y = 0 door line is excluded) so
    # every plane originates outside and slides in (#412 viewer motivation; user
    # decision 2026-06-07) — otherwise the shortest path keeps picking y = 0.
    depth = hangar.apron_depth_m
    y_samples: tuple[float, ...] = (-depth / 2.0, -depth) if depth > 0.0 else (0.0,)

    # Headings — forward cone always; rear-entry cone iff the target parked
    # heading is nose-out-ish (#480), independent of the apron. A nose-out slot
    # can then be backed in (cheap under the cusp cost, ADR-0010 #480 amendment)
    # instead of pirouetting inside; a nose-in slot keeps the forward cone only.
    nose_out = abs(_wrap180(target.heading_deg - 180.0)) <= _REAR_CONE_HALF_ANGLE_DEG
    broadside = (
        abs(_wrap180(target.heading_deg - 90.0)) <= _BROADSIDE_GATE_HALF_ANGLE_DEG
        or abs(_wrap180(target.heading_deg - 270.0)) <= _BROADSIDE_GATE_HALF_ANGLE_DEG
    )
    headings: tuple[float, ...] = _CONE_HEADINGS + (_REVERSE_CONE_HEADINGS if nose_out else ())
    if broadside:
        # Side-on seeds around the target heading and its reverse, so the search
        # may slide the plane in from either side; the cart lateral primitive
        # (#599) then carries it straight in. Duplicate (x, y, heading) triples
        # are dropped by the `seen` set below.
        headings = (
            headings
            + tuple((target.heading_deg + off) % 360.0 for off in _BROADSIDE_CONE_OFFSETS)
            + tuple((target.heading_deg + 180.0 + off) % 360.0 for off in _BROADSIDE_CONE_OFFSETS)
        )

    seen: set[tuple[float, float, float]] = set()
    poses: list[Pose] = []
    for x in x_samples:  # outer: x (door centre, clamped target, midpoint)
        for y in y_samples:  # middle: y (door line at depth 0; apron -d/2,-d when set)
            for h in headings:  # inner: headings (forward cone, then reverse cone)
                key = (x, y, h)
                if key in seen:
                    continue
                seen.add(key)
                poses.append(Pose(x_m=x, y_m=y, heading_deg=h))

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


def _staging_poses(target: Placement, hangar: Hangar) -> tuple[Pose, ...]:
    """Apron-out (y<0) nose-out LATERAL staging candidates for a displaced body
    (#667 Rung E move-aside).

    Reuses ``entry_poses``' apron y-samples but parks the body OFF TO THE SIDE of the
    door (x spans the apron width, not just the door opening) so it does not jam the
    corridor the stuck body must enter through — the real club shuffle rolls a plane
    laterally aside, not straight out the door. Headings are nose-out
    (``_REVERSE_CONE_HEADINGS``, ~180°) so the body parks fully outside regardless of
    its parked heading. Ordered **y-outer (deepest apron first → the #844 cost-margin
    lever), x-outer (off-to-side first), heading-inner**; the ``seen`` set dedups only,
    never orders (ADR-0003 tie-break 2). Empty if no apron. Infeasible / out-of-bounds
    candidates are dropped later by the routing legs (``path_first_conflict``), so no
    static pre-filter is applied here.
    """
    depth = hangar.apron_depth_m
    if depth <= 0.0:
        return ()
    door = hangar.door
    half = door.width_m / 2.0
    lo = door.center_x_m - half
    hi = door.center_x_m + half
    width = hangar.width_m

    def _clamp(x: float) -> float:
        return min(max(x, 0.0), width)

    # Lateral x: midpoint of the left apron strip, midpoint of the right strip, then
    # the door centre as a fallback. Off-to-side first (x-outer) so the body clears the
    # stuck plane's door swath before a centre pose is tried.
    x_left = _clamp(lo / 2.0)
    x_right = _clamp((hi + width) / 2.0)
    x_centre = _clamp(door.center_x_m)
    x_samples = (x_left, x_right, x_centre)
    y_samples = (-depth, -depth / 2.0)  # deepest apron first (#844 margin)

    seen: set[tuple[float, float, float]] = set()
    poses: list[Pose] = []
    for y in y_samples:  # outer: deepest apron first
        for x in x_samples:  # middle: off-to-side first, door-centre last
            for h in _REVERSE_CONE_HEADINGS:  # inner: nose-out cone
                key = (x, y, h)
                if key in seen:
                    continue
                seen.add(key)
                poses.append(Pose(x_m=x, y_m=y, heading_deg=h))
    return tuple(poses)


# ---------------------------------------------------------------------------
# Sampled collision-during-motion (spike Q4)
# ---------------------------------------------------------------------------


def _mover_motion_bounds_conflict(
    mover: Aircraft | GroundObject, placement: Placement, hangar: Hangar
) -> Conflict | None:
    """First wall violation for a mover *in transit*, else ``None``.

    The mover is an ``Aircraft`` or a ``GroundObject`` (#602); part geometry comes
    from the union-typed :func:`~hangarfit.geometry.aircraft_parts_world`, so the
    rule is identical for both.

    **Door-aware front-gap exemption (#222, refined in #411):** a mover being
    towed through the door legitimately protrudes in front of it (``y < 0`` — the
    conceptual apron, spike Q6) — but *only through the door opening*. The front
    wall is solid except for the door gap, so a ``y < 0`` vertex is allowed only
    when ``door_lo ≤ x ≤ door_hi``; a ``y < 0`` vertex *beside* the door clips the
    solid front wall / jamb (an off-centre entry, or a plane wider than the door)
    and is a conflict — making the door a true motion gate and matching what the
    renderer draws.

    **Staging apron (#412 / ADR-0021):** when the site has an apron
    (``hangar.apron_depth_m > 0``) the front-gap exemption widens from a transient
    door-width dip to *originating and manoeuvring* in the full apron rectangle
    ``x ∈ [0, width], y ∈ [−apron_depth_m, 0)``. The wall barrier stays: a
    footprint that **crosses** the front-wall line (vertices both at ``y < 0`` and
    ``y > 0``) must still pass its ``y < 0`` portion through the door opening
    (the **#411 jamb rejection, retained verbatim** for crossings); a footprint
    **wholly** in front of the wall (all ``y ≤ 0`` — staged on the apron) does not
    cross, so the door-gate does not apply to it — only the apron south bound
    ``y ≥ −apron_depth_m`` and the side walls. With ``apron_depth_m == 0`` the
    apron branch is unreachable and this is the verbatim pre-apron #411 rule.

    Unlike the static :func:`hangarfit.collisions.check` oracle (which forbids
    ``y < 0`` entirely), the apron / door-gap protrusion is exempt here only.
    The side walls (``0 ≤ x ≤ width``) and the back wall (``y ≤ length``) are
    enforced unchanged; the mover's final slot is itself a valid static placement,
    so full bounds hold at rest. Reuses the canonical
    :func:`~hangarfit.geometry.aircraft_parts_world` transform rather than
    re-deriving geometry — the determinant-(-1) trap lives there (ADR-0002).
    """
    door_half = hangar.door.width_m / 2.0
    door_lo = hangar.door.center_x_m - door_half
    door_hi = hangar.door.center_x_m + door_half
    apron_depth = hangar.apron_depth_m

    # Pose-memoized for any placeable body (#626): an Aircraft or a GroundObject
    # mover both reuse cached_parts_world. The key is pose-generic, and outside an
    # active pose_cache_scope it is a pure passthrough ⇒ byte-identical.
    world_parts = list(cached_parts_world(mover, placement))
    # Does the footprint cross the front-wall line (vertices on both sides of
    # y=0)? Only a crossing must thread the door (#411); a footprint wholly in
    # front (all y<=0) is staged on the apron and does not cross. Computed only
    # when an apron exists; at depth 0 the door-gate below is the verbatim #411
    # per-vertex rule and this flag stays False.
    straddles_front_wall = False
    if apron_depth > 0.0:
        ys = [y for wp in world_parts for _, y in list(wp.polygon.exterior.coords)[:-1]]
        straddles_front_wall = any(y < 0.0 for y in ys) and any(y > 0.0 for y in ys)

    for world_part in world_parts:
        for x, y in list(world_part.polygon.exterior.coords)[:-1]:
            # Side walls + back wall (unchanged): the static rule is
            # `0 <= x <= width and 0 <= y <= length`.
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
            if y >= 0.0:
                continue
            if apron_depth <= 0.0:
                # Front wall is solid except the door gap (#411): a vertex in
                # front of the hangar (y < 0) is legal only when it passes
                # *through* the door opening; beside the door it clips the jamb.
                if not (door_lo <= x <= door_hi):
                    return Conflict.single(
                        kind="hangar_bounds",
                        plane=mover.id,
                        detail=(
                            f"part {world_part.kind!r} vertex ({x:.3f}, {y:.3f}) "
                            f"clips the solid front wall beside the door during tow "
                            f"(door opening x in {door_lo:g}..{door_hi:g})"
                        ),
                    )
                continue
            # Apron open below y=0 down to the south bound (#412).
            if y < -apron_depth:
                return Conflict.single(
                    kind="hangar_bounds",
                    plane=mover.id,
                    detail=(
                        f"part {world_part.kind!r} vertex ({x:.3f}, {y:.3f}) "
                        f"past the apron south bound during tow "
                        f"(apron y >= {-apron_depth:g})"
                    ),
                )
            # Crossing the solid front wall beside the door is still a conflict
            # (#411): a straddling footprint's y<0 vertices must thread the door.
            if straddles_front_wall and not (door_lo <= x <= door_hi):
                return Conflict.single(
                    kind="hangar_bounds",
                    plane=mover.id,
                    detail=(
                        f"part {world_part.kind!r} vertex ({x:.3f}, {y:.3f}) "
                        f"crosses the solid front wall beside the door during tow "
                        f"(door opening x in {door_lo:g}..{door_hi:g})"
                    ),
                )
    return None


def path_first_conflict(
    arc: DubinsArc,
    mover: Aircraft | GroundObject,
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

    Precondition: ``mover.id`` must exist in ``placed.fleet`` (an aircraft mover)
    or ``placed.ground_objects`` (a ground-object mover, #602) — each per-sample
    :class:`Layout` references it there, so an unknown id raises ``ValueError`` from
    ``Layout`` construction rather than being silently skipped. The callers
    (``plan_fill`` #196 and ``plan_path`` #222, which re-validates its final
    path here) build ``placed`` from the full target fleet + ground objects,
    satisfying this.
    """
    # #643: the tow-MOTION oracle clears the mover against parked bodies at the
    # (tighter) MOTION clearance, not the parked spacing — a spotter threads the
    # wingtips far closer in motion than the parked margin. ``motion_hangar()``
    # IS the parked hangar when no motion clearance is set, so a layout without
    # the motion fields is byte-identical (ADR-0003).
    motion_hangar = placed.hangar.motion_hangar()
    for pose in arc.sample(step_m=step_m, step_deg=step_deg):
        moving = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=mover_on_carts)
        # Mover hangar bounds: front-gap-exempt (a plane being towed in
        # straddles the door at y < 0). Side/back walls still bite.
        bounds_conflict = _mover_motion_bounds_conflict(mover, moving, motion_hangar)
        if bounds_conflict is not None:
            return bounds_conflict
        # Rebuilding the Layout per sample re-runs Layout.__post_init__ (cart
        # cap, cart↔mode consistency, unique ids). Because placed.placements ∪
        # {mover} is a subset of a valid target layout those invariants hold;
        # a future caller that violates them gets a real ValueError, not a
        # suppressed one.
        if isinstance(mover, Aircraft):
            sample_layout = Layout(
                fleet=placed.fleet,
                hangar=motion_hangar,
                placements=(*placed.placements, moving),
                maintenance_plane=placed.maintenance_plane,
                ground_objects=placed.ground_objects,
                ground_object_placements=placed.ground_object_placements,
            )
        else:  # GroundObject mover -> belongs in ground_object_placements
            sample_layout = Layout(
                fleet=placed.fleet,
                hangar=motion_hangar,
                placements=placed.placements,
                maintenance_plane=placed.maintenance_plane,
                ground_objects=placed.ground_objects,
                ground_object_placements=(*placed.ground_object_placements, moving),
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


def egress_first_conflict(
    target: Layout,
    mover_id: str,
    *,
    heuristic: Literal["euclidean", "grid"] = "grid",
    max_expansions: int | None = None,
    egress_path_out: list[DubinsArc] | None = None,
) -> Conflict | None:
    """First conflict blocking ``mover_id``'s drive-OUT through the door, else None.

    By Reeds-Shepp reversibility (ADR-0010) an egress (slot -> out the door) is
    feasible iff an entry (door-cone -> slot) path exists against the FULL parked
    scene. Reuses :func:`plan_path` with the mover routed as a
    :class:`~hangarfit.models.GroundObject`; the mover is EXCLUDED from
    ``placed`` (:func:`path_first_conflict` re-injects it per sample — same
    contract as :func:`plan_fill`'s mover routing, #602). Honors the fuel-trailer
    keep-out and every other parked body. Closed-form, RNG-free. Returns a
    ``caddy_egress`` :class:`~hangarfit.models.Conflict` when blocked.

    ``egress_path_out`` is an optional diagnostics-only **out-param** (#652,
    mirroring :func:`plan_path`'s ``stats`` / :func:`plan_fill`'s
    ``apron_dropped_out``): when supplied and egress is feasible, the winning
    door-cone -> slot :class:`DubinsArc` (which, reversed, IS the drive-out
    corridor) is appended so a caller can DRAW the egress lane (the viewer/PNG
    decal). It is left untouched when egress is blocked (no corridor exists). Pure
    out-param: ``None`` (the default, which the solver passes) keeps the call
    byte-identical — the ``plan_path`` call and the verdict are unchanged
    (ADR-0003)."""
    mover = target.ground_objects[mover_id]
    slot = next((gp for gp in target.ground_object_placements if gp.plane_id == mover_id), None)
    if slot is None:
        raise ValueError(
            f"egress_first_conflict: hard-door mover {mover_id!r} has no placement in "
            f"target.ground_object_placements (nothing to check egress for)"
        )
    placed = Layout(
        fleet=target.fleet,
        hangar=target.hangar,
        placements=target.placements,
        maintenance_plane=target.maintenance_plane,
        ground_objects=target.ground_objects,
        ground_object_placements=tuple(
            gp for gp in target.ground_object_placements if gp.plane_id != mover_id
        ),
    )
    cone = entry_poses(slot, target.hangar)
    # The egress gate is the AUTHORITATIVE safety verdict, so it routes with the
    # full per-plane budget — deliberately NOT the globally-capped
    # min(budget, remaining) that plan_fill applies to the same mover during a
    # fill (an exhausted fill budget must never falsely declare the rescue vehicle
    # trapped). Fixed _MAX_EXPANSIONS, RNG-free => deterministic (ADR-0003).
    budget = _MAX_EXPANSIONS if max_expansions is None else max_expansions
    try:
        arc = plan_path(
            mover,
            cone[0],
            Pose.from_placement(slot),
            hangar=target.hangar,
            placed=placed,
            mover_on_carts=False,
            entries=cone,
            heuristic=heuristic,
            max_expansions=budget,
        )
        if egress_path_out is not None:
            egress_path_out.append(arc)
        return None
    except NoFeasiblePlanError as exc:
        return Conflict.single(
            kind="caddy_egress",
            plane=mover_id,
            detail=(
                f"hard-door mover {mover_id!r} cannot drive out the door "
                f"(no clear egress corridor): {exc.conflict.detail}"
            ),
        )


def egress_corridors(
    layout: Layout,
    *,
    heuristic: Literal["euclidean", "grid"] = "grid",
    max_expansions: int | None = None,
) -> dict[str, DubinsArc]:
    """The drive-out corridor :class:`DubinsArc` per hard-door mover that HAS a
    clear egress (#652). Blocked movers are omitted (no corridor to draw); a
    non-hard-door mover has no required egress lane and is skipped. For drawing the
    egress lane in the 2D PNG / 3D viewer — the caller passes the result to
    :func:`hangarfit.scene.build_scene` / :func:`hangarfit.visualize.render_layout`.

    Deterministic (id-sorted iteration, RNG-free closed-form routing). This re-runs
    the egress check purely for the corridor geometry; the solve verdict already
    ran its own gate (:func:`egress_first_conflict` in the solver), so this is a
    visualization-only second pass."""
    corridors: dict[str, DubinsArc] = {}
    for gp in sorted(layout.ground_object_placements, key=lambda p: p.plane_id):
        if not layout.ground_objects[gp.plane_id].hard_door_mover:
            continue
        arc_out: list[DubinsArc] = []
        egress_first_conflict(
            layout,
            gp.plane_id,
            heuristic=heuristic,
            max_expansions=max_expansions,
            egress_path_out=arc_out,
        )
        if arc_out:
            corridors[gp.plane_id] = arc_out[0]
    return corridors


@dataclass(frozen=True, slots=True)
class TeardownProbeResult:
    """Verdict of :func:`reverse_teardown_probe` (#667 Rung C) — diagnostic only.

    ``order`` is the deterministic extraction order discovered by the greedy peel
    (id-sorted within each peel round). ``stuck`` is the canonical
    mutually-blocking residual core (id-sorted) and ``blocking`` carries one
    ``teardown_egress`` :class:`~hangarfit.models.Conflict` per stuck body, aligned
    by position (``blocking[i]`` explains ``stuck[i]``) — why it cannot drive out
    against the rest of the core. :attr:`cleared` is a derived property (True iff
    ``stuck`` is empty), so the verdict cannot disagree with the core (mirrors
    :class:`~hangarfit.models.CheckResult.valid`); a full teardown order exists
    iff ``cleared``, which by ADR-0010 reversibility is exactly the condition that
    a **monotone fill** order exists. ``order`` and ``stuck`` partition the
    tow-routable aircraft (the completeness leg — that they cover the full towable
    set — is the producer's responsibility, as it needs the input ``Layout``)."""

    order: tuple[str, ...]
    stuck: tuple[str, ...]
    blocking: tuple[Conflict, ...]

    def __post_init__(self) -> None:
        # House style (Conflict / CheckResult validate in __post_init__): pin the
        # cross-field invariants the producer guarantees so an inconsistent result
        # cannot be constructed. The partition's *completeness* needs the Layout
        # and stays the producer's job; disjointness is checkable here.
        if len(self.blocking) != len(self.stuck):
            raise ValueError(
                f"blocking ({len(self.blocking)}) must align 1:1 with stuck ({len(self.stuck)})"
            )
        if tuple(c.planes[0] for c in self.blocking) != self.stuck:
            raise ValueError("blocking[i] must explain stuck[i] (positional alignment)")
        if not set(self.order).isdisjoint(self.stuck):
            overlap = set(self.order) & set(self.stuck)
            raise ValueError(f"order and stuck must be disjoint: {overlap}")

    @property
    def cleared(self) -> bool:
        """True iff a full teardown (⇔ monotone fill) order exists — i.e. no body
        is stuck. Derived from ``stuck`` so it can never disagree with the core."""
        return not self.stuck


def _aircraft_egress_conflict(
    placement: Placement,
    placed: Layout,
    hangar: Hangar,
    fleet: Mapping[str, Aircraft],
    *,
    heuristic: Literal["euclidean", "grid"],
    max_expansions: int,
) -> Conflict | None:
    """First conflict blocking aircraft ``placement``'s drive-OUT through the
    door against ``placed``, else None (the aircraft analogue of
    :func:`egress_first_conflict`).

    By Reeds-Shepp reversibility (ADR-0010) an egress (slot -> out the door) is
    feasible iff an entry (door-cone -> slot) path exists against ``placed``, so
    this routes the aircraft as a mover via :func:`plan_path` against the supplied
    partial scene (the mover is excluded from ``placed`` by the caller;
    :func:`path_first_conflict` re-injects it per sample). The mover keeps its
    parked ``on_carts`` mode. Closed-form, RNG-free => deterministic (ADR-0003).
    Returns a ``teardown_egress`` :class:`~hangarfit.models.Conflict` when
    blocked. This is the per-body seam :func:`reverse_teardown_probe` peels on.

    The Conflict detail records the bail **mode** — ``[budget-exhausted]`` (hit the
    expansion cap; a path may exist beyond it — a search-*efficiency* signal) vs
    ``[space-exhausted]`` (the open set drained; genuinely no path at this
    discretization — a *lock* signal) — read from :func:`plan_path`'s ``stats``
    out-param, so a STUCK core can self-certify which regime produced it rather
    than relying on eyeballed timings."""
    plane = fleet[placement.plane_id]
    cone = entry_poses(placement, hangar)
    stats: dict[str, object] = {}
    try:
        plan_path(
            plane,
            cone[0],
            Pose.from_placement(placement),
            hangar=hangar,
            placed=placed,
            mover_on_carts=placement.on_carts,
            entries=cone,
            heuristic=heuristic,
            max_expansions=max_expansions,
            stats=stats,
        )
        return None
    except NoFeasiblePlanError as exc:
        # Read BOTH flags rather than inferring space from "not budget": if a future
        # plan_path refactor ever raised without populating stats, default loudly to
        # "unknown-exhaustion" instead of silently mislabelling the bail as space.
        if stats.get("budget_exhausted"):
            mode = "budget-exhausted"
        elif stats.get("space_exhausted"):
            mode = "space-exhausted"
        else:
            mode = "unknown-exhaustion"
        return Conflict.single(
            kind="teardown_egress",
            plane=placement.plane_id,
            detail=(
                f"aircraft {placement.plane_id!r} cannot drive out the door against "
                f"the remaining parked bodies [{mode}]: {exc.conflict.detail}"
            ),
        )


def reverse_teardown_probe(
    target: Layout,
    *,
    heuristic: Literal["euclidean", "grid"] = "grid",
    max_expansions: int | None = None,
) -> TeardownProbeResult:
    """Whole-fill reverse-teardown feasibility probe (#667 Rung C). **Read-only
    diagnostic: no plan output, no data-model change, no production caller** — so
    every existing plan stays byte-identical (ADR-0003).

    Generalises :func:`egress_first_conflict` (one body, slot -> door) into a
    whole-fill teardown: greedily extract every **tow-routable aircraft** slot ->
    door against shrinking partial state, and report whether a full teardown order
    EXISTS. By ADR-0010 reversibility a teardown order is exactly a **monotone
    fill** order, so a CLEAR verdict means the Rung-B forward-fill wall is a pure
    *search-efficiency* limit (a monotone order exists at this grid + budget, the
    forward planner just can't find it cheaply), while a STUCK verdict identifies
    the mutually-blocking core that no monotone order can seat **at this grid +
    budget** — pointing at the relocation (move-aside, Rung E) rather than better
    search (the per-body ``blocking`` conflict's ``[budget-exhausted]`` vs
    ``[space-exhausted]`` tag says whether a body might still route with a bigger —
    if unaffordable — budget, or is genuinely wedged at this discretization).

    **Modelling.** Only tow-routable aircraft are extracted; hand-placed (dolly)
    bodies and all ground objects stay as fixed obstacles in every partial state
    (they go in/out by hand, never towed) — the faithful dual of the Rung-A
    forward fill, which keeps the same keep-outs and routes the same towable set.

    **Why greedy peel is complete (not a heuristic).** For *ideal* (unbounded)
    egress feasibility, feasibility is *monotone in obstacles*: a body that can
    drive out past a set of obstacles can drive out past any subset (removing
    bodies only opens paths). So repeatedly removing *every* currently-egressable
    body reaches the empty set iff some full teardown order exists, and the residual
    it stalls at is the unique order-independent mutually-blocking core. (Proof of
    the contrapositive: if a teardown order existed but the peel stalled at residual
    R, take the earliest order-body in R; when the order removed it, the
    still-parked set was a superset of R, so it egressed against a superset of R
    minus itself — hence also against R minus itself, contradicting the stall.)
    The probe evaluates feasibility with a *finite* per-plane budget, under which
    monotonicity is an approximation (freeing states can add ``f<=C*`` nodes and
    exhaust the cap), so the verdict is planner/budget-relative — "no monotone
    order findable at this grid + budget", apples-to-apples with the forward fill,
    not an unconditional existence disproof. Determinism: id-sorted iteration +
    RNG-free closed-form routing at a fixed per-plane budget (ADR-0003).

    ``max_expansions`` overrides the per-plane node-expansion budget (default
    :data:`_MAX_EXPANSIONS`, the same authoritative full budget
    :func:`egress_first_conflict` uses — deliberately *not* the globally-capped
    fill budget)."""
    budget = _MAX_EXPANSIONS if max_expansions is None else max_expansions
    # Fixed obstacles present in EVERY partial state (see "Modelling" above).
    # id-sorted for the same cross-process determinism reason as still_parked below.
    fixed = tuple(sorted((p for p in target.placements if p.hand_placed), key=lambda p: p.plane_id))
    extractable = {p.plane_id: p for p in target.placements if not p.hand_placed}

    order: list[str] = []
    remaining = set(extractable)
    blocking: tuple[Conflict, ...] = ()
    while remaining:
        round_conflicts: dict[str, Conflict] = {}
        egressable: list[str] = []
        for pid in sorted(remaining):
            # id-sorted (NOT set-iteration order): a set of plane-id strings
            # iterates in PYTHONHASHSEED-dependent order across processes, which
            # would make the blocking-conflict details non-byte-identical (ADR-0003).
            still_parked = tuple(extractable[q] for q in sorted(remaining) if q != pid)
            placed = Layout(
                fleet=target.fleet,
                hangar=target.hangar,
                placements=fixed + still_parked,
                maintenance_plane=target.maintenance_plane,
                ground_objects=target.ground_objects,
                ground_object_placements=target.ground_object_placements,
            )
            conflict = _aircraft_egress_conflict(
                extractable[pid],
                placed,
                target.hangar,
                target.fleet,
                heuristic=heuristic,
                max_expansions=budget,
            )
            if conflict is None:
                egressable.append(pid)
            else:
                round_conflicts[pid] = conflict
        if not egressable:
            # No remaining body can leave -> the canonical mutually-blocking core.
            # Every remaining body failed this round, so round_conflicts is total.
            blocking = tuple(round_conflicts[pid] for pid in sorted(remaining))
            break
        order.extend(egressable)  # egressable is already id-sorted
        remaining.difference_update(egressable)

    return TeardownProbeResult(
        order=tuple(order),
        stuck=tuple(sorted(remaining)),
        blocking=blocking,
    )


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


def plan_fill(
    target: Layout,
    *,
    heuristic: Literal["euclidean", "grid"] = "grid",
    max_expansions: int | None = None,
    max_total_expansions: int | None = None,
    max_backtracks: int | None = None,
    apron_dropped_out: list[ApronShallowDrop] | None = None,
    unroutable_movers: list[str] | None = None,
) -> MovesPlan:
    """Plan a collision-free entry order + per-plane path for an empty fill.

    ``heuristic`` selects the per-plane :func:`plan_path` heuristic. ``"grid"``
    (the default since #336) is the obstacle-aware free-space geodesic, which
    threads tight-clearance maneuvers in far fewer expansions than the
    straight-line ``"euclidean"`` (which ignores walls and floods the frontier);
    pass ``heuristic="euclidean"`` to opt out. ``max_expansions`` (``None`` ⇒ the
    module ``_MAX_EXPANSIONS`` per-plane budget) caps each plane's search.

    ``max_total_expansions`` (``None`` ⇒ the module ``_MAX_FILL_EXPANSIONS``) is
    a deterministic GLOBAL cap on the expansions summed across *every*
    :func:`plan_path` call in the whole fill — including the failed candidates of
    the greedy scan. A high per-plane budget is needed to *route* tight-feasible
    planes, but it makes an un-routable fill expensive to *disprove*
    (~per-plane × scan-retries); the global cap bounds that worst case so the
    planner bails in bounded time instead of running away, while a routable fill
    finishes well under it. RNG-free, so the cap is seed-deterministic (#336).

    ``apron_dropped_out`` is an optional diagnostics-only **out-param** (#503,
    mirroring how ``plan_path``'s ``stats`` out-dict works): when supplied, it is
    populated — in committed-move order — with an :class:`ApronShallowDrop` for
    each committed plane that towed via the ``y = 0`` door-line fallback despite an
    apron being set (``hangar.apron_depth_m > 0``), i.e. whose footprint was too
    deep for the apron so every apron start pose was filtered (it shows no
    slide-in). Each drop's ``min_depth_m`` is the plane's fore-aft footprint extent
    (:func:`_plane_fore_aft_length_m`) — a conservative sufficient depth to engage
    it, NOT the exact minimum. Purely observational: it is **never** read back into
    the :class:`MovesPlan`, so the plan stays byte-identical whether or not the
    list is passed (ADR-0003). ``None`` (the default) collects nothing. The caller
    (CLI / solver) emits the user-facing warning from this data, deduped.

    ``unroutable_movers`` is a second diagnostics-only **out-param** (#627/#612,
    same idiom): when supplied, it is populated — in committed-move order — with
    the id of each ground-object **mover** that could not be routed and so kept a
    best-effort ``Move(path=None)`` (ADR-0007 #197). Unlike an un-tow-routable
    aircraft, which :func:`plan_fill` *raises* for (the solver records it in
    ``diagnostics.unroutable_planes`` and the CLI warns), a None-path mover was
    previously silent. It is equally **plan-inert** — the mover's ``Move`` still
    carries ``path=None``, so the plan is byte-identical whether or not the list
    is passed. ``None`` collects nothing.

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

    Since #667 the scan is a deterministic greedy-first **backtracking** DFS over
    placement order (``_place_rest``): the first feasible candidate at each level is
    recursed first, so when the greedy back-first order already routes, the search
    returns that identical path untouched (byte-identical, ADR-0003); it backtracks
    the order only where a greedy commit deadlocks a later body — cases the old
    monotonic loop bailed on. The search is bounded by both ``max_total_expansions``
    (above) and ``max_backtracks`` (``None`` ⇒ the module ``_MAX_FILL_BACKTRACKS``),
    a separate deterministic ceiling on dead-end backtracks so a generous per-plane
    budget can't fund a runaway order search on an un-routable fill. Deterministic
    (ADR-0003): the order, the Hybrid-A* primitive fan, the cone grid, and the
    candidate scan are all RNG-free, so a given ``target`` always yields the same
    :class:`MovesPlan`. (A truly cyclic lock — no order routes — still bails; the
    move-aside that resolves those is tracked on #667.)
    """
    # #626: memoize body world-parts for the WHOLE fill. The static obstacle field
    # is rebuilt by every plan_path's _build_obstacles, and the greedy scan re-routes
    # the same bodies across iterations, so a fill-wide pose cache turns those
    # repeated fixed-pose rebuilds into hits — the #453 win movers previously
    # bypassed, and the root of the #604 routing congestion. Byte-identical
    # (ADR-0003: the cache returns the same immutable WorldPart list); nesting
    # inside an in-``solve`` scope is a no-op.
    with pose_cache_scope():
        return _plan_fill(
            target,
            heuristic=heuristic,
            max_expansions=max_expansions,
            max_total_expansions=max_total_expansions,
            max_backtracks=max_backtracks,
            apron_dropped_out=apron_dropped_out,
            unroutable_movers=unroutable_movers,
        )


def _plan_fill(
    target: Layout,
    *,
    heuristic: Literal["euclidean", "grid"] = "grid",
    max_expansions: int | None = None,
    max_total_expansions: int | None = None,
    max_backtracks: int | None = None,
    apron_dropped_out: list[ApronShallowDrop] | None = None,
    unroutable_movers: list[str] | None = None,
) -> MovesPlan:
    """Body of :func:`plan_fill`, run inside an active ``pose_cache_scope`` (#626)."""
    budget = _MAX_EXPANSIONS if max_expansions is None else max_expansions
    total_budget = _MAX_FILL_EXPANSIONS if max_total_expansions is None else max_total_expansions
    bt_cap = _MAX_FILL_BACKTRACKS if max_backtracks is None else max_backtracks
    total_used = 0
    backtracks_used = 0
    fleet = target.fleet
    hangar = target.hangar
    # #667 Stage 0: HAND-POSITIONED bodies (dolly-borne gliders) are parked by
    # hand, not tow-routed. They seed `placed` (so routed bodies treat them as
    # obstacles) and get a path-less at-rest move emitted first. With no
    # hand-placed body the partition is the whole fleet → `ordered` is unchanged
    # and `placed`/`moves` start empty, so the plan stays byte-identical.
    hand_placed_slots = back_first_order(tuple(p for p in target.placements if p.hand_placed))
    ordered = list(back_first_order(tuple(p for p in target.placements if not p.hand_placed)))

    # Fixed obstacles (e.g. the fuel trailer) are static keep-outs for BOTH
    # aircraft routes and mover routes. Empty when no ground objects => inert.
    fixed_obstacle_placements = tuple(
        gp
        for gp in target.ground_object_placements
        if target.ground_objects[gp.plane_id].object_class == "fixed_obstacle"
    )

    placed: list[Placement] = list(hand_placed_slots)
    moves: list[Move] = [Move(p.plane_id, Pose.from_placement(p), None) for p in hand_placed_slots]

    # #667 Stage 1 (order search): place the to-route bodies by a deterministic
    # BACKTRACKING DFS over order, greedy-first. The first feasible candidate at
    # each level is recursed first, so when the greedy back-first order already
    # works the search returns that identical path untouched (byte-identical,
    # ADR-0003); backtracking only happens where the old monotonic loop would have
    # bailed — a greedy commit that deadlocks a later body. The global expansion
    # budget bounds the search (expansions on dead branches are NOT refunded), so
    # it terminates and then raises the structured bail. deepest_conflict captures
    # the first (deepest plane's) routing conflict for that bail message.
    deepest_conflict: Conflict | None = None

    def _place_rest(
        placed_list: list[Placement], rest: list[Placement]
    ) -> list[tuple[Placement, DubinsArc, bool]] | None:
        """Return the chosen (slot, arc, apron_fallback) sequence that places every
        body in ``rest`` against ``placed_list``, greedy-first with backtracking, or
        ``None`` if no order does. Raises on global-budget or backtrack-cap exhaustion."""
        nonlocal total_used, deepest_conflict, backtracks_used
        if not rest:
            return []
        # `placed_list` is constant across candidates at this level; plan_path adds
        # the candidate mover per sample via its internal path_first_conflict check.
        placed_layout = Layout(
            fleet=fleet,
            hangar=hangar,
            placements=tuple(placed_list),
            maintenance_plane=target.maintenance_plane,
            ground_objects=target.ground_objects,
            ground_object_placements=fixed_obstacle_placements,
        )
        for idx, slot in enumerate(rest):
            remaining = total_budget - total_used
            if remaining <= 0:
                # Global fill-expansion budget exhausted (#336): bail in bounded
                # time. Name the deepest still-unplaced plane at this level.
                raise NoFeasiblePlanError(
                    rest[0].plane_id,
                    Conflict.single(
                        kind="no_feasible_path",
                        plane=rest[0].plane_id,
                        detail=f"global fill expansion budget ({total_budget}) exhausted",
                    ),
                )
            plane = fleet[slot.plane_id]
            stats: dict[str, object] = {}
            try:
                # The full door cone drives the multi-start search (#262). Compute
                # it once and pass it as `entries=`; the positional `entry` arg is
                # ignored by plan_path whenever `entries` is set. Each call is capped
                # at the smaller of the per-plane budget and the global remainder,
                # and its expansions are charged to the global total whether it
                # succeeds or fails (#336).
                cone = entry_poses(slot, hangar)
                arc = plan_path(
                    plane,
                    cone[0],
                    Pose.from_placement(slot),
                    hangar=hangar,
                    placed=placed_layout,
                    mover_on_carts=slot.on_carts,
                    entries=cone,
                    heuristic=heuristic,
                    max_expansions=min(budget, remaining),
                    stats=stats,
                )
            except NoFeasiblePlanError as exc:
                # Cannot route this body against the current obstacles; try the next
                # candidate. Remember its conflict for the bail message.
                exp = stats.get("expansions", 0)
                total_used += exp if isinstance(exp, int) else 0
                if deepest_conflict is None:
                    deepest_conflict = exc.conflict
                continue
            exp = stats.get("expansions", 0)
            total_used += exp if isinstance(exp, int) else 0
            apron_fb = stats.get("apron_fallback") is True
            sub = _place_rest(placed_list + [slot], rest[:idx] + rest[idx + 1 :])
            if sub is not None:
                return [(slot, arc, apron_fb), *sub]
            # Routed here, but the rest dead-ends downstream → backtrack to the next
            # candidate (the old monotonic loop could not). Bound the total dead-end
            # backtracks so a generous per-plane budget can't fund a runaway order
            # search on an un-routable fill (#667); 0 ⇒ no backtracking allowed.
            backtracks_used += 1
            if backtracks_used > bt_cap:
                # Surface the real per-body routing conflict (the actionable reason a
                # body could not be seated) and name THAT body — not the already-placed
                # deepest plane (#668 review). Synthesize a cap conflict only if nothing
                # ever failed to route (a pure ordering lock with all routes feasible).
                bail = deepest_conflict or Conflict.single(
                    kind="no_feasible_path",
                    plane=rest[0].plane_id,
                    detail=f"order-search backtracking cap ({bt_cap}) exceeded",
                )
                raise NoFeasiblePlanError(bail.planes[0], bail)
        return None

    result = _place_rest(placed, ordered)
    if result is None:
        # No placement order seats every body (a true lock — needs the Stage 1
        # move-aside, still to come) or every candidate conflicts. Name the
        # deepest body and carry its conflict (matches the old monotonic bail).
        bail = deepest_conflict or Conflict.single(
            kind="no_feasible_path",
            plane=ordered[0].plane_id,
            detail="no feasible tow order (every placement order deadlocks)",
        )
        # Name the conflict's own body so the named plane and the carried conflict
        # always agree (#668 review): on a real routing failure that's the stuck
        # body; on a pure ordering lock it's the deepest (the synthesized fallback).
        raise NoFeasiblePlanError(bail.planes[0], bail)

    for slot, arc, apron_fb in result:
        moves.append(Move(slot.plane_id, Pose.from_placement(slot), arc))
        placed.append(slot)
        # #503: record (plan-inert) the planes that towed via the y=0 door-line
        # fallback DESPITE an apron being set — their footprint is too deep for the
        # apron. Committed-move order (deterministic, ADR-0003), only when the
        # caller passed `apron_dropped_out`; the depth is the per-plane FOOTPRINT
        # extent, not the fleet-wide `auto` over-margin. Never read back.
        if apron_fb and apron_dropped_out is not None:
            apron_dropped_out.append(
                ApronShallowDrop(
                    plane_id=slot.plane_id,
                    min_depth_m=_plane_fore_aft_length_m(fleet[slot.plane_id]),
                )
            )

    # Ground-object movers (#602): route each placed-routed mover with its own
    # path. id-sorted + appended after the aircraft loop => aircraft moves stay
    # byte-identical (no ground objects => this loop is empty). Each mover routes
    # against all parked aircraft + fixed obstacles + movers already routed this
    # pass; the one being routed is excluded from `placed` (path_first_conflict
    # re-injects it per sample, matching the aircraft routing contract).
    routed_mover_placements: list[Placement] = []
    for gp in sorted(target.ground_object_placements, key=lambda p: p.plane_id):
        obj = target.ground_objects[gp.plane_id]
        if obj.object_class != "placed_routed_mover":
            continue
        mover_placed = Layout(
            fleet=fleet,
            hangar=hangar,
            placements=tuple(placed),
            maintenance_plane=target.maintenance_plane,
            ground_objects=target.ground_objects,
            ground_object_placements=(*fixed_obstacle_placements, *routed_mover_placements),
        )
        cone = entry_poses(gp, hangar)
        mover_stats: dict[str, object] = {}
        # Budget-exhausted (remaining <= 0) routes with a near-zero search budget
        # (1 expansion) so the node-expansion search bails immediately. The mover
        # can STILL succeed if a clean closed-form analytic shot from a door-cone
        # start exists (that path runs before the expansion cap); otherwise it is
        # best-effort-skipped (path=None below). Unlike the aircraft scan, which
        # RAISES on exhaustion, movers are best-effort (ADR-0007 #197), so an
        # exhausted budget must never abort the whole plan.
        remaining = total_budget - total_used
        mover_arc: DubinsArc | None
        try:
            mover_arc = plan_path(
                obj,
                cone[0],
                Pose.from_placement(gp),
                hangar=hangar,
                placed=mover_placed,
                mover_on_carts=False,
                entries=cone,
                heuristic=heuristic,
                max_expansions=min(budget, remaining) if remaining > 0 else 1,
                stats=mover_stats,
            )
        except NoFeasiblePlanError:
            # Best-effort (ADR-0007 #197): an unroutable mover keeps a None path so
            # it never aborts the whole fill. Unlike an un-tow-routable AIRCRAFT
            # (which raises -> solver -> stderr), a None-path mover used to be
            # silent. #627/#612: record its id in the optional ``unroutable_movers``
            # out-param so the solver/CLI can name it on stderr — observational,
            # plan-inert (the Move below still carries path=None), so byte-identical
            # whether or not the list is passed (mirrors ``apron_dropped_out``).
            mover_arc = None
            if unroutable_movers is not None:
                unroutable_movers.append(obj.id)
        exp = mover_stats.get("expansions", 0)
        total_used += exp if isinstance(exp, int) else 0
        moves.append(Move(gp.plane_id, Pose.from_placement(gp), path=mover_arc))
        routed_mover_placements.append(gp)

    return MovesPlan(target_layout=target, moves=tuple(moves))


# ── Hybrid-A* tow-path search (spike Q3 v2, #222) ───────────────────────────
# Deterministic search over Reeds–Shepp motion primitives (ADR-0010). Tuning
# constants; see the design spec. All RNG-free (ADR-0003).
_GRID_XY_M = 0.5  # (x, y) cell size for state binning
_GRID_DEG = 15.0  # heading cell size; 360 / 15 = 24 heading bins
_HEADING_BINS = round(360.0 / _GRID_DEG)
_TURN_PENALTY = 0.1  # per-radian g-cost penalty to prefer straighter paths
_MAX_EXPANSIONS = 8000  # node-expansion budget per plane before bailing (#336).
# Raised 2000 → 8000 when the GRID heuristic became the plan_fill default (#336).
# The earlier euclidean sweep concluded aviat_husky (first in back-first order at
# seed=1) was "un-routable even at 4000 — genuine tight-geometry exhaustion."
# That was a HEURISTIC artifact, NOT infeasibility: euclidean ignores walls and
# floods the frontier, needing ~13.5k expansions, whereas the obstacle-aware grid
# heuristic threaded the same maneuver in ~6062 with grid-default + #336 budgets.
#
# #512 — the ~6062 figure is PRE-#480. #480's fewest-moves cost model (an additive
# CUSP_PENALTY per direction reversal) roughly DOUBLED the per-plane expansion cost
# of the deep, heading-hard slots: aviat_husky now needs ~12515 (measured), and
# ctsl — cheap pre-#480 — exceeds even a 13000 cap. So the seed=1 spread=False
# six-plane fill is NO LONGER fully tow-routable at these budgets; routing it would
# need per-plane ~20k + global ~35k, pushing the un-routable-disprove wall-clock
# (the very thing _MAX_FILL_EXPANSIONS bounds) past the ~400 s perf intent. That is
# an ACCEPTED realism-over-routability trade of #480 (ADR-0010), NOT a budget that
# wants raising — see test_six_plane_fresh_fill_partial_routing_post_480 and #512.
# The budgets are kept at 8000/16000 so an un-routable fill still disproves fast.
# (The ~12515/~13000 figures are indicative, not asserted — re-measure after any
# CUSP_PENALTY or fixture-geometry change; the binding contract is that test's
# qualitative best-effort assertion, not these numbers.)
#
# Deterministic (machine-independent) bound on worst-case per-plane search cost.
# Budget exhaustion can still report a genuinely-feasible-but-hard layout as
# NoFeasiblePlanError (a false negative) — accepted v2 tradeoff; retune once real
# (measured) hangar geometry lands. Overridable per-invocation via the
# ``--tow-max-expansions`` CLI flag and the ``tow_max_expansions`` param on
# ``solve()`` / ``plan_fill()`` (#332).

_MAX_FILL_EXPANSIONS = 16000  # GLOBAL expansion budget across one whole fill (#336).
# Bounds the worst-case wall-clock of an UN-routable fill. The higher per-plane
# budget above is needed to ROUTE tight-but-feasible planes, but it makes an
# un-routable fill expensive to disprove: the greedy back-first scan retries the
# remaining planes, each burning up to the per-plane budget, so cost is
# ~per-plane × scan-retries (the default spread=True six-plane fill measured
# ~997 s at per-plane 8000 with NO global cap). Capping the SUM of expansions
# across every plan_path call — failed candidates included — bounds that: the
# same fill bails at the cap in ~334 s (< the 400 s perf gate), while a routable
# fill (seed=1: ~8.4k total) finishes well under it. Deterministic / RNG-free.
# A staging apron (#412) quadruples the per-plane start set (15 → up to 60 cone
# poses: forward+reverse headings × the apron y-samples), so the same un-routable
# six-plane disprove rises to ~346 s — still under the 400 s gate, and the bound
# that stays deterministic is the expansion COUNT, not the wall-clock. Re-tune the
# budget VALUE (not the count) with the profiling harness if a real apron site
# needs it; bound a hard apron fill per-run with ``--tow-max-expansions``.
# Overridable via the ``max_total_expansions`` param on ``plan_fill()``.

_MAX_FILL_BACKTRACKS = 2000  # GLOBAL cap on order-search dead-end backtracks (#667).
# The Stage-1 order search (`_place_rest`) backtracks the placement ORDER when a
# greedy commit deadlocks a later body. The expansion budget above is the primary
# bound, but a generous PER-PLANE budget (needed to route tight-feasible planes)
# also funds deep order-backtracking on an UN-routable fill — so this is a separate,
# deterministic secondary ceiling on the number of dead-end backtracks, independent
# of the per-plane budget. Inert for the greedy-success path (0 backtracks ⇒
# byte-identical, ADR-0003) and far above any legitimate small reorder; it only
# bounds a pathological/un-routable order search. Overridable via ``max_backtracks``.
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


def _primitives(turn_radius_m: float, *, lateral: bool = False) -> tuple[Segment, ...]:
    """The fixed motion-primitive fan, in deterministic order (ADR-0010 v2).

    Own-gear (``r > 0``): **six primitives** in fixed order ``Lf, Sf, Rf, Lr,
    Sr, Rr`` — the three forward steerings (left arc, straight, right arc) then
    the same three in reverse (gear ``-1``). The reverse primitives are the
    Reeds–Shepp extension: the search can now back up to reorient instead of
    driving a full turning-circle loop. Fixed order ⇒ deterministic expansion
    (ADR-0003).

    Own-gear (``r > 0``): each arc/straight is ``step`` metres, chosen so a turn
    changes heading by ~one heading cell. Zero-radius (``r == 0``): a reverse
    PIVOT rotates heading the same way as the forward pivot of the *opposite*
    steering (``pose_at`` holds position fixed and flips the heading delta for a
    reverse leg), so ``Lr``/``Rr`` are exact duplicates of ``Rf``/``Lf`` and
    would only ever lose the ``best_g`` race to their cheaper forward twin — pure
    dead work. They are therefore omitted: the zero-radius fan is ``Lf, Sf, Rf,
    Sr`` (in-place pivots + straight, plus the genuinely-new **reverse straight**
    that backs up).

    ``lateral`` (#599 / ADR-0010) appends the two **strafe** primitives ``Tf,
    Tr`` (a slide perpendicular to the heading, either side) to the zero-radius
    fan — emitted ONLY for a plane on a dolly/cart (``mover_on_carts``). A
    free-swivel / pivot-in-place plane (a castering tailwheel taildragger) is
    ``r == 0`` too but is towed on its wheels, which roll fore/aft only — it
    **cannot strafe** — so it gets ``lateral=False`` and the pivot+straight fan.
    The strafes are listed LAST so an existing pivot/straight path still wins a
    cost tie (minimal byte-identity churn). Fixed order ⇒ deterministic
    expansion (ADR-0003).
    """
    if turn_radius_m == 0.0:
        dtheta = math.radians(_GRID_DEG)
        base = (
            Segment("L", dtheta),
            Segment("S", _GRID_XY_M),
            Segment("R", dtheta),
            Segment("S", _GRID_XY_M, gear=-1),
        )
        if not lateral:
            return base  # pivot-in-place only (free-swivel): no strafe.
        return base + (Segment("T", _GRID_XY_M), Segment("T", _GRID_XY_M, gear=-1))
    step = max(_GRID_XY_M, turn_radius_m * math.radians(_GRID_DEG))
    return (
        Segment("L", step),
        Segment("S", step),
        Segment("R", step),
        Segment("L", step, gear=-1),
        Segment("S", step, gear=-1),
        Segment("R", step, gear=-1),
    )


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

    Gear-agnostic (#480): a reverse leg costs the same per-metre as a forward
    leg — reverse is no longer taxed here. Direction changes are charged once, as
    an additive :data:`CUSP_PENALTY` per cusp, in the expansion loop (which knows
    the parent's travel direction); a single segment has no cusp. Forward
    preference survives as the fixed primitive-order tie-break."""
    if seg.kind == "S" or seg.kind == "T":
        # "S" along heading, "T" lateral (#599): both pure translations, metres.
        return seg.length_m
    if turn_radius_m > 0.0:
        return seg.length_m + _TURN_PENALTY * (seg.length_m / turn_radius_m)
    return _TURN_PENALTY * seg.length_m  # cart pivot: length_m is radians


def _segments_cost(segs: tuple[Segment, ...], turn_radius_m: float) -> float:
    """Fewest-moves cost of a complete segment run (#480): Σ ``_seg_cost`` +
    ``CUSP_PENALTY`` per cusp. Used to rank start-seed analytic completions so the
    cheapest collision-clean one wins — e.g. a nose-out slot's reverse back-in
    (short, 0 cusps) beats a forward entry that pirouettes inside. A standalone
    run has no prior travel, so there is no boundary cusp to charge."""
    legs: list[tuple[int, bool]] = [
        (seg.gear, not (turn_radius_m == 0.0 and seg.kind in ("L", "R"))) for seg in segs
    ]
    seg_sum = math.fsum(_seg_cost(seg, turn_radius_m) for seg in segs)
    return seg_sum + CUSP_PENALTY * _count_cusps(legs)


def _cell(pose: Pose) -> tuple[int, int, int]:
    """Bin a pose into the search grid: ``(x, y)`` rounded to ``_GRID_XY_M`` and
    heading rounded to ``_GRID_DEG`` (wrapped into ``_HEADING_BINS``).

    Deliberately does NOT include the travel direction (#480 ``last_drive_gear``),
    so the ``best_g`` domination check may occasionally prune a higher-g node
    whose gear would have avoided a downstream cusp — an accepted Hybrid-A\\*
    approximation (same spirit as the pose-binning), kept to bound the state
    space / expansion budget. The dominant nose-out win comes from the
    rear-entry seed (a near-0-cusp reverse approach handled by the cost-aware
    start-seed pre-pass in :func:`plan_path`), not mid-search gear switches, so
    the measured nose-out cases do not exercise this approximation."""
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
    both — :func:`_reconstruct_segments` relies on this to stop at the root.

    ``last_drive_gear`` (#480) is the gear (+1/−1) of the most recent TRANSLATING
    leg on this node's branch, or ``0`` at the root (no travel yet). The
    expansion loop reads it to charge a :data:`CUSP_PENALTY` when the next
    translating primitive reverses direction — counting cusps incrementally
    without walking the parent chain. A non-translating cart pivot inherits the
    parent's value unchanged."""

    pose: Pose
    g: float
    seg: Segment | None
    parent: _SearchNode | None
    last_drive_gear: int


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
    parts, the (optional) maintenance-bay keep-out rectangle, and the always-on
    structural notch keep-outs (ADR-0018).

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
    # Structural notch keep-out boxes (Shapely), built once from
    # ``hangar.structural_notches``. Empty for the common rectangular hangar, in
    # which case every notch check below is a no-op (byte-identical to pre-notch
    # routing). Unlike the bay these are ALWAYS on — there is no state gate.
    notch_boxes: tuple[Polygon, ...] = ()

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

    The structural notch keep-outs (ADR-0018) are built here too, once, from
    ``hangar.structural_notches`` — always on (no state gate), and empty (a no-op
    downstream) for the common rectangular hangar.
    """
    parts: list[WorldPart] = []
    for placement in placed.placements:
        if placement.plane_id == mover_id:
            continue
        parts.extend(cached_parts_world(placed.fleet[placement.plane_id], placement))
    # Ground objects (#601): fixed obstacles are ALWAYS static keep-outs; placed
    # movers are static placed bodies EXCEPT the one currently being routed
    # (mover_id), mirroring how the routed aircraft is excluded above. Iterate in
    # id-sorted order so the world_parts tuple order is deterministic regardless
    # of the placements' authored order (determinism-guard, ADR-0003). Empty when
    # there are no ground objects → byte-identical to the pre-#601 obstacle set.
    for gp in sorted(placed.ground_object_placements, key=lambda p: p.plane_id):
        if gp.plane_id == mover_id:
            continue
        obj = placed.ground_objects[gp.plane_id]
        # Pose-memoized (#626): a static mover obstacle's fixed-pose world parts are
        # rebuilt by every plan_path's _build_obstacles otherwise — the #453 churn
        # movers bypassed, and the root of the #604 routing congestion.
        parts.extend(cached_parts_world(obj, gp))
    bay = placed.hangar.maintenance_bay
    return _Obstacles(
        world_parts=tuple(parts),
        world_part_aabbs=tuple(_aabb(p.polygon) for p in parts),
        bay_xmin=bay.center_x_m - bay.width_m / 2,
        bay_xmax=bay.center_x_m + bay.width_m / 2,
        bay_ymin=placed.hangar.length_m - bay.depth_m,
        bay_active=placed.maintenance_plane is not None,
        notch_boxes=tuple(
            box(n.x_min_m, n.y_min_m, n.x_max_m, n.y_max_m)
            for n in placed.hangar.structural_notches
        ),
    )


def _aabb(poly: Polygon) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a polygon as ``(xmin, ymin, xmax, ymax)``.

    A cheap plan-view pre-filter: two parts whose AABBs are separated by more
    than the clearance cannot conflict, so the (relatively costly) exact
    polygon predicate is skipped for them. Uses Shapely's ``bounds``.
    """
    xmin, ymin, xmax, ymax = poly.bounds
    return xmin, ymin, xmax, ymax


def _motion_clear(
    mover: Aircraft | GroundObject, pose: Pose, obstacles: _Obstacles, hangar: Hangar
) -> bool:
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

    (D) **Structural notch** — the mover may not overhang an always-on floor
        keep-out (ADR-0018). Polygon overlap (not per-vertex) so an edge crossing
        a notch with no vertex inside is caught, matching the static
        :func:`collisions._hangar_bounds_conflicts` ``floor.covers`` test. No-op
        when ``hangar.structural_notches`` is empty.

    ``on_carts`` is fixed to ``False`` here: :func:`~hangarfit.geometry.aircraft_parts_world`
    derives part geometry from pose only (``x``/``y``/``heading``) and the parts,
    NOT from ``on_carts``, so the part polygons are identical to the oracle's
    regardless of cart state. (Verified by reading ``aircraft_parts_world``.)

    The exact oracle remains the authority on the final returned path — see the
    module note above and the safety-net re-check in :func:`plan_path`.
    """
    placement = Placement(mover.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=False)
    # Pose-memoized for any placeable body (#626): an Aircraft or a GroundObject
    # mover both reuse cached_parts_world. NOTE the TOWED mover's pose changes per
    # sampled expansion, so it mostly MISSES here (expected) — the cache win is the
    # static obstacle field (_build_obstacles), memoized across the fill.
    mover_parts = cached_parts_world(mover, placement)

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

        # (D) Structural notch (always-on floor keep-out, ADR-0018): the mover
        # may not overhang a notch. Polygon overlap (intersects-and-not-touches —
        # the same boundary-inclusive predicate the static floor uses) so a part
        # EDGE crossing a notch with no vertex inside is caught too, and a part
        # flush against a notch wall stays clear. No-op when there are no notches.
        for nb in obstacles.notch_boxes:
            if polygon_overlap(mp.polygon, nb):
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


# ── Obstacle-aware grid heuristic (towplanner-v2 routability spike, #332) ────
#
# A goal-aware cost-to-go field that REPLACES the straight-line Euclidean A*
# heuristic when ``plan_path(..., heuristic="grid")``. The default planner is
# UNCHANGED — ``heuristic="euclidean"`` (the default) computes the same
# ``math.hypot`` heuristic byte-for-byte, so the ADR-0003 determinism canaries
# are untouched.
#
# Motivation (the documented multi-plane-fill un-routability): the Euclidean
# heuristic ignores obstacles, so when a goal sits behind an already-placed
# plane the Hybrid-A* search floods the obstacle pocket and exhausts
# ``_MAX_EXPANSIONS`` before routing around it. This field is the FREE-SPACE
# GEODESIC distance from every grid cell to the goal, computed by a
# deterministic Dijkstra over the SAME ``_GRID_XY_M`` occupancy grid the search
# bins poses into — so the heuristic "knows the way around" and the search
# beelines along the real corridor instead of flooding the dead pocket.
#
# Point-robot, un-inflated obstacles: a point can always take a route the
# finite-width plane can (never fewer metres), so the field is a LOWER BOUND on
# the true Reeds–Shepp path cost and stays admissible-leaning; the exact oracle
# in ``plan_path`` remains the SOLE authority on the returned path's validity
# (the #332 proposer-verifier contract). Deterministic (ADR-0003): fixed cell
# iteration order + a monotonic-counter Dijkstra tie-break, RNG-free.

# How far in front of the door (the y<0 apron, open during tow per #222) the
# field extends, so entry/approach poses at small negative y still get a
# geodesic value rather than the Euclidean fallback.
_GRID_H_Y_PAD_M = 6.0


def _build_grid_heuristic(
    goal: Pose, obstacles: _Obstacles, hangar: Hangar
) -> dict[tuple[int, int], float]:
    """Free-space geodesic cost-to-go to ``goal`` over the search grid (#332).

    A deterministic Dijkstra (8-connected, true Euclidean edge weights) from the
    goal cell across every in-bounds, obstacle-free cell. Returns a mapping from
    ``(ix, iy)`` cell (``round(x/_GRID_XY_M), round(y/_GRID_XY_M)`` — the same xy
    binning as :func:`_cell`) to metres-to-goal. Cells absent from the map are
    blocked or unreachable; :func:`plan_path` falls back to the Euclidean
    heuristic there, so the field only ever RE-PRIORITISES the frontier, it
    never makes a pose un-expandable.

    Obstacles are the placed planes' footprints (un-inflated, point-robot), the
    closed maintenance bay, and the always-on structural notch(es) (ADR-0018);
    the side/back walls bound the grid (the front ``y < 0`` apron is open during
    tow). RNG-free and pure ⇒ ADR-0003-safe.

    The southward extent reconciles the historic fixed ``_GRID_H_Y_PAD_M = 6 m``
    pad with the site's staging apron (``hangar.apron_depth_m``, #412/ADR-0021):
    the field reaches ``max(_GRID_H_Y_PAD_M, apron_depth_m)`` south of the door,
    so a deep apron is covered while a no-apron (or shallow-apron ≤ 6 m) site
    keeps the historic ``-12``-row floor byte-for-byte. ``apron_depth_m`` is
    bounded, so the grid stays finite and deterministic.
    """
    ix_max = round(hangar.width_m / _GRID_XY_M)
    iy_min = -round(max(_GRID_H_Y_PAD_M, hangar.apron_depth_m) / _GRID_XY_M)
    iy_max = round(hangar.length_m / _GRID_XY_M)

    # Union the static obstacle footprints once for fast point-in-region tests.
    blocked_geom = (
        union_all([wp.polygon for wp in obstacles.world_parts]) if (obstacles.world_parts) else None
    )

    def _cell_free(ix: int, iy: int) -> bool:
        cx = ix * _GRID_XY_M
        cy = iy * _GRID_XY_M
        # Side/back walls (front y<0 is the open apron, #222). Mirrors the
        # _mover_motion_bounds_conflict spirit: 0<=x<=width, y<=length.
        if cx < 0.0 or cx > hangar.width_m or cy > hangar.length_m:
            return False
        # Closed maintenance-bay keep-out (same predicate as _motion_clear (C)).
        if obstacles.bay_active and (
            obstacles.bay_xmin < cx < obstacles.bay_xmax and cy > obstacles.bay_ymin
        ):
            return False
        # Structural notch keep-out (always on, ADR-0018): route the geodesic
        # around the dead pocket. A cell whose centre is in a notch interior is
        # blocked (boundary cells stay free — the notch edge is floor). No-op
        # when there are no notches.
        if any(nb.contains(Point(cx, cy)) for nb in obstacles.notch_boxes):
            return False
        return blocked_geom is None or not blocked_geom.contains(Point(cx, cy))

    # Seed the goal cell at 0 unconditionally — it is the target (a valid resting
    # placement clear of obstacles by clearance), so its reference point is free
    # regardless of how the coarse raster rounds it.
    goal_cell = (round(goal.x_m / _GRID_XY_M), round(goal.y_m / _GRID_XY_M))
    dist: dict[tuple[int, int], float] = {goal_cell: 0.0}
    counter = 0
    heap: list[tuple[float, int, tuple[int, int]]] = [(0.0, counter, goal_cell)]
    diag = _GRID_XY_M * math.sqrt(2.0)
    # 8-neighbour offsets in a FIXED order (determinism, ADR-0003).
    neighbours = (
        (1, 0, _GRID_XY_M),
        (-1, 0, _GRID_XY_M),
        (0, 1, _GRID_XY_M),
        (0, -1, _GRID_XY_M),
        (1, 1, diag),
        (1, -1, diag),
        (-1, 1, diag),
        (-1, -1, diag),
    )
    while heap:
        d, _, (ix, iy) = heapq.heappop(heap)
        if d > dist.get((ix, iy), math.inf) + 1e-12:
            continue  # stale heap entry
        for dx, dy, w in neighbours:
            nx, ny = ix + dx, iy + dy
            if nx < 0 or nx > ix_max or ny < iy_min or ny > iy_max:
                continue
            if not _cell_free(nx, ny):
                continue
            nd = d + w
            if nd < dist.get((nx, ny), math.inf) - 1e-12:
                dist[(nx, ny)] = nd
                counter += 1
                heapq.heappush(heap, (nd, counter, (nx, ny)))
    return dist


def plan_path(
    mover: Aircraft | GroundObject,
    entry: Pose,
    goal: Pose,
    *,
    hangar: Hangar,
    placed: Layout,
    mover_on_carts: bool,
    entries: tuple[Pose, ...] | None = None,
    max_expansions: int = _MAX_EXPANSIONS,
    heuristic: Literal["euclidean", "grid"] = "euclidean",
    heuristic_fn: Callable[[Pose], float] | None = None,
    stats: dict[str, object] | None = None,
) -> DubinsArc:
    """Deterministic Hybrid-A* tow path from ``entry`` (or ``entries``) to ``goal``.

    **Single-start mode** (``entries=None``, the default / backward-compatible
    behaviour): seeds the search with the single ``entry`` pose at ``g = 0``.

    **Multi-start / door-cone mode** (``entries`` provided, #262): seeds the
    search frontier with every *surviving* start pose from the cone — a pose
    survives iff its footprint at the front boundary does not clip the side or
    back walls AND any ``y < 0`` protrusion stays within the door opening
    (:func:`_mover_motion_bounds_conflict`; the front-gap exemption is now
    door-gated, #411).  If ALL candidates are filtered out, the fallback is the
    door-centre straight-in pose so the search always has at least one start —
    which may itself be infeasible (e.g. a plane wider than the door), in which
    case no valid path is found and the caller raises
    :class:`NoFeasiblePlanError`.  Each surviving start is enqueued at ``g = 0`` with its
    own Euclidean heuristic; A* then naturally expands the most-promising start
    first and returns the best total path across the whole cone.  The
    ``DubinsArc.start`` of the returned arc is the cone pose that *won* (from
    :func:`_root_pose`), not necessarily ``entry``.

    Searches continuous ``(x, y, heading)`` with the fixed six-primitive fan
    (:func:`_primitives` — forward L/S/R then reverse L/S/R, ADR-0010),
    grid-binned via :func:`_cell`, an admissible Euclidean heuristic, and an
    analytic-expansion shortcut: at every popped node a direct
    :func:`plan_reeds_shepp` shot to ``goal`` is tried first, so an unobstructed
    plane finishes in one arc. Per-edge and analytic-shot validity during search use
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

    ``hangar`` is consumed directly by :func:`_motion_clear` (bounds, clearances,
    bay rectangle); ``placed.hangar`` is the same object and also reaches the
    exact-oracle safety net via :func:`path_first_conflict`.

    ``heuristic`` selects the A* cost-to-go estimate (towplanner-v2 spike, #332):
    ``"euclidean"`` (default) is the byte-identical straight-line lower bound the
    planner has always used; ``"grid"`` swaps in the obstacle-aware free-space
    geodesic field (:func:`_build_grid_heuristic`) that guides the search around
    placed planes instead of flooding the dead pocket in front of them. Only the
    node-expansion ORDER changes — the motion primitives, the validity checks,
    and the exact-oracle safety net are untouched, so a ``"grid"`` path is just
    as exact-oracle-clean as a ``"euclidean"`` one. Both modes are deterministic
    (ADR-0003): the grid Dijkstra is RNG-free with a monotonic-counter tie-break.
    ``heuristic_fn``, when not ``None``, overrides the cost-to-go callable
    selected by ``heuristic`` — intended for dev/test probes only (#840); passing
    ``None`` (the default) is byte-identical to omitting the parameter (ADR-0003).

    ``stats`` is an optional out-parameter (diagnostics only; ``None`` ⇒ no-op,
    no behaviour change): on return/raise it is populated with ``expansions``,
    ``found``, ``budget_exhausted`` (hit ``max_expansions``) vs ``space_exhausted``
    (open heap emptied first ⇒ genuine local infeasibility), ``start_poses``, and
    the ``heuristic`` used — the #332 routability characterisation harness reads it.
    It additionally carries ``apron_fallback=True`` (#503) when the site has an
    apron (``hangar.apron_depth_m > 0``) but every apron start pose was filtered,
    so this plane fell back to the ``y = 0`` door line and shows no slide-in. The
    key is set ONLY in that case (absent otherwise), and is purely observational —
    it never affects the returned :class:`DubinsArc` or the plan (ADR-0003).
    """
    r = mover.effective_turn_radius_m()
    # Static obstacle set, computed once: placed planes don't move while this one
    # is routed. Drives the fast per-pose `_motion_clear` used during search.
    obstacles = _build_obstacles(placed, mover_id=mover.id)
    # #643: the fast in-search ``_motion_clear`` screen clears the mover against
    # parked bodies at the (tighter) MOTION clearance, matching the exact
    # ``path_first_conflict`` oracle (which converts internally). The grid
    # heuristic and obstacle footprints are clearance-free (bounds/geometry
    # only), so they stay on the parked ``hangar``. ``motion_hangar()`` is the
    # parked hangar itself when no motion clearance is set ⇒ byte-identical
    # (ADR-0003).
    motion_hangar = hangar.motion_hangar()

    # A* heuristic seam (#332). ``euclidean`` (default) is the byte-identical
    # straight-line lower bound the planner has always used. ``grid`` swaps in
    # the obstacle-aware free-space geodesic field, falling back to Euclidean on
    # any cell outside the field (blocked / off-grid). The default branch's
    # expression is unchanged so the determinism canaries stay byte-identical.
    _h: Callable[[Pose], float]
    if heuristic == "grid":
        _field = _build_grid_heuristic(goal, obstacles, hangar)

        def _grid_h(p: Pose) -> float:
            cell = (round(p.x_m / _GRID_XY_M), round(p.y_m / _GRID_XY_M))
            g = _field.get(cell)
            if g is None:
                return math.hypot(goal.x_m - p.x_m, goal.y_m - p.y_m)
            return g

        _h = _grid_h
    else:

        def _euclid_h(p: Pose) -> float:
            return math.hypot(goal.x_m - p.x_m, goal.y_m - p.y_m)

        _h = _euclid_h

    # Generic dev/test-only cost-to-go injection seam (#840). An explicit
    # heuristic_fn overrides the `_h` cost-to-go estimate for an experiment that
    # needs a custom heuristic without monkeypatching this function's internals.
    # There is intentionally NO production caller: it was added for the #840
    # heading-aware-heuristic headroom probe (which measured NO-GO — the heuristic
    # class is dead for the fk9↔cessna nook, see docs/spikes/
    # herrenteich-fk9-cessna-lateral-shuffle.md), and is retained as a generic seam
    # for future heuristic experiments (its only consumer is
    # bench/se2_heuristic_probe.py + tests/test_towplanner_heuristic_fn.py). Default
    # None ⇒ the `heuristic` Literal's `_h` above is used unchanged ⇒ byte-identical
    # (ADR-0003): the determinism canaries never pass heuristic_fn.
    if heuristic_fn is not None:
        _h = heuristic_fn

    counter = 0

    # ── Build the effective start set ────────────────────────────────────────
    # Single-start (no cone): use the bare ``entry`` unchanged.
    # Multi-start (cone provided): filter cone candidates that clip the side/back
    # walls OR protrude in front of the solid wall beside the door; fall back to
    # the door-centre straight-in pose if all are filtered so the search always
    # has at least one start.
    if entries is None:
        start_poses: tuple[Pose, ...] = (entry,)
    else:
        # Filter: keep only poses whose footprint at the door boundary is clear of
        # the side/back walls AND whose y<0 protrusion (if any) stays within the
        # door opening (the door-gated front-gap exemption is built into the
        # predicate, #411).
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
            # Fallback: door-centre straight-in pose, so the search always has a
            # start. It is NOT guaranteed feasible — a plane wider than the door
            # clips even here, and the search then finds no valid path (#411).
            start_poses = (Pose(x_m=hangar.door.center_x_m, y_m=0.0, heading_deg=0.0),)
            # #503 signal (observational, plan-inert): when the site HAS an apron
            # (apron_depth_m > 0) but every apron start pose was filtered, this
            # plane tows via the y=0 door line and shows no slide-in — and that is
            # silent in the MovesPlan. Record it in `stats` (a diagnostics-only
            # out-param, never the plan) so the caller (:func:`plan_fill`) can warn,
            # naming the plane. Suppressed at depth 0, where the door-line start is
            # the correct pre-apron behaviour, not a dropped slide-in.
            if stats is not None and hangar.apron_depth_m > 0.0:
                stats["apron_fallback"] = True

    # ── Cost-aware start-seed analytic expansion (#480) ──────────────────────
    # A nose-out slot's cheap fix is a START-SEED analytic shot: back in from the
    # rear-entry cone rather than entering forward and pirouetting in the back
    # corner. Evaluate every surviving start seed's closed-form completion up front
    # and take the cheapest collision-clean one, so the back-in beats an
    # earlier-enumerated forward entry. Bounded work (≤ the cone size): the
    # closed-form cost is computed first and the expensive screen + EXACT oracle
    # are paid only when a seed could beat the best found. Forward preference is the
    # strict-< tie-break (entry_poses enumerates the forward cone first). If no seed
    # closes cleanly (an obstructed approach), fall through to the greedy node-level
    # Hybrid-A* search below — that case stays best-effort, not cost-optimised, and
    # keeps the pre-#480 routing speed (returns at the first clean shot).
    best_seed: tuple[float, DubinsArc] | None = None
    for start_pose in start_poses:
        seed_arc = plan_reeds_shepp(start_pose, goal, turn_radius_m=r, lateral=mover_on_carts)
        seed_cost = _segments_cost(seed_arc.segments, r)
        if best_seed is not None and seed_cost >= best_seed[0]:
            continue  # can't beat the best clean seed — skip the costly checks
        if not all(
            _motion_clear(mover, p, obstacles, motion_hangar)
            for p in seed_arc.sample(step_m=_SEARCH_STEP_M, step_deg=_SEARCH_STEP_DEG)
        ):
            continue
        candidate = DubinsArc(start_pose, goal, r, seed_arc.segments)
        if (
            path_first_conflict(candidate, mover, mover_on_carts=mover_on_carts, placed=placed)
            is None
        ):
            best_seed = (seed_cost, candidate)
    if best_seed is not None:
        if stats is not None:
            stats.update(
                expansions=0,
                found=True,
                budget_exhausted=False,
                space_exhausted=False,
                start_poses=len(start_poses),
                heuristic=heuristic,
            )
        return best_seed[1]

    # ── Seed the open heap with all surviving start poses ───────────────────
    # Heuristic: ``_h`` — straight-line Euclidean distance by default (deliberately
    # looser than the spec's Dubins-distance suggestion — Euclidean ≤ Dubins length
    # ≤ true cost and the g-cost turn penalty is ≥ 0, so it stays admissible; it may
    # expand a few more nodes, never fewer; do NOT "tighten" it to the Dubins shot
    # without re-checking admissibility and the determinism canary). The opt-in
    # ``heuristic="grid"`` seam (#332) swaps in the obstacle-aware free-space
    # geodesic field (lower-bound, admissible-leaning) without touching the default.
    open_heap: list[tuple[float, int, _SearchNode]] = []
    best_g: dict[tuple[int, int, int], float] = {}
    for start_pose in start_poses:
        start_node = _SearchNode(start_pose, 0.0, None, None, 0)
        start_key = _cell(start_pose)
        h_start = _h(start_pose)
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
        final_arc = plan_reeds_shepp(node.pose, goal, turn_radius_m=r, lateral=mover_on_carts)
        if all(
            _motion_clear(mover, p, obstacles, motion_hangar)
            for p in final_arc.sample(step_m=_SEARCH_STEP_M, step_deg=_SEARCH_STEP_DEG)
        ):
            segs = tuple(_reconstruct_segments(node)) + final_arc.segments
            # ``final_arc.segments`` is always non-empty (plan_reeds_shepp
            # guarantees it), so ``segs`` is non-empty by construction. Assert it
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
                if stats is not None:
                    stats.update(
                        expansions=expansions,
                        found=True,
                        budget_exhausted=False,
                        space_exhausted=False,
                        start_poses=len(start_poses),
                        heuristic=heuristic,
                    )
                return candidate
        # else (fast screen failed, OR fast passed but the exact oracle rejected
        # the full path): do not ship; fall through to primitive expansion.

        if expansions >= max_expansions:
            break
        expansions += 1

        # Primitive expansion (fixed order L, S, R for determinism). An edge is
        # valid iff every sampled pose is clear per the fast checker.
        for seg in _primitives(r, lateral=mover_on_carts):
            child_pose = _step_pose(node.pose, seg, r)
            edge = DubinsArc(node.pose, child_pose, r, (seg,))
            if not all(
                _motion_clear(mover, p, obstacles, motion_hangar)
                for p in edge.sample(step_m=_SEARCH_STEP_M, step_deg=_SEARCH_STEP_DEG)
            ):
                continue
            # Cusp charge (#480): a primitive that TRANSLATES and reverses the
            # branch's last travel direction costs one CUSP_PENALTY. In-place cart
            # pivots (r == 0 turns) don't translate — they inherit the parent's
            # last_drive_gear and never charge a cusp. Own-gear (r > 0) primitives
            # all translate, so this is just parent-gear vs this-gear.
            translates = not (r == 0.0 and seg.kind in ("L", "R"))
            is_cusp = translates and node.last_drive_gear != 0 and node.last_drive_gear != seg.gear
            child_drive_gear = seg.gear if translates else node.last_drive_gear
            child_g = node.g + _seg_cost(seg, r) + (CUSP_PENALTY if is_cusp else 0.0)
            child_key = _cell(child_pose)
            if child_g < best_g.get(child_key, math.inf) - 1e-9:
                best_g[child_key] = child_g
                counter += 1
                h = _h(child_pose)
                heapq.heappush(
                    open_heap,
                    (
                        child_g + h,
                        counter,
                        _SearchNode(child_pose, child_g, seg, node, child_drive_gear),
                    ),
                )

    # The per-edge / analytic validity checks use the fast `_motion_clear`, which
    # returns a boolean rather than a specific :class:`Conflict`, so no per-edge
    # conflict object is available to surface here — and budget exhaustion is not
    # any single named constraint (the blocker is "no path within the budget").
    # Report the honest ``no_feasible_path`` kind rather than mis-labelling it
    # ``hangar_bounds``, so a caller keying on ``conflict.kind`` (plan_fill / the
    # Wave 3 CLI) is not misled about the cause; the mover is still named.
    if stats is not None:
        # ``budget_exhausted`` (hit the cap) vs ``space_exhausted`` (the open
        # heap emptied first — every reachable discretised state settled with no
        # analytic shot closing to goal, i.e. genuine local infeasibility within
        # the grid/primitive discretisation). The distinction is the whole point
        # of the #332 failure characterisation.
        stats.update(
            expansions=expansions,
            found=False,
            budget_exhausted=expansions >= max_expansions,
            space_exhausted=expansions < max_expansions,
            start_poses=len(start_poses),
            heuristic=heuristic,
        )
    raise NoFeasiblePlanError(
        mover.id,
        Conflict.single(
            kind="no_feasible_path",
            plane=mover.id,
            detail=f"no in-bounds tow path found within {max_expansions} expansions",
        ),
    )

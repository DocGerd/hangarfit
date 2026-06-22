"""Reward-geometry helpers — reuse hangarfit's deterministic geometry oracle.

The agent IS the search; these functions reuse only the *rules of physics*
(collisions.check graded penetration, the parts-model world transform, the ADR-0010
motion primitives + swept-path clearance, the Caddy egress oracle), never the RR-MC
or Hybrid-A* search. All functions are pure and RNG-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import box

from hangarfit.collisions import check
from hangarfit.geometry import cached_parts_world
from hangarfit.models import Aircraft, GroundObject, Hangar, Layout, Placement
from hangarfit.towplanner import (
    CUSP_PENALTY as _CUSP_PENALTY,
)
from hangarfit.towplanner import (
    DubinsArc,
    Pose,
    Segment,
    _aabb,
    _build_obstacles,
    _motion_clear,
    _Obstacles,
    _primitives,
    egress_first_conflict,
)
from ml.types import Primitive

type ObstaclesT = _Obstacles

__all__ = [
    "overlap_area_m2",
    "intrusion_area_m2",
    "layout_valid",
    "LayoutScore",
    "score_layout",
    "legal_primitives",
    "apply_primitive",
    "build_obstacles",
    "swept_intrusion_m2",
    "movement_cost",
    "egress_blocked",
    "active_misfit_m2",
]

# Re-export so callers can use go.CUSP_PENALTY
CUSP_PENALTY: float = _CUSP_PENALTY


def overlap_area_m2(layout: Layout) -> float:
    """Summed pairwise overlap area (m²) — the graded collision signal (spec §5).

    Reuses ``collisions.check``'s ``total_penetration_m2`` so the env's collision
    gradient is identical to the solver's secondary score key.
    """
    return check(layout).total_penetration_m2


def intrusion_area_m2(
    body: Aircraft | GroundObject,
    placement: Placement,
    hangar: Hangar,
    *,
    bay_closed: bool = False,
) -> float:
    """Footprint area (m²) outside the hangar floor or inside a CLOSED maintenance bay.

    Mirrors collisions.check's ADR-0006 rule: the maintenance bay is a keep-out ONLY when
    it is closed (``layout.maintenance_plane is not None``). The env never sets a maintenance
    occupant, so it always passes ``bay_closed=False`` and the bay term vanishes — fixing the
    #694 over-strict-inert-bay divergence on the reward gradient. Out-of-floor (walls/notch via
    ``floor_polygon``) is always counted. The front apron (``y < 0``) counts as intrusion for a
    PARKED pose (it is a motion region, not a parked-validity region)."""
    floor = hangar.floor_polygon
    if floor is None:
        floor = box(0.0, 0.0, hangar.width_m, hangar.length_m)
    bay_poly = None
    if bay_closed:
        bay = hangar.maintenance_bay
        bay_poly = box(
            bay.center_x_m - bay.width_m / 2.0,
            hangar.length_m - bay.depth_m,
            bay.center_x_m + bay.width_m / 2.0,
            hangar.length_m,
        )
    total = 0.0
    for wp in cached_parts_world(body, placement):
        poly = wp.polygon
        total += poly.difference(floor).area  # outside the floor (walls/notch)
        if bay_poly is not None:
            total += poly.intersection(bay_poly).area  # inside a CLOSED maintenance bay
    return total


def legal_primitives(
    body: Aircraft | GroundObject, *, on_carts: bool, unit_magnitude_m: float = 1.0
) -> tuple[Primitive, ...]:
    """Legal movement primitives for ``body`` (ADR-0010), as unit-magnitude actions.

    Reuses ``towplanner._primitives``: own-gear → 6 Reeds–Shepp arcs; cart (r=0) → 4
    pivots/straights, plus the lateral strafe ``T`` (fwd/rev) when ``on_carts`` (#647).
    The returned magnitudes are unit (1 m / 1 rad); the policy scales them.
    """
    r = body.effective_turn_radius_m()
    lateral = on_carts and r == 0.0
    out: list[Primitive] = []
    for seg in _primitives(r, lateral=lateral):
        out.append(Primitive(kind=seg.kind, magnitude=unit_magnitude_m, gear=seg.gear))
    return tuple(out)


def apply_primitive(
    pose: Pose, primitive: Primitive, *, turn_radius_m: float
) -> tuple[Pose, tuple[Pose, ...]]:
    """Apply one primitive to ``pose``; return (end_pose, swept_poses).

    Builds a single-segment ``DubinsArc`` and integrates it with the same
    ``pose_at``/``sample`` machinery the renderers and towplanner consume, so the
    motion is identical to what the rest of the system sees.
    """
    seg = Segment(kind=primitive.kind, length_m=primitive.magnitude, gear=primitive.gear)
    # ``end`` is a placeholder: ``sample()``/``pose_at()`` integrate from ``start``
    # and nothing reads ``arc.end``, so the stored ``end`` is unused/irrelevant here.
    arc = DubinsArc(start=pose, end=pose, turn_radius_m=turn_radius_m, segments=(seg,))
    swept = tuple(arc.sample(step_m=0.05, step_deg=1.0))
    end = arc.pose_at(primitive.magnitude)
    return end, swept


def build_obstacles(parked_layout: Layout, mover_id: str) -> ObstaclesT:
    """Frozen-parked obstacle set for swept-path clearance (the mover is excluded).
    Exposed so the env can build it once per parked-set version and reuse it."""
    return _build_obstacles(parked_layout, mover_id=mover_id)


def _footprint_radius(body: Aircraft | GroundObject) -> float:
    """Max distance from the placement origin to any of ``body``'s part vertices.

    Heading-independent: the plane-local → world transform (:func:`local_to_world`) has
    determinant −1 (a rotation composed with a reflection), which preserves Euclidean norm,
    so a vertex at local ``(u, v)`` is always ``sqrt(u²+v²)`` from the pose's ``(x, y)`` at
    ANY heading. So this radius bounds the body's footprint at every pose — the basis for
    Lever A's conservative whole-leg envelope (#754). Built via ``cached_parts_world`` at the
    identity placement (one build, memoized within a pose-cache scope)."""
    parts = cached_parts_world(
        body, Placement(plane_id=body.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
    )
    r2 = 0.0
    for wp in parts:
        for x, y in wp.polygon.exterior.coords:
            r2 = max(r2, x * x + y * y)  # at identity placement the vertex IS its offset
    return math.sqrt(r2)


def _swept_envelope_clear(
    body: Aircraft | GroundObject, swept: tuple[Pose, ...], obstacles: ObstaclesT
) -> bool:
    """Lever A (#754): True iff a conservative whole-leg envelope proves NO sampled pose can
    overlap any obstacle part — i.e. :func:`swept_intrusion_m2` is provably 0.0, so it can
    short-circuit the per-pose loop with zero Polygon builds.

    The envelope is the bbox of the swept ``(x, y)`` inflated by the body's footprint radius R
    (:func:`_footprint_radius`, heading-independent) — a conservative SUPERSET of every sampled
    pose's footprint. A strict (gap > 0) AABB separation from EVERY precomputed obstacle-part
    AABB therefore means no pose footprint can overlap any obstacle part. Same conservative
    lower-bound logic as the per-pose AABB prefilter (and
    ``collisions._aabbs_separated_beyond_clearance``), hoisted to the whole leg: it returns True
    only when the full loop's result is exactly 0.0, so it can never mask a real intrusion.
    (``swept_intrusion_m2``'s leak only accumulates area against ``obstacles.world_parts``;
    walls/notch/bay gate ``_motion_clear`` but never contribute leak.)"""
    if not obstacles.world_parts:
        return True  # no parked parts → the per-pose leak loop has nothing to overlap → 0.0
    if not swept:
        return True  # no sampled poses → the per-pose loop is empty → 0.0
    r = _footprint_radius(body)
    env_xmin = min(p.x_m for p in swept) - r
    env_xmax = max(p.x_m for p in swept) + r
    env_ymin = min(p.y_m for p in swept) - r
    env_ymax = max(p.y_m for p in swept) + r
    return all(
        env_xmin - op_xmax > 0.0
        or op_xmin - env_xmax > 0.0
        or env_ymin - op_ymax > 0.0
        or op_ymin - env_ymax > 0.0
        for op_xmin, op_ymin, op_xmax, op_ymax in obstacles.world_part_aabbs
    )


def swept_intrusion_m2(
    body: Aircraft | GroundObject,
    swept: tuple[Pose, ...],
    *,
    parked_layout: Layout,
    active_id: str,
    obstacles: ObstaclesT | None = None,
) -> float:
    """Graded swept-path intrusion (m²) for a move of ``body`` along ``swept``.

    Reuses the towplanner's motion geometry: ``_build_obstacles`` (excludes the mover)
    + ``_motion_clear`` (the exact per-pose oracle, side/back walls enforced, front
    apron open). For each sampled pose that is NOT clear we measure that pose's
    overlap area against the obstacle parts, and return the **worst single-pose
    intrusion (max), deliberately NOT a sum**: a finely-sampled arc has heavily
    overlapping consecutive poses, so summing would inflate the penalty by the
    sample count. A fully clear sweep returns 0.0 (routability-by-construction
    along this leg).

    Pass a pre-built ``obstacles`` (from :func:`build_obstacles`) to avoid
    rebuilding the frozen parked-obstacle set on every call — useful when the
    parked layout is stable across many steps.
    """
    obstacles = (
        obstacles if obstacles is not None else _build_obstacles(parked_layout, mover_id=active_id)
    )
    hangar = parked_layout.hangar

    # Lever A (#754): a conservative whole-leg envelope short-circuits a clearly-clear leg to
    # 0.0 with zero per-pose Polygon builds (see :func:`_swept_envelope_clear`) — a byte-
    # identical lower-bound filter that fires only when the per-pose loop would also return 0.0.
    if _swept_envelope_clear(body, swept, obstacles):
        return 0.0

    worst = 0.0
    for pose in swept:
        if _motion_clear(body, pose, obstacles, hangar):
            continue
        pl = Placement(
            plane_id=active_id,
            x_m=pose.x_m,
            y_m=pose.y_m,
            heading_deg=pose.heading_deg,
            on_carts=False,
        )
        # Overlap against parked obstacle parts + out-of-floor (front apron excluded).
        # ``cached_parts_world`` reuses the mover parts ``_motion_clear`` just built for
        # this same pose inside an active pose_cache_scope (#733). The obstacle AABBs are
        # precomputed (static across poses), so an AABB-disjointness pre-filter skips the
        # exact shapely intersects for pairs that cannot overlap.
        leak = 0.0
        for wp in cached_parts_world(body, pl):
            wp_xmin, wp_ymin, wp_xmax, wp_ymax = _aabb(wp.polygon)
            for op, (op_xmin, op_ymin, op_xmax, op_ymax) in zip(
                obstacles.world_parts, obstacles.world_part_aabbs, strict=True
            ):
                # This loop measures raw overlap AREA (clearance-independent), unlike
                # _motion_clear's clearance-INflated filter (towplanner `> clearance`) --
                # so the gap threshold is 0.0, not clearance. A STRICT AABB gap (> 0) means
                # the polygons are disjoint -> 0 intersection area, so skipping is
                # byte-identical; a touching/overlapping AABB (gap <= 0) falls through to
                # the exact predicate.
                if (
                    wp_xmin - op_xmax > 0.0
                    or op_xmin - wp_xmax > 0.0
                    or wp_ymin - op_ymax > 0.0
                    or op_ymin - wp_ymax > 0.0
                ):
                    continue
                if wp.polygon.intersects(op.polygon):
                    leak += wp.polygon.intersection(op.polygon).area
        worst = max(worst, leak)
    return worst


def movement_cost(primitive: Primitive, *, prev_gear: int | None, cusp_penalty: float) -> float:
    """Per-move cost: travelled magnitude + a per-cusp penalty on a direction reversal.

    Mirrors the #480 cost model (``length + CUSP_PENALTY * cusps``). A cusp is a
    forward<->reverse change between consecutive TRANSLATING legs; cart pivots
    (``L``/``R`` at r=0) don't translate, so they never add a cusp here.
    """
    translates = primitive.kind in ("S", "T")
    cusp = 1.0 if (translates and prev_gear is not None and primitive.gear != prev_gear) else 0.0
    return abs(primitive.magnitude) + cusp_penalty * cusp


@dataclass(frozen=True, slots=True)
class LayoutScore:
    """One-pass score of a frozen layout: graded penetration + the two validity gates,
    from a single ``check`` + single ``egress_blocked``. ``layout_valid`` ==
    ``collisions_valid and not egress_blocked``."""

    penetration_m2: float
    collisions_valid: bool
    egress_blocked: bool


def score_layout(layout: Layout) -> LayoutScore:
    """Single-pass replacement for calling ``overlap_area_m2`` + ``layout_valid`` +
    ``egress_blocked`` separately (each re-runs ``check``/rebuilds world parts)."""
    cr = check(layout)
    return LayoutScore(
        penetration_m2=cr.total_penetration_m2,
        collisions_valid=cr.valid,
        egress_blocked=egress_blocked(layout),
    )


def layout_valid(layout: Layout) -> bool:
    """Whole-layout validity per the PRODUCT deterministic checker (== ``hangarfit check``):
    collisions.check reports no conflicts (overlap + hangar bounds/notch + CONDITIONAL
    maintenance bay + ground-obstacle keep-outs) AND no Caddy hard-door egress violation
    (ADR-0026). The single source of validity truth shared by the env gate, the r_valid_park
    bonus gate, and the benchmark — so the bonus and the promotion metric can never disagree.
    Replaces the env's old hand-rolled overlap+intrusion+egress, which over-enforced the inert
    maintenance bay (#694)."""
    return check(layout).valid and not egress_blocked(layout)


def egress_blocked(layout: Layout, *, mover_id: str | None = None) -> bool:
    """True iff a hard-door mover (e.g. the Caddy) cannot drive out (ADR-0026).

    Finds the hard-door mover automatically when ``mover_id`` is None; returns False
    when there is no hard-door mover in the layout.
    """
    if mover_id is None:
        for gp in layout.ground_object_placements:
            obj = layout.ground_objects.get(gp.plane_id)
            if obj is not None and getattr(obj, "hard_door_mover", False):
                mover_id = gp.plane_id
                break
    if mover_id is None:
        return False
    return egress_first_conflict(layout, mover_id) is not None


def active_misfit_m2(
    body: Aircraft | GroundObject,
    pose: Pose,
    parked_layout: Layout,
    hangar: Hangar,
) -> float:
    """Coarse, monotone 'how bad is parking the active body HERE' — for the
    dense_slot_potential shaping term. Pure state→scalar shapely query: the active
    footprint's overlap with parked bodies PLUS its in-hangar (y≥0) out-of-floor area.
    0.0 in a clean pocket; grows with intrusion. The apron (y<0) is EXCLUDED (the object
    legitimately starts there; the door-ingress term handles entry). NEVER calls solve()/a
    nester — that would re-import a search's reachable-distribution bias (constraint B)."""
    floor = hangar.floor_polygon
    if floor is None:
        floor = box(0.0, 0.0, hangar.width_m, hangar.length_m)
    upper = box(-1.0e6, 0.0, 1.0e6, hangar.length_m + 1.0e6)  # y >= 0 half-plane
    pl = Placement(
        plane_id=body.id,
        x_m=pose.x_m,
        y_m=pose.y_m,
        heading_deg=pose.heading_deg,
        on_carts=False,
    )
    obstacle_parts = [
        wp
        for p in parked_layout.placements
        for b in [parked_layout.fleet.get(p.plane_id)]
        if b is not None
        for wp in cached_parts_world(b, p)
    ] + [
        wp
        for gp in parked_layout.ground_object_placements
        for b in [parked_layout.ground_objects.get(gp.plane_id)]
        if b is not None
        for wp in cached_parts_world(b, gp)
    ]
    total = 0.0
    for wp in cached_parts_world(body, pl):
        poly = wp.polygon
        total += poly.difference(floor).intersection(upper).area  # in-hangar wall/notch intrusion
        for op in obstacle_parts:
            if poly.intersects(op.polygon):
                total += poly.intersection(op.polygon).area
    return total

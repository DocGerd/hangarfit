"""Reward-geometry helpers — reuse hangarfit's deterministic geometry oracle.

The agent IS the search; these functions reuse only the *rules of physics*
(collisions.check graded penetration, the parts-model world transform, the ADR-0010
motion primitives + swept-path clearance, the Caddy egress oracle), never the RR-MC
or Hybrid-A* search. All functions are pure and RNG-free.
"""

from __future__ import annotations

from shapely.geometry import box

from hangarfit.collisions import check
from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import Aircraft, GroundObject, Hangar, Layout, Placement
from hangarfit.towplanner import (
    CUSP_PENALTY as _CUSP_PENALTY,
)
from hangarfit.towplanner import (
    DubinsArc,
    Pose,
    Segment,
    _build_obstacles,
    _motion_clear,
    _primitives,
    egress_first_conflict,
)
from ml.types import Primitive

__all__ = [
    "overlap_area_m2",
    "intrusion_area_m2",
    "layout_valid",
    "legal_primitives",
    "apply_primitive",
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
    for wp in aircraft_parts_world(body, placement):
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


def swept_intrusion_m2(
    body: Aircraft | GroundObject,
    swept: tuple[Pose, ...],
    *,
    parked_layout: Layout,
    active_id: str,
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
    """
    obstacles = _build_obstacles(parked_layout, mover_id=active_id)
    hangar = parked_layout.hangar
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
        leak = 0.0
        for wp in aircraft_parts_world(body, pl):
            for op in obstacles.world_parts:
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
        for wp in aircraft_parts_world(b, p)
    ] + [
        wp
        for gp in parked_layout.ground_object_placements
        for b in [parked_layout.ground_objects.get(gp.plane_id)]
        if b is not None
        for wp in aircraft_parts_world(b, gp)
    ]
    total = 0.0
    for wp in aircraft_parts_world(body, pl):
        poly = wp.polygon
        total += poly.difference(floor).intersection(upper).area  # in-hangar wall/notch intrusion
        for op in obstacle_parts:
            if poly.intersects(op.polygon):
                total += poly.intersection(op.polygon).area
    return total

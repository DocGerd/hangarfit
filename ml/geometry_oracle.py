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
from hangarfit.towplanner import (  # type: ignore[attr-defined]
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
    "legal_primitives",
    "apply_primitive",
    "swept_intrusion_m2",
    "movement_cost",
    "egress_blocked",
]

# Re-export so callers can use go.CUSP_PENALTY
CUSP_PENALTY: float = _CUSP_PENALTY


def overlap_area_m2(layout: Layout) -> float:
    """Summed pairwise overlap area (m²) — the graded collision signal (spec §5).

    Reuses ``collisions.check``'s ``total_penetration_m2`` so the env's collision
    gradient is identical to the solver's secondary score key.
    """
    return check(layout).total_penetration_m2


def intrusion_area_m2(body: Aircraft | GroundObject, placement: Placement, hangar: Hangar) -> float:
    """Footprint area (m²) outside the hangar floor or inside a notch/keep-out.

    Graded counterpart to the binary bounds/notch checks (spec §5). Uses the same
    shapely polygons: the L-shaped ``hangar.floor_polygon`` when present, else the
    outer rectangle; plus the maintenance bay rectangle (a keep-out for placement).
    The front apron (``y < 0``) is part of the *motion* model, not a parked-validity
    region, so anything below ``y = 0`` counts as intrusion for a PARKED pose.
    """
    floor = hangar.floor_polygon
    if floor is None:
        floor = box(0.0, 0.0, hangar.width_m, hangar.length_m)
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
        total += poly.intersection(bay_poly).area  # inside the maintenance bay
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
    apron open). For any sampled pose that is NOT clear we add that pose's intrusion
    area against the obstacle parts + walls, so the agent feels a gradient. A fully
    clear sweep returns 0.0 (routability-by-construction along this leg).
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

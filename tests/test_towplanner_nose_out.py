"""#480: nose-out slots are backed in, not pirouetted inside the hangar.

The acceptance metric mirrors the issue's own measure — the heading swept while
the mover is INSIDE the hangar (``y_m > 0``). A nose-out slot used to force a
~160° reorientation in the cramped back corner because the door entry cone was
inward-only and the greedy analytic expansion returned the first (forward) clean
shot. With the #480 rear-entry cone + cusp cost + cost-aware analytic expansion,
the planner backs the plane in instead, sweeping little inside.
"""

from __future__ import annotations

from hangarfit.models import (
    Aircraft,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    Wheels,
)
from hangarfit.towplanner import Pose, _wrap180, entry_poses, plan_path


def _hangar() -> Hangar:
    return Hangar(
        length_m=30.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _plane(pid: str = "A") -> Aircraft:
    return Aircraft(
        id=pid,
        name=pid,
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=1.0,
                width_m=0.6,
                offset_x_m=0.5,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.0,
            ),
        ),
        wheels=Wheels(main_offset_x_m=0.2, track_m=1.8, third_wheel_offset_x_m=-2.0),
    )


def _swept_inside(arc: object) -> float:
    """Σ|Δheading| sampled along the path while inside the hangar (y > 0)."""
    samples = list(arc.sample(step_m=0.25, step_deg=5.0))  # type: ignore[attr-defined]
    return sum(
        abs(_wrap180(b.heading_deg - a.heading_deg))
        for a, b in zip(samples, samples[1:], strict=False)
        if a.y_m > 0.0 and b.y_m > 0.0
    )


def _route(target_heading: float) -> object:
    h = _hangar()
    plane = _plane()
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    slot = Placement(plane_id="A", x_m=10.0, y_m=20.0, heading_deg=target_heading, on_carts=False)
    cone = entry_poses(slot, h)
    goal = Pose(slot.x_m, slot.y_m, slot.heading_deg)
    return plan_path(
        plane,
        cone[0],
        goal,
        hangar=h,
        placed=placed,
        mover_on_carts=False,
        entries=cone,
        heuristic="grid",
    )


def test_nose_out_slot_is_backed_in_not_pirouetted() -> None:
    """A nose-out slot (heading 180) is reached by backing in (reverse legs) with
    little in-hangar reorientation — NOT a ~160° pirouette in the back corner.
    Pre-#480 this swept ~162° (the greedy forward-entry return); the cost-aware
    analytic expansion picks the cheaper back-in instead."""
    arc = _route(180.0)
    assert any(s.gear == -1 for s in arc.segments), "nose-out slot should be backed in"
    assert _swept_inside(arc) < 45.0  # ~162° before the fix


def test_nose_in_slot_enters_forward_with_little_sweep() -> None:
    """A nose-in slot still enters forward with little in-hangar reorientation."""
    arc = _route(0.0)
    assert _swept_inside(arc) < 45.0


def test_plan_path_nose_out_is_deterministic() -> None:
    a = _route(180.0)
    b = _route(180.0)
    fa = [(s.kind, s.gear, s.length_m) for s in a.segments]  # type: ignore[attr-defined]
    fb = [(s.kind, s.gear, s.length_m) for s in b.segments]  # type: ignore[attr-defined]
    assert fa == fb

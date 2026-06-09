"""#263: a ``tow_pivotable`` plane is routed with the pivot-in-place tow motion.

A flagged plane's :meth:`Aircraft.effective_turn_radius_m` returns ``0.0``, so the
tow planner routes it with the existing zero-radius cart-pivot fan (``_plan_cart``)
— no new motion primitive, ``towplanner.py`` untouched. These tests assert the
*observable* behaviour: a flagged plane's path uses the ``r == 0`` fan, reaches its
goal collision-free, and is deterministic.

Note (ADR-0022): ``tow_pivotable`` is a **realism** flag — these plane types
(free-castering tailwheel / tail-down nose-lift) genuinely pivot when towed. Its
original "pivot beats the arc loop" routing-cost rationale was superseded by #480
(which backs nose-out slots in via reverse at ~no extra cost), so these tests do
NOT assert a path-length win — they assert the motion model is what was declared.
"""

from __future__ import annotations

from dataclasses import replace

from hangarfit.models import Layout, Placement
from hangarfit.towplanner import Pose, entry_poses, path_first_conflict, plan_path
from tests.test_towplanner_nose_out import _hangar, _plane


def _route(plane, x_m: float, heading_deg: float):
    h = _hangar()
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    slot = Placement("A", x_m=x_m, y_m=20.0, heading_deg=heading_deg, on_carts=False)
    cone = entry_poses(slot, h)
    goal = Pose(slot.x_m, slot.y_m, slot.heading_deg)
    arc = plan_path(
        plane,
        cone[0],
        goal,
        hangar=h,
        placed=placed,
        mover_on_carts=False,
        entries=cone,
        heuristic="grid",
    )
    return arc, goal, h, placed


def test_tow_pivotable_plane_routes_via_zero_radius_pivot_fan() -> None:
    """A flagged own-gear plane routes with ``turn_radius_m == 0`` (the cart-pivot
    fan); the same plane unflagged routes with its real own-gear radius."""
    arc_plane = _plane()  # always_own_gear, turn_radius 4.0
    pivot_plane = replace(_plane(), tow_pivotable=True)
    assert arc_plane.effective_turn_radius_m() == 4.0
    assert pivot_plane.effective_turn_radius_m() == 0.0

    arc, *_ = _route(arc_plane, x_m=7.0, heading_deg=180.0)
    pivot, *_ = _route(pivot_plane, x_m=7.0, heading_deg=180.0)
    assert arc.turn_radius_m == 4.0
    assert pivot.turn_radius_m == 0.0


def test_tow_pivotable_path_reaches_goal_collision_free() -> None:
    pivot_plane = replace(_plane(), tow_pivotable=True)
    arc, goal, _h, placed = _route(pivot_plane, x_m=7.0, heading_deg=180.0)
    # Ends at the goal pose.
    end = arc.pose_at(arc.length_m)
    assert abs(end.x_m - goal.x_m) < 1e-3
    assert abs(end.y_m - goal.y_m) < 1e-3
    # Collision-free against the (empty) placed set at the fine sampling.
    assert (
        path_first_conflict(
            arc, pivot_plane, mover_on_carts=False, placed=placed, step_m=0.05, step_deg=1.0
        )
        is None
    )


def test_tow_pivotable_path_is_deterministic() -> None:
    pivot_plane = replace(_plane(), tow_pivotable=True)
    a, *_ = _route(pivot_plane, x_m=7.0, heading_deg=180.0)
    b, *_ = _route(pivot_plane, x_m=7.0, heading_deg=180.0)
    fa = [(s.kind, s.gear, s.length_m) for s in a.segments]
    fb = [(s.kind, s.gear, s.length_m) for s in b.segments]
    assert fa == fb

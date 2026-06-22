"""Lateral cart strafe + broadside entry (#599 / ADR-0010).

The cart motion model gains a perpendicular **lateral translate** ("T" Segment
kind), so a broadside-parked plane too wide to enter a narrow door nose-in (the
18 m Scheibe vs the 13.46 m Herrenteich door) can slide in side-on. Three
coordinated pieces are exercised here:

1. ``pose_at`` integrates "T" perpendicular to heading (heading unchanged).
2. ``_primitives(0.0)`` exposes two "T" strafes to the search (own-gear has none).
3. ``_plan_cart`` offers a lateral connector so the analytic shot snaps a
   broadside goal to a clean slide; ``entry_poses`` seeds broadside headings.
"""

from __future__ import annotations

import pytest

from hangarfit.models import Door, Hangar, MaintenanceBay, Placement
from hangarfit.towplanner import (
    DubinsArc,
    Pose,
    Segment,
    _plan_cart,
    _primitives,
    _seg_cost,
    entry_poses,
    plan_reeds_shepp,
)


def _hangar() -> Hangar:
    return Hangar(
        length_m=30.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# 1. pose_at: "T" translates perpendicular to heading, heading unchanged
# ---------------------------------------------------------------------------


def test_lateral_T_at_heading_90_slides_plus_y() -> None:
    # heading 90 (nose +x): a +1 strafe is the math-left = +y (straight in the
    # door). Position moves +y by the leg length; heading is unchanged.
    arc = DubinsArc(Pose(3.0, 1.0, 90.0), Pose(3.0, 6.0, 90.0), 0.0, (Segment("T", 5.0),))
    end = arc.pose_at(arc.length_m)
    assert _close(end.x_m, 3.0)
    assert _close(end.y_m, 6.0)
    assert _close(end.heading_deg, 90.0)


def test_lateral_T_reverse_gear_slides_minus_y_at_heading_90() -> None:
    arc = DubinsArc(Pose(3.0, 6.0, 90.0), Pose(3.0, 1.0, 90.0), 0.0, (Segment("T", 5.0, gear=-1),))
    end = arc.pose_at(arc.length_m)
    assert _close(end.x_m, 3.0)
    assert _close(end.y_m, 1.0)
    assert _close(end.heading_deg, 90.0)


def test_lateral_T_at_heading_0_slides_minus_x() -> None:
    # heading 0 (nose +y): a +1 strafe (math-left) points -x.
    arc = DubinsArc(Pose(5.0, 2.0, 0.0), Pose(3.0, 2.0, 0.0), 0.0, (Segment("T", 2.0),))
    end = arc.pose_at(arc.length_m)
    assert _close(end.x_m, 3.0)
    assert _close(end.y_m, 2.0)
    assert _close(end.heading_deg, 0.0)


def test_lateral_T_sample_yields_translation_density() -> None:
    # A long "T" leg samples by translation distance (not collapsed to one pose).
    arc = DubinsArc(Pose(0.0, 0.0, 90.0), Pose(0.0, 10.0, 90.0), 0.0, (Segment("T", 10.0),))
    poses = list(arc.sample(step_m=0.5, step_deg=1.0))
    assert len(poses) >= 20  # ~10 m / 0.5 m
    assert _close(poses[0].y_m, 0.0)
    assert _close(poses[-1].y_m, 10.0)
    # Every sample keeps the heading and the x coordinate (pure +y slide).
    assert all(_close(p.heading_deg, 90.0) and _close(p.x_m, 0.0) for p in poses)


def test_seg_cost_lateral_is_translation_metres() -> None:
    assert _seg_cost(Segment("T", 3.0), turn_radius_m=0.0) == pytest.approx(3.0)
    assert _seg_cost(Segment("T", 3.0, gear=-1), turn_radius_m=0.0) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 2. primitive fan: carts gain two strafes; own-gear has none
# ---------------------------------------------------------------------------


def test_cart_fan_includes_two_lateral_strafes_last() -> None:
    segs = _primitives(turn_radius_m=0.0, lateral=True)  # on a dolly
    assert [s.kind for s in segs] == ["L", "S", "R", "S", "T", "T"]
    assert segs[4].gear == 1 and segs[5].gear == -1


def test_pivot_in_place_fan_has_no_strafe() -> None:
    # Zero-radius but NOT on a dolly (a free-swivel tailwheel taildragger): it
    # pivots in place + drives straight, but cannot slide sideways (#599).
    assert all(s.kind != "T" for s in _primitives(turn_radius_m=0.0))


def test_own_gear_fan_has_no_lateral() -> None:
    # A plane on its own steerable gear taxis on its wheels and cannot strafe.
    assert all(s.kind != "T" for s in _primitives(turn_radius_m=4.0, lateral=True))


# ---------------------------------------------------------------------------
# 3. _plan_cart lateral connector + entry_poses broadside cone
# ---------------------------------------------------------------------------


def test_plan_cart_pure_broadside_is_single_lateral_slide() -> None:
    # Same heading, displacement purely perpendicular ⇒ one clean "T" leg, no
    # pivots — instead of the old pivot-straight-pivot dog-leg.
    arc = _plan_cart(
        Pose(0.0, 0.0, 90.0), Pose(0.0, 5.0, 90.0), allow_reverse=True, lateral_ok=True
    )
    assert [s.kind for s in arc.segments] == ["T"]
    end = arc.pose_at(arc.length_m)
    assert _close(end.x_m, 0.0) and _close(end.y_m, 5.0) and _close(end.heading_deg, 90.0)
    assert arc.length_m == pytest.approx(5.0)


def test_plan_cart_offset_broadside_reaches_goal() -> None:
    # With a sizeable along-heading component the diagonal pivot-straight-pivot is
    # cheaper than an L-shaped straight+strafe (hypot ≤ |along|+|perp|), so the
    # cost model legitimately keeps the dog-leg here. Either way the connector
    # must reach the goal pose exactly — that is the invariant under test.
    arc = _plan_cart(
        Pose(0.0, 0.0, 90.0), Pose(2.0, 5.0, 90.0), allow_reverse=True, lateral_ok=True
    )
    end = arc.pose_at(arc.length_m)
    assert _close(end.x_m, 2.0) and _close(end.y_m, 5.0) and _close(end.heading_deg, 90.0)


def test_plan_cart_nearly_perpendicular_offset_uses_lateral() -> None:
    # A small along-heading component (0.1 m) with a large perpendicular one ⇒ the
    # L-shaped straight+strafe is within the pivot penalty of the diagonal, so the
    # lateral connector wins and a "T" leg appears.
    arc = _plan_cart(
        Pose(0.0, 0.0, 90.0), Pose(0.1, 6.0, 90.0), allow_reverse=True, lateral_ok=True
    )
    assert "T" in [s.kind for s in arc.segments]
    end = arc.pose_at(arc.length_m)
    assert _close(end.x_m, 0.1) and _close(end.y_m, 6.0) and _close(end.heading_deg, 90.0)


def test_plan_cart_lateral_beats_pivot_straight_pivot_for_broadside() -> None:
    # The clean slide is cheaper than the double-pivot route it replaces.
    arc = _plan_cart(
        Pose(0.0, 0.0, 90.0), Pose(0.0, 5.0, 90.0), allow_reverse=True, lateral_ok=True
    )
    assert arc.length_m == pytest.approx(5.0)  # straight slide, no pivots


def test_plan_cart_collinear_forward_unchanged_no_lateral() -> None:
    # Goal straight ahead on the same heading ⇒ pure forward "S" still wins the
    # tie (no spurious lateral). heading 0 (nose +y), goal +y.
    arc = _plan_cart(Pose(0.0, 0.0, 0.0), Pose(0.0, 5.0, 0.0), allow_reverse=True)
    assert [s.kind for s in arc.segments] == ["S"]
    assert all(s.gear == 1 for s in arc.segments)


def test_plan_cart_lateral_is_deterministic() -> None:
    a = _plan_cart(
        Pose(1.0, 2.0, 270.0), Pose(4.0, 9.0, 270.0), allow_reverse=True, lateral_ok=True
    )
    b = _plan_cart(
        Pose(1.0, 2.0, 270.0), Pose(4.0, 9.0, 270.0), allow_reverse=True, lateral_ok=True
    )
    assert [(s.kind, s.gear, s.length_m) for s in a.segments] == [
        (s.kind, s.gear, s.length_m) for s in b.segments
    ]


def test_reeds_shepp_cart_with_lateral_routes_broadside_as_slide() -> None:
    # plan_reeds_shepp(r=0, lateral=True) — a cart-borne mover — routes a broadside
    # goal as a clean single slide.
    arc = plan_reeds_shepp(
        Pose(0.0, 0.0, 90.0), Pose(0.0, 7.0, 90.0), turn_radius_m=0.0, lateral=True
    )
    assert [s.kind for s in arc.segments] == ["T"]
    assert arc.pose_at(arc.length_m).y_m == pytest.approx(7.0)


def test_reeds_shepp_pivot_in_place_broadside_has_no_strafe() -> None:
    # Same broadside goal but lateral=False (a free-swivel pivot-in-place plane,
    # NOT on a dolly): it must NOT strafe — it pivot-straight-pivots instead, still
    # reaching the goal exactly.
    arc = plan_reeds_shepp(Pose(0.0, 0.0, 90.0), Pose(0.0, 7.0, 90.0), turn_radius_m=0.0)
    assert all(s.kind != "T" for s in arc.segments)
    assert arc.pose_at(arc.length_m).y_m == pytest.approx(7.0)


def test_entry_poses_broadside_target_emits_side_on_headings() -> None:
    h = _hangar()
    slot = Placement(plane_id="glider", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=True)
    headings = {round(p.heading_deg, 1) for p in entry_poses(slot, h)}
    # The forward nose-in cone is still present, plus the side-on cone around 90°
    # and 270° (either broadside approach).
    assert {0.0, 90.0, 270.0}.issubset(headings)
    assert {75.0, 105.0, 255.0, 285.0}.issubset(headings)


def test_entry_poses_nose_in_target_has_no_broadside_seeds() -> None:
    # A nose-in target (heading 0) keeps the forward 5-heading cone ONLY — no
    # broadside seeds, so its grid is byte-identical to the pre-#599 behaviour.
    h = _hangar()
    slot = Placement(plane_id="A", x_m=10.0, y_m=20.0, heading_deg=0.0, on_carts=False)
    headings = {round(p.heading_deg, 1) for p in entry_poses(slot, h)}
    assert headings == {330.0, 345.0, 0.0, 15.0, 30.0}


def test_entry_poses_broadside_is_deterministic() -> None:
    h = _hangar()
    slot = Placement(plane_id="g", x_m=10.0, y_m=20.0, heading_deg=270.0, on_carts=True)
    assert entry_poses(slot, h) == entry_poses(slot, h)

import math

import pytest

from hangarfit.models import Aircraft, Door, Hangar, Layout, MaintenanceBay, Part
from hangarfit.towplanner import (
    NoFeasiblePlanError,
    Pose,
    Segment,
    _cell,
    _primitives,
    _seg_cost,
    _step_pose,
    path_first_conflict,
    plan_path,
)


def test_primitives_own_gear_are_left_straight_right_in_order() -> None:
    segs = _primitives(turn_radius_m=4.0)
    assert [s.kind for s in segs] == ["L", "S", "R"]
    # Each is a positive-length short step.
    assert all(s.length_m > 0.0 for s in segs)


def test_primitives_cart_are_pivot_straight_pivot_in_order() -> None:
    segs = _primitives(turn_radius_m=0.0)
    assert [s.kind for s in segs] == ["L", "S", "R"]
    # Pivots encode one heading cell in radians; the straight encodes metres.
    # Pin both so a length/unit swap between the two is caught.
    assert segs[0].length_m == pytest.approx(math.radians(15.0))
    assert segs[1].length_m == pytest.approx(0.5)
    assert segs[2].length_m == pytest.approx(math.radians(15.0))


def test_step_pose_straight_advances_along_heading() -> None:
    # heading 0 => +y. A straight step moves +y by its length.
    p = _step_pose(Pose(3.0, 1.0, 0.0), Segment("S", 0.5), turn_radius_m=4.0)
    assert p.x_m == pytest.approx(3.0, abs=1e-9)
    assert p.y_m == pytest.approx(1.5, abs=1e-9)
    assert p.heading_deg == pytest.approx(0.0, abs=1e-9)


def test_step_pose_cart_pivot_rotates_in_place() -> None:
    # r == 0 turn: position held, heading changes by the pivot radians.
    seg = Segment("R", math.radians(15.0))
    p = _step_pose(Pose(3.0, 1.0, 0.0), seg, turn_radius_m=0.0)
    assert p.x_m == pytest.approx(3.0, abs=1e-9)
    assert p.y_m == pytest.approx(1.0, abs=1e-9)
    # Compass CW-positive: an "R" pivot of +15 deg increases the compass heading.
    assert p.heading_deg == pytest.approx(15.0, abs=1e-6)


def test_seg_cost_counts_translation_plus_turn_penalty() -> None:
    # Straight: pure translation, no turn penalty.
    assert _seg_cost(Segment("S", 2.0), turn_radius_m=4.0) == pytest.approx(2.0)
    # r>0 turn of arc length L: translation L + penalty * (L / r) radians.
    c = _seg_cost(Segment("L", 2.0), turn_radius_m=4.0)
    assert c == pytest.approx(2.0 + 0.1 * (2.0 / 4.0))


def test_cell_bins_pose_into_grid() -> None:
    # Same 0.5 m / 15 deg cell for nearby poses; different for far ones.
    assert _cell(Pose(3.01, 1.02, 1.0)) == _cell(Pose(2.99, 0.98, 2.0))
    assert _cell(Pose(3.0, 1.0, 0.0)) != _cell(Pose(9.0, 9.0, 180.0))
    # Heading wraps: 359 deg rounds to bin 24 % 24 = 0; 1 deg rounds to bin 0.
    # Both land in bin 0.
    assert _cell(Pose(3.0, 1.0, 359.0))[2] == _cell(Pose(3.0, 1.0, 1.0))[2]


# ---------------------------------------------------------------------------
# plan_path (Hybrid-A* search core) tests
# ---------------------------------------------------------------------------


def _hangar(width_m: float = 20.0, length_m: float = 25.0) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=width_m / 2, width_m=10.0),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _winged_plane(pid: str, *, span_m: float = 10.0, turn_radius_m: float = 5.0) -> Aircraft:
    """A high-wing plane: a fuselage centered on the origin plus a wide wing."""
    return Aircraft(
        id=pid,
        name=f"Winged {pid}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(
            Part(
                kind="fuselage",
                length_m=6.0,
                width_m=0.9,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.4,
            ),
            Part(
                kind="wing",
                length_m=1.4,
                width_m=span_m,
                offset_x_m=-0.5,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=1.4,
                z_top_m=1.8,
            ),
        ),
    )


def test_plan_path_clear_straight_is_a_single_analytic_shot() -> None:
    # Slot straight ahead, same heading: the start node's analytic Dubins shot
    # is clear, so the search returns immediately with that arc's endpoint.
    h = _hangar()
    plane = _winged_plane("A", span_m=6.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(10.0, 0.0, 0.0)
    goal = Pose(10.0, 12.0, 0.0)
    arc = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(10.0, abs=1e-6)
    assert last.y_m == pytest.approx(12.0, abs=1e-6)
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


def test_plan_path_finds_inbounds_path_when_direct_shot_sweeps_a_wing_out() -> None:
    # Moderately wide wing + a 90-degree final heading: the direct shortest Dubins
    # shot picks a long R-S-L arc that sweeps the left wing tip outside x=0, so the
    # search must maneuver. Geometry chosen so plan_path resolves within budget
    # (span=6 in a 14m hangar; the 10m-span/18m-hangar original is physically
    # infeasible for any heading-change path — see Task 3 report).
    # The returned path must be exact-oracle clean and land on the goal.
    h = _hangar(width_m=14.0, length_m=20.0)
    plane = _winged_plane("A", span_m=6.0, turn_radius_m=2.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(7.0, 0.0, 0.0)
    goal = Pose(7.0, 5.0, 90.0)
    arc = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(7.0, abs=1e-3)
    assert last.y_m == pytest.approx(5.0, abs=1e-3)
    assert abs(((last.heading_deg - 90.0 + 180.0) % 360.0) - 180.0) < 0.5
    # In-bounds invariant: the full path passes the exact (front-gap-exempt) oracle.
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


def test_plan_path_is_deterministic() -> None:
    h = _hangar(width_m=14.0, length_m=20.0)
    plane = _winged_plane("A", span_m=6.0, turn_radius_m=2.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry, goal = Pose(7.0, 0.0, 0.0), Pose(7.0, 5.0, 90.0)
    a = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    b = plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    assert a.segments == b.segments


def test_plan_path_bails_when_boxed_in() -> None:
    # A goal whose slot is itself jammed against a wall such that no in-bounds
    # approach exists within the budget -> NoFeasiblePlanError naming the mover.
    # span=11.8 fills nearly the full 12m hangar width; ANY heading-change path
    # is blocked (wing always clips a wall). Adapted from span=11.0 — span bumped
    # to 11.8 to ensure genuine infeasibility within _MAX_EXPANSIONS.
    h = _hangar(width_m=12.0, length_m=12.0)
    plane = _winged_plane("A", span_m=11.8, turn_radius_m=5.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    entry = Pose(6.0, 0.0, 0.0)
    goal = Pose(6.0, 6.0, 90.0)  # wing along x spans nearly the whole width while turning
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_path(plane, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    assert ei.value.plane_id == "A"


def test_plan_path_canary_pins_a_known_maneuver() -> None:
    # Pins the segment-kind sequence + length for a fixed maneuvering case, in
    # the spirit of the 45-degree Dubins canary (#189). If this changes, a
    # tie-break or primitive-order regression is the likely cause — investigate
    # before updating the expected values.
    # Geometry matches test_plan_path_finds_inbounds_path_when_direct_shot_sweeps_a_wing_out.
    h = _hangar(width_m=14.0, length_m=20.0)
    plane = _winged_plane("A", span_m=6.0, turn_radius_m=2.0)
    placed = Layout(fleet={"A": plane}, hangar=h, placements=())
    arc = plan_path(
        plane,
        Pose(7.0, 0.0, 0.0),
        Pose(7.0, 5.0, 90.0),
        hangar=h,
        placed=placed,
        mover_on_carts=False,
    )
    kinds = "".join(s.kind for s in arc.segments)
    # Values pinned from first green run (2026-05-26).
    assert kinds == "SSSRSL"
    assert arc.length_m == pytest.approx(16.81759168818229, abs=1e-6)

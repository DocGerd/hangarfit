import math

import pytest

from hangarfit.models import Aircraft, Door, Hangar, Layout, MaintenanceBay, Part, Placement
from hangarfit.towplanner import (
    DubinsArc,
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


def test_primitives_own_gear_are_six_in_lf_sf_rf_lr_sr_rr_order() -> None:
    # ADR-0010: six primitives — forward L/S/R then reverse L/S/R, in that
    # fixed deterministic order (the order the tie-break depends on).
    segs = _primitives(turn_radius_m=4.0)
    assert [s.kind for s in segs] == ["L", "S", "R", "L", "S", "R"]
    assert [s.gear for s in segs] == [1, 1, 1, -1, -1, -1]
    # Each is a positive-length short step.
    assert all(s.length_m > 0.0 for s in segs)


def test_primitives_cart_are_six_pivot_straight_in_order() -> None:
    # Cart (r == 0): the same six-primitive fan. Pivots encode one heading cell
    # in radians; the straights encode metres. Reverse straight is the new cart
    # move (ADR-0010); reverse pivots are redundant but kept for a uniform fan.
    segs = _primitives(turn_radius_m=0.0)
    assert [s.kind for s in segs] == ["L", "S", "R", "L", "S", "R"]
    assert [s.gear for s in segs] == [1, 1, 1, -1, -1, -1]
    assert segs[0].length_m == pytest.approx(math.radians(15.0))
    assert segs[1].length_m == pytest.approx(0.5)
    assert segs[2].length_m == pytest.approx(math.radians(15.0))
    assert segs[4].length_m == pytest.approx(0.5)  # reverse straight, metres


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
    # The mover must reach a heading-90 goal, but two parked planes flank the
    # turn region so closely that NO maneuver — forward OR reverse (ADR-0010) —
    # can reorient the wide wing without clipping a neighbour. The goal slot
    # itself IS a valid static placement; only the *approach* is impossible, so
    # the planner bails with NoFeasiblePlanError naming the mover.
    #
    # Note (Reeds–Shepp v2): the earlier span-11.8 "fills the hangar" framing no
    # longer guarantees infeasibility — reverse legs let a wide plane shuffle to
    # reorient even in a tight box. Genuine infeasibility now requires obstacles
    # that block every gear, not just a tight wall fit. A modest expansion
    # budget keeps the test fast while still proving the bail.
    h = _hangar(width_m=12.0, length_m=12.0)
    mover = _winged_plane("A", span_m=8.0, turn_radius_m=5.0)
    obs_left = _winged_plane("L", span_m=2.0)
    obs_right = _winged_plane("R", span_m=2.0)
    fleet = {"A": mover, "L": obs_left, "R": obs_right}
    placed = Layout(
        fleet=fleet,
        hangar=h,
        placements=(
            Placement("L", 1.5, 6.0, 0.0, on_carts=False),
            Placement("R", 10.5, 6.0, 0.0, on_carts=False),
        ),
    )
    entry = Pose(6.0, 0.0, 0.0)
    goal = Pose(6.0, 6.0, 90.0)
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_path(
            mover, entry, goal, hangar=h, placed=placed, mover_on_carts=False, max_expansions=100
        )
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
    gears = [s.gear for s in arc.segments]
    # Values pinned from first green run on the Reeds–Shepp motion model
    # (ADR-0010, 2026-05-26). The path now closes in a single LRL Reeds–Shepp
    # analytic shot whose final arc is driven in REVERSE (gear −1) — the
    # back-up-to-reorient maneuver Reeds–Shepp unlocks, far shorter than the
    # forward-only Dubins SSSRSL it replaced (16.82 m → 6.60 m). If this
    # changes, a tie-break or primitive-order regression is the likely cause —
    # investigate before updating the expected values.
    assert kinds == "LRL"
    assert gears == [1, 1, -1]
    assert arc.length_m == pytest.approx(6.601662791609364, abs=1e-6)


# ---------------------------------------------------------------------------
# Task 5: fast _motion_clear must match the exact oracle's verdict, and the
# returned path must always survive the exact-oracle safety net.
# ---------------------------------------------------------------------------


def test_motion_clear_matches_path_first_conflict_verdict_on_samples() -> None:
    # The fast checker and the exact oracle must agree (clear vs conflict) on a
    # set of probe poses: clear interior, side-wall clip, overlap with a placed
    # plane, and a front-door protrusion (clear, because front-gap-exempt).
    from hangarfit.towplanner import _build_obstacles, _motion_clear

    h = _hangar(width_m=18.0, length_m=25.0)
    a = _winged_plane("A", span_m=8.0)
    b = _winged_plane("B", span_m=8.0)
    placed = Layout(
        fleet={"A": a, "B": b},
        hangar=h,
        placements=(Placement("A", 4.0, 10.0, 0.0, on_carts=False),),
    )
    obstacles = _build_obstacles(placed, mover_id="B")
    probes = [
        Pose(12.0, 12.0, 0.0),  # clear interior
        Pose(0.0, 12.0, 90.0),  # wing pokes past x<0 side wall
        Pose(4.0, 10.0, 0.0),  # right on top of placed A -> overlap
        Pose(12.0, 0.0, 0.0),  # front protrusion (y<0) -> clear (exempt)
    ]
    for pose in probes:
        fast = _motion_clear(b, pose, obstacles, h)  # True iff clear
        exact = (
            path_first_conflict(
                DubinsArc(pose, pose, 5.0, (Segment("S", 0.0),)),
                b,
                mover_on_carts=False,
                placed=placed,
            )
            is None
        )
        assert fast == exact, f"divergence at {pose}: fast={fast} exact={exact}"


def test_motion_clear_matches_oracle_on_small_positive_z_gap() -> None:
    # Guards the gap-vs-wing_layer_clearance rule: two horizontally-overlapping
    # parts whose z-intervals do NOT overlap but are within wing_layer_clearance
    # must BOTH be flagged by the oracle and by _motion_clear (a naive z-interval
    # skip would wrongly call this clear). Build an obstacle part whose z sits a
    # small positive gap (< wing_layer_clearance_m) above the mover's top part.
    from hangarfit.towplanner import _build_obstacles, _motion_clear

    # wing_layer_clearance_m = 0.2. Two single-part planes whose only part is a
    # horizontal slab. Placed plane "A" sits z=1.6..2.0; mover "B" sits z=0.0..1.5.
    # The vertical gap is 1.6 - 1.5 = 0.1 m, strictly in (0, 0.2) => oracle flags.
    h = _hangar(width_m=18.0, length_m=25.0)  # wing_layer_clearance_m=0.2, clearance_m=0.3

    def _slab(pid: str, z_bottom: float, z_top: float) -> Aircraft:
        return Aircraft(
            id=pid,
            name=f"Slab {pid}",
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="wing",
                    length_m=4.0,
                    width_m=4.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=z_bottom,
                    z_top_m=z_top,
                ),
            ),
        )

    a = _slab("A", z_bottom=1.6, z_top=2.0)
    b = _slab("B", z_bottom=0.0, z_top=1.5)
    placed = Layout(
        fleet={"A": a, "B": b},
        hangar=h,
        placements=(Placement("A", 9.0, 12.0, 0.0, on_carts=False),),
    )
    obstacles = _build_obstacles(placed, mover_id="B")
    # Mover B coincident with A in plan view at this pose => plan-view overlap;
    # z-gap 0.1 m < 0.2 m wlc => oracle conflict.
    coincident = Pose(9.0, 12.0, 0.0)
    exact = path_first_conflict(
        DubinsArc(coincident, coincident, 5.0, (Segment("S", 0.0),)),
        b,
        mover_on_carts=False,
        placed=placed,
    )
    assert exact is not None, "oracle must flag the small-positive-z-gap overlap"
    fast = _motion_clear(b, coincident, obstacles, h)
    assert fast is False, "fast checker must also flag the small-positive-z-gap overlap"


def test_motion_clear_matches_oracle_on_bay_intrusion() -> None:
    # Guards the (C) bay-intrusion branch of _motion_clear, which the other
    # probes never reach (they set no maintenance_plane => bay_active is False).
    # With the bay CLOSED, a mover vertex strictly inside the bay must be flagged
    # by BOTH the exact oracle and _motion_clear; a mover clear of the bay must be
    # cleared by both. An inverted bay condition would pass the other tests but
    # fail here.
    from hangarfit.towplanner import _build_obstacles, _motion_clear

    h = _hangar(width_m=18.0, length_m=25.0)  # bay: x in (8, 10), y in (23, 25]

    def _small(pid: str) -> Aircraft:
        return Aircraft(
            id=pid,
            name=pid,
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="fuselage",
                    length_m=1.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.0,
                ),
            ),
        )

    # "M" is the bay occupant: in the fleet but NOT in placements (Layout
    # invariant), so the bay is a closed keep-out for the mover "B".
    fleet = {"B": _small("B"), "M": _small("M")}
    placed = Layout(fleet=fleet, hangar=h, placements=(), maintenance_plane="M")
    obstacles = _build_obstacles(placed, mover_id="B")
    assert obstacles.bay_active is True
    b = fleet["B"]
    probes = [
        Pose(9.0, 24.0, 0.0),  # 1x1 part spans (8.5,9.5)x(23.5,24.5) -> inside bay
        Pose(9.0, 12.0, 0.0),  # interior, clear of bay
        Pose(9.0, 22.0, 0.0),  # spans y (21.5,22.5), below bay front (23) -> clear
    ]
    for pose in probes:
        fast = _motion_clear(b, pose, obstacles, h)
        exact = (
            path_first_conflict(
                DubinsArc(pose, pose, 5.0, (Segment("S", 0.0),)),
                b,
                mover_on_carts=False,
                placed=placed,
            )
            is None
        )
        assert fast == exact, f"bay divergence at {pose}: fast={fast} exact={exact}"


def test_plan_path_result_always_passes_the_exact_oracle() -> None:
    # The safety net: whatever the search used, the returned arc is exact-clean.
    # Geometry matches the feasible maneuvering case (span=6, 14x20, turn 2,
    # goal 90 deg) settled on in Task 3 (span=10/18x25/234 deg is infeasible).
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
    assert path_first_conflict(arc, plane, mover_on_carts=False, placed=placed) is None


# ── Branch-coverage tests for the search internals ──────────────────────────


def _slab_plane(pid: str, z_bottom: float, z_top: float) -> Aircraft:
    """An own-gear plane whose single part is a flat 4x4 m slab at a fixed
    z-layer — lets a test place two parts that overlap in plan view but sit at
    chosen vertical gaps, to exercise the z pre-filter branches."""
    return Aircraft(
        id=pid,
        name=pid,
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        parts=(
            Part(
                kind="wing",
                length_m=4.0,
                width_m=4.0,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=z_bottom,
                z_top_m=z_top,
            ),
        ),
    )


def test_seg_cost_cart_pivot_is_penalty_times_radians() -> None:
    # Cart pivot (r == 0): no translation; the cost is the turn penalty over the
    # pivot radians (length_m encodes radians). Covers the r==0 branch of
    # _seg_cost (the own-gear test only exercises r > 0).
    from hangarfit.towplanner import _TURN_PENALTY

    radians = math.radians(15.0)
    assert _seg_cost(Segment("L", radians), turn_radius_m=0.0) == pytest.approx(
        _TURN_PENALTY * radians
    )


def test_build_obstacles_excludes_the_mover_from_its_own_obstacle_set() -> None:
    # If the mover is itself among placed.placements, _build_obstacles must skip
    # it — it is the thing being routed, not an obstacle. Covers the mover-skip
    # `continue` in _build_obstacles.
    from hangarfit.towplanner import _build_obstacles

    h = _hangar()
    a = _winged_plane("A", span_m=6.0)
    b = _winged_plane("B", span_m=6.0)
    placed = Layout(
        fleet={"A": a, "B": b},
        hangar=h,
        placements=(
            Placement("A", 5.0, 10.0, 0.0, on_carts=False),
            Placement("B", 12.0, 14.0, 0.0, on_carts=False),
        ),
    )
    obstacles = _build_obstacles(placed, mover_id="B")
    assert {wp.plane_id for wp in obstacles.world_parts} == {"A"}
    assert len(obstacles.world_parts) == len(a.parts)


def test_motion_clear_skips_obstacle_separated_beyond_wing_layer_clearance() -> None:
    # gap_z >= wing_layer_clearance: the parts cannot collide regardless of
    # plan-view overlap, so the z pre-filter skips the pair. _motion_clear is
    # clear and the exact oracle agrees. Covers the `gap_z >= wlc` skip branch.
    from hangarfit.towplanner import _build_obstacles, _motion_clear

    h = _hangar(width_m=18.0, length_m=25.0)  # wing_layer_clearance_m = 0.2
    mover = _slab_plane("B", z_bottom=0.0, z_top=1.0)  # z 0..1
    high = _slab_plane("A", z_bottom=2.0, z_top=3.0)  # z 2..3 -> gap_z 1.0 >= 0.2
    placed = Layout(
        fleet={"A": high, "B": mover},
        hangar=h,
        placements=(Placement("A", 9.0, 12.0, 0.0, on_carts=False),),
    )
    obstacles = _build_obstacles(placed, mover_id="B")
    pose = Pose(9.0, 12.0, 0.0)  # coincident with A in plan view, but z-separated
    assert _motion_clear(mover, pose, obstacles, h) is True
    exact_clear = (
        path_first_conflict(
            DubinsArc(pose, pose, 5.0, (Segment("S", 0.0),)),
            mover,
            mover_on_carts=False,
            placed=placed,
        )
        is None
    )
    assert exact_clear is True


def test_motion_clear_zero_wing_layer_clearance_skips_non_overlapping_z() -> None:
    # wlc == 0: the z pre-filter skips pairs whose z-intervals do not strictly
    # overlap (gap_z >= 0). Covers the `elif gap_z >= 0.0` (wlc == 0) branch.
    from hangarfit.towplanner import _build_obstacles, _motion_clear

    h = Hangar(
        length_m=25.0,
        width_m=18.0,
        door=Door(center_x_m=9.0, width_m=10.0),
        maintenance_bay=MaintenanceBay(center_x_m=9.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.0,
    )
    mover = _slab_plane("B", z_bottom=0.0, z_top=1.0)  # z 0..1
    touching = _slab_plane("A", z_bottom=1.0, z_top=2.0)  # z 1..2 -> gap_z 0.0 (>= 0)
    placed = Layout(
        fleet={"A": touching, "B": mover},
        hangar=h,
        placements=(Placement("A", 9.0, 12.0, 0.0, on_carts=False),),
    )
    obstacles = _build_obstacles(placed, mover_id="B")
    pose = Pose(9.0, 12.0, 0.0)
    # gap_z == 0 with wlc == 0 is "not strictly overlapping" -> no conflict.
    assert _motion_clear(mover, pose, obstacles, h) is True
    exact_clear = (
        path_first_conflict(
            DubinsArc(pose, pose, 5.0, (Segment("S", 0.0),)),
            mover,
            mover_on_carts=False,
            placed=placed,
        )
        is None
    )
    assert exact_clear is True


def test_plan_path_budget_exhaustion_breaks_and_raises_no_feasible_path() -> None:
    # A small expansion budget on a boxed-in, turned-goal case forces the budget
    # `break` and the `no_feasible_path` raise, and the multi-node exploration of
    # the constrained space exercises the stale-heap-entry skip — all without the
    # cost of a full-budget search. Two parked planes flank the heading-90 goal
    # so neither forward nor reverse (ADR-0010) can reorient the wide wing; the
    # goal slot itself is a valid placement, so the failure is "no reachable
    # approach within budget", surfaced as `no_feasible_path`.
    #
    # (Reeds–Shepp v2 note: the earlier open-hangar "empty turn interval"
    # framing no longer holds — reverse arcs reach turned goals a forward-only
    # Dubins car could not. Genuine infeasibility now needs gear-blocking
    # obstacles, not just a wall fit.)
    h = _hangar(width_m=12.0, length_m=12.0)
    mover = _winged_plane("A", span_m=8.0, turn_radius_m=5.0)
    obs_left = _winged_plane("L", span_m=2.0)
    obs_right = _winged_plane("R", span_m=2.0)
    fleet = {"A": mover, "L": obs_left, "R": obs_right}
    placed = Layout(
        fleet=fleet,
        hangar=h,
        placements=(
            Placement("L", 1.5, 6.0, 0.0, on_carts=False),
            Placement("R", 10.5, 6.0, 0.0, on_carts=False),
        ),
    )
    with pytest.raises(NoFeasiblePlanError) as ei:
        plan_path(
            mover,
            Pose(6.0, 0.0, 0.0),
            Pose(6.0, 6.0, 90.0),
            hangar=h,
            placed=placed,
            mover_on_carts=False,
            max_expansions=150,
        )
    assert ei.value.plane_id == "A"
    assert ei.value.conflict.kind == "no_feasible_path"


def test_plan_path_routes_around_a_parked_plane() -> None:
    # A plane parked in the straight-shot corridor blocks the direct Dubins path;
    # plan_path must detour around it and return an exact-oracle-clean,
    # multi-segment arc that still lands on the goal. This is the focused
    # plan_path-WITH-obstacles guard (the equivalence tests only probe
    # _motion_clear at single poses; the full search-against-obstacles path is
    # otherwise exercised only indirectly, via plan_fill).
    from hangarfit.towplanner import plan_dubins

    def _box(pid: str) -> Aircraft:
        return Aircraft(
            id=pid,
            name=pid,
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=2.0,
            measured=False,
            parts=(
                Part(
                    kind="fuselage",
                    length_m=2.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.4,
                ),
            ),
        )

    h = Hangar(
        length_m=16.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=10.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=2.0, depth_m=2.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )
    mover, parked = _box("M"), _box("X")
    placed = Layout(
        fleet={"M": mover, "X": parked},
        hangar=h,
        placements=(Placement("X", 10.0, 6.0, 0.0, on_carts=False),),
    )
    entry, goal = Pose(10.0, 0.0, 0.0), Pose(10.0, 12.0, 0.0)
    # The straight Dubins shot would drive through the parked plane.
    assert (
        path_first_conflict(
            plan_dubins(entry, goal, turn_radius_m=2.0),
            mover,
            mover_on_carts=False,
            placed=placed,
        )
        is not None
    )
    arc = plan_path(mover, entry, goal, hangar=h, placed=placed, mover_on_carts=False)
    # A detour (more than a single <=3-leg Dubins shot) that lands on the goal...
    assert len(arc.segments) > 3
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(10.0, abs=1e-3)
    assert last.y_m == pytest.approx(12.0, abs=1e-3)
    assert abs(((last.heading_deg - 0.0 + 180.0) % 360.0) - 180.0) < 0.5
    # ...and is exact-oracle clean against the parked plane.
    assert path_first_conflict(arc, mover, mover_on_carts=False, placed=placed) is None


def test_obstacles_rejects_mismatched_parallel_arrays() -> None:
    # The parallel-array invariant (world_parts || world_part_aabbs, same length)
    # is asserted at construction so a divergence fails loudly here, not deep in
    # the search. Covers the __post_init__ parity guard.
    from hangarfit.towplanner import _Obstacles

    with pytest.raises(ValueError, match="equal-length"):
        _Obstacles(
            world_parts=(),
            world_part_aabbs=((0.0, 0.0, 1.0, 1.0),),  # length 1 vs 0 -> mismatch
            bay_xmin=0.0,
            bay_xmax=0.0,
            bay_ymin=0.0,
            bay_active=False,
        )

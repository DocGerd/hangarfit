import random

import pytest

from hangarfit.geometry import aircraft_parts_world, cached_parts_world
from hangarfit.models import (
    Aircraft,
    Door,
    GroundObject,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    PlaneConstraint,
    Scenario,
    SearchConfig,
)
from hangarfit.solver import (
    _body,
    _body_parts_world,
    _build_layout,
    _descent_step,
    _initial_placements,
    _perturb_plane,
    solve,
)
from tests.conftest import make_test_aircraft


def _bounds_key(parts):
    return [(wp.plane_id, wp.kind, tuple(round(c, 9) for c in wp.polygon.bounds)) for wp in parts]


def test_body_returns_aircraft_for_fleet_id(region_scenario):
    b = _body(region_scenario, "fuji")
    assert isinstance(b, Aircraft) and b.id == "fuji"
    # identity: same object the solver looks up today
    assert b is region_scenario.fleet["fuji"]


def test_body_returns_ground_object_for_mover_id(region_scenario):
    b = _body(region_scenario, "glider_trailer_1")
    assert isinstance(b, GroundObject) and b.object_class == "placed_routed_mover"


def test_body_parts_world_aircraft_byte_identical(region_scenario):
    p = Placement(plane_id="fuji", x_m=10.0, y_m=12.0, heading_deg=30.0, on_carts=False)
    # aircraft path delegates to cached_parts_world on the SAME aircraft object
    got = _body_parts_world(region_scenario, "fuji", p)
    ref = cached_parts_world(region_scenario.fleet["fuji"], p)
    assert _bounds_key(got) == _bounds_key(ref)


def test_body_parts_world_mover_matches_uncached_transform(region_scenario):
    p = Placement(plane_id="glider_trailer_1", x_m=20.0, y_m=15.0, heading_deg=90.0, on_carts=False)
    got = _body_parts_world(region_scenario, "glider_trailer_1", p)
    ref = aircraft_parts_world(region_scenario.ground_object_defs["glider_trailer_1"], p)
    assert _bounds_key(got) == _bounds_key(ref)


def test_build_layout_aircraft_only_matches_plain_layout(region_scenario_no_go):
    s = region_scenario_no_go  # NO ground objects
    placements = {
        "fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False),
        "cessna_150": Placement("cessna_150", 12.0, 9.0, 0.0, on_carts=False),
    }
    built = _build_layout(s, placements)
    plain = Layout(
        fleet=s.fleet,
        hangar=s.hangar,
        placements=tuple(placements.values()),
        maintenance_plane=s.maintenance_plane,
    )
    assert built.placements == plain.placements  # same order, same poses
    assert built.ground_object_placements == ()


def test_build_layout_splits_movers_and_injects_fixed(region_scenario):
    s = region_scenario  # fuji+cessna_150 ; movers glider_trailer_1/2 ; fixed maul_fuel_trailer
    placements = {
        "fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False),
        "cessna_150": Placement("cessna_150", 12.0, 9.0, 0.0, on_carts=False),
        "glider_trailer_1": Placement("glider_trailer_1", 20.0, 20.0, 90.0, on_carts=False),
    }
    built = _build_layout(s, placements)
    assert {p.plane_id for p in built.placements} == {"fuji", "cessna_150"}
    go_ids = {p.plane_id for p in built.ground_object_placements}
    assert "glider_trailer_1" in go_ids and "maul_fuel_trailer" in go_ids  # mover + injected fixed


def test_initial_placements_samples_movers(region_scenario):
    pl = _initial_placements(
        scenario=region_scenario, rng=random.Random(0), cart_bucket=frozenset()
    )
    assert "glider_trailer_1" in pl and "glider_trailer_2" in pl
    assert pl["glider_trailer_1"].on_carts is False
    # aircraft still present
    assert "fuji" in pl and "cessna_150" in pl


def test_initial_placements_deterministic_with_movers(region_scenario):
    a = _initial_placements(scenario=region_scenario, rng=random.Random(7), cart_bucket=frozenset())
    b = _initial_placements(scenario=region_scenario, rng=random.Random(7), cart_bucket=frozenset())

    def key(d):
        return {k: (v.x_m, v.y_m, v.heading_deg, v.on_carts) for k, v in d.items()}

    assert key(a) == key(b)


def test_initial_placements_no_go_same_seed(region_scenario_no_go):
    a = _initial_placements(
        scenario=region_scenario_no_go, rng=random.Random(7), cart_bucket=frozenset()
    )
    b = _initial_placements(
        scenario=region_scenario_no_go, rng=random.Random(7), cart_bucket=frozenset()
    )
    assert {k: (v.x_m, v.y_m) for k, v in a.items()} == {k: (v.x_m, v.y_m) for k, v in b.items()}
    assert "glider_trailer_1" not in a  # no movers in the no-GO scenario


def test_perturb_mover_no_keyerror_and_stays_off_carts(region_scenario):
    cur = Placement("glider_trailer_1", 20.0, 20.0, 90.0, on_carts=False)
    out = _perturb_plane(
        current=cur,
        scenario=region_scenario,
        rng=random.Random(0),
        search=SearchConfig(),
        large_jump=False,
    )
    assert out.plane_id == "glider_trailer_1"
    assert out.on_carts is False


def test_descent_step_handles_layout_with_mover(region_scenario):
    # A placements dict that includes a mover overlapping an aircraft must not
    # KeyError, and the descent must be able to choose the mover as a target.
    placements = {
        "fuji": Placement("fuji", 12.0, 12.0, 0.0, on_carts=False),
        "cessna_150": Placement("cessna_150", 12.2, 12.0, 0.0, on_carts=False),  # overlap
        # overlap (mover):
        "glider_trailer_1": Placement("glider_trailer_1", 12.1, 12.0, 0.0, on_carts=False),
    }
    out = _descent_step(
        placements=placements,
        scenario=region_scenario,
        rng=random.Random(1),
        search=SearchConfig(),
        current_score=(99, 9.9),
        pinned_planes=frozenset(),
    )
    # returns a (placements, score, accepted) triple (or None if all conflicts pinned);
    # the key assertion is that it ran without KeyError on the mover id.
    assert out is None or (isinstance(out, tuple) and len(out) == 3)


def test_solve_with_movers_byte_identical_same_seed(region_scenario):
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(region_scenario, search=cfg, seed=42, budget_s=120.0, plan_paths=False)
    b = solve(region_scenario, search=cfg, seed=42, budget_s=120.0, plan_paths=False)

    def sig(result):
        return [
            [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in layout.placements]
            + [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in layout.ground_object_placements]
            for layout in result.layouts
        ]

    assert a.layouts  # the demo scenario solves
    assert sig(a) == sig(b)  # aircraft AND mover poses bit-identical (max_restarts-scoped)


@pytest.mark.slow
def test_solve_with_caddy_exercises_egress_gate(region_scenario_with_caddy):
    # The VW Caddy is a hard_door_mover: once the solver places it, the #603
    # egress gate runs in solve (inert before #604). Assert solve completes with a
    # well-formed status and — when a layout is returned — the caddy is in that
    # layout's ground_object_placements (so the gate operated on a caddy-bearing
    # layout). A trapped caddy would drop the plan / mark it unroutable, not raise.
    # tow_max_expansions=4000 keeps the routing budget bounded (~60-90 s on a
    # typical dev machine); reduce to 2000 if this flakes on the CI timing gate.
    r = solve(
        region_scenario_with_caddy,
        search=SearchConfig(max_restarts=4, spread=True),
        seed=0,
        budget_s=120.0,
        plan_paths=True,
        tow_max_expansions=4000,
    )
    assert r.status in ("found", "found_partial", "exhausted_budget")
    if r.layouts:
        go_ids = {p.plane_id for p in r.layouts[0].ground_object_placements}
        assert "vw_caddy" in go_ids  # the hard-door mover is in the gated layout


# ── #912 Task 4: solver seats a pinned mover path-less at its pin ──────────
#
# A ``Scenario.mover_pins`` entry short-circuits a placed_routed_mover to its
# hand-placed pose (see ``solver._initial_placement_for_plane``), excludes it
# from the descent/spread search (``pinned_planes``), and the T3 tow planner
# (already shipped) seats it as a path-less keep-out. These tests build small,
# fully-controlled scenarios (the aircraft is ALSO pinned wherever tow-routing
# is exercised) so the egress-gate assertions are deterministic rather than
# depending on where a randomly-placed aircraft happens to land.


def _pin_hangar() -> Hangar:
    """A roomy 40x40 hangar (mirrors ``test_solver_door_order.py``'s ``_hangar``)."""
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _pin_caddy() -> GroundObject:
    """A hard_door_mover placed_routed_mover (mirrors the VW Caddy in
    ``test_solver_towplanner.py::test_hard_door_mover_egress_blocked_is_unroutable``)."""
    return GroundObject(
        id="caddy",
        name="VW Caddy",
        parts=(
            Part(
                kind="ground",
                length_m=4.5,
                width_m=1.8,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.5,
            ),
        ),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=5.5,
        hard_door_mover=True,
    )


def _pin_wall() -> GroundObject:
    """A fixed_obstacle spanning the full hangar width — used to box the
    pinned Caddy off from the door."""
    return GroundObject(
        id="wall",
        name="Wall",
        parts=(
            Part(
                kind="ground",
                length_m=1.0,
                width_m=39.8,  # spans the 40 m hangar width, just inside bounds
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=1.5,
            ),
        ),
        object_class="fixed_obstacle",
    )


def _pin_scenario(
    pin: Placement,
    *,
    aircraft_pin: Placement | None = None,
    extra_ground_objects: tuple[GroundObject, ...] = (),
    fixed_obstacle_placements: tuple[Placement, ...] = (),
) -> Scenario:
    """A one-aircraft scenario with the Caddy mover PINNED at ``pin`` (#912).

    ``aircraft_pin``, when given, hard-pins the sole aircraft ``"a"`` too — used
    by the tow-routing tests so the aircraft's position (and hence whether it
    could incidentally block the Caddy's egress corridor) is deterministic
    rather than left to the random initial-placement draw.
    """
    caddy = _pin_caddy()
    defs = {caddy.id: caddy, **{go.id: go for go in extra_ground_objects}}
    constraints = {} if aircraft_pin is None else {"a": PlaneConstraint(pin=aircraft_pin)}
    return Scenario(
        fleet={"a": make_test_aircraft(id="a")},
        hangar=_pin_hangar(),
        fleet_in=("a",),
        constraints=constraints,
        ground_objects=(caddy.id, *(go.id for go in extra_ground_objects)),
        ground_object_defs=defs,
        fixed_obstacle_placements=fixed_obstacle_placements,
        mover_pins={caddy.id: pin},
    )


def test_pinned_mover_seats_at_its_pin_and_is_pathless():
    pin = Placement("caddy", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False, hand_placed=True)
    aircraft_pin = Placement("a", x_m=30.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    scenario = _pin_scenario(pin, aircraft_pin=aircraft_pin)

    result = solve(
        scenario,
        search=SearchConfig(max_restarts=1, spread=False),
        seed=1,
        budget_s=10.0,
        plan_paths=True,
        tow_max_expansions=2000,
    )

    assert result.layouts
    layout = result.layouts[0]
    gp = next(p for p in layout.ground_object_placements if p.plane_id == "caddy")
    assert (gp.x_m, gp.y_m, gp.heading_deg) == (pin.x_m, pin.y_m, pin.heading_deg)
    assert gp.hand_placed is True

    plan = result.plans[0]
    assert plan is not None, "caddy's egress is clear in this open scenario; plan must build"
    caddy_moves = [m for m in plan.moves if m.plane_id == "caddy"]
    assert caddy_moves, "the pinned caddy must still appear in the moves plan"
    assert all(m.path is None for m in caddy_moves), "a pinned mover's move(s) must be path-less"


def test_pinned_mover_is_excluded_from_the_search():
    # The aircraft is left UNPINNED here (unlike the tow-routing tests above/
    # below) so the two seeds draw genuinely different initial-placement RNG —
    # the point is that despite that, the caddy (pinned) lands identically.
    pin = Placement("caddy", x_m=10.0, y_m=20.0, heading_deg=90.0, on_carts=False, hand_placed=True)
    scenario = _pin_scenario(pin)

    def _mover_pose(result):
        gp = next(p for p in result.layouts[0].ground_object_placements if p.plane_id == "caddy")
        return (gp.x_m, gp.y_m, gp.heading_deg)

    r1 = solve(
        scenario,
        search=SearchConfig(max_restarts=1, spread=False),
        seed=1,
        budget_s=10.0,
        plan_paths=False,
    )
    r2 = solve(
        scenario,
        search=SearchConfig(max_restarts=1, spread=False),
        seed=2,
        budget_s=10.0,
        plan_paths=False,
    )

    assert r1.layouts and r2.layouts
    assert _mover_pose(r1) == _mover_pose(r2) == (pin.x_m, pin.y_m, pin.heading_deg)


def test_pinned_caddy_blocking_the_door_is_rejected():
    # Wall spans the full width at y=15; caddy pinned at y=30 (behind the
    # wall, far from the door at y=0) has no clear egress corridor — the #603
    # egress gate (unchanged by #912; ADR-0026) must reject the plan the same
    # way it rejects a SOLVER-PLACED boxed-in Caddy (see
    # test_solver_towplanner.py::test_hard_door_mover_egress_blocked_is_unroutable).
    wall = _pin_wall()
    pin = Placement("caddy", x_m=20.0, y_m=30.0, heading_deg=0.0, on_carts=False, hand_placed=True)
    aircraft_pin = Placement("a", x_m=30.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    scenario = _pin_scenario(
        pin,
        aircraft_pin=aircraft_pin,
        extra_ground_objects=(wall,),
        fixed_obstacle_placements=(
            Placement("wall", x_m=20.0, y_m=15.0, heading_deg=0.0, on_carts=False),
        ),
    )

    result = solve(
        scenario,
        search=SearchConfig(max_restarts=1, spread=False),
        seed=1,
        budget_s=10.0,
        plan_paths=True,
        tow_max_expansions=2000,
    )

    # The static layout is still valid (the caddy/wall don't collide with "a")
    # — only its tow plan is rejected by the #603 egress gate, the same
    # exit-3-equivalent path a solver-placed boxed-in mover takes (the gate
    # converts NoFeasiblePlanError into unroutable_planes + plans[i]=None
    # rather than propagating the exception out of solve()).
    assert result.layouts
    layout = result.layouts[0]
    gp = next(p for p in layout.ground_object_placements if p.plane_id == "caddy")
    # Pin honored (not incidentally landed behind the wall by the free-search
    # draw) — pins this test's failure mode to the actual feature, not luck.
    assert (gp.x_m, gp.y_m, gp.heading_deg) == (pin.x_m, pin.y_m, pin.heading_deg)
    assert gp.hand_placed is True
    assert "caddy" in result.diagnostics.unroutable_planes
    assert result.plans[0] is None

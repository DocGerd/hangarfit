"""Obstacle-aware grid heuristic for the Hybrid-A* tow planner (#332 spike).

These tests pin the contract of the opt-in ``heuristic="grid"`` seam added by the
towplanner-v2 routability spike:

* the DEFAULT (``"euclidean"``) path is byte-for-byte unchanged — adding the
  ``stats`` out-param or passing ``heuristic="euclidean"`` explicitly produces an
  identical :class:`~hangarfit.towplanner.DubinsArc` (the ADR-0003 determinism
  contract; the existing canaries in ``test_solver_canaries.py`` and the other
  ``test_towplanner_*`` files cover the rest);
* ``heuristic="grid"`` is itself deterministic (RNG-free Dijkstra) — identical
  bytes across repeat runs;
* a ``"grid"`` path is exact-oracle-clean (the heuristic only re-orders the
  frontier; the validity oracle is untouched — the #332 proposer-verifier split);
* the geodesic field is well-formed (0 at the goal, increasing with distance,
  finite where free, and absent — i.e. ``+inf``/fallback — where walled off).

These pin the *correctness* and *determinism* of the seam — NOT a routability
win. Whether the grid heuristic buys routability is an empirical question the
spike benchmark (``docs/spikes/towplanner_v2_routability_bench.py``) and the spike
doc answer, and the honest answer is "no, not on these fixtures": their failures
are budget-exhausted *finite-width maneuvering*, not interior-obstacle clutter, so
a 2-D cost-to-go heuristic (this one, or a learned one) is inert. There is
therefore deliberately **no** "grid routes what euclidean cannot" test — it would
not hold, on the fixtures *or* on constructed bug-traps (once a detour is forced,
the tight-maneuver bottleneck defeats both heuristics within budget). The seam is
kept as a tested, opt-in PoC and the home for a future clutter/learned heuristic.
"""

from __future__ import annotations

import pytest

from hangarfit.cli import build_parser
from hangarfit.loader import load_layout, load_scenario
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
from hangarfit.solver import solve
from hangarfit.towplanner import (
    DubinsArc,
    NoFeasiblePlanError,
    Pose,
    _build_grid_heuristic,
    _build_obstacles,
    entry_poses,
    path_first_conflict,
    plan_fill,
    plan_path,
)

_TAIL_WHEELS = Wheels(main_offset_x_m=0.20, track_m=1.8, third_wheel_offset_x_m=-2.0)


def _box_plane(plane_id: str, *, turn_radius_m: float = 4.0) -> Aircraft:
    return Aircraft(
        id=plane_id,
        name=f"Plane {plane_id}",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
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
        wheels=_TAIL_WHEELS,
    )


def _hangar(width_m: float = 20.0, length_m: float = 30.0) -> Hangar:
    return Hangar(
        length_m=length_m,
        width_m=width_m,
        door=Door(center_x_m=width_m / 2, width_m=6.0),
        maintenance_bay=MaintenanceBay(center_x_m=width_m / 2, width_m=2.0, depth_m=2.0),
        clearance_m=0.5,
        wing_layer_clearance_m=0.3,
    )


def _route_one(
    layout: Layout, mover_id: str, *, heuristic: str, max_expansions: int = 700, stats=None
) -> DubinsArc:
    """Route ``mover_id`` to its slot in ``layout`` against every OTHER plane as
    an obstacle (a single-plane harness independent of plan_fill's ordering)."""
    slot = next(p for p in layout.placements if p.plane_id == mover_id)
    others = tuple(p for p in layout.placements if p.plane_id != mover_id)
    placed = Layout(
        fleet=layout.fleet,
        hangar=layout.hangar,
        placements=others,
        maintenance_plane=layout.maintenance_plane,
    )
    cone = entry_poses(slot, layout.hangar)
    return plan_path(
        layout.fleet[mover_id],
        cone[0],
        Pose.from_placement(slot),
        hangar=layout.hangar,
        placed=placed,
        mover_on_carts=slot.on_carts,
        entries=cone,
        heuristic=heuristic,  # type: ignore[arg-type]
        max_expansions=max_expansions,
        stats=stats,
    )


# ── default path is byte-identical (determinism contract) ────────────────────


def test_default_heuristic_and_stats_do_not_change_the_path() -> None:
    """Default == explicit euclidean == euclidean-with-stats, bit-for-bit."""
    h = _hangar()
    fleet = {"A": _box_plane("A")}
    layout = Layout(
        fleet=fleet, hangar=h, placements=(Placement("A", 10.0, 20.0, 0.0, on_carts=False),)
    )

    default = _route_one(layout, "A", heuristic="euclidean")
    # The literal default (no heuristic kwarg at all) must match too.
    slot = layout.placements[0]
    cone = entry_poses(slot, h)
    empty = Layout(fleet=fleet, hangar=h, placements=())
    bare = plan_path(
        fleet["A"],
        cone[0],
        Pose.from_placement(slot),
        hangar=h,
        placed=empty,
        mover_on_carts=False,
        entries=cone,
    )
    stats: dict[str, object] = {}
    with_stats = _route_one(layout, "A", heuristic="euclidean", stats=stats)

    assert default.segments == bare.segments
    assert default.segments == with_stats.segments
    assert default.start == bare.start == with_stats.start
    # The stats out-param was populated without perturbing the result.
    assert stats["found"] is True


# ── grid mode determinism ────────────────────────────────────────────────────


def test_grid_heuristic_is_deterministic() -> None:
    """``heuristic="grid"`` is RNG-free ⇒ byte-identical across repeat runs."""
    layout = load_layout("tests/fixtures/valid_all_nine_planes.yaml")
    mover = min(layout.placements, key=lambda p: p.y_m).plane_id  # a shallow, routable plane
    stats: dict[str, object] = {}
    a = _route_one(layout, mover, heuristic="grid", stats=stats)
    b = _route_one(layout, mover, heuristic="grid")
    assert a.segments == b.segments
    assert a.start == b.start
    assert a.turn_radius_m == b.turn_radius_m
    # The stats payload the #332 harness reads is populated on a GRID success too
    # (the success branch is only asserted on the euclidean path elsewhere).
    assert stats["found"] is True
    assert stats["heuristic"] == "grid"
    assert stats["budget_exhausted"] is False and stats["space_exhausted"] is False
    assert isinstance(stats["expansions"], int) and isinstance(stats["start_poses"], int)


# ── grid path is exact-oracle clean (proposer/verifier) ──────────────────────


def test_grid_path_is_oracle_clean() -> None:
    layout = load_layout("tests/fixtures/valid_all_nine_planes.yaml")
    mover = min(layout.placements, key=lambda p: p.y_m).plane_id
    others = tuple(p for p in layout.placements if p.plane_id != mover)
    placed = Layout(
        fleet=layout.fleet,
        hangar=layout.hangar,
        placements=others,
        maintenance_plane=layout.maintenance_plane,
    )
    slot = next(p for p in layout.placements if p.plane_id == mover)
    arc = _route_one(layout, mover, heuristic="grid")
    assert (
        path_first_conflict(arc, layout.fleet[mover], mover_on_carts=slot.on_carts, placed=placed)
        is None
    )


# ── grid field is well-formed ────────────────────────────────────────────────


def test_grid_field_zero_at_goal_and_increases_with_distance() -> None:
    h = _hangar(width_m=20.0, length_m=30.0)
    empty = Layout(fleet={"A": _box_plane("A")}, hangar=h, placements=())
    obstacles = _build_obstacles(empty, mover_id="A")
    goal = Pose(x_m=10.0, y_m=20.0, heading_deg=0.0)
    field = _build_grid_heuristic(goal, obstacles, h)

    goal_cell = (round(goal.x_m / 0.5), round(goal.y_m / 0.5))
    near_cell = (round(10.0 / 0.5), round(15.0 / 0.5))  # 5 m shy in y
    door_cell = (round(10.0 / 0.5), round(0.0 / 0.5))  # at the door

    assert field[goal_cell] == pytest.approx(0.0)
    assert field[near_cell] > field[goal_cell]
    assert field[door_cell] > field[near_cell]
    # Empty hangar: the geodesic equals the straight-line distance (no detour).
    assert field[door_cell] == pytest.approx(20.0, abs=1.0)


def test_grid_field_marks_unreachable_cells_absent() -> None:
    """A goal walled off behind a full-width obstacle band is unreachable: the
    door-side cells are simply absent from the field (the planner then falls back
    to Euclidean there, and the search exhausts its budget — genuine
    infeasibility, which no heuristic can fix)."""
    h = _hangar(width_m=8.0, length_m=30.0)
    # A wide, deep obstacle plane spanning the whole width near mid-depth.
    wall = Aircraft(
        id="W",
        name="wall",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=4.0,
                width_m=10.0,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=3.0,
            ),
        ),
        wheels=_TAIL_WHEELS,
    )
    layout = Layout(
        fleet={"W": wall, "A": _box_plane("A")},
        hangar=h,
        placements=(Placement("W", 4.0, 15.0, 0.0, on_carts=False),),
    )
    obstacles = _build_obstacles(layout, mover_id="A")
    goal = Pose(x_m=4.0, y_m=25.0, heading_deg=0.0)  # behind the wall
    field = _build_grid_heuristic(goal, obstacles, h)
    door_cell = (round(4.0 / 0.5), round(0.0 / 0.5))
    assert door_cell not in field  # walled off from the goal


# ── stats out-param ──────────────────────────────────────────────────────────


def test_stats_records_outcome_on_failure() -> None:
    """A boxed-in goal at a tiny budget records found=False with the cap flag."""
    h = _hangar(width_m=8.0, length_m=30.0)
    wall = Aircraft(
        id="W",
        name="wall",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=4.0,
                width_m=10.0,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=3.0,
            ),
        ),
        wheels=_TAIL_WHEELS,
    )
    layout = Layout(
        fleet={"W": wall, "A": _box_plane("A")},
        hangar=h,
        placements=(
            Placement("W", 4.0, 15.0, 0.0, on_carts=False),
            Placement("A", 4.0, 25.0, 0.0, on_carts=False),
        ),
    )
    stats: dict[str, object] = {}
    with pytest.raises(NoFeasiblePlanError):
        _route_one(layout, "A", heuristic="euclidean", max_expansions=50, stats=stats)
    assert stats["found"] is False
    assert isinstance(stats["expansions"], int)
    # A tiny budget against a routable-but-hard goal hits the cap: this pins the
    # ``budget_exhausted`` arm specifically (the XOR alone would pass either way).
    assert stats["budget_exhausted"] is True
    assert stats["space_exhausted"] is False
    assert stats["expansions"] == 50  # the cap-break sets expansions == max_expansions
    # NOTE on the OTHER arm: ``space_exhausted=True`` requires the open heap to
    # drain before the cap — i.e. a FINITE reachable state set. With the #222
    # front-gap exemption the apron (``y < 0``) is unbounded, so the mover can
    # always reverse straight out the door onto fresh cells; the frontier never
    # empties and the cap always fires first. The spike benchmark corroborates
    # this — *every* un-routed plane exited ``budget_exhausted``, none on space.
    # ``space_exhausted`` is therefore a latent/defensive branch (it would matter
    # only if a future change bounded the apron), so there is intentionally no
    # fixture exercising it: none exists that wouldn't have to fight the planner's
    # own geometry. See the PR thread on this assertion for the rationale.


# ── opt-in plumbing through plan_fill / solve (the #332 flags) ───────────────


def test_plan_fill_accepts_grid_heuristic_and_explicit_budget() -> None:
    """``plan_fill(..., heuristic="grid", max_expansions=N)`` routes a friendly
    two-plane layout — exercises the grid forward + the explicit-budget path."""
    h = _hangar(width_m=20.0, length_m=30.0)
    fleet = {"A": _box_plane("A"), "B": _box_plane("B")}
    layout = Layout(
        fleet=fleet,
        hangar=h,
        placements=(
            Placement("A", 8.0, 8.0, 0.0, on_carts=False),  # shallow
            Placement("B", 12.0, 22.0, 0.0, on_carts=False),  # deep
        ),
    )
    plan = plan_fill(layout, heuristic="grid", max_expansions=400)
    assert [m.plane_id for m in plan.moves] == ["B", "A"]  # deepest first


def test_solve_accepts_grid_tow_heuristic() -> None:
    """``solve(..., tow_heuristic="grid", tow_max_expansions=N)`` runs the opt-in
    grid branch in the solver. The plane may or may not route (best-effort); the
    point is the layout is still returned and the grid code path is exercised."""
    scenario = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    result = solve(
        scenario,
        budget_s=10.0,
        alternatives=1,
        seed=1,
        plan_paths=True,
        tow_heuristic="grid",
        tow_max_expansions=80,
    )
    assert result.layouts  # a feasible single-plane scenario always yields a layout
    assert len(result.plans) == len(result.layouts)  # plans index-aligned (None allowed)


# ── grid-field branch coverage (bay keep-out, off-grid fallback, edge bounds) ─


def _wall_plane(plane_id: str, *, width_m: float) -> Aircraft:
    return Aircraft(
        id=plane_id,
        name=plane_id,
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=4.0,
        measured=False,
        parts=(
            Part(
                kind="fuselage_aft",
                length_m=2.0,
                width_m=width_m,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=0.0,
                z_top_m=3.0,
            ),
        ),
        wheels=_TAIL_WHEELS,
    )


def test_grid_field_excludes_closed_maintenance_bay_cells() -> None:
    """With a maintenance occupant set the bay is a keep-out: cells inside it are
    absent from the field (covers the ``bay_active`` branch of ``_cell_free``)."""
    h = _hangar(width_m=20.0, length_m=30.0)  # bay center x=10, width 2, depth 2 → x(9,11), y>28
    fleet = {"A": _box_plane("A"), "M": _box_plane("M")}
    goal = Pose(x_m=10.0, y_m=20.0, heading_deg=0.0)
    bay_cell = (round(10.0 / 0.5), round(29.0 / 0.5))  # inside the back-anchored bay

    open_layout = Layout(fleet=fleet, hangar=h, placements=())  # bay OPEN (no occupant)
    open_field = _build_grid_heuristic(goal, _build_obstacles(open_layout, mover_id="A"), h)
    assert bay_cell in open_field  # reachable when the bay is just floor

    closed_layout = Layout(fleet=fleet, hangar=h, placements=(), maintenance_plane="M")
    closed_field = _build_grid_heuristic(goal, _build_obstacles(closed_layout, mover_id="A"), h)
    assert bay_cell not in closed_field  # the closed bay keeps the mover out


def test_grid_search_falls_back_to_euclidean_off_grid() -> None:
    """When the goal is walled off, the door-side start cells are absent from the
    field, so the grid ``_h`` returns the Euclidean fallback (``g is None``); the
    search then exhausts its (tiny) budget — covers the fallback path."""
    h = _hangar(width_m=8.0, length_m=30.0)
    wall = _wall_plane("W", width_m=10.0)  # spans the full 8 m width near mid-depth
    layout = Layout(
        fleet={"W": wall, "A": _box_plane("A")},
        hangar=h,
        placements=(
            Placement("W", 4.0, 15.0, 0.0, on_carts=False),
            Placement("A", 4.0, 25.0, 0.0, on_carts=False),  # behind the wall
        ),
    )
    with pytest.raises(NoFeasiblePlanError):
        _route_one(layout, "A", heuristic="grid", max_expansions=60)


def test_grid_field_blocks_cells_past_non_aligned_walls() -> None:
    """A hangar dimension that is not a multiple of the grid pitch puts an edge
    cell centre just past the wall; ``_cell_free`` rejects it (covers the
    side/back-wall bound in the field builder)."""
    h = _hangar(width_m=8.3, length_m=12.7)  # neither a multiple of 0.5 m
    empty = Layout(fleet={"A": _box_plane("A")}, hangar=h, placements=())
    field = _build_grid_heuristic(
        Pose(x_m=4.0, y_m=10.0, heading_deg=0.0), _build_obstacles(empty, mover_id="A"), h
    )
    # No field cell centre may lie outside the side/back walls.
    assert all(0.0 <= ix * 0.5 <= h.width_m and iy * 0.5 <= h.length_m for (ix, iy) in field)
    # The cell whose centre (8.5 m) overshoots the 8.3 m wall must be absent.
    assert (round(8.5 / 0.5), round(10.0 / 0.5)) not in field


# ── CLI flag wiring (the #332 experimental knobs) ────────────────────────────


def test_cli_tow_flags_default_to_the_byte_identical_shipped_planner() -> None:
    """No ``--tow-*`` flags ⇒ the namespace defaults that route ``solve()`` down
    the unchanged ``plan_fill(layout)`` path (the byte-identical-default guard)."""
    args = build_parser().parse_args(["solve", "scenario.yaml"])
    assert args.tow_heuristic == "euclidean"
    assert args.tow_max_expansions is None


def test_cli_tow_flags_parse_the_opt_in_values() -> None:
    """The opt-in values reach the namespace with the right dests/types."""
    args = build_parser().parse_args(
        ["solve", "scenario.yaml", "--tow-heuristic", "grid", "--tow-max-expansions", "2000"]
    )
    assert args.tow_heuristic == "grid"
    assert args.tow_max_expansions == 2000


def test_cli_tow_heuristic_rejects_unknown_choice() -> None:
    """``choices`` guards against a typo silently flowing into the planner."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["solve", "scenario.yaml", "--tow-heuristic", "astar"])

"""#614 SOFT door-priority tie-breaker — solver selection term.

``door_order`` orders otherwise-equally-valid layouts by door-proximity: the
deviation is a Kendall-tau inversion count over the placed ``door_order`` bodies
ranked by ``y_m`` (smaller ``y`` = nearer the door at ``y=0``). It is a
selection-time term, lexicographically subordinate to every HARD rule (the pool
``_select_spread_diverse`` ranks is already collision-valid) and ABOVE the
ADR-0008 spread terms — so a door-order-matching valid layout beats a
door-order-violating valid one, but no door order can make an invalid layout
selectable. Unset ⇒ a constant ``0.0`` deviation ⇒ byte-identical solver (ADR-0003).
"""

from hangarfit.collisions import check
from hangarfit.models import (
    DiversityConfig,
    Door,
    Hangar,
    MaintenanceBay,
    Placement,
    Scenario,
    SearchConfig,
)
from hangarfit.solver import (
    _door_order_deviation,
    _empty_layout,
    _select_spread_diverse,
    _SpreadCandidate,
    solve,
)
from tests.conftest import make_test_aircraft  # noqa: E402


def _hangar() -> Hangar:
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
    )


def _scenario(**kwargs) -> Scenario:
    fleet = {pid: make_test_aircraft(id=pid) for pid in ("a", "b", "c")}
    return Scenario(
        fleet=fleet,
        hangar=_hangar(),
        fleet_in=("a", "b", "c"),
        **kwargs,
    )


def _pl(pid: str, y: float) -> Placement:
    return Placement(pid, 10.0, y, 0.0, on_carts=False)


# --- _door_order_deviation metric -----------------------------------------


def test_door_order_deviation_unset_is_zero():
    s = _scenario()  # door_order is None
    placements = {"a": _pl("a", 5.0), "b": _pl("b", 1.0)}  # any arrangement
    assert _door_order_deviation(placements, s) == 0.0  # inert ⇒ byte-identical


def test_door_order_deviation_perfect_order_is_zero():
    s = _scenario(door_order=("a", "b", "c"))
    # a nearest the door (smallest y), then b, then c — matches the request
    placements = {"a": _pl("a", 1.0), "b": _pl("b", 5.0), "c": _pl("c", 9.0)}
    assert _door_order_deviation(placements, s) == 0.0


def test_door_order_deviation_full_reversal_counts_all_pairs():
    s = _scenario(door_order=("a", "b", "c"))
    # exactly reversed: c nearest, a farthest ⇒ all 3 pairs inverted
    placements = {"a": _pl("a", 9.0), "b": _pl("b", 5.0), "c": _pl("c", 1.0)}
    assert _door_order_deviation(placements, s) == 3.0


def test_door_order_deviation_single_inversion():
    s = _scenario(door_order=("a", "b", "c"))
    # a and b swapped (b before a), c correct ⇒ one inverted pair (a,b)
    placements = {"a": _pl("a", 5.0), "b": _pl("b", 1.0), "c": _pl("c", 9.0)}
    assert _door_order_deviation(placements, s) == 1.0


def test_door_order_deviation_skips_absent_bodies():
    s = _scenario(door_order=("a", "b", "c"))
    # b absent (e.g. a maintenance plane treated as away): rank only a,c —
    # a nearer than c matches their relative request order ⇒ 0 inversions
    placements = {"a": _pl("a", 1.0), "c": _pl("c", 9.0)}
    assert _door_order_deviation(placements, s) == 0.0


# --- _select_spread_diverse ordering --------------------------------------


def test_door_order_dominates_spread_in_selection():
    """A door-order-matching valid candidate (lower deviation) is selected over a
    better-spread one (larger min_gap) — door_order ranks ABOVE the spread terms."""
    s = _scenario(door_order=("a", "b", "c"))
    layout = _empty_layout(s)
    # door_matching: deviation 0 but a SMALLER min_gap (worse spread)
    door_matching = _SpreadCandidate(
        layout=layout, min_gap=1.0, energy=9.0, restart_index=0, door_deviation=0.0
    )
    # better_spread: a LARGER min_gap (better spread) but violates the order
    better_spread = _SpreadCandidate(
        layout=layout, min_gap=8.0, energy=1.0, restart_index=1, door_deviation=2.0
    )
    selected, _ = _select_spread_diverse(
        [better_spread, door_matching], alternatives=1, diversity=DiversityConfig()
    )
    assert selected[0] is door_matching


def test_selection_spread_breaks_ties_within_equal_door_deviation():
    """Among candidates with EQUAL door deviation, the larger-min_gap (better
    spread) one still wins — spread is the lower-priority tie-breaker."""
    s = _scenario(door_order=("a", "b", "c"))
    layout = _empty_layout(s)
    worse = _SpreadCandidate(
        layout=layout, min_gap=2.0, energy=5.0, restart_index=0, door_deviation=1.0
    )
    better = _SpreadCandidate(
        layout=layout, min_gap=7.0, energy=1.0, restart_index=1, door_deviation=1.0
    )
    selected, _ = _select_spread_diverse(
        [worse, better], alternatives=1, diversity=DiversityConfig()
    )
    assert selected[0] is better


# --- solve-level contracts -------------------------------------------------


def _key(layouts):
    return [
        [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in layout.placements] for layout in layouts
    ]


def test_solve_door_order_none_deterministic():
    s = _scenario()  # door_order None
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)


def test_solve_door_order_never_returns_invalid_layout():
    """door_order can never make an invalid layout selectable: the returned
    layout is always collision-valid (the pool is hard-valid by construction)."""
    s = _scenario(door_order=("c", "b", "a"))
    cfg = SearchConfig(max_restarts=4, spread=True)
    res = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert res.status == "found"
    for layout in res.layouts:
        assert check(layout).conflicts == ()


def test_solve_door_order_active_deterministic():
    s = _scenario(door_order=("c", "a", "b"))
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)

import pytest

from hangarfit.models import Placement, SearchConfig
from hangarfit.solver import _inter_plane_energy, _region_energy, _resolve_spread_scale, solve


def test_inter_plane_energy_includes_movers(region_scenario):
    s = region_scenario
    scale = _resolve_spread_scale(s, SearchConfig())
    placements = {
        "fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False),
        "glider_trailer_1": Placement("glider_trailer_1", 6.0, 8.0, 0.0, on_carts=False),
    }
    # must not KeyError on the mover, and two near bodies must repel (>0)
    assert _inter_plane_energy(placements, s, scale) > 0.0


def test_inter_plane_energy_no_go_deterministic(region_scenario_no_go):
    s = region_scenario_no_go
    scale = _resolve_spread_scale(s, SearchConfig())
    placements = {
        "fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False),
        "cessna_150": Placement("cessna_150", 9.0, 8.0, 0.0, on_carts=False),
    }
    assert _inter_plane_energy(placements, s, scale) == _inter_plane_energy(placements, s, scale)


def test_region_energy_empty_is_zero(region_scenario_no_go):
    placements = {"fuji": Placement("fuji", 5.0, 8.0, 0.0, on_carts=False)}
    assert _region_energy(placements, region_scenario_no_go) == 0.0  # no prefs ⇒ inert


def test_region_energy_right_minimized_at_right_wall(region_scenario):
    W = region_scenario.hangar.width_m
    near_left = {"glider_trailer_1": Placement("glider_trailer_1", 1.0, 10.0, 0.0, on_carts=False)}
    near_right = {
        "glider_trailer_1": Placement("glider_trailer_1", W - 1.0, 10.0, 0.0, on_carts=False)
    }
    assert _region_energy(near_right, region_scenario) < _region_energy(near_left, region_scenario)


def test_region_energy_formula(region_scenario):
    W = region_scenario.hangar.width_m
    x = 3.0
    pl = {"glider_trailer_1": Placement("glider_trailer_1", x, 10.0, 0.0, on_carts=False)}
    # weight 1.5, side right ⇒ 1.5 * (W - x)/W   (glider_trailer_2 absent ⇒ skipped)
    assert _region_energy(pl, region_scenario) == pytest.approx(1.5 * (W - x) / W)


def _key(layouts):
    return [
        [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in layout.placements] for layout in layouts
    ]


def test_solve_no_region_pref_byte_identical(region_scenario_no_go):
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(region_scenario_no_go, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(region_scenario_no_go, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)


def test_solve_region_pref_active_deterministic(region_scenario):
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(region_scenario, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(region_scenario, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)


def _trailer_x(result, trailer_id="glider_trailer_1"):
    return next(
        p.x_m for p in result.layouts[0].ground_object_placements if p.plane_id == trailer_id
    )


def test_region_pref_pulls_trailer_right(region_scenario, region_scenario_left):
    cfg = SearchConfig(max_restarts=6, spread=True)
    r = solve(region_scenario, search=cfg, seed=1, budget_s=120.0, plan_paths=False)
    lft = solve(region_scenario_left, search=cfg, seed=1, budget_s=120.0, plan_paths=False)
    assert r.status == "found" and lft.status == "found"
    # right-preferring trailer settles further right than the left-preferring one
    assert _trailer_x(r) > _trailer_x(lft)
    # and the right one is actually in the right half of the 24 m-wide hangar
    assert _trailer_x(r) > region_scenario.hangar.width_m / 2


def test_region_alignment_surfaced_in_diagnostics(region_scenario):
    r = solve(
        region_scenario,
        search=SearchConfig(max_restarts=6, spread=True),
        seed=1,
        budget_s=120.0,
        plan_paths=False,
    )
    align = r.diagnostics.region_alignment
    assert len(align) == len(r.layouts)
    layout0 = dict(align[0])
    assert 0.0 <= layout0["glider_trailer_1"] <= 1.0
    # right-pref trailer ends near the right wall ⇒ alignment high
    assert layout0["glider_trailer_1"] > 0.5


def test_region_alignment_empty_when_no_pref(region_scenario_no_go):
    r = solve(
        region_scenario_no_go,
        search=SearchConfig(max_restarts=4, spread=True),
        seed=0,
        budget_s=120.0,
        plan_paths=False,
    )
    assert r.diagnostics.region_alignment == ()


def test_demo_solver_places_and_right_biases_both_trailers(region_scenario):
    cfg = SearchConfig(max_restarts=8, spread=True)
    r = solve(region_scenario, search=cfg, seed=0, budget_s=60.0, plan_paths=False)
    assert r.status == "found"
    layout = r.layouts[0]
    go_x = {p.plane_id: p.x_m for p in layout.ground_object_placements}
    # both trailers were PLACED by the solver
    assert {"glider_trailer_1", "glider_trailer_2"}.issubset(go_x)
    # ... and RIGHT-biased: physically in the right half of the 24 m-wide hangar
    half = region_scenario.hangar.width_m / 2
    assert go_x["glider_trailer_1"] > half
    assert go_x["glider_trailer_2"] > half
    # ... reflected in the surfaced region_alignment diagnostic (1.0 = at right wall)
    align = dict(r.diagnostics.region_alignment[0])
    assert align["glider_trailer_1"] > 0.5
    assert align["glider_trailer_2"] > 0.5
    # the fixed fuel trailer stays at its authored keep-out pose (front-left)
    assert go_x["maul_fuel_trailer"] < half

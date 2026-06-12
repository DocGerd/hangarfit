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

from hangarfit.models import Placement, SearchConfig
from hangarfit.solver import _inter_plane_energy, _resolve_spread_scale


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

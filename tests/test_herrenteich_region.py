from pathlib import Path

import pytest

from hangarfit.loader import load_scenario
from hangarfit.models import SearchConfig
from hangarfit.solver import solve

HERRENTEICH = Path(__file__).parent.parent / "examples" / "herrenteich"


def test_herrenteich_scenario_loads_with_region_prefs():
    s = load_scenario(HERRENTEICH / "scenario.yaml")
    assert s.region_preferences  # opted in
    assert {"glider_trailer_1", "glider_trailer_2"}.issubset(set(s.mover_ids))
    # fuel trailer is a fixed keep-out, not a mover
    fixed_ids = {p.plane_id for p in s.fixed_obstacle_placements}
    assert "maul_fuel_trailer" in fixed_ids
    assert s.region_preferences["glider_trailer_1"].side == "right"


@pytest.mark.slow
def test_herrenteich_solve_terminates_cleanly():
    # The real all-eight set may be intractable (#599); the contract is that solve
    # TERMINATES with a well-formed status and never raises — not that it succeeds.
    s = load_scenario(HERRENTEICH / "scenario.yaml")
    r = solve(s, search=SearchConfig(max_restarts=4, spread=True), seed=0, budget_s=30.0)
    assert r.status in ("found", "found_partial", "exhausted_budget", "trivially_infeasible")

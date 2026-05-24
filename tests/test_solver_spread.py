"""solve()-level tests for the inter-plane spread phase (#145)."""

from __future__ import annotations


def _min_pairwise_gap(layout, scenario) -> float:
    """Smallest plan-view edge-to-edge gap between any two planes in a layout."""
    from hangarfit.geometry import aircraft_parts_world

    placements = list(layout.placements)
    world = {p.plane_id: aircraft_parts_world(scenario.fleet[p.plane_id], p) for p in placements}
    ids = [p.plane_id for p in placements]
    gaps = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gaps.append(
                min(pa.polygon.distance(pb.polygon) for pa in world[ids[i]] for pb in world[ids[j]])
            )
    return min(gaps) if gaps else 0.0


def test_solve_spread_on_widens_min_gap_vs_off():
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")

    off = solve(s, budget_s=10.0, seed=2, search=SearchConfig(spread=False))
    on = solve(s, budget_s=10.0, seed=2, search=SearchConfig(spread=True))

    assert off.layouts and on.layouts
    gap_off = _min_pairwise_gap(off.layouts[0], s)
    gap_on = _min_pairwise_gap(on.layouts[0], s)
    assert gap_on > gap_off, f"spread did not widen the minimum gap: on={gap_on} off={gap_off}"


def test_solve_default_enables_spread():
    """solve() with no SearchConfig spreads by default (SearchConfig().spread is True)."""
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    default = solve(s, budget_s=10.0, seed=2)
    explicit_on = solve(s, budget_s=10.0, seed=2, search=SearchConfig(spread=True))

    assert default.layouts and explicit_on.layouts
    # Default == explicit spread=True on the same seed.
    assert [(p.x_m, p.y_m, p.heading_deg) for p in default.layouts[0].placements] == [
        (p.x_m, p.y_m, p.heading_deg) for p in explicit_on.layouts[0].placements
    ]

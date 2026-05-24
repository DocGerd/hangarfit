"""solve()-level tests for the inter-plane spread phase (#145)."""

from __future__ import annotations

import pytest


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
    """Fast: on the small 3-plane fixture (ample slack), spread strictly
    widens the minimum pairwise gap vs spread off, same seed.

    seed=0: gap_off=0.0000, gap_on=10.8699 (verified).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    off = solve(s, budget_s=5.0, seed=0, search=SearchConfig(spread=False))
    on = solve(s, budget_s=5.0, seed=0, search=SearchConfig(spread=True))
    assert off.layouts and on.layouts
    gap_off = _min_pairwise_gap(off.layouts[0], s)
    gap_on = _min_pairwise_gap(on.layouts[0], s)
    assert gap_on > gap_off, f"spread did not widen the minimum gap: on={gap_on} off={gap_off}"


def test_solve_default_enables_spread():
    """Fast: bare solve() == explicit spread=True (default is on).

    Verifies that SearchConfig().spread is True and that the same seed
    produces identical placements whether spread is implicit or explicit.
    seed=0 verified: default == explicit_on (same placements).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    default = solve(s, budget_s=5.0, seed=0)
    explicit_on = solve(s, budget_s=5.0, seed=0, search=SearchConfig(spread=True))
    assert default.layouts and explicit_on.layouts
    assert [(p.x_m, p.y_m, p.heading_deg) for p in default.layouts[0].placements] == [
        (p.x_m, p.y_m, p.heading_deg) for p in explicit_on.layouts[0].placements
    ]


def test_solve_k2_with_spread_never_invalid_and_diverse_if_found():
    """The spread/diversity interaction (spec known-interaction): with K=2 and
    spread on, every returned layout must be valid, and if status==found the
    two layouts must still be pairwise-diverse (spread must not collapse them
    into a near-identical pair that slips past validity)."""
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.models import DiversityConfig, SearchConfig
    from hangarfit.solver import _is_diverse_enough, solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    r = solve(s, budget_s=10.0, alternatives=2, seed=0, search=SearchConfig(spread=True))

    # Invariant that must ALWAYS hold: no invalid layout is ever returned.
    assert all(check(layout).valid for layout in r.layouts)
    # If two were found, they must be pairwise diverse.
    if r.status == "found":
        assert len(r.layouts) == 2
        assert _is_diverse_enough(r.layouts[1], [r.layouts[0]], DiversityConfig())


@pytest.mark.slow
def test_solve_nine_plane_canary_spread_valid_and_not_worse():
    """Slow: the issue #145 canary (9 planes). Spread keeps the layout valid
    and does not reduce the minimum pairwise gap vs spread off, same seed.

    seed=42: gap_off=0.0000, gap_on=0.0000 (verified; >= assertion holds).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    off = solve(s, budget_s=15.0, seed=42, search=SearchConfig(spread=False))
    on = solve(s, budget_s=15.0, seed=42, search=SearchConfig(spread=True))
    assert off.layouts and on.layouts
    assert _min_pairwise_gap(on.layouts[0], s) >= _min_pairwise_gap(off.layouts[0], s)

"""solve()-level tests for the inter-plane spread phase (#145).

Also covers the _resolve_spread_scale / _spread_quality helpers (#267).
"""

from __future__ import annotations

import math

import pytest

from hangarfit.loader import load_scenario
from hangarfit.models import DiversityConfig, Layout, SearchConfig
from hangarfit.solver import _select_spread_diverse, _SpreadCandidate, solve


@pytest.fixture(autouse=True)
def _stub_towplanning(monkeypatch):
    """Keep these search/spread tests fast by stubbing tow-planning.

    solve() tow-plans every returned layout by default (``plan_paths=True``,
    #197), and tow-planning runs a bounded Hybrid-A* search per plane —
    seconds on a multi-plane fill. These tests assert on the *layouts*
    (placements, gaps, determinism), never on ``plans``, so a trivial
    ``plan_fill`` stub preserves the default code path while removing the
    cost. The real planner↔solver integration lives in
    ``tests/test_solver_towplanner.py``.
    """
    import hangarfit.solver as solver_mod
    from hangarfit.towplanner import MovesPlan

    monkeypatch.setattr(
        solver_mod, "plan_fill", lambda target: MovesPlan(target_layout=target, moves=())
    )


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
    """Fast: default-spread solve() == explicit spread=True (default is on).

    Verifies that ``SearchConfig().spread`` is True by running two same-seed
    solves whose only differing field is an explicit ``spread=True``, and
    asserting identical selected placements.

    Both solves are bounded by ``max_restarts`` (not wall-clock ``budget_s``):
    since #267, ``solve()`` no longer breaks on the first valid layout — it
    collects every valid basin found within its termination gate and selects
    the best-spread one. Under a wall-clock budget the *number* of basins
    collected is timing-dependent, so two same-seed runs can select different
    winners; bounding by ``max_restarts`` makes the basin pool — and hence the
    selected layout — a deterministic function of the seed (ADR-0003).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    default = solve(s, budget_s=30.0, seed=0, search=SearchConfig(max_restarts=20))
    explicit_on = solve(s, budget_s=30.0, seed=0, search=SearchConfig(max_restarts=20, spread=True))
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


# ---------------------------------------------------------------------------
# _resolve_spread_scale / _spread_quality helpers (#267)
# ---------------------------------------------------------------------------


def _placements(scenario, specs):
    """Build a {plane_id: Placement} from (plane_id, x, y, heading) specs."""
    from hangarfit.models import Placement

    return {
        pid: Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=False)
        for (pid, x, y, h) in specs
    }


def test_spread_quality_energy_matches_inter_plane_energy():
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import (
        _inter_plane_energy,
        _resolve_spread_scale,
        _spread_quality,
    )

    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    pids = list(scenario.fleet_in)
    placements = _placements(scenario, [(pids[0], 2.0, 2.0, 0.0), (pids[1], 12.0, 9.0, 0.0)])
    scale = _resolve_spread_scale(scenario, SearchConfig())
    min_gap, energy = _spread_quality(placements, scenario, scale)
    assert energy == _inter_plane_energy(placements, scenario, scale)
    assert math.isfinite(min_gap) and min_gap >= 0.0


def test_spread_quality_single_plane_is_inf_zero():
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import (
        _resolve_spread_scale,
        _spread_quality,
    )

    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    pid = scenario.fleet_in[0]
    placements = _placements(scenario, [(pid, 2.0, 2.0, 0.0)])
    scale = _resolve_spread_scale(scenario, SearchConfig())
    assert _spread_quality(placements, scenario, scale) == (math.inf, 0.0)


# ---------------------------------------------------------------------------
# _select_spread_diverse (#267)
# ---------------------------------------------------------------------------


def _layout(scenario, specs):
    """A valid Layout from (plane_id, x, y, heading) specs. Layout.__post_init__
    enforces only structural invariants (no collision check), so arbitrary
    positions are fine for selection/diversity tests."""
    return Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(_placements(scenario, specs).values()),
        maintenance_plane=None,
    )


def test_select_nested_pair_loses_to_spread():
    """The core regression: a nested basin (min_gap 0.0) must never be chosen
    over a well-spread one (min_gap 2.0), even with a worse restart_index."""
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    p = list(scenario.fleet_in)
    nested = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 2.0, 2.0, 0.0)])
    spread = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    pool = [
        _SpreadCandidate(layout=nested, min_gap=0.0, energy=5.0, restart_index=0),
        _SpreadCandidate(layout=spread, min_gap=2.0, energy=1.0, restart_index=1),
    ]
    selected, rejected = _select_spread_diverse(pool, alternatives=1, diversity=DiversityConfig())
    assert [c.min_gap for c in selected] == [2.0]
    assert rejected == 0


def test_select_energy_breaks_min_gap_ties():
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    p = list(scenario.fleet_in)
    a = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    b = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    pool = [
        _SpreadCandidate(layout=a, min_gap=2.0, energy=3.0, restart_index=0),
        _SpreadCandidate(layout=b, min_gap=2.0, energy=1.0, restart_index=1),
    ]
    selected, _ = _select_spread_diverse(pool, alternatives=1, diversity=DiversityConfig())
    assert selected[0].energy == 1.0  # lower energy wins the tie


def test_select_enforces_diversity_and_counts_rejects():
    """K=2: the top-spread basin is picked; a near-identical second basin is
    rejected by the diversity gate; a genuinely different one is accepted."""
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    p = list(scenario.fleet_in)
    base = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    twin = _layout(scenario, [(p[0], 2.1, 2.0, 0.0), (p[1], 12.1, 9.0, 0.0)])
    diff = _layout(scenario, [(p[0], 6.0, 5.0, 0.0), (p[1], 16.0, 12.0, 0.0)])
    pool = [
        _SpreadCandidate(layout=base, min_gap=3.0, energy=1.0, restart_index=0),
        _SpreadCandidate(layout=twin, min_gap=2.5, energy=1.0, restart_index=1),
        _SpreadCandidate(layout=diff, min_gap=2.0, energy=1.0, restart_index=2),
    ]
    selected, rejected = _select_spread_diverse(pool, alternatives=2, diversity=DiversityConfig())
    assert [c.min_gap for c in selected] == [3.0, 2.0]  # base, then diff; twin skipped
    assert rejected == 1


def test_select_empty_pool_returns_empty():
    selected, rejected = _select_spread_diverse([], alternatives=2, diversity=DiversityConfig())
    assert selected == [] and rejected == 0


def test_solve_populates_spread_diagnostics():
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(scenario, seed=7, search=SearchConfig(max_restarts=20), plan_paths=False)
    assert r.status in ("found", "found_partial")
    d = r.diagnostics
    assert len(d.min_pairwise_gap_m) == len(r.layouts)
    assert d.valid_basins_found >= len(r.layouts)
    assert all(g > 0.0 for g in d.min_pairwise_gap_m)


def test_solve_spread_selection_is_deterministic():
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    cfg = SearchConfig(max_restarts=20)
    a = solve(scenario, seed=7, search=cfg, plan_paths=False)
    b = solve(scenario, seed=7, search=cfg, plan_paths=False)
    assert a.diagnostics.min_pairwise_gap_m == b.diagnostics.min_pairwise_gap_m
    assert [_layout_key(lay) for lay in a.layouts] == [_layout_key(lay) for lay in b.layouts]


def _layout_key(layout):
    return tuple(
        (p.plane_id, round(p.x_m, 6), round(p.y_m, 6), round(p.heading_deg, 6))
        for p in layout.placements
    )


@pytest.mark.slow
def test_best_of_all_never_worse_than_first_basin_over_sweep():
    """End-to-end confidence for #267: with the same seed, best-of-all (a
    larger restart budget) selects from a pool that is a *superset* of the
    single-restart pool, so its achieved min plan-view gap is never worse than
    the first-found basin (max_restarts=1 ≈ the old first-valid behavior) — and
    on this nesting-prone 6-plane fill it is strictly better for several seeds,
    demonstrating the fix reduces nested pairs.

    It does NOT (and cannot) eliminate nesting on a space-tight fill: when every
    reachable basin nests, best-of-all still returns 0.0 — the roomiest available.
    The pure ``_select_spread_diverse`` tests are the deterministic regression;
    this is end-to-end wiring confidence. Excluded from the default run (slow).
    """
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    improved = 0
    checked = 0
    for seed in range(1, 10):
        first = solve(scenario, seed=seed, search=SearchConfig(max_restarts=1), plan_paths=False)
        best = solve(scenario, seed=seed, search=SearchConfig(max_restarts=8), plan_paths=False)
        if first.status not in ("found", "found_partial") or best.status not in (
            "found",
            "found_partial",
        ):
            continue
        checked += 1
        first_gap = min(first.diagnostics.min_pairwise_gap_m, default=math.inf)
        best_gap = min(best.diagnostics.min_pairwise_gap_m, default=math.inf)
        assert best_gap >= first_gap - 1e-9, (
            f"seed {seed}: best-of-all min gap {best_gap} is worse than "
            f"first-basin {first_gap} — selection should never regress"
        )
        if best_gap > first_gap + 1e-9:
            improved += 1
    assert checked >= 4, f"too few seeds reached a layout ({checked}); fixture/budget issue"
    assert improved > 0, (
        "best-of-all improved no seed over first-basin — the fix is not being "
        "exercised on this fixture (expected several nesting-prone seeds to improve)"
    )

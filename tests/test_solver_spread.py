"""solve()-level tests for the inter-plane spread phase (#145).

Also covers the _resolve_spread_scale / _spread_quality helpers (#267).
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from hangarfit.loader import load_scenario
from hangarfit.models import DiversityConfig, Layout, PlaneConstraint, SearchConfig
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

    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    default = solve(scenario, seed=7, search=SearchConfig(max_restarts=5), plan_paths=False)
    explicit_on = solve(
        scenario, seed=7, search=SearchConfig(spread=True, max_restarts=5), plan_paths=False
    )
    assert default.layouts and explicit_on.layouts
    assert [(p.x_m, p.y_m, p.heading_deg) for p in default.layouts[0].placements] == [
        (p.x_m, p.y_m, p.heading_deg) for p in explicit_on.layouts[0].placements
    ]


def test_solve_back_fill_parks_single_plane_at_back_wall():
    """#320 acceptance: with back-fill a lone plane parks deep against the back
    wall — and strictly deeper than with back-fill off, same seed. Bounded by
    ``max_restarts`` so the basin pool (and selection) is seed-deterministic."""
    s = load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")
    off = solve(
        s, seed=42, search=SearchConfig(max_restarts=5, back_bias_weight=0.0), plan_paths=False
    )
    on = solve(
        s, seed=42, search=SearchConfig(max_restarts=5, back_bias_weight=1.0), plan_paths=False
    )
    assert off.layouts and on.layouts
    (p_off,) = off.layouts[0].placements
    (p_on,) = on.layouts[0].placements
    assert p_on.y_m > p_off.y_m  # back-fill pulls the lone plane deeper
    assert p_on.y_m > 0.7 * s.hangar.length_m  # and up against the back wall


def test_solve_back_fill_clears_the_door_on_two_plane_fill():
    """#320 acceptance: on the 2-plane minimal fill, back-fill leaves free space
    at the door — the front-most plane sits deeper than with back-fill off, and
    no plane is parked in the front third of the hangar."""
    s = load_scenario("tests/fixtures/scenario_minimal.yaml")
    off = solve(
        s, seed=1, search=SearchConfig(max_restarts=5, back_bias_weight=0.0), plan_paths=False
    )
    on = solve(
        s, seed=1, search=SearchConfig(max_restarts=5, back_bias_weight=1.0), plan_paths=False
    )
    assert off.layouts and on.layouts
    front_off = min(p.y_m for p in off.layouts[0].placements)
    front_on = min(p.y_m for p in on.layouts[0].placements)
    assert front_on > front_off  # door-side cleared relative to the spread-only baseline
    assert front_on > s.hangar.length_m / 3  # no plane left in the front (door) third


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
    # 2-plane fixture + small restart bound keeps this wiring check fast and
    # deterministic; it verifies the diagnostics are populated and aligned, not
    # the (space-dependent) achievement of a positive gap.
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    r = solve(scenario, seed=7, search=SearchConfig(max_restarts=5), plan_paths=False)
    assert r.status in ("found", "found_partial")
    d = r.diagnostics
    assert len(d.min_pairwise_gap_m) == len(r.layouts)
    assert d.valid_basins_found >= len(r.layouts)
    # gaps are real edge-to-edge distances: non-negative (inf only for <2 planes,
    # which this 2-plane fixture never produces).
    assert all(g >= 0.0 for g in d.min_pairwise_gap_m)


def test_solve_spread_selection_is_deterministic():
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    cfg = SearchConfig(max_restarts=5)
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

    Seed sweep widened from ``range(1, 10)`` to ``range(1, 20)`` for #282: the
    wing-strut spar-axis fix shifts each strut ~0.97 m forward (off the wing
    trailing edge onto the quarter-chord spar), which tightens this
    nesting-prone fill. Under that geometry only 3 of seeds 1–9 reach a layout
    on a single restart and all three nest at gap 0.0, so the old range no
    longer carried enough reaching-and-improving seeds to satisfy the
    ``checked``/``improved`` floors below. The wider sweep restores a healthy
    sample (≈7 reach, ≥2 improve) without touching the actual invariant
    assertions (best-of-all never regresses; the fix is exercised). Verified by
    sweep, not flakiness: see issue #282.
    """
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    improved = 0
    checked = 0
    for seed in range(1, 20):
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


# ── #441: soft PlaneConstraint.priority weights the spread repulsion ─────


def _with_priority(scenario, **priorities):
    """A copy of ``scenario`` with ``PlaneConstraint.priority`` set per plane id."""
    cons = dict(scenario.constraints)
    for pid, p in priorities.items():
        cons[pid] = dataclasses.replace(cons.get(pid, PlaneConstraint()), priority=p)
    return dataclasses.replace(scenario, constraints=cons)


def _placements_key(result):
    return [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in result.layouts[0].placements]


def test_priority_weight_helper():
    from hangarfit.solver import _priority_weight

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    pid = sorted(s.fleet_in)[0]
    assert _priority_weight(s, pid) == 1.0  # unset → neutral 1.0
    s2 = _with_priority(s, **{pid: 2.0})
    assert _priority_weight(s2, pid) == 3.0  # 1.0 + 2.0
    assert _priority_weight(s2, sorted(s.fleet_in)[1]) == 1.0  # others unaffected


def test_inter_plane_energy_inert_when_all_priorities_zero():
    """priority 0.0 on every plane is byte-identical to no constraints (ADR-0003)."""
    from hangarfit.solver import _inter_plane_energy, _resolve_spread_scale

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    res = solve(s, seed=0, search=SearchConfig(max_restarts=3), plan_paths=False)
    placements = {p.plane_id: p for p in res.layouts[0].placements}
    scale = _resolve_spread_scale(s, SearchConfig())
    base = _inter_plane_energy(placements, s, scale)
    s0 = _with_priority(s, **{pid: 0.0 for pid in s.fleet_in})
    assert _inter_plane_energy(placements, s0, scale) == base  # exact, not approx


def test_inter_plane_energy_strictly_increases_with_priority():
    """A positive priority weights that plane's pairs up, raising total energy."""
    from hangarfit.solver import _inter_plane_energy, _resolve_spread_scale

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    res = solve(s, seed=0, search=SearchConfig(max_restarts=3), plan_paths=False)
    placements = {p.plane_id: p for p in res.layouts[0].placements}
    assert len(placements) >= 2
    scale = _resolve_spread_scale(s, SearchConfig())
    base = _inter_plane_energy(placements, s, scale)
    hi = _with_priority(s, **{sorted(s.fleet_in)[0]: 3.0})
    assert _inter_plane_energy(placements, hi, scale) > base


# ---------------------------------------------------------------------------
# Incremental single-plane gap cache in _inter_plane_energy (#455)
#
# The spread hill-climb moves ONE plane per iteration and evaluates several
# candidates for it. Pairs that do NOT involve the moved plane are identical
# across all candidates, so their (expensive) shapely edge-to-edge distance can
# be computed once and reused. The SAFE form re-sums ALL pairs in canonical
# sorted order using cached distances for the unchanged pairs — bit-identical to
# the full recompute (ADR-0003), unlike a delta-update which drifts ~1e-15 and
# flips acceptance (#455).
# ---------------------------------------------------------------------------


def _three_plane_placements(scenario):
    """A valid 3-plane layout's placements dict (plane_id -> Placement)."""
    res = solve(scenario, seed=0, search=SearchConfig(max_restarts=3), plan_paths=False)
    return {p.plane_id: p for p in res.layouts[0].placements}


def test_gap_cache_byte_identical_to_full_recompute():
    """A populated gap cache yields the EXACT same energy as the cache-free full
    recompute — including after moving ONLY the cached ``moved`` plane, where the
    unchanged pairs stay valid. Bit-identical (``==``), not approximate."""
    from hangarfit.solver import _inter_plane_energy, _resolve_spread_scale

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    placements = _three_plane_placements(s)
    scale = _resolve_spread_scale(s, SearchConfig())
    moved = sorted(placements)[0]

    cache: dict = {}
    e1 = _inter_plane_energy(placements, s, scale, gap_cache=cache, moved=moved)
    assert e1 == _inter_plane_energy(placements, s, scale)  # exact, not approx

    # Perturb ONLY ``moved``; the other pair(s) are unchanged, so the cache from
    # the first call stays valid and the reused result must equal a fresh full
    # sum (the moved plane's own pairs are always recomputed).
    shifted = dataclasses.replace(placements[moved], x_m=placements[moved].x_m + 1.0)
    moved_layout = {**placements, moved: shifted}
    e2 = _inter_plane_energy(moved_layout, s, scale, gap_cache=cache, moved=moved)
    assert e2 == _inter_plane_energy(moved_layout, s, scale)  # exact, not approx


def test_gap_cache_is_consulted_for_unchanged_pairs():
    """The cache is actually READ, not accepted-and-ignored: poisoning the cached
    unchanged-pair distance changes the computed energy. Only the non-``moved``
    pairs are cached (n=3 with one moved plane => exactly one cacheable pair); the
    ``moved`` plane's pairs are always recomputed fresh."""
    from hangarfit.solver import _inter_plane_energy, _resolve_spread_scale

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    placements = _three_plane_placements(s)
    scale = _resolve_spread_scale(s, SearchConfig())
    moved = sorted(placements)[0]

    cache: dict = {}
    e_clean = _inter_plane_energy(placements, s, scale, gap_cache=cache, moved=moved)
    assert len(cache) == 1  # only the single unchanged (non-moved) pair is cached

    for key in list(cache):
        cache[key] = 1.0e9  # poison: a huge gap => ~0 repulsion contribution
    e_poisoned = _inter_plane_energy(placements, s, scale, gap_cache=cache, moved=moved)
    assert e_poisoned < e_clean  # the cached unchanged pair was consulted


def test_solve_priority_zero_byte_identical_to_no_constraints():
    """Inert-by-default at solve() level: all-zero priority selects the SAME
    layout as no priority, same seed (ADR-0003, max_restarts-scoped)."""
    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    base = solve(s, seed=3, search=SearchConfig(max_restarts=4), plan_paths=False)
    s0 = _with_priority(s, **{pid: 0.0 for pid in s.fleet_in})
    zero = solve(s0, seed=3, search=SearchConfig(max_restarts=4), plan_paths=False)
    assert base.layouts and zero.layouts
    assert _placements_key(base) == _placements_key(zero)


def test_solve_with_priority_is_deterministic():
    """ADR-0003 still holds with priority active: same seed → byte-identical."""
    base = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    s = _with_priority(base, **{sorted(base.fleet_in)[0]: 6.0})
    a = solve(s, seed=1, search=SearchConfig(max_restarts=4), plan_paths=False)
    b = solve(s, seed=1, search=SearchConfig(max_restarts=4), plan_paths=False)
    assert a.layouts and b.layouts
    assert _placements_key(a) == _placements_key(b)


def test_solve_priority_changes_the_selected_layout():
    """The soft weight actually influences placement: a large priority on one
    plane shifts the spread-selected layout away from the no-priority one (same
    seed). Proves the hook is live, not just inert — distinct from the inertness
    and determinism guarantees above."""
    base_s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    pid = sorted(base_s.fleet_in)[0]
    base = solve(base_s, seed=0, search=SearchConfig(max_restarts=4), plan_paths=False)
    hi = solve(
        _with_priority(base_s, **{pid: 20.0}),
        seed=0,
        search=SearchConfig(max_restarts=4),
        plan_paths=False,
    )
    assert base.layouts and hi.layouts
    assert _placements_key(base) != _placements_key(hi)

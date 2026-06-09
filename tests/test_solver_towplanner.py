"""Tests for solver.solve() — tow-plan bundling (Wave 2, Task 5 / #197).

Verifies that ``solve()`` returns a ``MovesPlan`` (or ``None``) per accepted
layout (index-aligned in ``SolveResult.plans``) and that the **best-effort**
behaviour is correct: a layout the tow planner cannot route is recorded as
``plans[i] is None`` (with the blocking plane in
``diagnostics.unroutable_planes``) and the valid static layout is still
returned — the tow plan is advisory enrichment, never a gate (ADR-0007 +
ADR-0010).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def solvable_scenario():
    """Minimal solvable scenario: one plane in a large hangar.

    Uses ``solve_trivial_single_plane.yaml`` — one Husky in the 30×25 m
    test hangar. This is the most reliable fixture for getting
    ``status='found'`` with a *towable* layout (a single plane always has
    a clear door-to-slot path) without depending on a tight budget or
    multi-plane ordering luck.
    """
    from hangarfit.loader import load_scenario

    return load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")


def test_solve_bundles_a_plan_per_layout(solvable_scenario):
    from hangarfit.solver import solve
    from hangarfit.towplanner import MovesPlan

    result = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=7)
    assert result.status in ("found", "found_partial")
    # Index-aligned, one entry per layout.
    assert len(result.plans) == len(result.layouts)
    # The single-plane fixture is towable, so every entry is a real MovesPlan.
    assert all(isinstance(p, MovesPlan) for p in result.plans)
    for layout, plan in zip(result.layouts, result.plans, strict=True):
        assert plan is not None
        assert plan.target_layout is layout
        assert {m.plane_id for m in plan.moves} == {p.plane_id for p in layout.placements}
    # No layout was un-routable, so the advisory list is empty.
    assert result.diagnostics.unroutable_planes == ()


# Marked `serial` (#492): a wall-clock `budget_s`-bounded double-solve determinism
# assert — it runs solve() twice and compares, so it must run outside the `-n auto`
# xdist pool (same rationale as tests/test_solver_canaries.py and
# test_solver_search.py::test_solve_is_deterministic_for_same_seed). Load-safe today
# only via the single-plane `solvable_scenario` fixture (selection collapses to the
# restart-index tie-break, _spread no-ops on a lone plane); pinned serial so a future
# multi-plane fixture cannot silently reintroduce a parallel-pool flake.
@pytest.mark.serial
def test_solve_bundle_is_deterministic_for_a_seed(solvable_scenario):
    from hangarfit.solver import solve

    a = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=42)
    b = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=42)
    assert a.status == b.status
    assert a.layouts == b.layouts
    assert a.plans == b.plans
    assert a.diagnostics.unroutable_planes == b.diagnostics.unroutable_planes


def test_solve_best_effort_none_when_a_layout_is_untowable(solvable_scenario, monkeypatch):
    """A layout the planner cannot route → plans[i] is None, layout kept."""
    import hangarfit.solver as solver_mod
    from hangarfit.models import Conflict
    from hangarfit.solver import solve
    from hangarfit.towplanner import NoFeasiblePlanError

    def boom(target, **kwargs):
        raise NoFeasiblePlanError("A", Conflict.single(kind="parts_overlap", plane="A", detail="x"))

    monkeypatch.setattr(solver_mod, "plan_fill", boom)
    result = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=7)
    # The valid static layout is NOT discarded — status stays search-driven.
    assert result.status in ("found", "found_partial")
    assert len(result.layouts) >= 1
    # ...but its tow plan is absent, and the blocking plane is recorded.
    assert len(result.plans) == len(result.layouts)
    assert all(p is None for p in result.plans)
    assert result.diagnostics.unroutable_planes == ("A",) * len(result.layouts)


def test_solve_plan_paths_false_skips_planning(solvable_scenario, monkeypatch):
    """plan_paths=False must not invoke the planner at all (the perf opt-out)."""
    import hangarfit.solver as solver_mod
    from hangarfit.solver import solve

    def fail_if_called(target, **kwargs):
        raise AssertionError("plan_fill must not be called when plan_paths=False")

    monkeypatch.setattr(solver_mod, "plan_fill", fail_if_called)
    result = solve(solvable_scenario, budget_s=10.0, alternatives=1, seed=7, plan_paths=False)
    assert result.status in ("found", "found_partial")
    assert len(result.layouts) >= 1
    # Still index-aligned, but unpopulated, and nothing was flagged un-routable.
    assert len(result.plans) == len(result.layouts)
    assert all(p is None for p in result.plans)
    assert result.diagnostics.unroutable_planes == ()


@pytest.mark.slow
def test_solve_best_effort_real_planner_on_untowable_fill():
    """End-to-end: the real tow planner on a dense fill it cannot route.

    ``solve_fresh_six_planes`` packs six planes into the tight placeholder
    hangar — a valid static layout the Reeds–Shepp + bounded Hybrid-A*
    planner cannot fully route (spike Risk #1). Best-effort must still
    return the layout(s) with a ``None`` plan and name the blocking plane,
    rather than failing the whole solve. Slow (real per-plane search), so
    excluded from the default suite.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    result = solve(scenario, budget_s=10.0, alternatives=1, seed=7)
    # Search succeeds: a valid static layout exists.
    assert result.status in ("found", "found_partial")
    assert len(result.layouts) >= 1
    assert len(result.plans) == len(result.layouts)
    # The dense fill is un-routable, so the plan is None and the blocking
    # plane is recorded — but the valid layout is preserved.
    assert any(p is None for p in result.plans)
    assert len(result.diagnostics.unroutable_planes) == sum(1 for p in result.plans if p is None)
    assert result.diagnostics.unroutable_planes  # non-empty


@pytest.mark.slow
def test_six_plane_fresh_fill_partial_routing_post_480():
    """#512: post-#480, the tow-friendly (spread=False) fill of
    ``solve_fresh_six_planes`` (seed=1) is NO LONGER fully tow-routable at shipped
    defaults — an ACCEPTED realism-over-routability trade, not a bug.

    #336 originally shipped this as a *full*-routing guarantee: the obstacle-aware
    grid heuristic threaded ``aviat_husky``'s 10.82 m wing into the back corner in
    ~6062 expansions, and the seed=1 fill reached 5/5 at ~8.4 k total. #480's
    fewest-moves cost model (an additive ``CUSP_PENALTY`` per direction reversal,
    ADR-0010) roughly DOUBLED the per-plane cost of the deep, heading-hard slots:
    ``aviat_husky`` now needs ~12515 (measured, > the 8000 per-plane cap) and
    ``ctsl`` — cheap pre-#480 — exceeds even a 13000 cap. Routing the fill again
    would need per-plane ~20 k + global ~35 k, pushing the un-routable-disprove
    wall-clock past the ~400 s perf intent the 16000 global cap exists to hold (see
    the ``_MAX_EXPANSIONS`` comment in towplanner.py). So the budgets stay at
    8000/16000 and the solve is BEST-EFFORT here: it returns the valid static
    layout with the over-budget plane's plan ``None`` and that plane named in
    ``unroutable_planes``, rather than failing the whole solve.

    (Was ``test_six_plane_fresh_fill_fully_routes_at_shipped_defaults``, which
    asserted full routing under the pre-#480 ~6062-expansion path.) Slow (real
    per-plane search to the budget). The separate ``spread=True`` placement tension
    is #280 / back-fill #320, not this.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    result = solve(
        scenario, budget_s=15.0, alternatives=1, seed=1, search=SearchConfig(spread=False)
    )
    # The static layout is still found (a valid placement exists) — only its tow
    # plan is best-effort.
    assert result.status in ("found", "found_partial")
    assert len(result.layouts) == 1
    assert len(result.plans) == len(result.layouts)
    # Best-effort: at least one plane exceeds the shipped tow budget post-#480, so
    # its plan is None and it is named in unroutable_planes — the valid layout is
    # preserved, the whole solve does not fail.
    assert any(p is None for p in result.plans), (
        f"expected best-effort partial routing post-#480; plans={result.plans}"
    )
    assert result.diagnostics.unroutable_planes  # non-empty: names the blocking plane(s)
    assert len(result.diagnostics.unroutable_planes) == sum(1 for p in result.plans if p is None)


def test_solve_k_gt_1_bundle_alignment_with_mixed_routability(monkeypatch):
    """K>1: the per-layout loop builds an aligned plans tuple with a mix of
    real plans and None, and unroutable_planes records exactly the failed
    layouts in order.

    Uses the real RR-MC search to produce multiple diverse layouts, but a
    selective ``plan_fill`` stub that fails only the *second* layout planned —
    so we can pin the positional alignment (plans[1] is None, the others are
    real) deterministically, which no single-alternative test can.
    """
    import hangarfit.solver as solver_mod
    from hangarfit.loader import load_scenario
    from hangarfit.models import Conflict, SearchConfig
    from hangarfit.solver import solve
    from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError

    calls = {"n": 0}

    def fail_second_only(target, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            pid = target.placements[0].plane_id
            raise NoFeasiblePlanError(
                pid, Conflict.single(kind="no_feasible_path", plane=pid, detail="stub")
            )
        return MovesPlan(target_layout=target, moves=())

    monkeypatch.setattr(solver_mod, "plan_fill", fail_second_only)

    scenario = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    # Pin max_restarts (not budget_s) so the search WORK is load-independent
    # (ADR-0003): a wall-clock budget here found <2 basins on a loaded CI runner
    # once the empennage model made each restart heavier (#531). max_restarts=6
    # yields all 3 diverse layouts for this fixture+seed regardless of machine
    # speed (measured: >=4 restarts is sufficient; 6 for margin). budget_s is set
    # well above the 6-restart wall-clock (~5 s local, ~14 s CI) so max_restarts is
    # the sole termination gate even on a very slow runner — no latent budget flake.
    result = solve(
        scenario, budget_s=120.0, search=SearchConfig(max_restarts=6), alternatives=3, seed=0
    )

    # This fixture+seed must yield at least 2 diverse layouts for the test to
    # be meaningful; fail loudly (not vacuously) if the search regresses.
    assert len(result.layouts) >= 2, f"need >=2 layouts to test K>1 alignment, got {result.layouts}"
    assert len(result.plans) == len(result.layouts)
    # The 2nd layout planned is the only failure: positionally aligned None.
    assert result.plans[0] is not None
    assert result.plans[1] is None
    assert all(p is not None for i, p in enumerate(result.plans) if i != 1)
    # unroutable_planes is the compacted list of exactly that one failure.
    assert result.diagnostics.unroutable_planes == (result.layouts[1].placements[0].plane_id,)
    assert len(result.diagnostics.unroutable_planes) == sum(1 for p in result.plans if p is None)

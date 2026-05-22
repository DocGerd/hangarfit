"""Tests for solver.solve() — pre-search infeasibility + smoke."""

from __future__ import annotations


def test_solve_feasible_smoke_returns_exhausted_budget_placeholder():
    """Until Chunk D lands, any feasible scenario returns exhausted_budget."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    r = solve(s, budget_s=0.1, seed=42)

    # Chunk C placeholder: no search yet, so feasible inputs return
    # exhausted_budget immediately.
    assert r.status == "exhausted_budget"
    assert r.layouts == ()
    assert r.diagnostics.seed == 42
    assert r.diagnostics.wall_time_s < 1.0
    # best_partial is None at this stage (no search means no partial)
    assert r.diagnostics.best_partial is None


def test_solve_resolves_none_seed_to_entropy():
    """seed=None resolves to a random int and is recorded in diagnostics."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    r = solve(s, budget_s=0.1, seed=None)
    assert isinstance(r.diagnostics.seed, int)
    assert r.diagnostics.seed != 0  # entropy is essentially always nonzero
